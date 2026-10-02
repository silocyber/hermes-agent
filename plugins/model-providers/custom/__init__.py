"""Custom / Ollama (local) provider profile: any endpoint registered as
provider="custom" (Ollama, vLLM, llama.cpp, GLM-5.2 on ARK, …)."""

from typing import Any
from urllib.parse import urlparse

from agent.reasoning_effort import OPENAI_COMPAT_WIRE_EFFORTS, clamp_effort
from providers import register_provider
from providers.base import ProviderProfile
from utils import base_url_host_matches

import logging
import os

logger = logging.getLogger(__name__)


def _fallback_output_cap() -> int:
    """HERMES_FALLBACK_MAX_OUTPUT_TOKENS when it is a positive integer, else 16384."""
    try:
        value = int(os.environ.get("HERMES_FALLBACK_MAX_OUTPUT_TOKENS", "16384"))
    except ValueError:
        return 16384
    return value if value > 0 else 16384


def _looks_like_ollama_endpoint(base_url: str | None) -> bool:
    """True only for explicit Ollama signatures (port 11434 or an ``ollama`` host label).
    ``think`` is Ollama-native; strict hosts (Mistral, Groq) 422 on it, and
    arbitrary localhost may be llama.cpp / vLLM / LM Studio."""
    raw = (base_url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    try:  # urlparse raises ValueError on malformed ports ("host:99999"); treat as not-Ollama.
        if parsed.port == 11434:
            return True
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return bool(host) and (host == "ollama.com" or host.endswith(".ollama.com") or "ollama" in host.split("."))


class CustomProfile(ProviderProfile):
    """Custom/Ollama local provider — think=false and num_ctx support."""

    def supported_reasoning_efforts(self, model: str | None) -> tuple[str, ...]:
        """The OpenAI-compat wire set, mirroring this profile's own chat-completions clamp.

        Without this declaration the Responses transport clamps onto the OpenAI
        per-model ladder (``codex_supported_efforts``), where ``max`` is gpt-5.6-only —
        so a custom relay's model had a configured ``max`` silently demoted to
        ``xhigh`` while the same provider over chat-completions forwarded ``max``
        unchanged (#114249). A custom endpoint's vocabulary is undiscoverable, so
        the widest OpenAI-compat set is the honest ceiling; ``ultra`` still clamps
        to ``max`` via the shared ``clamp_effort`` policy.
        """
        return OPENAI_COMPAT_WIRE_EFFORTS

    def default_reasoning_config(self, model: str | None = None) -> dict | None:
        """Unset ``agent.reasoning_effort`` → ``medium``, as on the Nous / OpenRouter profiles.

        Leaving the field off lets the endpoint's own default apply, and for a hosted reasoning
        model that default can be its ceiling: kimi-k3 behind an OpenAI-compatible relay defaults
        to ``max`` — 3x the reasoning tokens and ~3x the latency of medium. The agent skips this
        default for models the catalog marks non-reasoning (``agent.reasoning_params``).
        """
        return {"enabled": True, "effort": "medium"}

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, ollama_num_ctx: int | None = None, **ctx: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        top_level: dict[str, Any] = {}
        if ollama_num_ctx:
            extra_body["options"] = {"num_ctx": ollama_num_ctx}
        # disabled -> top-level reasoning_effort="none" (Ollama's /v1 ignores
        # extra_body.think) plus think=False only on Ollama URLs; enabled+effort ->
        # top-level reasoning_effort clamped to the OpenAI-compat wire (GLM/ARK,
        # vLLM and SGLang all top out at "max"; "ultra" verbatim 400s); None ->
        # omit so the server default applies (auxiliary calls without an effort, and
        # the main loop after the route rejected the reasoning field — an unset main
        # effort arrives here already filled by default_reasoning_config). Never emit
        # think=True (Ollama-only flag).
        if reasoning_config and isinstance(reasoning_config, dict):
            effort = (reasoning_config.get("effort") or "").strip().lower()
            if effort == "none" or reasoning_config.get("enabled", True) is False:
                # See #14820.
                top_level["reasoning_effort"] = "none"
                if _looks_like_ollama_endpoint(ctx.get("base_url")):
                    extra_body["think"] = False
            elif effort and base_url_host_matches(str(ctx.get("base_url") or ""), "api.groq.com"):
                # Groq's OpenAI-compatible wire accepts top-level reasoning_effort only as
                # "none" / "default"; any graded level ("medium", "high") 400s (#75089).
                top_level["reasoning_effort"] = "default"
            elif effort:
                top_level["reasoning_effort"] = clamp_effort(effort, OPENAI_COMPAT_WIRE_EFFORTS)
        return extra_body, top_level

    def get_max_tokens(self, model: str | None) -> int | None:
        """Per-seat output cap (silocyber patch, VIGI-511).

        Upstream fd3565deec stopped reading `max_output_tokens` from model_overrides, and this
        profile sets no default_max_tokens, so a custom-provider seat would send no cap at all and
        the endpoint's default would decide. This returns
        model_overrides.<provider>.<model>.max_output_tokens when it is a positive number, else the
        profile default (None: no cap, as upstream). A FAILED lookup never silently sends no cap:
        it logs a warning and returns HERMES_FALLBACK_MAX_OUTPUT_TOKENS (default 16384).
        """
        if not model:
            return self.default_max_tokens
        try:
            from agent.models_dev import _explicit_model_override
            override = _explicit_model_override(self.name, model)
        except Exception as exc:  # the lookup broke: cap conservatively and say so
            fallback = _fallback_output_cap()
            logger.warning("custom provider: max_output_tokens lookup failed for %s (%s); capping output at %d",
                           model, exc, fallback)
            return fallback
        val = (override or {}).get("max_output_tokens")
        if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0:
            return self.default_max_tokens
        return int(val)

    def fetch_models(
        self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0
    ) -> list[str] | None:
        """base_url is user-configured; fetch only if set."""
        if not (base_url or self.base_url):
            return None
        return super().fetch_models(api_key=api_key, base_url=base_url, timeout=timeout)


custom = CustomProfile(
    name="custom", aliases=("ollama", "local", "vllm", "llamacpp", "llama.cpp", "llama-cpp"),
    env_vars=(),  # No fixed key — custom endpoint
    base_url="",  # User-configured
    # An arbitrary client ceiling can exceed a local server's actual output limit.
    # The endpoint owns its generation default.
)

register_provider(custom)
