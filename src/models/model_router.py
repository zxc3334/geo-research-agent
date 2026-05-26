"""Backward-compatible facade for creating LLM policies.

Historically this project called different API services "backends".  M6 keeps
``ModelRouter`` for existing imports, but the implementation now delegates to
``LLMModelFactory`` where the clearer names are:

- provider: OpenAI-compatible API service and credentials.
- profile: model name plus sampling parameters.
- module profile: planner/solver/summarizer routing.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..utils.env_config import ensure_env_loaded, get_env
from .model_factory import LLMModelFactory
from .vllm_policy import VLLMPolicy


__all__ = ["ModelRouter"]


_BACKEND_CACHE: dict[str, VLLMPolicy] = {}


class ModelRouter:
    """Compatibility wrapper around provider/profile model creation."""

    @staticmethod
    def create_backend(
        backend_name: str | None = None,
        use_cache: bool = True,
        **override_kwargs: Any,
    ) -> VLLMPolicy:
        """Create a policy for a legacy backend/provider name.

        Prefer ``LLMModelFactory.create_policy()`` for new code.  This method is
        retained for modules that still request a single provider directly.
        """
        ensure_env_loaded()
        name = (backend_name or get_env("DEFAULT_LLM_BACKEND", "vllm")).lower().strip()
        model_config = {
            "default_profile": "default",
            "providers": {
                name: {
                    "adapter": "openai_compatible",
                    "env_prefix": name.upper(),
                }
            },
            "profiles": {
                "default": {
                    "provider": name,
                    **override_kwargs,
                }
            },
            "module_profiles": {
                "default": "default",
            },
        }
        factory = LLMModelFactory(model_config)
        resolved = factory.resolve("default")
        kwargs = resolved.to_policy_kwargs()

        cache_key = None
        if use_cache:
            cache_key = json.dumps(
                {
                    "provider": name,
                    "base_url": resolved.provider.base_url,
                    "kwargs": {k: v for k, v in kwargs.items() if k != "api_key"},
                },
                sort_keys=True,
                default=str,
            )
            if cache_key in _BACKEND_CACHE:
                return _BACKEND_CACHE[cache_key]

        policy = VLLMPolicy(**kwargs)
        if cache_key is not None:
            _BACKEND_CACHE[cache_key] = policy
        return policy

    @staticmethod
    def get_all_backends(backend_names: list[str] | None = None) -> dict[str, VLLMPolicy]:
        """Return configured providers using the legacy method name."""
        ensure_env_loaded()
        if backend_names is None:
            backend_names = ["deepseek", "vllm", "openai", "mimo"]
            for key in os.environ:
                if key.endswith("_API_KEY"):
                    prefix = key[:-len("_API_KEY")].lower()
                    if prefix not in backend_names:
                        backend_names.append(prefix)

        backends: dict[str, VLLMPolicy] = {}
        for name in backend_names:
            if not ModelRouter._is_backend_configured(name):
                continue
            try:
                backends[name] = ModelRouter.create_backend(name)
            except ValueError:
                continue
        return backends

    @staticmethod
    def clear_cache() -> None:
        _BACKEND_CACHE.clear()

    @staticmethod
    def _is_backend_configured(name: str) -> bool:
        prefix = name.upper()
        return get_env(f"{prefix}_API_KEY") is not None or name.lower() == "vllm"
