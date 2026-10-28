"""Canonical device-profile routing for all local model requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from auditbox.device_profiles import H3C_COMWARE, LINUX, normalize_device_profile
from auditbox.env import (
    AUDITBOX_ASSISTANT_BACKEND_URL,
    AUDITBOX_ASSISTANT_LLM_MODEL,
    AUDITBOX_H3C_BACKEND_URL,
    AUDITBOX_H3C_LLM_MODEL,
    AUDITBOX_INFERENCE_PROFILES,
    AUDITBOX_LINUX_REVIEW_LLM_MODEL,
    AUDITBOX_REVIEW_BACKEND_URL,
)

DEVICE_PROFILE_HEADER = "X-AuditBox-Device-Profile"
InferenceKind = Literal["assistant", "review"]


@dataclass(frozen=True)
class InferenceModelRoute:
    device_profile: str
    assistant_model: str
    review_model: str
    assistant_backend_url: str
    review_backend_url: str

    def backend_url(self, kind: InferenceKind) -> str:
        if kind == "assistant":
            return self.assistant_backend_url
        if kind == "review":
            return self.review_backend_url
        raise ValueError(f"unsupported inference kind: {kind}")

    def model_for(self, kind: InferenceKind) -> str:
        if kind == "assistant":
            return self.assistant_model
        if kind == "review":
            return self.review_model
        raise ValueError(f"unsupported inference kind: {kind}")


_ROUTES = {
    LINUX: InferenceModelRoute(
        device_profile=LINUX,
        assistant_model=AUDITBOX_ASSISTANT_LLM_MODEL,
        review_model=AUDITBOX_LINUX_REVIEW_LLM_MODEL,
        assistant_backend_url=AUDITBOX_ASSISTANT_BACKEND_URL,
        review_backend_url=AUDITBOX_REVIEW_BACKEND_URL,
    ),
    H3C_COMWARE: InferenceModelRoute(
        device_profile=H3C_COMWARE,
        assistant_model=AUDITBOX_ASSISTANT_LLM_MODEL,
        review_model=AUDITBOX_H3C_LLM_MODEL,
        assistant_backend_url=AUDITBOX_ASSISTANT_BACKEND_URL,
        review_backend_url=AUDITBOX_H3C_BACKEND_URL,
    ),
}


def enabled_inference_profiles() -> tuple[str, ...]:
    profiles = tuple(dict.fromkeys(normalize_device_profile(p) for p in AUDITBOX_INFERENCE_PROFILES))
    if not profiles or any(p not in _ROUTES for p in profiles):
        raise ValueError("AUDITBOX_INFERENCE_PROFILES contains an unsupported model profile")
    return profiles


def resolve_inference_route(device_profile: str = LINUX) -> InferenceModelRoute:
    """Return the configured model route for one canonical device profile."""
    normalized = normalize_device_profile(device_profile) or LINUX
    route = _ROUTES.get(normalized)
    if route is None:
        raise ValueError(f"unsupported inference device profile: {device_profile}")
    if normalized not in enabled_inference_profiles():
        raise ValueError(f"inference device profile is disabled: {normalized}")
    return route


__all__ = [
    "DEVICE_PROFILE_HEADER",
    "InferenceKind",
    "InferenceModelRoute",
    "enabled_inference_profiles",
    "resolve_inference_route",
]
