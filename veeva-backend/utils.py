import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────
# Logger
# ─────────────────────────────────────────────────────────────

def setup_logger(
    name: str,
    level: int = logging.INFO,
) -> logging.Logger:

    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)

    logger.propagate = False

    return logger


# ─────────────────────────────────────────────────────────────
# File Helpers
# ─────────────────────────────────────────────────────────────

def ensure_directory(
    directory: Path,
) -> Path:

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    return directory


def save_json_file(
    path: Path,
    data: Dict[str, Any],
) -> None:

    ensure_directory(path.parent)

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )


def load_json_file(
    path: Path,
) -> Dict[str, Any]:

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


# ─────────────────────────────────────────────────────────────
# Session Helpers
# ─────────────────────────────────────────────────────────────

def generate_session_name(
    prefix: Optional[str] = None,
) -> str:

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    if prefix:
        safe_prefix = (
            prefix.replace(" ", "_")
            .replace("/", "_")
            .strip()
        )

        return f"{safe_prefix}_{timestamp}"

    return timestamp


# ─────────────────────────────────────────────────────────────
# Text Helpers
# ─────────────────────────────────────────────────────────────

def normalize_whitespace(
    text: str,
) -> str:

    return " ".join(text.split())


def safe_lower(
    value: Optional[str],
) -> str:

    return value.lower().strip() if value else ""


def truncate_text(
    text: str,
    max_length: int = 100,
) -> str:

    if len(text) <= max_length:
        return text

    return text[: max_length - 3] + "..."


# ─────────────────────────────────────────────────────────────
# Report Builders
# ─────────────────────────────────────────────────────────────

def build_step_script(
    steps: List[Any],
) -> str:

    lines: List[str] = []

    current_user_step = None

    for step in steps:

        if (
            getattr(step, "userStep", None)
            and step.userStep != current_user_step
        ):
            if lines:
                lines.append("")

            lines.append(f"{step.userStep}:")

            current_user_step = step.userStep

        lines.append(
            f"{step.step}. {step.enhanced_output}"
        )

    return "\n".join(lines)


def build_text_report(
    session_id: str,
    result: Any,
) -> str:

    lines: List[str] = []

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

        if getattr(
            step,
            "confidence",
            None,
        ) is not None:
            lines.append(
                "  Confidence   : "
                f"{round(step.confidence, 3)}"
            )

        if getattr(
            step,
            "validation_reason",
            None,
        ):
            lines.append(
                "  Validation   : "
                f"{step.validation_reason}"
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


# ─────────────────────────────────────────────────────────────
# Statistics Helpers
# ─────────────────────────────────────────────────────────────

def calculate_average_confidence(
    steps: List[Any],
) -> float:

    confidences = [
        step.confidence
        for step in steps
        if getattr(step, "confidence", None)
        is not None
    ]

    if not confidences:
        return 0.0

    return round(
        sum(confidences) / len(confidences),
        3,
    )


# ─────────────────────────────────────────────────────────────
# Dictionary Helpers
# ─────────────────────────────────────────────────────────────

def deep_merge_dicts(
    base: Dict[str, Any],
    override: Dict[str, Any],
) -> Dict[str, Any]:

    merged = dict(base)

    for key, value in override.items():

        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge_dicts(
                merged[key],
                value,
            )

        else:
            merged[key] = value

    return merged


# ─────────────────────────────────────────────────────────────
# Exported Helpers
# ─────────────────────────────────────────────────────────────

__all__ = [
    "setup_logger",
    "ensure_directory",
    "save_json_file",
    "load_json_file",
    "generate_session_name",
    "normalize_whitespace",
    "safe_lower",
    "truncate_text",
    "build_step_script",
    "build_text_report",
    "calculate_average_confidence",
    "deep_merge_dicts",
]