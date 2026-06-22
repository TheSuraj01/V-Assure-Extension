import hmac
import json
import os
import signal
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Load .env file FIRST — before any module that calls os.getenv()
# override=True ensures .env changes are always picked up on uvicorn reload
from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from config.config_loader import get_config, reload_config
from generator import (
    run_batch_enhanced,
    run_streaming_enhanced,
    sanitize_output,
    validate_output,
)
from models import GenerateRequest, GenerateResponse, HealthResponse
from rag import EnhancedRAGEngine
from utils import setup_logger, build_text_report
from services.pattern_loader import (
    init_patterns,
    init_patterns_from_bytes,
)
from services.excel_validator import (
    validate_dynamic_excel,
    validate_dynamic_excel_from_bytes,
)
from services.s3_service import S3Service
from services.json_store_service import (
    load_patterns as json_load_patterns,
    save_patterns as json_save_patterns,
)

logger = setup_logger(__name__)

config = get_config()

APP_CONFIG = config.get("app", {})
GENERATION_CONFIG = config.get("generation", {})

APP_NAME = APP_CONFIG.get("name", "Veeva Vault Step Generator")
APP_VERSION = APP_CONFIG.get("version", "3.0.0")

MAX_SESSION_CACHE = GENERATION_CONFIG.get("max_session_cache", 100)

OUTPUTS_DIR = Path("outputs")
OUTPUTS_DIR.mkdir(exist_ok=True)

DATA_DIR = Path(os.getenv("KB_DATA_DIR", "data"))

rag = EnhancedRAGEngine()


def init_dynamic_patterns(
    sheet_bytes: "io.BytesIO | None" = None,
) -> None:
    """
    Initialize dynamic Excel templates.

    When sheet_bytes is provided (BytesIO), the template is validated
    and parsed entirely in memory — zero disk I/O.

    When sheet_bytes is None, falls back to the local disk file
    (config/dynamic_step_patterns.xlsx) if it exists.
    """
    import io  # noqa: F811

    if sheet_bytes is not None:
        logger.info("DYNAMIC TEMPLATE INITIALIZATION (in-memory)")

        try:
            sheet_bytes.seek(0)
            validate_dynamic_excel_from_bytes(sheet_bytes)
            logger.info("Dynamic Excel validation successful")

            sheet_bytes.seek(0)
            patterns = init_patterns_from_bytes(sheet_bytes)
            logger.info(
                "Dynamic templates loaded successfully | total=%s",
                len(patterns),
            )
        except Exception:
            logger.exception("Dynamic template initialization failed (in-memory)")
            logger.warning("Falling back to JSON templates")
        return

    try:
        excel_path = config.get_pattern_excel_path()

        if not excel_path.exists():
            logger.info(
                "Excel template file not found on disk: %s — using JSON fallback",
                excel_path,
            )
            return

        logger.info("DYNAMIC TEMPLATE INITIALIZATION (from disk)")
        logger.info("Validating dynamic Excel | %s", excel_path)

        validate_dynamic_excel(excel_path)
        logger.info("Dynamic Excel validation successful")

        patterns = init_patterns()
        logger.info(
            "Dynamic templates loaded successfully | total=%s",
            len(patterns),
        )
    except Exception:
        logger.exception("Dynamic template initialization failed")
        logger.warning("Falling back to JSON templates")


sessions: OrderedDict[str, GenerateResponse] = OrderedDict()

generation_stats: Dict[str, float] = {
    "total_sessions": 0,
    "total_steps_generated": 0,
    "avg_confidence": 0.0,
}


def build_session_rag() -> EnhancedRAGEngine:
    """
    Return the shared RAG instance for generation.

    The RAG index is read-only during request processing — no deep copy needed.
    """
    return rag


def init_rag() -> None:
    """Load KB data files and build the RAG index."""
    if DATA_DIR.exists():
        logger.info("Loading KB files from %s", DATA_DIR.resolve())
        rag.load_directory(str(DATA_DIR), "*.json")
    else:
        logger.warning("Data directory not found: %s", DATA_DIR.resolve())

    logger.info("Building enhanced indices...")
    rag.build_index()

    stats = rag.get_stats()
    logger.info(
        "RAG initialized | entries=%s | files=%s | actions=%s | terms=%s",
        stats.get("total_entries"),
        stats.get("loaded_files"),
        stats.get("action_types"),
        stats.get("index_terms"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 70)
    logger.info("APPLICATION STARTUP")
    logger.info("=" * 70)

    logger.info("STARTUP VALIDATION")
    env = config.environment
    debug = config.is_debug
    logger.info("Running in environment: %s (debug=%s)", env, debug)

    if not OUTPUTS_DIR.exists():
        try:
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            logger.info("Created outputs directory: %s", OUTPUTS_DIR.resolve())
        except Exception as exc:
            logger.error("Failed to create outputs directory %s: %s", OUTPUTS_DIR, exc)
            raise RuntimeError(f"Outputs directory invalid: {exc}")

    if not DATA_DIR.exists():
        logger.warning("Data directory %s does not exist. RAG will start with an empty index.", DATA_DIR.resolve())

    groq_key = config.get_secret("groq_api_key") or os.getenv("GROQ_API_KEY", "")
    bedrock_creds = config.get_secret("bedrock_credentials") or os.getenv("BEDROCK_CREDENTIALS", "")
    local_key = config.get_secret("local_api_key") or os.getenv("LOCAL_API_KEY", "")
    local_base = config.get_secret("local_api_base") or os.getenv("LOCAL_API_BASE", "")

    providers_configured = []
    if groq_key:
        providers_configured.append("groq")
    if bedrock_creds:
        providers_configured.append("bedrock")
    if local_key or local_base:
        providers_configured.append("local")

    if not providers_configured:
        logger.warning(
            "No LLM provider keys (groq_api_key, bedrock_credentials, local_api_key/base) "
            "configured in encrypted secrets or env. Request-level keys will be required."
        )
    else:
        logger.info("Configured LLM providers: %s", ", ".join(providers_configured))
    logger.info("Startup validation complete")

    # Initialize RAG
    init_rag()

    logger.info("TEMPLATE INITIALIZATION")

    cached_patterns = json_load_patterns()

    if cached_patterns:
        logger.info(
            "Startup: Loaded %d templates from patterns_cache.json (fast path)",
            len(cached_patterns),
        )
        get_config().set_runtime_cache("dynamic_templates", cached_patterns)
    else:
        logger.info(
            "Startup: patterns_cache.json empty or missing — fetching Excel from S3"
        )

        s3_bucket = os.getenv("S3_BUCKET", "").strip()
        s3_key = os.getenv("S3_KEY", "").strip()
        startup_bytes = None

        if s3_bucket and s3_key:
            try:
                startup_bytes = S3Service.download_excel_as_bytes(
                    bucket=s3_bucket,
                    key=s3_key,
                )
                logger.info("Startup: Excel downloaded from S3 into memory")
            except Exception:
                logger.warning("Startup: S3 download failed — falling back to disk")
        else:
            logger.info("Startup: S3_BUCKET or S3_KEY not set — skipping S3 download")

        # Parse & register patterns (in-memory S3 bytes or disk/JSON fallback)
        init_dynamic_patterns(startup_bytes)

        # Persist freshly parsed patterns to JSON cache for next restart
        if startup_bytes is not None:
            fresh = get_config().get_runtime_cache("dynamic_templates", {})
            if fresh:
                saved = json_save_patterns(fresh)
                logger.info(
                    "Startup: Persisted %d templates to patterns_cache.json", saved
                )

    logger.info("Application startup completed successfully")

    yield

    logger.info("=" * 70)
    logger.info("APPLICATION SHUTDOWN")
    logger.info("=" * 70)


app = FastAPI(
    title=APP_NAME,
    description=(
        "AI-powered test step generation "
        "with advanced RAG and validation"
    ),
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if config.is_debug else None,
    redoc_url="/redoc" if config.is_debug else None,
)

# CORS — configurable origins (default: restrictive in production)
_cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
if _cors_origins_env:
    _cors_origins = [origin.strip() for origin in _cors_origins_env.split(",")]
else:
    # Default to permissive for Chrome Extension compatibility
    _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def resolve_api_config(req: GenerateRequest):
    """Resolve API key and base URL from request or server config."""
    api_key = req.api_key
    api_base = req.api_base

    if req.provider == "groq":
        api_key = api_key or config.get_secret("groq_api_key") or os.getenv("GROQ_API_KEY", "")
    elif req.provider == "bedrock":
        api_key = api_key or config.get_secret("bedrock_credentials") or os.getenv("BEDROCK_CREDENTIALS", "")
    elif req.provider in ("local", "openai"):
        # "openai" is treated identically to "local" — the extension may send either.
        # Always fall back to the server-side LOCAL_API_KEY / LOCAL_API_BASE so that
        # the request is routed to the custom endpoint, not to real OpenAI servers.
        api_key = api_key or config.get_secret("local_api_key") or os.getenv("LOCAL_API_KEY", "")
        api_base = api_base or config.get_secret("local_api_base") or os.getenv("LOCAL_API_BASE", "")

    return api_key, api_base


def update_generation_stats(result: GenerateResponse) -> None:
    """Update rolling generation statistics."""
    generation_stats["total_sessions"] += 1
    generation_stats["total_steps_generated"] += result.total_steps

    confidences = []
    for step in result.steps:
        confidence = getattr(step, "confidence", None)
        if confidence is not None:
            confidences.append(confidence)

    if confidences:
        avg_confidence = sum(confidences) / len(confidences)
        generation_stats["avg_confidence"] = (
            generation_stats["avg_confidence"] * 0.9
            + avg_confidence * 0.1
        )


def build_full_script(step_results) -> str:
    """Build a combined human-readable test script from step results."""
    script_lines = []
    current_user_step = None

    for result in step_results:
        if result.userStep and result.userStep != current_user_step:
            if script_lines:
                script_lines.append("")
            script_lines.append(f"{result.userStep}:")
            current_user_step = result.userStep

        script_lines.append(f"{result.step}. {result.enhanced_output}")

    return "\n".join(script_lines)


def save_session(
    session_id: str,
    result: GenerateResponse,
) -> None:
    """Persist session to memory cache and disk."""
    if len(sessions) >= MAX_SESSION_CACHE:
        sessions.popitem(last=False)

    sessions[session_id] = result
    update_generation_stats(result)

    # Save text report
    txt_path = OUTPUTS_DIR / f"{session_id}.txt"
    txt_content = build_text_report(session_id, result)

    with open(txt_path, "w", encoding="utf-8") as file:
        file.write(txt_content)

    # Save JSON report
    json_path = OUTPUTS_DIR / f"{session_id}.json"

    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(
            result.model_dump(),
            file,
            indent=2,
            ensure_ascii=False,
        )

    logger.info("Session saved successfully | %s", session_id)


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        kb_files_loaded=len(rag.loaded_files),
        kb_entries_total=rag.total_entries,
        groq_configured=bool(
            config.get_secret("groq_api_key") or os.getenv("GROQ_API_KEY")
        ),
    )


class AdminSyncRequest(BaseModel):
    """Body for the admin template-sync endpoint."""
    admin_code: str


@app.post("/admin/sync-templates")
async def admin_sync_templates(req: AdminSyncRequest):
    """
    Admin-only endpoint: sync templates from S3.

    TEMPORARILY DISABLED — remove the early return below to re-enable.
    """
    raise HTTPException(status_code=503, detail="Admin sync is temporarily disabled")

    global config

    expected_code = (
        config.get_secret("admin_sync_code")
        or os.getenv("ADMIN_SYNC_CODE", "")
    ).strip()

    if not expected_code:
        logger.warning("Admin sync attempted but ADMIN_SYNC_CODE is not configured")
        raise HTTPException(status_code=503, detail="Admin sync is not configured")

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(req.admin_code.strip(), expected_code):
        logger.warning("Admin sync attempt with invalid code")
        raise HTTPException(status_code=403, detail="Invalid admin code")

    s3_bucket = os.getenv("S3_BUCKET", "").strip()
    s3_key = os.getenv("S3_KEY", "").strip()

    if not s3_bucket or not s3_key:
        raise HTTPException(
            status_code=503,
            detail="S3_BUCKET or S3_KEY not configured on the server",
        )

    try:
        logger.info(
            "Admin sync: downloading Excel from S3 | bucket=%s key=%s",
            s3_bucket, s3_key,
        )
        sheet_bytes = S3Service.download_excel_as_bytes(
            bucket=s3_bucket,
            key=s3_key,
        )
    except Exception:
        logger.exception("Admin sync: S3 download failed")
        raise HTTPException(
            status_code=502,
            detail="Failed to download Excel from S3",
        )

    # Reload config and re-parse patterns from the downloaded bytes
    config = reload_config()
    init_dynamic_patterns(sheet_bytes)

    # Persist freshly parsed patterns to JSON cache
    fresh = get_config().get_runtime_cache("dynamic_templates", {})
    saved = json_save_patterns(fresh)

    logger.info(
        "Admin sync complete | saved=%d templates to patterns_cache.json", saved
    )

    return {
        "status": "ok",
        "synced": saved,
        "message": f"{saved} templates synced from S3 and saved to cache",
    }


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    """Generate enhanced test steps from KB entries."""
    if not req.entries:
        raise HTTPException(status_code=400, detail="No KB entries provided")

    api_key, api_base = resolve_api_config(req)

    if req.provider == "groq" and not api_key:
        raise HTTPException(
            status_code=400,
            detail="Groq API key required. Pass 'api_key' or set GROQ_API_KEY env var",
        )

    session_rag = (
        build_session_rag()
        if req.use_rag
        else EnhancedRAGEngine()
    )

    logger.info(
        "GENERATION REQUEST | Steps=%s | Provider=%s | Model=%s | Temperature=%s | UseRAG=%s",
        len(req.entries), req.provider, req.model, req.temperature, req.use_rag,
    )

    try:
        step_results = await run_batch_enhanced(
            entries=req.entries,
            rag=session_rag,
            api_key=api_key,
            model=req.model,
            provider=req.provider,
            api_base=api_base,
            temperature=req.temperature,
            use_multi_candidate=True,
        )
    except Exception:
        logger.exception("Batch generation failed")
        raise HTTPException(
            status_code=500,
            detail="Step generation failed. Please try again.",
        )

    full_script = build_full_script(step_results)
    session_id = str(uuid.uuid4())[:8]

    if req.session_name:
        safe_name = req.session_name.replace(" ", "_").strip()
        session_id = f"{safe_name}_{session_id}"

    response = GenerateResponse(
        session_id=session_id,
        total_steps=len(step_results),
        steps=step_results,
        full_script=full_script,
        download_url=f"/download/{session_id}",
        model_used=req.model,
    )

    save_session(session_id, response)
    logger.info("Session completed successfully | %s", session_id)

    return response


@app.post("/generate/stream")
async def generate_stream(req: GenerateRequest):
    """Generate enhanced test steps with SSE streaming."""
    if not req.entries:
        raise HTTPException(status_code=400, detail="No KB entries provided")

    api_key, api_base = resolve_api_config(req)

    if req.provider == "groq" and not api_key:
        raise HTTPException(status_code=400, detail="Groq API key required")

    session_rag = (
        build_session_rag()
        if req.use_rag
        else EnhancedRAGEngine()
    )

    return StreamingResponse(
        run_streaming_enhanced(
            entries=req.entries,
            rag=session_rag,
            api_key=api_key,
            model=req.model,
            provider=req.provider,
            api_base=api_base,
            temperature=req.temperature,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/download/{session_id}")
async def download(session_id: str, format: str = "txt"):
    """Download a generated session as TXT or JSON."""
    extension = "json" if format == "json" else "txt"
    path = OUTPUTS_DIR / f"{session_id}.{extension}"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found",
        )

    return FileResponse(
        path=str(path),
        filename=f"veeva_test_{session_id}.{extension}",
        media_type="application/octet-stream",
    )


@app.get("/sessions")
async def list_sessions():
    """List all stored sessions."""
    results = []

    txt_files = sorted(OUTPUTS_DIR.glob("*.txt"), reverse=True)

    for txt_file in txt_files:
        session_id = txt_file.stem
        json_file = OUTPUTS_DIR / f"{session_id}.json"

        entry = {
            "session_id": session_id,
            "txt_available": True,
            "json_available": json_file.exists(),
            "created": datetime.fromtimestamp(
                txt_file.stat().st_mtime
            ).isoformat(),
        }

        if json_file.exists():
            try:
                with open(json_file, "r", encoding="utf-8") as file:
                    data = json.load(file)
                entry["total_steps"] = data.get("total_steps", 0)
                entry["model_used"] = data.get("model_used", "")
            except Exception:
                logger.warning(
                    "Failed reading session metadata | %s", session_id
                )

        results.append(entry)

    return {
        "sessions": results,
        "total": len(results),
    }


@app.get("/stats")
async def get_stats():
    """Return application statistics."""
    kb_stats = rag.get_stats()

    return {
        "knowledge_base": kb_stats,
        "generation": generation_stats,
        "system": {
            "total_sessions_stored": len(sessions),
            "output_files": len(list(OUTPUTS_DIR.glob("*.txt"))),
        },
    }


@app.get("/patterns")
async def get_patterns():
    """Return loaded dynamic patterns."""
    patterns = config.get_runtime_cache("dynamic_templates", {})

    return {
        "total_patterns": len(patterns),
        "patterns": patterns,
    }


@app.post("/kb/upload")
async def upload_kb(file: UploadFile = File(...)):
    """Upload a new KB data file."""
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only .json files are accepted")

    DATA_DIR.mkdir(exist_ok=True)
    destination = DATA_DIR / file.filename

    content = await file.read()

    try:
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            raise ValueError("File must be a JSON array")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    with open(destination, "wb") as file_obj:
        file_obj.write(content)

    added = rag.load_file(str(destination))
    rag.build_index()

    logger.info("KB uploaded successfully | %s", file.filename)

    return {
        "message": f"Loaded {added} entries from {file.filename}",
        "total_entries": rag.total_entries,
        "stats": rag.get_stats(),
    }


@app.post("/kb/reload")
async def reload_kb():
    """Reload all KB data from disk."""
    rag.clear()
    init_rag()

    logger.info("Knowledge base reloaded")

    return {
        "message": "KB reloaded successfully",
        "stats": rag.get_stats(),
    }


class ValidateRequest(BaseModel):
    output: str
    action: str
    label: str
    value: Optional[str] = None


@app.post("/validate")
async def validate_output_endpoint(req: ValidateRequest):
    """Validate a single step output."""
    cleaned = sanitize_output(req.output)

    is_valid, score, reason = validate_output(
        cleaned,
        req.action,
        req.label,
        req.value or "",
    )

    return {
        "original": req.output,
        "sanitized": cleaned,
        "is_valid": is_valid,
        "score": round(score, 3),
        "reason": reason,
        "action": req.action,
        "label": req.label,
    }


def _handle_signal(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    logger.info("Received signal %s — initiating graceful shutdown", signum)
    raise SystemExit(0)


# Register signal handlers (only in main process)
if os.getenv("_UVICORN_WORKER", "") != "1":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=config.is_debug,
    )