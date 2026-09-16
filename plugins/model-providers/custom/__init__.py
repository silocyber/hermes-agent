"""Custom / Ollama (local) provider profile: any endpoint registered as
provider="custom" (Ollama, vLLM, llama.cpp, GLM-5.2 on ARK, …)."""

from typing import Any
from urllib.parse import urlparse

from agent.reasoning_effort import OPENAI_COMPAT_WIRE_EFFORTS, clamp_effort
from providers import register_provider
from providers.base import ProviderProfile


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
        # vLLM and SGLang all top out at "max"; "ultra" verbatim 400s); enabled
        # without effort -> omit so the server default applies. Never emit
        # think=True (Ollama-only flag).
        if reasoning_config and isinstance(reasoning_config, dict):
            effort = (reasoning_config.get("effort") or "").strip().lower()
            if effort == "none" or reasoning_config.get("enabled", True) is False:
                # See #14820.
                top_level["reasoning_effort"] = "none"
                if _looks_like_ollama_endpoint(ctx.get("base_url")):
                    extra_body["think"] = False
            elif effort:
                top_level["reasoning_effort"] = clamp_effort(effort, OPENAI_COMPAT_WIRE_EFFORTS)
        return extra_body, top_level

    def get_max_tokens(self, model: str | None) -> int | None:
        """Honor an explicit per-model ``max_output_tokens`` before the profile default.

        A custom endpoint is whatever the operator pointed it at, so no single
        client-side cap is right for all of them. Where the operator has stated
        one for a specific model, send it; otherwise fall through to the profile
        default, which is ``None`` here -- the endpoint owns its own generation
        default (#39281 removed the 65536 floor).

        Resolution order:
          1. explicit ``model_overrides.<provider>.<model>.max_output_tokens``
          2. ``self.default_max_tokens``
        """
        if model:
            try:
                from agent.models_dev import _explicit_model_override

                override = _explicit_model_override(self.name, model) or {}
                value = override.get("max_output_tokens")
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                    return int(value)
            except Exception:  # a metadata lookup must never fail a request
                pass
        return self.default_max_tokens

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
