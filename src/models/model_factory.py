"""Provider/profile based LLM policy factory.

This module is the M6 replacement for the old "backend" glue code.  The core
distinction is:

- provider: API service adapter and credentials, e.g. DeepSeek/OpenAI/vLLM.
- profile: model name plus sampling/runtime parameters for one role.
- module mapping: which profile a project module uses.

The factory keeps backward compatibility with the legacy config keys
``backend``, ``backend_sampling`` and ``backend_mapping`` so existing demos keep
running while newer configs can use clearer names.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..utils.env_config import ensure_env_loaded, get_env
from .vllm_policy import VLLMPolicy


__all__ = [
    "LLMProviderConfig",
    "LLMProfileConfig",
    "ResolvedModelConfig",
    "LLMModelFactory",
]


KNOWN_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "vllm": {
        "env_prefix": "VLLM",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "default_base_url": "http://localhost:8000/v1",
        "default_api_key": "EMPTY",
    },
    "deepseek": {
        "env_prefix": "DEEPSEEK",
        "default_model": "deepseek-chat",
        "default_base_url": "https://api.deepseek.com/v1",
    },
    "openai": {
        "env_prefix": "OPENAI",
        "default_model": "gpt-4o",
        "default_base_url": "https://api.openai.com/v1",
    },
    "mimo": {
        "env_prefix": "MIMO",
        "default_model": "mimo-v2.5-pro",
        "default_base_url": "https://api.xiaomimimo.com/v1",
    },
}

POLICY_KWARG_KEYS = {
    "model_name",
    "temperature",
    "top_p",
    "max_tokens",
    "tools",
}


@dataclass(frozen=True)
class LLMProviderConfig:
    """API provider configuration without per-task sampling parameters."""

    name: str
    adapter: str = "openai_compatible"
    env_prefix: str = ""
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    allow_missing_api_key: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMProfileConfig:
    """Model and sampling config for one logical role."""

    name: str
    provider: str
    model_name: str = ""
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 1024
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedModelConfig:
    """Fully resolved config needed to instantiate a policy."""

    module_name: str
    profile_name: str
    provider: LLMProviderConfig
    profile: LLMProfileConfig

    def to_policy_kwargs(self) -> dict[str, Any]:
        if self.provider.adapter != "openai_compatible":
            raise ValueError(
                f"Unsupported provider adapter '{self.provider.adapter}' for provider '{self.provider.name}'."
            )
        if not self.provider.api_key and not self.provider.allow_missing_api_key:
            raise ValueError(
                f"Provider '{self.provider.name}' is not configured. "
                f"Set {self.provider.env_prefix}_API_KEY or enable allow_missing_api_key for local providers."
            )
        if not self.provider.base_url:
            raise ValueError(
                f"Provider '{self.provider.name}' has no base_url. "
                f"Set {self.provider.env_prefix}_BASE_URL or default_base_url."
            )

        kwargs: dict[str, Any] = {
            "model_name": self.profile.model_name or self.provider.default_model,
            "base_url": self.provider.base_url,
            "api_key": self.provider.api_key,
            "temperature": self.profile.temperature,
            "top_p": self.profile.top_p,
            "max_tokens": self.profile.max_tokens,
        }
        for key, value in self.profile.extra.items():
            if key in POLICY_KWARG_KEYS:
                kwargs[key] = value
        return kwargs


class LLMModelFactory:
    """Create policies from provider/profile/module config."""

    def __init__(self, model_config: dict[str, Any] | None = None) -> None:
        ensure_env_loaded()
        self.model_config = model_config or {}
        self.providers = self._load_providers()
        self.profiles = self._load_profiles()
        self.module_profiles = self._load_module_profiles()
        self.default_profile = str(
            self.model_config.get("default_profile")
            or self.model_config.get("default_module")
            or self.model_config.get("backend")
            or "default"
        )
        if self.default_profile not in self.profiles:
            self.default_profile = "default"
        self._cache: dict[str, VLLMPolicy] = {}

    def create_policy(
        self,
        module_name: str = "default",
        use_cache: bool = True,
        **overrides: Any,
    ) -> VLLMPolicy:
        resolved = self.resolve(module_name, overrides=overrides)
        kwargs = resolved.to_policy_kwargs()

        cache_key = None
        if use_cache:
            cache_key = self._cache_key(resolved, kwargs)
            if cache_key in self._cache:
                return self._cache[cache_key]

        policy = VLLMPolicy(**kwargs)
        if cache_key is not None:
            self._cache[cache_key] = policy
        return policy

    def resolve(self, module_name: str = "default", overrides: dict[str, Any] | None = None) -> ResolvedModelConfig:
        profile_name = self.module_profiles.get(module_name) or self.default_profile
        profile = self.profiles.get(profile_name) or self.profiles["default"]
        if overrides:
            profile = self._profile_with_overrides(profile, overrides)
        provider = self.providers.get(profile.provider)
        if provider is None:
            provider = self._provider_from_name(profile.provider)
        return ResolvedModelConfig(
            module_name=module_name,
            profile_name=profile.name,
            provider=provider,
            profile=profile,
        )

    def describe_module(self, module_name: str = "default") -> dict[str, Any]:
        resolved = self.resolve(module_name)
        return {
            "module": module_name,
            "profile": resolved.profile_name,
            "provider": resolved.provider.name,
            "adapter": resolved.provider.adapter,
            "model": resolved.profile.model_name or resolved.provider.default_model,
            "temperature": resolved.profile.temperature,
            "top_p": resolved.profile.top_p,
            "max_tokens": resolved.profile.max_tokens,
        }

    def clear_cache(self) -> None:
        self._cache.clear()

    def _load_providers(self) -> dict[str, LLMProviderConfig]:
        raw_providers = self.model_config.get("providers")
        if isinstance(raw_providers, dict) and raw_providers:
            return {
                str(name).lower(): self._provider_from_mapping(str(name).lower(), cfg or {})
                for name, cfg in raw_providers.items()
            }

        names = {str(self.model_config.get("backend", "vllm")).lower()}
        backend_mapping = self.model_config.get("backend_mapping", {})
        if isinstance(backend_mapping, dict):
            names.update(str(name).lower() for name in backend_mapping.values())
        return {name: self._provider_from_name(name) for name in names}

    def _load_profiles(self) -> dict[str, LLMProfileConfig]:
        raw_profiles = self.model_config.get("profiles")
        if isinstance(raw_profiles, dict) and raw_profiles:
            profiles = {
                str(name): self._profile_from_mapping(str(name), cfg or {})
                for name, cfg in raw_profiles.items()
            }
            if "default" not in profiles:
                first = next(iter(profiles.values()))
                profiles["default"] = LLMProfileConfig(
                    name="default",
                    provider=first.provider,
                    model_name=first.model_name,
                    temperature=first.temperature,
                    top_p=first.top_p,
                    max_tokens=first.max_tokens,
                    extra=first.extra,
                )
            return profiles

        return self._legacy_profiles()

    def _load_module_profiles(self) -> dict[str, str]:
        raw_mapping = self.model_config.get("module_profiles")
        if isinstance(raw_mapping, dict) and raw_mapping:
            return {str(module): str(profile) for module, profile in raw_mapping.items()}

        legacy_mapping = self.model_config.get("backend_mapping", {})
        if isinstance(legacy_mapping, dict):
            return {str(module): str(module) for module in legacy_mapping}
        return {}

    def _legacy_profiles(self) -> dict[str, LLMProfileConfig]:
        default_provider = str(self.model_config.get("backend", "vllm")).lower()
        backend_mapping = self.model_config.get("backend_mapping", {})
        backend_sampling = self.model_config.get("backend_sampling", {})
        if not isinstance(backend_mapping, dict):
            backend_mapping = {}
        if not isinstance(backend_sampling, dict):
            backend_sampling = {}

        default_sampling = self._sampling_for(default_provider, "default", backend_sampling)
        profiles = {
            "default": self._profile_from_legacy("default", default_provider, default_sampling),
        }
        for module_name, provider_name in backend_mapping.items():
            provider = str(provider_name).lower()
            sampling = self._sampling_for(provider, str(module_name), backend_sampling)
            profiles[str(module_name)] = self._profile_from_legacy(str(module_name), provider, sampling)
        return profiles

    def _sampling_for(self, provider: str, module_name: str, backend_sampling: dict[str, Any]) -> dict[str, Any]:
        sampling: dict[str, Any] = {}
        provider_sampling = backend_sampling.get(provider, {})
        if isinstance(provider_sampling, dict):
            sampling.update(provider_sampling)
        module_sampling = backend_sampling.get("modules", {}).get(module_name, {})
        if isinstance(module_sampling, dict):
            sampling.update(module_sampling)
        for key in ("temperature", "top_p", "max_tokens"):
            if key in self.model_config and key not in sampling:
                sampling[key] = self.model_config[key]
        return sampling

    def _provider_from_mapping(self, name: str, cfg: dict[str, Any]) -> LLMProviderConfig:
        defaults = KNOWN_PROVIDER_DEFAULTS.get(name, {})
        env_prefix = str(cfg.get("env_prefix") or defaults.get("env_prefix") or name.upper())
        base_url = (
            get_env(f"{env_prefix}_BASE_URL")
            or cfg.get("base_url")
            or cfg.get("default_base_url")
            or defaults.get("default_base_url")
            or ""
        )
        api_key = get_env(f"{env_prefix}_API_KEY") or cfg.get("api_key") or defaults.get("default_api_key") or ""
        default_model = (
            get_env(f"{env_prefix}_MODEL")
            or cfg.get("model")
            or cfg.get("model_name")
            or cfg.get("default_model")
            or defaults.get("default_model")
            or ""
        )
        known_keys = {
            "adapter",
            "env_prefix",
            "base_url",
            "api_key",
            "allow_missing_api_key",
            "model",
            "model_name",
            "default_model",
            "default_base_url",
        }
        extra = {k: v for k, v in cfg.items() if k not in known_keys}
        return LLMProviderConfig(
            name=name,
            adapter=str(cfg.get("adapter") or "openai_compatible"),
            env_prefix=env_prefix,
            base_url=str(base_url),
            api_key=str(api_key),
            default_model=str(default_model),
            allow_missing_api_key=self._as_bool(
                cfg.get("allow_missing_api_key"),
                default=bool(defaults.get("default_api_key")),
            ),
            extra=extra,
        )

    def _provider_from_name(self, name: str) -> LLMProviderConfig:
        return self._provider_from_mapping(name, {})

    def _profile_from_mapping(self, name: str, cfg: dict[str, Any]) -> LLMProfileConfig:
        provider = str(cfg.get("provider") or self._default_provider_name()).lower()
        provider_cfg = self.providers.get(provider) or self._provider_from_name(provider)
        known_keys = {"provider", "model", "model_name", "temperature", "top_p", "max_tokens"}
        return LLMProfileConfig(
            name=name,
            provider=provider,
            model_name=str(cfg.get("model") or cfg.get("model_name") or provider_cfg.default_model),
            temperature=float(cfg.get("temperature", self.model_config.get("temperature", 0.0))),
            top_p=float(cfg.get("top_p", self.model_config.get("top_p", 1.0))),
            max_tokens=int(cfg.get("max_tokens", self.model_config.get("max_tokens", 1024))),
            extra={k: v for k, v in cfg.items() if k not in known_keys},
        )

    def _profile_from_legacy(self, name: str, provider: str, sampling: dict[str, Any]) -> LLMProfileConfig:
        provider_cfg = self.providers.get(provider) or self._provider_from_name(provider)
        model_name = str(
            sampling.get("model")
            or sampling.get("model_name")
            or provider_cfg.default_model
        )
        extra = {
            key: value
            for key, value in sampling.items()
            if key not in {"model", "model_name", "temperature", "top_p", "max_tokens"}
        }
        return LLMProfileConfig(
            name=name,
            provider=provider,
            model_name=model_name,
            temperature=float(sampling.get("temperature", self.model_config.get("temperature", 0.0))),
            top_p=float(sampling.get("top_p", self.model_config.get("top_p", 1.0))),
            max_tokens=int(sampling.get("max_tokens", self.model_config.get("max_tokens", 1024))),
            extra=extra,
        )

    def _profile_with_overrides(self, profile: LLMProfileConfig, overrides: dict[str, Any]) -> LLMProfileConfig:
        merged = {
            "model_name": profile.model_name,
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": profile.max_tokens,
            **profile.extra,
            **overrides,
        }
        return LLMProfileConfig(
            name=profile.name,
            provider=str(overrides.get("provider", profile.provider)).lower(),
            model_name=str(merged.get("model") or merged.get("model_name") or profile.model_name),
            temperature=float(merged.get("temperature", profile.temperature)),
            top_p=float(merged.get("top_p", profile.top_p)),
            max_tokens=int(merged.get("max_tokens", profile.max_tokens)),
            extra={k: v for k, v in merged.items() if k not in {"model", "model_name", "temperature", "top_p", "max_tokens"}},
        )

    def _cache_key(self, resolved: ResolvedModelConfig, kwargs: dict[str, Any]) -> str:
        payload = {
            "module": resolved.module_name,
            "profile": resolved.profile_name,
            "provider": resolved.provider.name,
            "base_url": resolved.provider.base_url,
            "kwargs": {k: v for k, v in kwargs.items() if k != "api_key"},
        }
        return json.dumps(payload, sort_keys=True, default=str)

    def _default_provider_name(self) -> str:
        if self.model_config.get("backend"):
            return str(self.model_config["backend"])
        if self.providers:
            return next(iter(self.providers))
        return "vllm"

    def _as_bool(self, value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
