#!/usr/bin/env python3
"""Create local gateway configuration without replacing an operator's settings."""

import json
import os
from pathlib import Path
import secrets


def init_local_config(root: Path) -> dict:
    config = Path(root).resolve() / "box" / ".env"
    result = {"path": str(config), "status": "existing"}
    if os.path.lexists(config):
        return result

    lines = config.with_name(".env.example").read_text(encoding="utf-8").splitlines(keepends=True)
    required = {
        "AUDITBOX_ADMIN_USERNAME", "AUDITBOX_SUPER_ADMIN_USERNAME",
        "AUDITBOX_ADMIN_PASSWORD", "AUDITBOX_SUPER_ADMIN_PASSWORD",
        "AUDITBOX_AUTH_SECRET", "AUDITBOX_HISTORY_REVIEW_ENABLED",
    }
    fields = {}
    for index, line in enumerate(lines):
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key in required:
            if key in fields:
                raise ValueError(f"Duplicate configuration template field: {key}")
            fields[key] = (index, value.strip())
    missing = required - fields.keys()
    if missing:
        raise ValueError("Missing configuration template fields: " + ", ".join(sorted(missing)))
    accounts = [fields[key][1] for key in ("AUDITBOX_ADMIN_USERNAME", "AUDITBOX_SUPER_ADMIN_USERNAME")]
    if not all(accounts):
        raise ValueError("Configuration template account names must not be empty")

    replacements = {
        "AUDITBOX_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "AUDITBOX_SUPER_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "AUDITBOX_AUTH_SECRET": secrets.token_urlsafe(48),
        "AUDITBOX_HISTORY_REVIEW_ENABLED": "false",
    }
    for key, value in replacements.items():
        lines[fields[key][0]] = f"{key}={value}\n"
    content = "".join(lines)

    try:
        descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return result
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
    except BaseException:
        config.unlink()
        raise
    return {**result, "status": "created", "accounts": accounts}


if __name__ == "__main__":
    print(json.dumps(init_local_config(Path(__file__).resolve().parents[1]), ensure_ascii=False))
