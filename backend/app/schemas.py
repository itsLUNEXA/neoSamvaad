from typing import List, Optional
from pydantic import BaseModel, Field


# Ticket schemas
class TicketCreateRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, description="Stable customer identifier (e.g., C-001)")
    product_id: str = Field(..., min_length=1, description="Stable product identifier (e.g., P-LOGIN)")
    issue_description: str = Field(..., min_length=3, description="Description of the customer issue")
    category: str = Field(..., min_length=1, description="Categorization key for the issue")


class TicketResponse(BaseModel):
    ticket_id: str
    status: str
    customer_id: str
    product_id: str
    issue_id: str
    created_at: str


# Chat schemas
class ChatRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, description="Customer submitting the query")
    message: str = Field(..., min_length=1, description="Customer message or complaint")


class EvidenceItem(BaseModel):
    ticket_id: str
    attempt: str
    outcome: str


class ChatResponse(BaseModel):
    answer: str
    evidence: List[EvidenceItem] = Field(default_factory=list)
    suggested_next_step: str
    uncertainty: str
    provider: Optional[str] = "gemini"


# Customer history schemas
class AttemptHistoryItem(BaseModel):
    attempt_id: Optional[str] = None
    method: Optional[str] = None
    outcome: Optional[str] = None
    timestamp: Optional[str] = None
    notes: Optional[str] = None
    resolution_action: Optional[str] = None
    resolution_status: Optional[str] = None


class TicketHistoryItem(BaseModel):
    ticket_id: str
    ticket_status: Optional[str] = None
    created_at: Optional[str] = None
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    issue_id: Optional[str] = None
    issue_category: Optional[str] = None
    issue_description: Optional[str] = None
    attempts: List[AttemptHistoryItem] = Field(default_factory=list)


class CustomerHistoryResponse(BaseModel):
    customer_id: str
    history: List[TicketHistoryItem] = Field(default_factory=list)


# Health schema
class HealthResponse(BaseModel):
    status: str
    neo4j_connected: bool
    llm_configured: bool
    app_env: str
