import copy
import json
import os
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from config.config_loader import get_config
from generator import (
    run_batch_enhanced,
    run_streaming_enhanced,
    sanitize_output,
    validate_output,
)
from models import GenerateRequest, GenerateResponse, HealthResponse
from rag import EnhancedRAGEngine
from utils import setup_logger
from services.pattern_loader import (
    init_patterns,
)

from services.excel_validator import (
    validate_dynamic_excel,
)

load_dotenv()

logger = setup_logger(__name__)

config = get_config()

APP_CONFIG = config.get("app", {})
GENERATION_CONFIG = config.get("generation", {})

APP_NAME = APP_CONFIG.get(
    "name",
    "Veeva Vault Step Generator",
)

APP_VERSION = APP_CONFIG.get(
    "version",
    "2.0.0",
)

DEBUG_MODE = APP_CONFIG.get(
    "debug",
    True,
)

MAX_SESSION_CACHE = GENERATION_CONFIG.get(
    "max_session_cache",
    100,
)

OUTPUTS_DIR = Path("outputs")
OUTPUTS_DIR.mkdir(exist_ok=True)

DATA_DIR = Path(
    os.getenv("KB_DATA_DIR", "data")
)

rag = EnhancedRAGEngine()

def init_dynamic_patterns() -> None:
    """
    Initialize dynamic Excel templates.
    """

    try:

        excel_path = (
            config.get_pattern_excel_path()
        )

        logger.info("=" * 70)
        logger.info(
            "DYNAMIC TEMPLATE INITIALIZATION"
        )
        logger.info("=" * 70)

        logger.info(
            "Validating dynamic Excel | %s",
            excel_path,
        )

        validate_dynamic_excel(
            excel_path,
        )

        logger.info(
            "Dynamic Excel validation successful"
        )

        patterns = init_patterns()

        logger.info(
            "Dynamic templates loaded successfully"
        )

        logger.info(
            "Total dynamic templates: %s",
            len(patterns),
        )

    except Exception:

        logger.exception(
            "Dynamic template initialization failed"
        )

        logger.warning(
            "Falling back to JSON templates"
        )

sessions: OrderedDict[str, GenerateResponse] = OrderedDict()

generation_stats: Dict[str, float] = {
    "total_sessions": 0,
    "total_steps_generated": 0,
    "avg_confidence": 0.0,
}


def build_session_rag() -> EnhancedRAGEngine:
    session_rag = EnhancedRAGEngine()

    session_rag.entries = copy.deepcopy(rag.entries)
    session_rag._loaded_files = copy.deepcopy(
        rag.loaded_files
    )

    session_rag.tfidf_index = copy.deepcopy(
        rag.tfidf_index
    )

    session_rag.bm25_index = copy.deepcopy(
        rag.bm25_index
    )

    session_rag.action_clusters = copy.deepcopy(
        rag.action_clusters
    )

    session_rag.label_index = copy.deepcopy(
        rag.label_index
    )

    session_rag.avg_doc_length = rag.avg_doc_length

    return session_rag


def init_rag() -> None:
    if DATA_DIR.exists():
        logger.info(
            "Loading KB files from %s",
            DATA_DIR.resolve(),
        )

        rag.load_directory(
            str(DATA_DIR),
            "*.json",
        )

    else:
        logger.warning(
            "Data directory not found: %s",
            DATA_DIR.resolve(),
        )

    logger.info(
        "Building enhanced indices..."
    )

    rag.build_index()

    stats = rag.get_stats()

    logger.info(
        "RAG initialized successfully"
    )

    logger.info(
        "Total entries: %s",
        stats.get("total_entries"),
    )

    logger.info(
        "Loaded files: %s",
        stats.get("loaded_files"),
    )

    logger.info(
        "Action types: %s",
        stats.get("action_types"),
    )

    logger.info(
        "Unique labels: %s",
        stats.get("unique_labels"),
    )

    logger.info(
        "Index terms: %s",
        stats.get("index_terms"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info("=" * 70)
    logger.info("APPLICATION STARTUP")
    logger.info("=" * 70)

    # Initialize RAG
    init_rag()

    # Initialize dynamic templates
    init_dynamic_patterns()

    logger.info(
        "Application startup completed successfully"
    )

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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def resolve_api_config(req: GenerateRequest):
    api_key = req.api_key
    api_base = req.api_base

    if req.provider == "groq":
        api_key = api_key or os.getenv("GROQ_API_KEY")
    elif req.provider == "bedrock":
        api_key = api_key or os.getenv("BEDROCK_CREDENTIALS")
    elif req.provider == "local":
        api_key = api_key or os.getenv("LOCAL_API_KEY")
        api_base = api_base or os.getenv("LOCAL_API_BASE")
    
    return api_key, api_base


def update_generation_stats(
    result: GenerateResponse,
) -> None:

    generation_stats["total_sessions"] += 1

    generation_stats[
        "total_steps_generated"
    ] += result.total_steps

    confidences = []

    for step in result.steps:
        confidence = getattr(
            step,
            "confidence",
            None,
        )

        if confidence is not None:
            confidences.append(confidence)

    if confidences:
        avg_confidence = (
            sum(confidences)
            / len(confidences)
        )

        generation_stats["avg_confidence"] = (
            generation_stats["avg_confidence"]
            * 0.9
            + avg_confidence * 0.1
        )


def build_text_report(
    session_id: str,
    result: GenerateResponse,
) -> str:

    lines = []

    lines.append("=" * 70)
    lines.append(
        "  VEEVA VAULT TEST AUTOMATION SCRIPT"
    )

    lines.append("=" * 70)
    lines.append("")

    lines.append(
        f"Session ID    : {session_id}"
    )

    lines.append(
        "Generated     : "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    lines.append(
        f"Model Used    : {result.model_used}"
    )

    lines.append(
        f"Total Steps   : {result.total_steps}"
    )

    lines.append("")
    lines.append("=" * 70)
    lines.append("TEST STEPS")
    lines.append("=" * 70)
    lines.append("")

    current_user_step = None
    step_counter = 1

    for step in result.steps:
        if (
            step.userStep
            and step.userStep != current_user_step
        ):
            if current_user_step is not None:
                lines.append("")

            lines.append(f"{step.userStep}:")

            current_user_step = step.userStep

        lines.append(
            f"{step_counter}. "
            f"{step.enhanced_output}"
        )

        step_counter += 1

    lines.append("")
    lines.append("=" * 70)
    lines.append("DETAILED STEP INFORMATION")
    lines.append("=" * 70)
    lines.append("")

    for step in result.steps:
        lines.append(
            f"Step {step.step}: {step.name}"
        )

        lines.append(
            f"  Action       : {step.action}"
        )

        lines.append(
            f"  Label        : {step.label}"
        )

        if step.value:
            lines.append(
                f"  Value        : {step.value}"
            )

        lines.append(
            f"  Original     : {step.original_output}"
        )

        lines.append(
            f"  Enhanced     : {step.enhanced_output}"
        )

        if step.rag_context_used:
            lines.append(
                "  RAG Examples : "
                f"{len(step.rag_context_used)} retrieved"
            )

        lines.append("")

    lines.append("=" * 70)

    lines.append(
        f"End of test script - {session_id}"
    )

    lines.append("=" * 70)

    return "\n".join(lines)


def save_session(
    session_id: str,
    result: GenerateResponse,
) -> None:

    if len(sessions) >= MAX_SESSION_CACHE:
        sessions.popitem(last=False)

    sessions[session_id] = result

    update_generation_stats(result)

    txt_path = (
        OUTPUTS_DIR
        / f"{session_id}.txt"
    )

    txt_content = build_text_report(
        session_id,
        result,
    )

    with open(
        txt_path,
        "w",
        encoding="utf-8",
    ) as file:
        file.write(txt_content)

    json_path = (
        OUTPUTS_DIR
        / f"{session_id}.json"
    )

    with open(
        json_path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result.model_dump(),
            file,
            indent=2,
            ensure_ascii=False,
        )

    logger.info(
        "Session saved successfully | %s",
        session_id,
    )


def build_full_script(
    step_results,
) -> str:

    script_lines = []

    current_user_step = None

    for result in step_results:
        if (
            result.userStep
            and result.userStep != current_user_step
        ):
            if script_lines:
                script_lines.append("")

            script_lines.append(
                f"{result.userStep}:"
            )

            current_user_step = result.userStep

        script_lines.append(
            f"{result.step}. "
            f"{result.enhanced_output}"
        )

    return "\n".join(script_lines)


@app.get(
    "/health",
    response_model=HealthResponse,
)
async def health():

    return HealthResponse(
        status="ok",
        kb_files_loaded=len(
            rag.loaded_files
        ),
        kb_entries_total=rag.total_entries,
        groq_configured=bool(
            os.getenv("GROQ_API_KEY")
        ),
    )


@app.post(
    "/generate",
    response_model=GenerateResponse,
)
async def generate(
    req: GenerateRequest,
):

    if not req.entries:
        raise HTTPException(
            status_code=400,
            detail="No KB entries provided",
        )

    api_key, api_base = resolve_api_config(req)

    if req.provider == "groq" and not api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "Groq API key required. "
                "Pass 'api_key' or "
                "set GROQ_API_KEY env var"
            ),
        )

    session_rag = (
        build_session_rag()
        if req.use_rag
        else EnhancedRAGEngine()
    )

    logger.info("=" * 70)
    logger.info("GENERATION REQUEST")
    logger.info("=" * 70)

    logger.info(
        "Steps=%s | Provider=%s | Model=%s",
        len(req.entries),
        req.provider,
        req.model,
    )

    logger.info(
        "Temperature=%s | UseRAG=%s",
        req.temperature,
        req.use_rag,
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

    except Exception as exc:
        logger.exception(
            "Batch generation failed"
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )

    full_script = build_full_script(
        step_results
    )

    session_id = str(uuid.uuid4())[:8]

    if req.session_name:
        safe_name = (
            req.session_name
            .replace(" ", "_")
            .strip()
        )

        session_id = (
            f"{safe_name}_{session_id}"
        )

    response = GenerateResponse(
        session_id=session_id,
        total_steps=len(step_results),
        steps=step_results,
        full_script=full_script,
        download_url=(
            f"/download/{session_id}"
        ),
        model_used=req.model,
    )

    save_session(
        session_id,
        response,
    )

    logger.info(
        "Session completed successfully | %s",
        session_id,
    )

    return response


@app.post("/generate/stream")
async def generate_stream(
    req: GenerateRequest,
):

    if not req.entries:
        raise HTTPException(
            status_code=400,
            detail="No KB entries provided",
        )

    api_key, api_base = resolve_api_config(req)

    if req.provider == "groq" and not api_key:
        raise HTTPException(
            status_code=400,
            detail="Groq API key required",
        )

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
async def download(
    session_id: str,
    format: str = "txt",
):

    extension = (
        "json"
        if format == "json"
        else "txt"
    )

    path = (
        OUTPUTS_DIR
        / f"{session_id}.{extension}"
    )

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Session "
                f"'{session_id}' not found"
            ),
        )

    return FileResponse(
        path=str(path),
        filename=(
            f"veeva_test_"
            f"{session_id}.{extension}"
        ),
        media_type="application/octet-stream",
    )


@app.get("/sessions")
async def list_sessions():

    results = []

    txt_files = sorted(
        OUTPUTS_DIR.glob("*.txt"),
        reverse=True,
    )

    for txt_file in txt_files:
        session_id = txt_file.stem

        json_file = (
            OUTPUTS_DIR
            / f"{session_id}.json"
        )

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
                with open(
                    json_file,
                    "r",
                    encoding="utf-8",
                ) as file:
                    data = json.load(file)

                entry["total_steps"] = data.get(
                    "total_steps",
                    0,
                )

                entry["model_used"] = data.get(
                    "model_used",
                    "",
                )

            except Exception:
                logger.warning(
                    "Failed reading session metadata | %s",
                    session_id,
                )

        results.append(entry)

    return {
        "sessions": results,
        "total": len(results),
    }


@app.get("/stats")

@app.get("/patterns")
async def get_patterns():

    patterns = (
        config.get_runtime_cache(
            "dynamic_templates",
            {},
        )
    )

    return {
        "total_patterns": len(patterns),
        "patterns": patterns,
    }
    
async def get_stats():

    kb_stats = rag.get_stats()

    return {
        "knowledge_base": kb_stats,
        "generation": generation_stats,
        "system": {
            "total_sessions_stored": len(
                sessions
            ),
            "output_files": len(
                list(
                    OUTPUTS_DIR.glob("*.txt")
                )
            ),
        },
    }


@app.post("/kb/upload")
async def upload_kb(
    file: UploadFile = File(...),
):

    if not file.filename.endswith(".json"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Only .json files are accepted"
            ),
        )

    DATA_DIR.mkdir(exist_ok=True)

    destination = (
        DATA_DIR
        / file.filename
    )

    content = await file.read()

    try:
        parsed = json.loads(content)

        if not isinstance(parsed, list):
            raise ValueError(
                "File must be a JSON array"
            )

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON: {exc}",
        )

    with open(
        destination,
        "wb",
    ) as file_obj:
        file_obj.write(content)

    added = rag.load_file(
        str(destination)
    )

    rag.build_index()

    logger.info(
        "KB uploaded successfully | %s",
        file.filename,
    )

    return {
        "message": (
            f"Loaded {added} entries "
            f"from {file.filename}"
        ),
        "total_entries": rag.total_entries,
        "stats": rag.get_stats(),
    }


@app.post("/kb/reload")
async def reload_kb():

    rag.entries = []
    rag.tfidf_index = {}
    rag.bm25_index = {}
    rag.action_clusters = {}
    rag.label_index = {}
    rag._loaded_files = []

    init_rag()

    logger.info(
        "Knowledge base reloaded"
    )

    return {
        "message": (
            "KB reloaded successfully"
        ),
        "stats": rag.get_stats(),
    }


class ValidateRequest(BaseModel):
    output: str
    action: str
    label: str
    value: Optional[str] = None


@app.post("/validate")
async def validate_output_endpoint(
    req: ValidateRequest,
):

    cleaned = sanitize_output(
        req.output
    )

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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=DEBUG_MODE,
    )