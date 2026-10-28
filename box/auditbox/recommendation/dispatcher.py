"""Device-aware dispatch for deterministic command recommendations."""

from __future__ import annotations

from collections.abc import Sequence
from auditbox import command_packages

from auditbox.device_profiles import get_device_profile, normalize_device_profile
from auditbox.recommendation.h3c_factory import (
    H3CSessionState,
    advance_h3c_session_state,
    complete_h3c_command,
    recommend_next_h3c_commands,
)
from auditbox.recommendation.linux_factory import (
    classify_linux_command,
    complete_linux_command,
    recommend_next_linux_commands,
)
from auditbox.recommendation.session import RecommendationSessionState
from auditbox.device_profiles import REFERENCE_PROFILES
from auditbox.recommendation.switch_factory import (
    SwitchSessionState, advance_switch_session_state, complete_switch_commands,
    recommend_next_switch_commands,
)


_GENERIC_PROTOCOL_NAMESPACES = {"console", "serial", "ssh", "telnet"}


def recommend_next_commands(
    command: str,
    *,
    device_profile: str = "",
    device_type: str = "",
    limit: int,
    history: Sequence[str] = (),
    session_state: RecommendationSessionState | None = None,
) -> list[dict]:
    """Route a command to the explicitly selected platform factory graph.

    ``device_type`` remains as a compatibility input for historical tests and
    protocol/vendor namespaces. New runtime callers must pass
    ``device_profile``.
    """
    if command_packages.registered(device_profile):
        return command_packages.recommend(command, device_profile, limit, history)
    namespace = _recommendation_namespace(device_profile, device_type)
    state_matches_profile = bool(
        session_state
        and normalize_device_profile(session_state.device_profile)
        == normalize_device_profile(device_profile)
    )
    current_parameters = (
        dict(session_state.current_intent.parameters)
        if state_matches_profile and session_state.current_intent
        else None
    )
    if namespace == "h3c":
        platform_state = (
            session_state.platform_state
            if state_matches_profile
            and isinstance(session_state.platform_state, H3CSessionState)
            else None
        )
        return recommend_next_h3c_commands(
            command,
            limit=limit,
            history=history,
            state=platform_state,
            context_parameters=current_parameters,
        )
    if namespace == "linux":
        return recommend_next_linux_commands(
            command,
            limit=limit,
            context_parameters=current_parameters,
        )
    profile_key = normalize_device_profile(device_profile)
    if profile_key in REFERENCE_PROFILES:
        platform_state = session_state.platform_state if state_matches_profile else None
        return recommend_next_switch_commands(
            command, profile_key, limit=limit, history=history,
            state=platform_state if isinstance(platform_state, SwitchSessionState) else None,
        )
    return []


def create_recommendation_session(
    device_profile: str,
) -> RecommendationSessionState:
    """Create an isolated runtime context for one selected device profile."""
    normalized_profile = normalize_device_profile(device_profile)
    state = RecommendationSessionState(device_profile=normalized_profile)
    if _recommendation_namespace(normalized_profile, "") == "h3c":
        state.platform_state = H3CSessionState()
    elif normalized_profile in REFERENCE_PROFILES:
        state.platform_state = SwitchSessionState(normalized_profile)
    return state


def advance_recommendation_session(
    state: RecommendationSessionState,
    command: str,
    *,
    status: str = "executed",
) -> None:
    """Apply a submitted command to only its selected platform adapter."""
    if status != "executed":
        return

    namespace = _recommendation_namespace(state.device_profile, "")
    context = None
    if namespace == "h3c":
        if not isinstance(state.platform_state, H3CSessionState):
            state.platform_state = H3CSessionState()
        context = advance_h3c_session_state(state.platform_state, command)
    elif namespace == "linux":
        context = classify_linux_command(command)
    elif state.device_profile in REFERENCE_PROFILES:
        if not isinstance(state.platform_state, SwitchSessionState):
            state.platform_state = SwitchSessionState(state.device_profile)
        context = advance_switch_session_state(state.platform_state, command)

    if context is None:
        state.clear_current_intent()
        return
    state.record_intent(context.action, context.parameters, command)


def complete_commands(prefix: str, *, device_profile: str, limit: int = 8) -> list[dict]:
    """Complete commands inside one profile namespace without cross-fallback."""
    if command_packages.registered(device_profile):
        return command_packages.complete(prefix, device_profile, limit)
    namespace = _recommendation_namespace(device_profile, "")
    if namespace == "h3c":
        return complete_h3c_command(prefix=prefix, limit=limit)
    if namespace == "linux":
        return complete_linux_command(prefix=prefix, limit=limit)
    profile_key = normalize_device_profile(device_profile)
    if profile_key in REFERENCE_PROFILES:
        return complete_switch_commands(prefix, profile_key, limit)
    return []


def _recommendation_namespace(device_profile: str, legacy_namespace: str) -> str:
    profile = get_device_profile(normalize_device_profile(device_profile))
    if profile:
        return profile.recommendation_namespace

    namespace = str(legacy_namespace or "").strip().lower()
    if namespace == "h3c" or namespace.endswith("_h3c"):
        return "h3c"
    if (
        namespace == "linux"
        or namespace.endswith("_linux")
        or namespace in _GENERIC_PROTOCOL_NAMESPACES
    ):
        return "linux"
    return ""


__all__ = [
    "advance_recommendation_session",
    "complete_commands",
    "create_recommendation_session",
    "recommend_next_commands",
]
