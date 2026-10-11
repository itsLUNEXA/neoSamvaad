# Context-Aware Customer Support Agent

### Neo4j GraphRAG Mini Hack --- Architecture, Design Decisions, and Weakness Mitigations

> **Project goal:** Build a support agent that remembers customer issues
> across conversations, retrieves connected ticket and troubleshooting
> history from Neo4j, and recommends a next step without blindly
> repeating failed actions.

------------------------------------------------------------------------

## 1. Executive Summary

Traditional support bots may repeat generic troubleshooting because they
do not reliably connect a customer's current message to the history and
outcomes of earlier support attempts. This prototype models support
history as a graph and uses graph retrieval to supply relevant evidence
to an LLM.

The core differentiator is **resolution-aware memory**:

-   Remember the issue and the product involved.
-   Record each troubleshooting attempt and its outcome.
-   Retrieve relevant history when the customer returns in a new
    conversation.
-   Generate a response grounded in that retrieved history.
-   Show the evidence used, so a judge can verify why the response
    changed.

**Scope for a five-hour hack:** one customer-support workflow, a small
seeded dataset, one Neo4j graph, one retrieval query, one LLM call, and
a simple interface. Do not build a full helpdesk platform.

## 2. Problem Statement

Customer support systems can lose useful context between interactions.
Customers may need to repeat their issue, while a bot may recommend a
troubleshooting step that was already attempted and failed.

Build an AI customer-support agent that stores customer, product, issue,
ticket, troubleshooting-attempt, and outcome information in Neo4j. When
a customer starts a new conversation, the agent retrieves the relevant
connected history and generates a context-aware response with an
appropriate next step.

### Intended outcome

The prototype should demonstrate that a graph-backed retrieval step can
change the response by identifying previous attempts and their recorded
outcomes.

### Non-goals

For the mini hack, the system will **not**: - Automatically access real
customer accounts or perform account changes. - Diagnose real production
incidents without evidence. - Guarantee that a suggested action will
resolve an issue. - Build authentication, billing, escalation workflows,
or a full ticketing suite. - Require vector search or a multi-agent
framework.

------------------------------------------------------------------------

## 3. Architecture at a Glance

``` text
                         CUSTOMER
                            |
                            v
+---------------------------------------------------+
| FRONTEND — React + Tailwind                       |
| - Chat / complaint form                           |
| - Customer ID for the demo                        |
| - Retrieved ticket evidence                       |
| - Recommended next step                           |
+---------------------------------------------------+
                            |
                         HTTP/JSON
                            |
                            v
+---------------------------------------------------+
| BACKEND API — Python + FastAPI                     |
| 1. Validate request and customer ID                |
| 2. Create ticket or handle chat                    |
| 3. Call graph-retrieval service                    |
| 4. Build a bounded evidence context                |
| 5. Call the LLM with grounded-response rules       |
| 6. Return answer + evidence + uncertainty          |
+---------------------------------------------------+
                |                         |
                | Cypher query            | Context + prompt
                v                         v
+----------------------------+   +----------------------------+
| NEO4J AURADB               |   | LLM API                    |
| Persistent graph memory    |   | Response generation        |
| Customers, tickets, issues |   | Uses supplied evidence     |
| Attempts, outcomes         |   | Must not invent history    |
+----------------------------+   +----------------------------+
                ^                         |
                |                         |
                +-------------------------+
                   Response returned
                   through FastAPI
                            |
                            v
+---------------------------------------------------+
| FRONTEND RESPONSE                                 |
| - Context-aware answer                            |
| - Which ticket/attempts were retrieved             |
| - Suggested next step                              |
| - "No relevant history found" when appropriate     |
+---------------------------------------------------+
```

### Why this architecture?

-   **React:** separates the user experience from backend logic and
    makes it easy to show evidence beside the answer.
-   **FastAPI:** keeps database credentials and LLM keys on the server,
    not in the browser.
-   **Neo4j:** represents linked entities and supports multi-hop
    retrieval across tickets, issues, attempts, and outcomes.
-   **LLM:** turns retrieved evidence into a natural-language response.
    It should not be the source of historical facts.
-   **No vector database in the MVP:** a customer ID and structured
    Cypher traversal are sufficient for the seeded demo. Add semantic
    retrieval only if a tested use case needs it.

------------------------------------------------------------------------

## 4. Request and Data Flow

``` text
Customer sends message
        |
        v
FastAPI validates input and customer_id
        |
        +---- Is this a new complaint?
        |           |
        |           v
        |     Create Ticket / Issue records in Neo4j
        |
        v
Query Neo4j for recent relevant tickets,
attempts, outcomes, and status
        |
        v
Build a compact evidence package
(ticket IDs, issue, attempts, outcomes, status)
        |
        v
Send current message + evidence + response rules to LLM
        |
        v
Validate response structure and preserve evidence references
        |
        v
Return answer + retrieved evidence to frontend
        |
        v
If a new attempt occurs, record the attempt and outcome
in Neo4j; do not mark an outcome successful without evidence
```

**Important distinction:** retrieving memory and recording a new event
are separate operations. The agent should not invent a troubleshooting
attempt or mark a ticket resolved just because it suggested a step.

------------------------------------------------------------------------

## 5. Neo4j Graph Schema

### 5.1 Nodes

  -----------------------------------------------------------------------
  Node label              Suggested properties    Purpose
  ----------------------- ----------------------- -----------------------
  `Customer`              `customer_id`,          Stable customer
                          `display_name`          identity

  `Product`               `product_id`, `name`    Product involved

  `Issue`                 `issue_id`, `category`, The problem being
                          `description`,          reported
                          `normalized_key`        

  `Ticket`                `ticket_id`, `status`,  One support interaction
                          `created_at`,           or case
                          `updated_at`            

  `Attempt`               `attempt_id`, `method`, A troubleshooting
                          `outcome`, `timestamp`, action and recorded
                          `notes`                 result

  `Resolution`            `resolution_id`,        Confirmed or proposed
                          `action`, `status`,     resolution; keep
                          `recorded_at`           proposed and confirmed
                                                  states distinct
  -----------------------------------------------------------------------

### 5.2 Relationships

``` text
(:Customer)-[:USES]->(:Product)
(:Customer)-[:REPORTED]->(:Ticket)
(:Ticket)-[:CONCERNS]->(:Issue)
(:Ticket)-[:HAS_ATTEMPT]->(:Attempt)
(:Attempt)-[:RESULTED_IN]->(:Resolution)
(:Issue)-[:RELATED_TO]->(:Issue)       // optional; only when justified
(:Ticket)-[:INVOLVES]->(:Product)      // optional shortcut for easier retrieval
```

Relationship direction is a design choice; keep it consistent across
writes and reads. The model should avoid creating duplicate `Issue`
nodes for the same normalized issue where practical.

### 5.3 Example graph path

``` text
(Customer C-001)
   -[:REPORTED]->
(Ticket T-101)
   -[:CONCERNS]->
(Issue "login_failure")

(Ticket T-101)
   -[:HAS_ATTEMPT]->
(Attempt "password_reset", outcome="FAILED")
   -[:RESULTED_IN]->
(Resolution "issue_unresolved", status="UNRESOLVED")
```

The next conversation can retrieve this path and avoid presenting
password reset as a new, untried action.

### 5.4 Constraints and indexes

Create uniqueness constraints for stable IDs. Syntax below is suitable
for current Neo4j versions; confirm the AuraDB version if syntax
differs.

``` cypher
CREATE CONSTRAINT customer_id_unique IF NOT EXISTS
FOR (c:Customer) REQUIRE c.customer_id IS UNIQUE;

CREATE CONSTRAINT product_id_unique IF NOT EXISTS
FOR (p:Product) REQUIRE p.product_id IS UNIQUE;

CREATE CONSTRAINT issue_id_unique IF NOT EXISTS
FOR (i:Issue) REQUIRE i.issue_id IS UNIQUE;

CREATE CONSTRAINT ticket_id_unique IF NOT EXISTS
FOR (t:Ticket) REQUIRE t.ticket_id IS UNIQUE;

CREATE CONSTRAINT attempt_id_unique IF NOT EXISTS
FOR (a:Attempt) REQUIRE a.attempt_id IS UNIQUE;

CREATE CONSTRAINT resolution_id_unique IF NOT EXISTS
FOR (r:Resolution) REQUIRE r.resolution_id IS UNIQUE;
```

Uniqueness constraints reduce accidental duplicate records when the same
seed data or request is submitted more than once.

------------------------------------------------------------------------

## 6. Retrieval Query

This is an example query for a schema using the relationship directions
defined above. It retrieves recent tickets and their troubleshooting
attempts for a stable customer ID.

``` cypher
MATCH (c:Customer {customer_id: $customer_id})
      -[:REPORTED]->(t:Ticket)
OPTIONAL MATCH (t)-[:CONCERNS]->(i:Issue)
OPTIONAL MATCH (t)-[:HAS_ATTEMPT]->(a:Attempt)
OPTIONAL MATCH (a)-[:RESULTED_IN]->(r:Resolution)
WITH t, i, a, r
ORDER BY t.created_at DESC
WITH t, i, collect(
  CASE WHEN a IS NULL THEN null ELSE {
    attempt_id: a.attempt_id,
    method: a.method,
    outcome: a.outcome,
    timestamp: a.timestamp,
    resolution_action: r.action,
    resolution_status: r.status
  } END
) AS attempts
RETURN
  t.ticket_id AS ticket_id,
  t.status AS ticket_status,
  t.created_at AS created_at,
  i.issue_id AS issue_id,
  i.category AS issue_category,
  i.description AS issue_description,
  attempts
ORDER BY created_at DESC
LIMIT 5;
```

### Query safety and correctness

-   Always pass `customer_id` as a **parameter**; never interpolate user
    input into the Cypher string.
-   Use a stable customer ID to avoid mixing different customers'
    records.
-   Limit the number of returned tickets and evidence size.
-   Test the query against the exact seeded schema before the demo.
-   If no history is returned, tell the LLM that no relevant history was
    found. Do not fabricate one.
-   For a larger dataset, consider filtering by ticket status, issue
    category, or time window. Do not add complexity before the MVP
    works.

------------------------------------------------------------------------

## 7. LLM Grounding Contract

The LLM receives: 1. The current customer message. 2. The structured
records returned by Neo4j. 3. Clear instructions that historical claims
must be supported by retrieved records.

Suggested system instruction:

``` text
You are a customer-support assistant.

Use the supplied retrieved records as the only source for claims
about the customer's previous tickets and troubleshooting history.

Rules:
- Never claim an action was attempted unless the retrieved evidence says so.
- Never claim a fix succeeded unless a recorded outcome supports it.
- Do not recommend repeating a step recorded as FAILED unless you
  explain why a materially different retry is justified.
- Separate retrieved facts from hypotheses and recommendations.
- If the records are missing, ambiguous, or contradictory, say so
  and ask a clarifying question.
- Do not claim to have checked or changed a real account.
- Recommend safe, reversible next steps; escalate when the evidence
  is insufficient or the issue requires privileged account access.
- Keep the response concise and refer to ticket IDs when available.

Return:
1. customer-facing response
2. evidence used (ticket IDs and relevant attempt outcomes)
3. suggested next step
4. uncertainty or missing information
```

**Do not rely on prompting alone for safety.** Validate output fields in
the backend, display the retrieved evidence, and do not let the LLM
write arbitrary Cypher or execute account operations.

------------------------------------------------------------------------

## 8. API Contract

Keep the MVP to three endpoints.

### `POST /tickets`

Creates a ticket and its initial issue data.

Example request:

``` json
{
  "customer_id": "C-001",
  "product_id": "P-LOGIN",
  "issue_description": "Unable to log in",
  "category": "login_failure"
}
```

Expected behavior: - Validate required fields. - Create or reuse the
customer and product using stable IDs. - Create the ticket and issue
relationships. - Return the ticket ID and status.

### `POST /chat`

Retrieves context and generates a response.

Example request:

``` json
{
  "customer_id": "C-001",
  "message": "My account still isn't working."
}
```

Example response shape:

``` json
{
  "answer": "Your previous ticket records show that password reset failed. Let's inspect the recorded login error next.",
  "evidence": [
    {
      "ticket_id": "T-101",
      "attempt": "password_reset",
      "outcome": "FAILED"
    }
  ],
  "suggested_next_step": "Check the specific login error and account status",
  "uncertainty": "Account status has not been verified by this prototype"
}
```

The example response is illustrative; it is not a claim about a live
account.

### `GET /customers/{customer_id}/history`

Returns the customer's recent tickets and recorded troubleshooting
attempts for the evidence panel.

### Operational requirements

-   Keep Neo4j credentials and LLM API keys in environment variables.
-   Never expose secrets to the React client or commit `.env` files.
-   Use request validation, sensible timeouts, and error handling.
-   Return a controlled error if Neo4j or the LLM is unavailable.
-   Do not show another customer's history when an ID is invalid or
    unauthorized.

For the hackathon demo, a seeded customer ID can simplify the interface,
but clearly label this as demo-only and not production authentication.

------------------------------------------------------------------------

## 9. Weaknesses Identified and Mitigations

  -----------------------------------------------------------------------
  Weakness /        Why it matters    Mitigation in     How to
  failure mode                        this design       demonstrate the
                                                        mitigation
  ----------------- ----------------- ----------------- -----------------
  Looks like a      Graph value is    Store issues,     Show the graph
  generic chatbot   not clear         tickets,          path and explain
  with chat history                   attempts, and     how it changes
                                      outcomes as       the response
                                      connected nodes   

  LLM invents       False history     Ground historical Ask about a step
  previous actions  damages trust     claims in         absent from the
                                      retrieved records graph; agent must
                                      and display       not claim it
                                      evidence          happened

  Repeats a failed  The main user     Record attempt    Seed a failed
  solution          pain remains      outcome and       password reset
                    unsolved          instruct the      and verify it is
                                      agent to account  not blindly
                                      for it            repeated

  Superficial Neo4j Judges may view   Use a multi-hop   Display query
  integration       Neo4j as storage  Cypher query      output or a graph
                    only              across ticket,    visualization
                                      issue, attempt,   
                                      and resolution    

  Duplicate issues  Retrieval can     Stable IDs,       Submit the same
  or tickets        become confusing  uniqueness        seed data twice
                                      constraints,      and check for
                                      idempotent        duplicate nodes
                                      writes,           
                                      normalized issue  
                                      keys              

  Wrong customer    Privacy and       Parameterized     Test unknown IDs
  history           correctness       queries and       and ensure no
                    failure           stable customer   other customer's
                                      IDs; enforce      records appear
                                      authorization in  
                                      a real deployment 

  Ambiguous issue   Similar wording   Use customer      Test two
  matching          may refer to      identity,         different issues
                    different         product, issue    with similar
                    problems          category, and     wording
                                      explicit          
                                      clarification     
                                      when needed       

  False claim that  Recommendations   Separate proposed Show that merely
  a ticket is       can be misleading actions from      suggesting a fix
  resolved                            recorded          does not mark the
                                      outcomes; only    ticket resolved
                                      confirmed         
                                      outcomes update   
                                      status            

  Graph or LLM      Live demo can     Health checks,    Disconnect one
  service outage    fail              controlled        dependency in a
                                      errors, seeded    non-production
                                      data, and         test and verify
                                      screenshots as    graceful failure
                                      backup            

  Five-hour scope   Core workflow may Exclude auth,     Track the MVP
  creep             never work        vector DB,        checklist and
                                      analytics,        stop adding
                                      multiple agents,  features until
                                      and automated     end-to-end tests
                                      account actions   pass

  Weak evaluation   A polished demo   Use a small fixed Run the same
                    may hide          test set and      scripted
                    retrieval errors  measure evidence  scenarios and
                                      retrieval and     record results
                                      response behavior 

  Sensitive         Support histories Use synthetic     Inspect API
  customer data     may contain       demo data,        responses and
  exposure          private           minimize returned repository for
                    information       fields, keep      accidental
                                      secrets           secrets
                                      server-side       

  LLM output is     UI may fail or    Require           Test an invalid
  malformed         display           structured output response and
                    unsupported       and validate it   handle it without
                    claims            in FastAPI        crashing

  Graph             A connection      Label             Ask whether a
  relationships     alone does not    recommendations   prior change
  imply unsupported prove root cause  as hypotheses     definitely caused
  causation                           unless the        the issue;
                                      evidence confirms require
                                      them              uncertainty
  -----------------------------------------------------------------------

### Honest limitations

-   A graph does not automatically make the AI more accurate. The graph
    must contain correct relationships and retrieval must return
    relevant evidence.
-   A shared issue category is not proof that two tickets have the same
    root cause.
-   The prototype does not establish that the recommended next step will
    solve the issue.
-   Synthetic data demonstrates the workflow, not real-world production
    effectiveness.
-   The five-hour time estimate assumes the development environment,
    credentials, and basic dependencies are ready.

------------------------------------------------------------------------

## 10. Five-Hour Build Plan

  -----------------------------------------------------------------------
  Elapsed time            Task                    Definition of done
  ----------------------- ----------------------- -----------------------
  0:00--0:45              Neo4j connection,       Can query seeded
                          schema, constraints,    customer history
                          seed data               

  0:45--1:30              Cypher retrieval and    Query returns correct
                          ticket creation         attempts and outcomes

  1:30--2:30              FastAPI and LLM         `/chat` returns an
                          grounding               evidence-backed
                                                  response

  2:30--3:30              React interface         User can submit a
                                                  message and inspect
                                                  evidence

  3:30--4:15              Persistence and failure New conversation
                          handling                retrieves old history;
                                                  unknown history is
                                                  handled

  4:15--5:00              Test, rehearse, prepare Complete demo works
                          backup                  repeatedly
  -----------------------------------------------------------------------

**Cut features in this order if time is running out:** visual graph
explorer → styling → ticket creation UI. Do not cut persistent
retrieval, recorded outcomes, evidence display, or the new-conversation
test.

------------------------------------------------------------------------

## 11. Acceptance Tests

Before presenting, verify all of the following:

-   [ ] A ticket can be stored in Neo4j.
-   [ ] A troubleshooting attempt can be recorded with a failed outcome.
-   [ ] A new chat request for the same customer retrieves the earlier
    ticket.
-   [ ] The LLM response references the retrieved failure accurately.
-   [ ] The response does not invent an unrecorded troubleshooting step.
-   [ ] An unknown customer or empty history is handled without
    fabricating memory.
-   [ ] The frontend shows the evidence used to generate the answer.
-   [ ] Repeated seeding does not create duplicate stable IDs.
-   [ ] No secrets are committed or exposed to the browser.
-   [ ] The end-to-end demo succeeds more than once.

------------------------------------------------------------------------

## 12. Demo Script for Judges (About 90--120 Seconds)

1.  **Introduce the issue:** "A customer has already contacted support
    about a login failure."
2.  **Show the graph:** Display the customer, ticket, issue, attempted
    password reset, and its failed outcome.
3.  **Start a new conversation:** Submit "My account still isn't
    working" using the same customer ID.
4.  **Show retrieval:** Display the retrieved ticket ID and failed
    attempt.
5.  **Show the answer:** The agent acknowledges the failed step and
    recommends a different evidence-appropriate next action.
6.  **Explain the graph's value:** "The response changes because the
    agent retrieved connected resolution history, not because we
    hardcoded this exact reply."
7.  **Show a negative test:** Ask about history that is not present; the
    agent should admit it has no evidence rather than inventing an
    answer.

### Suggested pitch line

> "Our agent remembers not only what the customer reported, but also
> what support tried and whether it worked. Neo4j connects that history,
> and GraphRAG retrieves the evidence needed to make the next response
> more context-aware."

------------------------------------------------------------------------

## 13. Repository Structure

``` text
context-aware-support-agent/
├── README.md
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI endpoints
│   │   ├── config.py            # Environment configuration
│   │   ├── neo4j_client.py      # Driver and query functions
│   │   ├── retrieval.py         # Graph retrieval logic
│   │   ├── support_agent.py     # LLM prompt and response handling
│   │   └── schemas.py           # Request/response validation
│   ├── scripts/
│   │   ├── schema.cypher        # Constraints and indexes
│   │   └── seed.cypher          # Synthetic demo data
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── App.jsx
│   │   └── api.js
│   └── package.json
└── tests/
    ├── test_retrieval.py
    └── test_chat_flow.py
```

This is the recommended structure, not a claim that the files have
already been implemented.

------------------------------------------------------------------------

## 14. Environment Variables

Use a backend-only `.env` file, excluded from version control.

``` text
NEO4J_URI=neo4j+s://<your-instance>
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<your-password>
LLM_API_KEY=<your-api-key>
LLM_MODEL=<supported-model-name>
```

Create `.env.example` with placeholders only. Never commit real
credentials. Configure the actual model name according to the LLM
provider you select.

------------------------------------------------------------------------

## 15. Final Design Justification

This architecture is intentionally small:

-   **Graph first:** Neo4j models the connected support history and
    supplies the evidence.
-   **LLM second:** the model explains that evidence instead of
    inventing memory.
-   **Evidence visible:** the judge can verify why the response was
    generated.
-   **Persistent across chats:** a stable customer ID reconnects a new
    conversation to stored history.
-   **Bounded scope:** the core end-to-end workflow is achievable within
    a short hackathon if setup is ready.
-   **Failure-aware:** missing history, uncertain diagnoses, duplicate
    writes, malformed output, and dependency failures have explicit
    handling strategies.

The project succeeds when the demo proves a specific claim: **connected
history and recorded outcomes help the agent avoid repeating a failed
troubleshooting step and produce a more relevant next response.**

------------------------------------------------------------------------

## 16. Quickstart & Demo Guide (Google Gemini + Neo4j AuraDB)

### 16.1 Installation & Setup

1. **Clone the repository and navigate to root**:
   ```bash
   cd e:/PROJECTS/Neo4j
   ```

2. **Install Python Dependencies**:
   ```bash
   pip install -r backend/requirements.txt
   ```

3. **Configure Environment Variables**:
   Copy `.env.example` to `.env` (or `backend/.env`):
   ```bash
   cp .env.example .env
   ```
   Provide your Neo4j credentials and Google Gemini API key:
   ```env
   NEO4J_URI=neo4j+s://<your-instance>.databases.neo4j.io
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<your-password>
   GEMINI_API_KEY=<your-google-ai-studio-api-key>
   LLM_MODEL=gemini-2.5-flash
   ```

4. **Initialize Schema Constraints and Seed Data**:
   ```bash
   python backend/scripts/run_cypher.py schema
   python backend/scripts/run_cypher.py seed
   ```

### 16.2 Starting the FastAPI Server

Run the development server with auto-reload:
```bash
python -m uvicorn backend.app.main:app --reload --port 8000
```
- Interactive API Docs (Swagger UI): [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health check: `GET http://127.0.0.1:8000/health`

### 16.3 Running Automated Tests

Run the full mocked unit and integration test suite:
```bash
pytest tests/ -v
```

### 16.4 End-to-End Demo Walkthrough

#### Scenario: Customer `DEMO-CUST-1`
1. **Retrieve History**:
   Query `GET /customers/DEMO-CUST-1/history`.
   - The graph returns ticket `DEMO-TKT-1` for product `P-LOGIN` (`Auth Service`), showing an earlier troubleshooting attempt `password_reset` with outcome **`FAILED`**.

2. **Send Chat Complaint**:
   Submit `POST /chat` with:
   ```json
   {
     "customer_id": "DEMO-CUST-1",
     "message": "I still cannot log in after resetting my password. What should I try next?"
   }
   ```
3. **GraphRAG Response Behavior**:
   - **Grounded Answer**: The agent acknowledges ticket `DEMO-TKT-1` and notes that `password_reset` was already tried and failed.
   - **Avoid Repeating Failure**: Instead of advising the customer to reset their password again, Gemini recommends checking session tokens or locked account status.
   - **Evidence Transparency**: Returns `evidence: [{"ticket_id": "DEMO-TKT-1", "attempt": "password_reset", "outcome": "FAILED"}]`.
   - **Uncertainty**: Distinguishes verified graph facts from unverified account hypotheses.

### 16.5 Fallback & Error Handling

If Google Gemini is unavailable or `GEMINI_API_KEY` is not provided:
- The system automatically triggers the deterministic rule-based grounding engine.
- It still retrieves graph history and prevents repeating failed actions.
- The `uncertainty` field transparently declares: `"Note: Response synthesized using deterministic grounder (Gemini API key not configured)..."`.

