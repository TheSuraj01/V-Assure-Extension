import asyncio
import json
import os
import re
from typing import AsyncIterator, Dict, List, Optional, Tuple

import litellm

litellm.set_verbose = False
litellm.suppress_debug_info = True

from config.config_loader import get_config
from models import KBEntry, StepResult
from rag import EnhancedRAGEngine
from utils import setup_logger
from services.pattern_matcher import (
    get_pattern_matcher,
)

logger = setup_logger(__name__)

config = get_config()

pattern_matcher = (
    get_pattern_matcher()
)

ELEMENT_TYPE_NAMES = {
    "button": "button",
    "link": "link",
    "tab": "tab",
    "menu-item": "menu item",
    "dropdown-option": "dropdown option",
    "checkbox": "checkbox",
    "radio": "radio button",
    "select": "dropdown",
    "input": "input field",
    "textarea": "text area",
    "toggle": "toggle",
    "file-upload": "file upload",
    "image": "image",
    "label": "label",
    "icon": "icon",
}

FORBIDDEN_WORDS = config.get(
    "prompting.forbidden_words",
    [],
)

ACTION_VERBS = config.get(
    "actions",
    {},
)

# ─────────────────────────────────────────────────────────────
# Config Helpers
# ─────────────────────────────────────────────────────────────

def get_validation_config() -> Dict:
    return config.get("validation", {})


def get_generation_config() -> Dict:
    return config.get("generation", {})


def get_prompt_config() -> Dict:
    return config.get("prompt", {})

def get_dynamic_template(
    action: str,
    template_key: str,
) -> Optional[str]:
    """
    Resolve dynamic template safely.
    """

    return (
        pattern_matcher.get_template_with_fallback(
            action=action,
            template_key=template_key,
        )
    )

# ─────────────────────────────────────────────────────────────
# Completion Wrapper
# ─────────────────────────────────────────────────────────────

async def safe_acompletion(
    *args,
    **kwargs,
):
    model_name = kwargs.get("model", "")
    provider = kwargs.get("custom_llm_provider")
    api_key = kwargs.get("api_key")

    if provider == "local":
        kwargs["custom_llm_provider"] = "openai"
        provider = "openai"

    # When routing to a custom/local OpenAI-compatible endpoint (api_base is set),
    # the model name must NOT carry the "openai/" prefix — litellm treats that
    # prefix as a signal to route to real OpenAI servers, ignoring api_base.
    if provider == "openai":
        api_base_val = kwargs.get("api_base", "")
        if api_base_val and model_name.startswith("openai/"):
            stripped = model_name[len("openai/"):]
            kwargs["model"] = stripped
            model_name = stripped

    if provider and provider != "bedrock" and "/" not in model_name:
        kwargs["model"] = f"{provider}/{model_name}"

    # NOTE: Bedrock AWS credentials disabled — not currently in use.
    # Uncomment if you need to use AWS Bedrock as an LLM provider.
    # if (
    #     model_name.startswith("bedrock/")
    #     and api_key
    #     and ":" in api_key
    # ):
    #     parts = api_key.split(":")
    #     kwargs["aws_access_key_id"] = parts[0].strip()
    #     kwargs["aws_secret_access_key"] = parts[1].strip()
    #     kwargs["aws_region_name"] = (
    #         parts[2].strip()
    #         if len(parts) >= 3
    #         else os.getenv("AWS_REGION_NAME", "us-east-1")
    #     )
    #     kwargs["api_key"] = None

    generation_config = get_generation_config()

    max_retries = generation_config.get(
        "max_retries",
        8,
    )

    retry_backoff_base = generation_config.get(
        "retry_backoff_base",
        2,
    )

    default_wait_time = generation_config.get(
        "default_wait_time",
        2,
    )

    for attempt in range(max_retries):
        try:
            return await litellm.acompletion(
                *args,
                **kwargs,
            )

        except Exception as exc:
            error_message = str(exc).lower()

            retryable = any(
                keyword in error_message
                for keyword in [
                    "429",
                    "ratelimit",
                    "rate_limit",
                    "timeout",
                    "temporarily unavailable",
                    "overloaded",
                ]
            )

            if not retryable:
                logger.error("Non-retryable error: %s", exc)
                raise

            if attempt == max_retries - 1:
                logger.error("Max retries (%s) exceeded for %s", max_retries, model_name)
                raise

            wait_time = default_wait_time

            match = re.search(
                r"try again in\s*([0-9.]+)\s*s?",
                str(exc),
                re.IGNORECASE,
            )

            if match:
                try:
                    wait_time = float(match.group(1)) + 0.3
                except ValueError:
                    pass
            else:
                wait_time = (retry_backoff_base**attempt) * default_wait_time

            logger.warning(
                "Rate limit hit for %s. Retrying in %.2fs (Attempt %d/%d)",
                model_name, wait_time, attempt + 1, max_retries,
            )

            await asyncio.sleep(wait_time)


# ─────────────────────────────────────────────────────────────
# Sanitization
# ─────────────────────────────────────────────────────────────

def sanitize_output(
    text: str,
) -> str:

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r"^step\s*\d*[\.:\)]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"^[\d]+[\.:\)]\s*",
        "",
        text,
    )

    text = text.strip("\"'")

    text = re.sub(
        r"'([^']+)'",
        r"'\1'",
        text,
    )

    text = " ".join(
        text.split()
    )

    text = text.rstrip(".")

    if not text.endswith(
        ("?", "!")
    ):
        text += "."

    if text:
        text = (
            text[0].upper()
            + text[1:]
        )

    return text


# ─────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────

def validate_output(
    text: str,
    action: str,
    label: str,
    value: str = "",
) -> Tuple[bool, float, str]:

    validation_config = (
        get_validation_config()
    )

    min_word_count = (
        validation_config.get(
            "min_word_count",
            2,
        )
    )

    max_word_count = (
        validation_config.get(
            "max_word_count",
            40,
        )
    )

    min_threshold = (
        validation_config.get(
            "min_confidence_threshold",
            0.55,
        )
    )

    if not text:
        return (
            False,
            0.0,
            "Empty output",
        )

    text_lower = text.lower()

    score = 0.5

    reasons = []

    valid_start = False

    expected_verbs = (
        ACTION_VERBS.get(
            action.lower(),
            [],
        )
    )

    if action.lower() == "select":
        expected_verbs = expected_verbs + ACTION_VERBS.get("enter", [])

    for verb in expected_verbs:

        if text.startswith(verb):
            valid_start = True
            score += 0.20
            break

    if not valid_start:
        return (
            False,
            0.0,
            "Invalid action verb",
        )

    for forbidden in FORBIDDEN_WORDS:
        term = forbidden.strip().lower()
        if not term:
            continue
        if term.isalnum():
            pattern = rf"\b{re.escape(term)}\b"
            if re.search(pattern, text_lower):
                return (
                    False,
                    0.0,
                    f"Forbidden word: {forbidden}",
                )
        else:
            if term in text_lower:
                return (
                    False,
                    0.0,
                    f"Forbidden word: {forbidden}",
                )

    label_lower = label.lower()

    if label_lower in text_lower:
        score += 0.20
        reasons.append(
            "Label matched"
        )

    if value:

        if (
            value.lower()
            in text_lower
        ):
            score += 0.20
            reasons.append(
                "Value matched"
            )

        if action.lower() in ("enter", "input", "type"):
            has_wrapped = (
                f"<<{value.lower()}>>" in text_lower
                or f"<< {value.lower()} >>" in text_lower
            )
            if not has_wrapped:
                return (
                    False,
                    0.0,
                    f"Missing <<value>> wrapper for enter action (value={value!r})",
                )

    word_count = len(
        text.split()
    )

    if (
        min_word_count
        <= word_count
        <= max_word_count
    ):
        score += 0.10

    else:
        score -= 0.10

    if text.endswith("."):
        score += 0.05

    score = max(
        0.0,
        min(1.0, score),
    )

    return (
        score >= min_threshold,
        score,
        " | ".join(reasons)
        if reasons
        else "Valid",
    )


# ─────────────────────────────────────────────────────────────
# Candidate Extraction
# ─────────────────────────────────────────────────────────────

def extract_candidate_lines(
    raw_output: str,
) -> List[str]:

    lines = [
        line.strip()
        for line in raw_output.split("\n")
        if line.strip()
    ]

    candidates = []

    for line in lines[:3]:

        cleaned = sanitize_output(
            line
        )

        if (
            cleaned
            and len(cleaned) > 4
        ):
            candidates.append(
                cleaned
            )

    return candidates


# ─────────────────────────────────────────────────────────────
# Prompt Builder
# ─────────────────────────────────────────────────────────────

def build_enhanced_prompt(
    entry: KBEntry,
    rag_examples: List[str],
    previous_steps: List[str],
    dynamic_examples: Optional[List[str]] = None,
    context_summary: str = "",
    template_string: Optional[str] = None,
    is_navbar: bool = False,
) -> str:
    """
    Build the LLM prompt for a single step generation.

    When is_navbar=True this function is NOT called — the caller returns
    the deterministic navbar output directly.  The parameter is kept as
    a guard in case the function is invoked directly elsewhere.
    """
    if is_navbar:
        return f"Click on {entry.input.label} from navbar."

    prompt_config = get_prompt_config()
    inp = entry.input

    interaction_lines = [
        f"Action Type: {inp.action}",
        f"UI Element Label: '{inp.label}'",
    ]

    if inp.value:
        interaction_lines.append(f"Text Entered: '{inp.value}'")
    if inp.selectedText:
        interaction_lines.append(f"Option Selected: '{inp.selectedText}'")
    if inp.placeholder:
        interaction_lines.append(f"Placeholder: '{inp.placeholder}'")
    if inp.dropdownLabel:
        interaction_lines.append(f"Dropdown Context: '{inp.dropdownLabel}'")
    if inp.ariaLabel:
        interaction_lines.append(f"ARIA Label: '{inp.ariaLabel}'")

    interaction_block = "\n".join(interaction_lines)

    mandatory_context_section = ""
    if rag_examples:
        if rag_examples and isinstance(rag_examples[0], dict):
            lines = []
            for i, ex in enumerate(rag_examples, 1):
                inp_parts = [f"action={ex.get('action', '')}", f"label={ex.get('label', '')}"]
                if ex.get("value"):
                    inp_parts.append(f"value={ex['value']}")
                inp_str = ", ".join(inp_parts)
                lines.append(f"{i}. [{inp_str}] → {ex.get('output', '')}")
            examples_text = "\n".join(lines)
        else:
            examples_text = "\n".join(
                f"{i + 1}. {example}"
                for i, example in enumerate(rag_examples)
            )
        mandatory_context_section = (
            "\nMANDATORY CONTEXT FROM KNOWLEDGE BASE:\n"
            "The following are verified input→output examples from this system. "
            "Each entry shows [what triggered the action] → [the exact output produced]. "
            "Your output MUST follow the exact same phrasing, style, and structure. "
            "Do NOT deviate from these patterns.\n"
            f"{examples_text}\n"
        )

    dynamic_section = ""
    if dynamic_examples:
        dynamic_text = "\n".join(
            f"{i + 1}. {example}"
            for i, example in enumerate(
                dynamic_examples
                )
            )
        dynamic_section = (
            "\nDYNAMIC TEMPLATE EXAMPLES:\n"
            f"{dynamic_text}\n"
        )

    prev_section = ""
    if previous_steps:
        previous_steps_text = "\n".join(
            f"{i + 1}. {step}"
            for i, step in enumerate(previous_steps[-5:])
        )
        prev_section = f"\nPREVIOUS STEPS:\n{previous_steps_text}\n"

    context_section = ""
    if context_summary:
        context_section = f"\nCONTEXT SUMMARY:\n{context_summary}\n"

    critical_rules = "\n".join(prompt_config.get("critical_rules", []))
    system_instruction = prompt_config.get(
        "system_instruction",
        "You are an expert Veeva Vault test automation engineer. Your task is to convert a UI interaction into ONE precise, professional test step."
    )

    draft = entry.output or ""
    subst = {
        "value":  inp.value or "",
        "label":  inp.label or "",
        "option": inp.selectedText or "",
    }
    for k, v in subst.items():
        if v:
            draft = draft.replace(f"{{{k}}}", v)
            draft = draft.replace(f"<<{k}>>", v)

    format_section = ""
    if template_string:
        format_section = f"\nREQUIRED TEMPLATE FORMAT:\n{template_string}\n"

    prompt = f"""{system_instruction}

CRITICAL RULES (violating ANY rule = failure):
{critical_rules}
{context_section}
{mandatory_context_section}
{dynamic_section}
{format_section}
{prev_section}
CURRENT INTERACTION:
{interaction_block}
DRAFT (may be inaccurate or poorly formatted):
{draft}
YOUR TASK: Generate ONE perfect test step following all rules above. \
If MANDATORY CONTEXT is provided above, your answer MUST match the style \
and phrasing of those examples.
OUTPUT (one sentence only, no prefix, no explanation):""".strip()

    logger.debug(
        "[PROMPT] Final prompt sent to LLM (first 1500 chars):\n%s",
        prompt[:1500],
    )

    return prompt


# ─────────────────────────────────────────────────────────────
# Template Generation
# ─────────────────────────────────────────────────────────────

def try_template_generation(
    entry: KBEntry,
) -> Optional[str]:

    inp = entry.input

    action = (
        inp.action.lower()
    )

    label = inp.label

    value = inp.value or ""

    selected = (
        inp.selectedText or ""
    )

    try:

        # ─────────────────────────────────────────────
        # Navigation
        # ─────────────────────────────────────────────

        if (
            action == "click"
            and ">" in label
        ):

            template = (
                get_dynamic_template(
                    action="click",
                    template_key="navigate_to",
                )
            )

            if template:

                return template.format(
                    label=label,
                )

        # ─────────────────────────────────────────────
        # Input Fields
        # ─────────────────────────────────────────────

        if (
            action
            in ["enter", "input", "type"]
            and value
        ):

            template = (
                get_dynamic_template(
                    action="enter",
                    template_key="input_with_value",
                )
            )

            if template:

                return template.format(
                    value=value,
                    label=label,
                )

        # ─────────────────────────────────────────────
        # Dropdown Select
        # ─────────────────────────────────────────────

        if (
            action == "select"
            and selected
        ):
            template = None
            if getattr(inp, "hasInput", False):
                template = get_dynamic_template(
                    action="select",
                    template_key="dropdown_search_select",
                )
                if template:
                    return template.format(
                        value=selected,
                        label=(
                            inp.dropdownLabel
                            or label
                        ),
                    )

            if not template:
                template = get_dynamic_template(
                    action="select",
                    template_key="dropdown_select",
                )
                if template:
                    return template.format(
                        option=selected,
                        label=(
                            inp.dropdownLabel
                            or label
                        ),
                    )

        # ─────────────────────────────────────────────
        # Click Actions
        # ─────────────────────────────────────────────

        if action == "click":

            template = (
                get_dynamic_template(
                    action="click",
                    template_key="button_click",
                )
            )

            if template:

                return template.format(
                    label=label,
                )

    except Exception:

        logger.exception(
            "Dynamic template generation failed"
        )

    return None


# ─────────────────────────────────────────────────────────────
# Single Step Generation
# ─────────────────────────────────────────────────────────────

_generation_config = get_generation_config()
_validation_config = get_validation_config()
_rag_config = config.get("rag", {})
_template_confidence = _validation_config.get("template_confidence_threshold", 0.80)

async def generate_single_step(
    entry: KBEntry,
    rag: EnhancedRAGEngine,
    api_key: str,
    model: str,
    provider: str,
    api_base: Optional[str] = None,
    temperature: float = 0.12,
    previous_steps: Optional[List[str]] = None,
) -> StepResult:

    previous_steps = previous_steps or []
    inp = entry.input

    _CONTEXT_FORMATS = {
        "navbar":              "Click on {label} from navbar.",
        "tab_collection_menu": "Click on {label} from tab collection menu.",
    }
    _ctx = getattr(inp, "context", None)
    if _ctx in _CONTEXT_FORMATS:
        _raw_output = _CONTEXT_FORMATS[_ctx].format(label=inp.label)
        fast_output = sanitize_output(_raw_output)
        logger.info(
            "[FAST-PATH] context=%r | label=%r | output=%r",
            _ctx,
            inp.label,
            fast_output,
        )
        return StepResult(
            step=1,
            name=entry.name,
            original_output=entry.output,
            enhanced_output=fast_output,
            action=inp.action,
            label=inp.label,
            value=inp.value,
            userStep=inp.userStep,
            rag_context_used=[],
            confidence=1.0,
            validation_reason=f"{_ctx}_fast_path",
        )

    template_result = try_template_generation(entry)

    if template_result:
        cleaned_template = sanitize_output(template_result)
        is_valid, t_score, t_reason = validate_output(
            text=cleaned_template,
            action=inp.action,
            label=inp.label,
            value=inp.value or inp.selectedText or "",
        )

        if t_score >= _template_confidence:
            return StepResult(
                step=1,
                name=entry.name,
                original_output=entry.output,
                enhanced_output=cleaned_template,
                action=inp.action,
                label=inp.label,
                value=inp.value,
                userStep=inp.userStep,
                rag_context_used=[],
                confidence=round(t_score, 3),
                validation_reason=f"template_fast_path | {t_reason}",
            )

    # ── Full LLM generation path ────────────────────────────────────────
    logger.debug(
        "[RAG] Pre-retrieval | action=%r label=%r value=%r",
        inp.action,
        inp.label,
        inp.value or inp.selectedText or "",
    )

    rag_examples = []
    dynamic_examples = pattern_matcher.get_template_examples(
        action=inp.action,
    )

    if rag:
        try:
            rag_examples = rag.retrieve_with_context(
                action=inp.action,
                label=inp.label,
                value=inp.value or inp.selectedText or "",
                top_k=_rag_config.get("top_k", 5),
                diversity_weight=_rag_config.get("diversity_weight", 0.3),
            )
            logger.info(
                "[RAG] Retrieved %d example(s) for label=%r action=%r",
                len(rag_examples),
                inp.label,
                inp.action,
            )
        except Exception:
            logger.exception("RAG retrieval failed")

    candidate_outputs = []

    if template_result:
        candidate_outputs.append(template_result)

    prompt = build_enhanced_prompt(
        entry=entry,
        rag_examples=rag_examples,
        previous_steps=previous_steps,
        dynamic_examples=dynamic_examples,
        template_string=template_result,
    )

    num_candidates = _generation_config.get("num_candidates", 1)

    for _ in range(num_candidates):
        try:
            response = await safe_acompletion(
                model=model,
                custom_llm_provider=provider,
                api_key=api_key,
                api_base=api_base,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )

            content = response.choices[0].message.content

            if content:
                candidate_outputs.extend(extract_candidate_lines(content))

        except Exception:
            logger.exception("Generation failed")

    best_output = entry.output
    best_score = 0.0
    best_reason = "fallback"

    for candidate in candidate_outputs:
        cleaned = sanitize_output(candidate)
        is_valid, score, reason = validate_output(
            text=cleaned,
            action=inp.action,
            label=inp.label,
            value=inp.value or inp.selectedText or "",
        )

        if score > best_score:
            best_output = cleaned
            best_score = score
            best_reason = reason

    logger.info(
        "[GEN] Step complete | label=%r | score=%.3f | reason=%s | output=%r",
        inp.label,
        best_score,
        best_reason,
        best_output[:80] if best_output else "",
    )

    return StepResult(
        step=1,
        name=entry.name,
        original_output=entry.output,
        enhanced_output=best_output,
        action=inp.action,
        label=inp.label,
        value=inp.value,
        userStep=inp.userStep,
        rag_context_used=rag_examples,
        confidence=round(best_score, 3),
        validation_reason=best_reason,
    )


# ─────────────────────────────────────────────────────────────
# Batch Generation (with concurrency)
# ─────────────────────────────────────────────────────────────

async def run_batch_enhanced(
    entries: List[KBEntry],
    rag: EnhancedRAGEngine,
    api_key: str,
    model: str,
    provider: str,
    api_base: Optional[str] = None,
    temperature: float = 0.12,
    use_multi_candidate: bool = True,
) -> List[StepResult]:

    max_parallel = _generation_config.get("max_parallel_generations", 5)
    semaphore = asyncio.Semaphore(max_parallel)

    async def _generate_one(index: int, entry: KBEntry) -> StepResult:
        async with semaphore:
            result = await generate_single_step(
                entry=entry,
                rag=rag,
                api_key=api_key,
                model=model,
                provider=provider,
                api_base=api_base,
                temperature=temperature,
                previous_steps=[],  # parallel steps can't have sequential context
            )
            result.step = index + 1
            return result

    results = await asyncio.gather(
        *[_generate_one(i, entry) for i, entry in enumerate(entries)]
    )

    return list(results)



# ─────────────────────────────────────────────────────────────
# Streaming Generation
# ─────────────────────────────────────────────────────────────

async def run_streaming_enhanced(
    entries: List[KBEntry],
    rag: EnhancedRAGEngine,
    api_key: str,
    model: str,
    provider: str,
    api_base: Optional[str] = None,
    temperature: float = 0.12,
) -> AsyncIterator[str]:

    previous_steps: List[
        str
    ] = []

    for index, entry in enumerate(
        entries
    ):

        result = (
            await generate_single_step(
                entry=entry,
                rag=rag,
                api_key=api_key,
                model=model,
                provider=provider,
                api_base=api_base,
                temperature=temperature,
                previous_steps=previous_steps,
            )
        )

        result.step = index + 1

        previous_steps.append(
            result.enhanced_output
        )

        payload = {
            "step": result.step,
            "name": result.name,
            "enhanced_output": (
                result.enhanced_output
            ),
            "confidence": (
                result.confidence
            ),
        }

        yield (
            f"data: "
            f"{json.dumps(payload)}\n\n"
        )

    yield 'data: {"done": true}\n\n'