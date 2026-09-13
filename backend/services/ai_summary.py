"""Optional AI summary of one user's journey.

Everything else in WACA path works with no AI provider at all. This module is
the one place that talks to an LLM, and it is *off* unless both of these are
set in the environment:

    WACA_PATH_AI_PROVIDER = anthropic | openai | google
    <provider key>        = ANTHROPIC_API_KEY | OPENAI_API_KEY | GOOGLE_API_KEY

Optional:

    WACA_PATH_AI_MODEL              override the provider default model
    WACA_PATH_AI_RATE_LIMIT_PER_MIN per-process cap on calls (default 10)
    WACA_PATH_AI_MAX_TOKENS         output cap (default 1200)

Design rules:

* The provider SDKs are **not** in ``backend/requirements.txt``. They are
  imported lazily inside the call, and only when the feature is enabled, so
  an install without ``requirements-ai.txt`` never fails to import.
* Keys are read from the environment at call time and are never logged,
  printed or echoed in an error message.
* The only data sent to the model is the journey context and timeline
  digest built by :mod:`services.journey_context` (pseudonymous IDs, page
  paths, timestamps, event names). No custom-dimension values are sent.
* A small sliding-window rate limit protects against a runaway page reload
  turning into a bill.

The model is asked for JSON with three parts, borrowed in spirit (not schema)
from snowprism's persona page: a one-paragraph persona-style summary, a 3-5
stage customer-journey sketch with evidence pages and a confidence, and up
to three draft improvement ideas. The result is a *draft for an analyst*,
never an automatic change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("waca_path_backend.ai_summary")

PROVIDER_ENV = "WACA_PATH_AI_PROVIDER"
MODEL_ENV = "WACA_PATH_AI_MODEL"
RATE_LIMIT_ENV = "WACA_PATH_AI_RATE_LIMIT_PER_MIN"
MAX_TOKENS_ENV = "WACA_PATH_AI_MAX_TOKENS"

# provider -> (key env var, default model, pip package)
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-4-6", "anthropic"),
    "openai": ("OPENAI_API_KEY", "gpt-5-mini", "openai"),
    "google": ("GOOGLE_API_KEY", "gemini-2.5-flash", "google-genai"),
}
DEFAULT_RATE_LIMIT_PER_MIN = 10
DEFAULT_MAX_TOKENS = 1200
MAX_MAX_TOKENS = 4000
REQUEST_TIMEOUT_SEC = 60.0

STAGE_MIN, STAGE_MAX = 3, 5
IDEAS_MAX = 3
CONFIDENCES = ("low", "medium", "high")


# --------------------------------------------------------------------------
# Errors (the router maps these to HTTP statuses)
# --------------------------------------------------------------------------
class AIError(Exception):
    code = "ai_error"


class AIDisabled(AIError):
    """No provider configured, or the provider's key is missing -> 409."""

    code = "ai_disabled"


class AIRateLimited(AIError):
    """Per-process call cap reached -> 429."""

    code = "ai_rate_limited"


class AISDKMissing(AIError):
    """Provider enabled but its SDK is not installed -> 503."""

    code = "ai_sdk_missing"


class AIProviderError(AIError):
    """The provider call failed or returned something unusable -> 502."""

    code = "ai_provider_error"


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AIConfig:
    provider: str
    model: str
    key_env: str
    max_tokens: int

    @property
    def package(self) -> str:
        return PROVIDERS[self.provider][2]


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except ValueError:
        value = default
    return max(lo, min(value, hi))


def ai_config() -> AIConfig | None:
    """The active configuration, or ``None`` when AI is not enabled.

    Enabled means: a known provider name in ``WACA_PATH_AI_PROVIDER`` *and* a
    non-empty key in that provider's env var. Anything else - unset, unknown
    provider, key missing - is simply "off"; there is no error state.
    """
    provider = (os.environ.get(PROVIDER_ENV) or "").strip().lower()
    if provider not in PROVIDERS:
        if provider:
            logger.warning("%s=%r is not one of %s; AI summary disabled", PROVIDER_ENV, provider, sorted(PROVIDERS))
        return None
    key_env, default_model, _ = PROVIDERS[provider]
    if not (os.environ.get(key_env) or "").strip():
        logger.info("%s=%s but %s is not set; AI summary disabled", PROVIDER_ENV, provider, key_env)
        return None
    model = (os.environ.get(MODEL_ENV) or "").strip() or default_model
    return AIConfig(
        provider=provider,
        model=model,
        key_env=key_env,
        max_tokens=_int_env(MAX_TOKENS_ENV, DEFAULT_MAX_TOKENS, lo=200, hi=MAX_MAX_TOKENS),
    )


def is_enabled() -> bool:
    return ai_config() is not None


def public_status() -> dict[str, Any]:
    """What the UI and the context API may reveal: never the key."""
    cfg = ai_config()
    if cfg is None:
        return {"enabled": False, "provider": None, "model": None}
    return {"enabled": True, "provider": cfg.provider, "model": cfg.model}


# --------------------------------------------------------------------------
# Rate limit (per process; Cloud Run instances each get their own)
# --------------------------------------------------------------------------
class SlidingWindowLimiter:
    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = max(1, per_minute)
        self._clock = clock
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._calls and now - self._calls[0] >= 60.0:
                self._calls.popleft()
            if len(self._calls) >= self.per_minute:
                return False
            self._calls.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


_limiter: SlidingWindowLimiter | None = None


def limiter() -> SlidingWindowLimiter:
    global _limiter
    if _limiter is None:
        _limiter = SlidingWindowLimiter(_int_env(RATE_LIMIT_ENV, DEFAULT_RATE_LIMIT_PER_MIN, lo=1, hi=600))
    return _limiter


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a web analytics assistant helping a human analyst read ONE anonymous "
    "visitor's click-stream from Google Analytics 4 (via WACA core). You only see "
    "page paths, timestamps, event names and coarse device / traffic labels. "
    "Never invent facts that are not in the data; when you infer, say so and keep the "
    "confidence honest. Do not guess the person's identity, demographics or protected "
    "attributes. Answer strictly as one JSON object and nothing else."
)

_OUTPUT_SHAPE = {
    "persona_summary": "one paragraph (3-5 sentences) describing what this visitor seems to be trying to do and how they behave on the site",
    "journey_stages": [
        {
            "stage": "short stage name, e.g. Discover / Compare / Decide / Return",
            "evidence_pages": ["/path", "..."],
            "summary": "one sentence on what happened in this stage",
            "confidence": "low | medium | high",
        }
    ],
    "improvement_ideas": ["up to 3 concrete, testable draft ideas for the site owner"],
}


def build_prompt(context: dict[str, Any], journey: dict[str, Any], lang: str) -> tuple[str, str]:
    """Return ``(system, user)`` prompt strings. Pure; tested offline."""
    language = "Japanese" if lang == "ja" else "English"
    sessions = []
    for sess in journey.get("sessions") or []:
        sessions.append(
            {
                "session_no": sess.get("session_no"),
                "start": str(sess.get("session_start") or ""),
                "duration_sec": int(sess.get("duration_sec") or 0),
                "device": sess.get("device_category"),
                "traffic": f"{sess.get('traffic_source') or '(direct)'} / {sess.get('traffic_medium') or '(none)'}",
                "timeline": [
                    {
                        "t": str(pv.get("event_timestamp") or "")[11:19],
                        "path": pv.get("page_path"),
                        "title": (pv.get("page_title") or "")[:60],
                        "dwell_sec": int(pv.get("dwell_sec") or 0),
                        "events": [e.get("event_name") for e in pv.get("attached_events") or []][:12],
                        "key": bool(pv.get("key_event_count")),
                    }
                    if pv.get("kind") == "pv"
                    else {"t": str(pv.get("event_timestamp") or "")[11:19], "event": pv.get("event_name")}
                    for pv in sess.get("timeline") or []
                ][:40],
                "notes": [n.get("text", {}).get("en") or n.get("code") for n in sess.get("inference_notes") or []],
            }
        )
    payload = {
        "data_window": context.get("data_window"),
        "journey_outcome": context.get("journey_outcome"),
        "session_gaps": {
            "long_gaps": context.get("session_gaps", {}).get("long_gaps"),
            "max_gap_days": context.get("session_gaps", {}).get("max_gap_days"),
        },
        "visited_paths": context.get("visited_paths"),
        "expected_next_pages": context.get("expected_next_pages"),
        "device_and_traffic": context.get("device_and_traffic"),
        "sessions": sessions[:30],
    }
    user = (
        f"Write the JSON values in {language}.\n"
        f"Required JSON shape (keys in English, values in {language}):\n"
        f"{json.dumps(_OUTPUT_SHAPE, ensure_ascii=False, indent=1)}\n"
        f"Rules: {STAGE_MIN}-{STAGE_MAX} journey_stages in time order; evidence_pages must be paths that "
        f"appear in the data; at most {IDEAS_MAX} improvement_ideas; confidence is one of "
        f"{', '.join(CONFIDENCES)}.\n\n"
        f"Visitor data:\n{json.dumps(payload, ensure_ascii=False, default=str)}"
    )
    return SYSTEM_PROMPT, user


# --------------------------------------------------------------------------
# Provider calls (lazy SDK imports; each returns the raw text)
# --------------------------------------------------------------------------
def _key(cfg: AIConfig) -> str:
    value = (os.environ.get(cfg.key_env) or "").strip()
    if not value:
        raise AIDisabled(cfg.key_env)
    return value


def _call_anthropic(cfg: AIConfig, system: str, user: str) -> str:
    import anthropic  # optional dependency: backend/requirements-ai.txt

    client = anthropic.Anthropic(api_key=_key(cfg), timeout=REQUEST_TIMEOUT_SEC, max_retries=1)
    response = client.messages.create(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")


def _call_openai(cfg: AIConfig, system: str, user: str) -> str:
    import openai  # optional dependency: backend/requirements-ai.txt

    client = openai.OpenAI(api_key=_key(cfg), timeout=REQUEST_TIMEOUT_SEC, max_retries=1)
    response = client.responses.create(
        model=cfg.model,
        instructions=system,
        input=user,
        max_output_tokens=cfg.max_tokens,
    )
    return response.output_text or ""


def _call_google(cfg: AIConfig, system: str, user: str) -> str:
    from google import genai  # optional dependency: backend/requirements-ai.txt
    from google.genai import types

    client = genai.Client(api_key=_key(cfg))
    response = client.models.generate_content(
        model=cfg.model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=cfg.max_tokens,
            response_mime_type="application/json",
        ),
    )
    return response.text or ""


_CALLS: dict[str, Callable[[AIConfig, str, str], str]] = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "google": _call_google,
}


def complete_text(cfg: AIConfig, system: str, user: str) -> str:
    """Dispatch to the provider. Tests monkeypatch this single function."""
    call = _CALLS[cfg.provider]
    try:
        return call(cfg, system, user)
    except ImportError as exc:
        raise AISDKMissing(cfg.package) from exc
    except AIError:
        raise
    except Exception as exc:  # SDK-specific error classes; never include the key
        logger.warning("AI provider call failed: provider=%s model=%s error=%s", cfg.provider, cfg.model, type(exc).__name__)
        raise AIProviderError(type(exc).__name__) from exc


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model reply (fenced or not)."""
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text or "")] + [text or ""]
    for cand in candidates:
        cand = cand.strip()
        start, end = cand.find("{"), cand.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            obj = json.loads(cand[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise AIProviderError("no JSON object in reply")


def normalize_summary(obj: dict[str, Any]) -> dict[str, Any]:
    """Coerce the model's JSON into the documented shape; drop anything else."""
    persona = str(obj.get("persona_summary") or "").strip()
    stages_out: list[dict[str, Any]] = []
    for st in obj.get("journey_stages") or []:
        if not isinstance(st, dict):
            continue
        conf = str(st.get("confidence") or "low").strip().lower()
        pages = st.get("evidence_pages") or []
        stages_out.append(
            {
                "stage": str(st.get("stage") or "").strip(),
                "summary": str(st.get("summary") or "").strip(),
                "evidence_pages": [str(p) for p in pages if isinstance(p, (str, int))][:8]
                if isinstance(pages, list)
                else [],
                "confidence": conf if conf in CONFIDENCES else "low",
            }
        )
    ideas = [str(i).strip() for i in (obj.get("improvement_ideas") or []) if str(i).strip()]
    return {
        "persona_summary": persona,
        "journey_stages": stages_out[:STAGE_MAX],
        "improvement_ideas": ideas[:IDEAS_MAX],
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
async def summarize(context: dict[str, Any], journey: dict[str, Any], *, lang: str = "ja") -> dict[str, Any]:
    """Run the optional AI summary. Raises :class:`AIError` subclasses."""
    cfg = ai_config()
    if cfg is None:
        raise AIDisabled()
    if not limiter().try_acquire():
        raise AIRateLimited()
    system, user = build_prompt(context, journey, lang)
    started = time.monotonic()
    text = await asyncio.to_thread(complete_text, cfg, system, user)
    summary = normalize_summary(parse_json_object(text))
    logger.info(
        "AI summary ok: provider=%s model=%s lang=%s stages=%d ideas=%d elapsed=%.1fs",
        cfg.provider,
        cfg.model,
        lang,
        len(summary["journey_stages"]),
        len(summary["improvement_ideas"]),
        time.monotonic() - started,
    )
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "lang": lang,
        "summary": summary,
        "disclaimer_key": "ai.disclaimer",
    }
