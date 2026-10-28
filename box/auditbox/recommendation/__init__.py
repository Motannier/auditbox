"""Deterministic, factory-provided command assistance."""

from auditbox.recommendation.dispatcher import (
    advance_recommendation_session,
    complete_commands,
    create_recommendation_session,
    recommend_next_commands,
)
from auditbox.recommendation.h3c_factory import (
    BEHAVIOR_LABELS as H3C_BEHAVIOR_LABELS,
    COMMAND_COUNT as H3C_COMMAND_COUNT,
    H3C_BEHAVIOR_GRAPH,
    H3C_COMMAND_NODES,
    H3C_RECOMMENDATION_NODES,
    H3C_RECOMMENDATION_SYNTAX_ROOTS,
    H3CSessionState,
    advance_h3c_session_state,
    catalog_command_titles as h3c_catalog_command_titles,
    classify_h3c_command,
    complete_h3c_command,
    recommend_next_h3c_commands,
)
from auditbox.recommendation.linux_factory import (
    BEHAVIOR_LABELS as LINUX_BEHAVIOR_LABELS,
    LINUX_BEHAVIOR_GRAPH,
    LINUX_COMMAND_NODES,
    LINUX_RECOMMENDATION_NODES,
    MAX_RECOMMENDATIONS,
    complete_linux_command,
    recommend_next_linux_commands,
)

__all__ = [
    "H3C_BEHAVIOR_GRAPH",
    "H3C_BEHAVIOR_LABELS",
    "H3C_COMMAND_COUNT",
    "H3C_COMMAND_NODES",
    "H3C_RECOMMENDATION_NODES",
    "H3C_RECOMMENDATION_SYNTAX_ROOTS",
    "H3CSessionState",
    "LINUX_BEHAVIOR_GRAPH",
    "LINUX_BEHAVIOR_LABELS",
    "LINUX_COMMAND_NODES",
    "LINUX_RECOMMENDATION_NODES",
    "MAX_RECOMMENDATIONS",
    "advance_h3c_session_state",
    "advance_recommendation_session",
    "complete_commands",
    "complete_h3c_command",
    "complete_linux_command",
    "create_recommendation_session",
    "classify_h3c_command",
    "h3c_catalog_command_titles",
    "recommend_next_commands",
    "recommend_next_h3c_commands",
    "recommend_next_linux_commands",
]
