import logging
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

try:
    from app.config import get_settings
    from app.neo4j_client import get_neo4j_client
    from app.retrieval import CustomerHistoryRetriever
    from app.schemas import (
        ChatRequest,
        ChatResponse,
        CustomerHistoryResponse,
        HealthResponse,
        TicketCreateRequest,
        TicketResponse,
    )
    from app.support_agent import SupportAgent
except ImportError:
    from .config import get_settings
    from .neo4j_client import get_neo4j_client
    from .retrieval import CustomerHistoryRetriever
    from .schemas import (
        ChatRequest,
        ChatResponse,
        CustomerHistoryResponse,
        HealthResponse,
        TicketCreateRequest,
        TicketResponse,
    )
    from .support_agent import SupportAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context for driver resource management."""
    settings = get_settings()
    client = get_neo4j_client()
    logger.info("Initializing Context-Aware Support Agent backend with Neo4j and Gemini...")
    if settings.is_neo4j_configured:
        connected = client.verify_connectivity()
        logger.info(f"Neo4j connection test on startup: {'SUCCESS' if connected else 'FAILED/OFFLINE'}")
    else:
        logger.info("Neo4j is not fully configured yet; will run in standalone mode.")
    yield
    logger.info("Shutting down backend and closing database connections.")
    client.close()


app = FastAPI(
    title="Context-Aware Support Agent API",
    description="GraphRAG backend powered by Neo4j and LLM for resolution-aware customer support memory.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Middleware for React frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    """Health status check endpoint reporting Neo4j and LLM readiness."""
    settings = get_settings()
    client = get_neo4j_client()
    neo4j_ok = client.verify_connectivity()
    return HealthResponse(
        status="ok",
        neo4j_connected=neo4j_ok,
        llm_configured=settings.is_llm_configured,
        app_env=settings.app_env,
    )


@app.post("/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED, tags=["Tickets"])
def create_ticket(request: TicketCreateRequest):
    """
    Create a new support ticket connected to a customer, product, and issue.
    Maintains graph relationships and prevents duplicate customer/product nodes.
    """
    client = get_neo4j_client()
    if not client.verify_connectivity():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j database is unavailable. Please verify connection credentials.",
        )

    retriever = CustomerHistoryRetriever(client=client)
    try:
        created = retriever.create_ticket(
            customer_id=request.customer_id,
            product_id=request.product_id,
            issue_description=request.issue_description,
            category=request.category,
        )
        return TicketResponse(
            ticket_id=created["ticket_id"],
            status=created["status"],
            customer_id=created["customer_id"],
            product_id=created["product_id"],
            issue_id=created["issue_id"],
            created_at=created["created_at"],
        )
    except Exception as e:
        logger.error(f"Failed to create ticket: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error persisting ticket in Neo4j: {str(e)}",
        )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
def chat_with_agent(request: ChatRequest):
    """
    Main GraphRAG chat endpoint:
    1. Validates customer_id and input.
    2. Retrieves connected ticket history, previous attempts, and outcomes from Neo4j.
    3. Builds bounded evidence context and queries the grounded support agent.
    4. Returns grounded answer, evidence references, suggested next step, and uncertainty.
    """
    client = get_neo4j_client()
    retriever = CustomerHistoryRetriever(client=client)
    agent = SupportAgent()

    # Step 1: Retrieve history from graph if database is online
    history = []
    if client.verify_connectivity():
        try:
            history = retriever.get_customer_history(customer_id=request.customer_id)
        except Exception as e:
            logger.error(f"Retrieval failure for customer {request.customer_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to retrieve graph history from Neo4j: {str(e)}",
            )
    else:
        logger.warning("Neo4j offline; proceeding with empty history and graceful notification.")

    # Step 2: Extract verifiable evidence
    evidence = retriever.extract_evidence(history)

    # Step 3: Grounded response generation
    response = agent.answer_query(
        customer_id=request.customer_id,
        message=request.message,
        history=history,
        evidence=evidence,
    )
    return response


@app.get("/customers/{customer_id}/history", response_model=CustomerHistoryResponse, tags=["History"])
def get_customer_history(customer_id: str):
    """
    Retrieve customer ticket and troubleshooting history for UI evidence panel.
    Returns an empty history list if customer has no prior records or is unknown.
    """
    client = get_neo4j_client()
    if not client.verify_connectivity():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j database is unavailable. Please verify connection credentials.",
        )

    retriever = CustomerHistoryRetriever(client=client)
    try:
        history = retriever.get_customer_history(customer_id=customer_id)
        return CustomerHistoryResponse(
            customer_id=customer_id,
            history=history,
        )
    except Exception as e:
        logger.error(f"Failed to fetch history for customer {customer_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reading history from Neo4j: {str(e)}",
        )
