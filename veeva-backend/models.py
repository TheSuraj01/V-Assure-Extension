import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")


# ─────────────────────────────────────────────────────────────
# Base Model Configuration
# ─────────────────────────────────────────────────────────────

class AppBaseModel(BaseModel):
    model_config = ConfigDict(
        protected_namespaces=(),
        extra="ignore",
        validate_assignment=True,
        populate_by_name=True,
    )


# ─────────────────────────────────────────────────────────────
# KB Input
# ─────────────────────────────────────────────────────────────

class KBInput(AppBaseModel):
    action: str = Field(
        ...,
        description="Action type like click, enter, select",
    )

    label: str = Field(
        ...,
        description="UI element label",
    )

    value: Optional[str] = Field(
        default=None,
        description="Input value for enter/type actions",
    )

    selectedText: Optional[str] = Field(
        default=None,
        description="Selected dropdown option text",
    )

    placeholder: Optional[str] = Field(
        default=None,
        description="Element placeholder",
    )

    elementId: Optional[str] = Field(
        default=None,
        description="DOM element ID",
    )

    ariaLabel: Optional[str] = Field(
        default=None,
        description="ARIA label",
    )

    dropdownLabel: Optional[str] = Field(
        default=None,
        description="Dropdown label",
    )

    userStep: Optional[str] = Field(
        default=None,
        description="Grouped workflow step name",
    )

    @field_validator(
        "action",
        "label",
        mode="before",
    )
    @classmethod
    def strip_required_strings(
        cls,
        value: Any,
    ) -> Any:

        if isinstance(value, str):
            return value.strip()

        return value

    @field_validator(
        "value",
        "selectedText",
        "placeholder",
        "elementId",
        "ariaLabel",
        "dropdownLabel",
        "userStep",
        mode="before",
    )
    @classmethod
    def strip_optional_strings(
        cls,
        value: Any,
    ) -> Any:

        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned if cleaned else None

        return value


# ─────────────────────────────────────────────────────────────
# KB Entry
# ─────────────────────────────────────────────────────────────

class KBEntry(AppBaseModel):
    name: str = Field(
        ...,
        description="Step name from extension",
    )

    input: KBInput = Field(
        ...,
        description="Structured KB input",
    )

    output: str = Field(
        ...,
        description="Human-readable generated step",
    )

    @field_validator(
        "name",
        "output",
        mode="before",
    )
    @classmethod
    def clean_text_fields(
        cls,
        value: Any,
    ) -> Any:

        if isinstance(value, str):
            return value.strip()

        return value


# ─────────────────────────────────────────────────────────────
# Step Result
# ─────────────────────────────────────────────────────────────

class StepResult(AppBaseModel):
    step: int = Field(  #Step number can be removed from extension
        ...,
        ge=1,
        description="Step number",
    )

    name: str = Field(
        ...,
        description="Original step name",
    )

    original_output: str = Field(
        ...,
        description="Original extension output",
    )

    enhanced_output: str = Field(
        ...,
        description="Enhanced AI-generated output",
    )

    action: str = Field(
        ...,
        description="Interaction action",
    )

    label: str = Field(
        ...,
        description="Element label",
    )

    value: Optional[str] = Field(
        default=None,
        description="Associated value",
    )

    userStep: Optional[str] = Field(
        default=None,
        description="Workflow group",
    )

    rag_context_used: List[str] = Field(
        default_factory=list,
        description="Retrieved RAG examples",
    )

    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score",
    )

    validation_reason: Optional[str] = Field(
        default=None,
        description="Validation explanation",
    )


# ─────────────────────────────────────────────────────────────
# Generate Request
# ─────────────────────────────────────────────────────────────

class GenerateRequest(AppBaseModel):
    entries: List[KBEntry] = Field(
        ...,
        min_length=1,
        description="KB entries from extension",
    )

    api_key: Optional[str] = Field(
        default=GROQ_API_KEY,   # change to local server API key
        description="Provider API key",
    )

    model: str = Field(
        default="llama-3.1-8b-instant",     # change to local server model
        description="LLM model name",
    )

    provider: str = Field(
        default="groq", #This will change to local server later
        description="Provider name",
    )

    api_base: Optional[str] = Field(
        default=None,
        description="Custom API base URL",
    )

    temperature: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Generation temperature",
    )

    use_rag: bool = Field(
        default=True,
        description="Enable RAG retrieval",
    )

    session_name: Optional[str] = Field(
        default=None,
        description="Optional session label",
    )

    use_multi_candidate: bool = Field(
        default=True,
        description="Enable multi-candidate generation",
    )

    max_candidates: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum candidate generations",
    )

    stream: bool = Field(
        default=False,
        description="Enable streaming generation",
    )

    @field_validator(
        "provider",
        "model",
        mode="before",
    )
    @classmethod
    def normalize_provider_fields(
        cls,
        value: Any,
    ) -> Any:

        if isinstance(value, str):
            return value.strip()

        return value


# ─────────────────────────────────────────────────────────────
# Generate Response
# ─────────────────────────────────────────────────────────────

class GenerateResponse(AppBaseModel):
    session_id: str = Field(
        ...,
        description="Generated session identifier",
    )

    total_steps: int = Field(
        ...,
        ge=0,
        description="Total generated steps",
    )

    steps: List[StepResult] = Field(
        default_factory=list,
        description="Generated step results",
    )

    full_script: str = Field(
        ...,
        description="Combined automation script",
    )

    download_url: str = Field(
        ...,
        description="Download endpoint",
    )

    model_used: str = Field(
        ...,
        description="Model used for generation",
    )


# ─────────────────────────────────────────────────────────────
# Health Response
# ─────────────────────────────────────────────────────────────

class HealthResponse(AppBaseModel):
    status: str = Field(
        ...,
        description="Application status",
    )

    kb_files_loaded: int = Field(
        ...,
        ge=0,
        description="Loaded KB files",
    )

    kb_entries_total: int = Field(
        ...,
        ge=0,
        description="Total KB entries",
    )

    groq_configured: bool = Field(
        ...,
        description="Groq configuration status",
    )


# ─────────────────────────────────────────────────────────────
# Optional Internal Stats Models
# ─────────────────────────────────────────────────────────────

class GenerationStats(AppBaseModel):
    total_sessions: int = 0
    total_steps_generated: int = 0
    avg_confidence: float = 0.0


class SessionMetadata(AppBaseModel):
    session_id: str
    created: str
    total_steps: int
    model_used: Optional[str] = None
    txt_available: bool = True
    json_available: bool = True


class ValidationResponse(AppBaseModel):
    original: str
    sanitized: str
    is_valid: bool
    score: float
    reason: str
    action: str
    label: str


# ─────────────────────────────────────────────────────────────
# Export Helper
# ─────────────────────────────────────────────────────────────

__all__ = [
    "KBInput",
    "KBEntry",
    "StepResult",
    "GenerateRequest",
    "GenerateResponse",
    "HealthResponse",
    "GenerationStats",
    "SessionMetadata",
    "ValidationResponse",
]