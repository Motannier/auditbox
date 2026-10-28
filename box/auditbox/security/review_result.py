"""Risk reviews are advisory, including results from older model versions."""


def current_command_review(result: dict) -> dict:
    """A window's severity is not an individual command's severity."""
    history = result.get("history") or []
    sequence = history[-1].get("seq") if history else None
    current = result.get("current")
    if result.get("status") in {"ready", "locked"}:
        if isinstance(current, dict) and current.get("seq") == sequence and sequence is not None:
            return current
        if len(history) == 1 and result.get("reviewed_through_seq") == sequence:
            return {"seq": sequence, "risk": result.get("risk", "unknown"), "reason": result.get("reason", "")}
    return {"seq": sequence, "risk": "unknown", "reason": "当前命令风险未判定"}


def stale_review_update(result: dict, previous: dict) -> bool:
    revision, prior = result.get("review_revision", 0), previous.get("review_revision", 0)
    return revision < prior or (revision == prior and revision > 0 and result.get("status") == "queued"
                                and previous.get("status") in {"ready", "error"})


def advisory_review(result: dict) -> dict:
    """Keep risk evidence, but never turn a review into terminal access control."""
    return {
        **result,
        "status": "ready" if result.get("status") == "locked" else result.get("status", "idle"),
        # Retain false flags for older clients; these are no longer capabilities.
        "lock": False,
        "locked": False,
        "lock_policy": {
            "allow_lock": False,
            "code": "advisory_only",
            "reason": "仅展示风险和记录审计，不锁定盒子或终端",
            "signals": [],
        },
    }
