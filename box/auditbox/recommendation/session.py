"""Per-terminal recommendation context shared by device adapters.

The behavior graphs remain immutable data.  This module only records runtime
intent and keeps device-specific state opaque so another device profile can
swap in its own graph and adapter without inheriting Comware semantics.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field


MAX_ACTIVE_TASKS = 8


@dataclass
class TaskState:
    """Most recent parameters for one independently resumable behavior."""

    behavior: str
    parameters: dict[str, str]
    last_command: str
    sequence: int


@dataclass
class RecommendationSessionState:
    """Generic session envelope with an opaque platform adapter state."""

    device_profile: str
    platform_state: object | None = None
    sequence: int = 0
    current_intent: TaskState | None = None
    active_tasks: list[TaskState] = field(default_factory=list)

    def record_intent(
        self,
        behavior: str,
        parameters: dict[str, str],
        command: str,
    ) -> None:
        """Promote a behavior with parameters resolved for this command."""
        if not behavior:
            self.current_intent = None
            return

        self.sequence += 1
        task = TaskState(
            behavior=behavior,
            parameters=dict(parameters),
            last_command=command,
            sequence=self.sequence,
        )
        self.active_tasks = [
            item for item in self.active_tasks if item.behavior != behavior
        ]
        self.active_tasks.insert(0, task)
        del self.active_tasks[MAX_ACTIVE_TASKS:]
        self.current_intent = task

    def clear_current_intent(self) -> None:
        self.current_intent = None

    def snapshot(self) -> "RecommendationSessionState":
        """Return an isolated copy safe to rank outside the engine lock."""
        return deepcopy(self)


__all__ = [
    "MAX_ACTIVE_TASKS",
    "RecommendationSessionState",
    "TaskState",
]
