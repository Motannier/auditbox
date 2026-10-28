"""Environment configuration loaded from .env."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BOX_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path.cwd() / ".env"


def _env_files() -> list[Path]:
    candidates = [Path.cwd() / ".env"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    candidates.append(BOX_ROOT / ".env")

    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_candidates.append(resolved)
    return unique_candidates


for _env_file in _env_files():
    try:
        exists = _env_file.exists()
    except OSError:
        # A service may be launched with a working directory that its
        # unprivileged account cannot inspect (for example /root on Linux).
        # Ignore that candidate and continue to the application-local .env.
        continue
    if exists:
        ENV_FILE = _env_file
        load_dotenv(_env_file, override=False)


def get_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def get_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def get_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return float(value)


def get_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def get_csv(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or default


AUDITBOX_PORT = get_int("AUDITBOX_PORT", 19000)
AUDITBOX_PREDICT_PORT = get_int("AUDITBOX_PREDICT_PORT", 19001)
AUDITBOX_ENABLE_MARKOV_RECOMMENDATIONS = get_bool(
    "AUDITBOX_ENABLE_MARKOV_RECOMMENDATIONS", False
)
AUDITBOX_AUDIT_DIR = get_str("AUDITBOX_AUDIT_DIR", "/tmp/auditbox/logs")

AUDITBOX_DB_DIR = Path(get_str("AUDITBOX_DB_DIR", "/tmp/auditbox/db")).expanduser()
AUDITBOX_DB_PATH = Path(
    get_str("AUDITBOX_DB_PATH", str(AUDITBOX_DB_DIR / "auditbox.sqlite3"))
).expanduser()

AUDITBOX_PROXY_HOST = get_str("AUDITBOX_PROXY_HOST", "127.0.0.1")
AUDITBOX_PROXY_PORT = get_int("AUDITBOX_PROXY_PORT", AUDITBOX_PORT)

AUDITBOX_GATEWAY_HOST = get_str("AUDITBOX_GATEWAY_HOST", "127.0.0.1")
AUDITBOX_GATEWAY_HOSTS = get_csv(
    "AUDITBOX_GATEWAY_HOSTS", [AUDITBOX_GATEWAY_HOST]
)
AUDITBOX_GATEWAY_PORT = get_int("AUDITBOX_GATEWAY_PORT", 18080)
AUDITBOX_GATEWAY_LOG_LEVEL = get_str("AUDITBOX_GATEWAY_LOG_LEVEL", "info")
AUDITBOX_GATEWAY_CORS_ORIGINS = get_csv(
    "AUDITBOX_GATEWAY_CORS_ORIGINS", ["http://127.0.0.1:18080", "http://127.0.0.1:5173"]
)
AUDITBOX_PUBLIC_URL = get_str("AUDITBOX_PUBLIC_URL", "http://127.0.0.1:18080")
AUDITBOX_WEB_DIST_DIR = Path(
    get_str("AUDITBOX_WEB_DIST_DIR", str(Path.cwd() / "dist"))
).expanduser()
# Shared servers must not let a browser change the host clock or network by default.
AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED = get_bool("AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED", False)
AUDITBOX_NETWORK_SETTINGS_ENABLED = get_bool("AUDITBOX_NETWORK_SETTINGS_ENABLED", False)

AUDITBOX_ADMIN_USERNAME = get_str("AUDITBOX_ADMIN_USERNAME", "opsadmin")
# Missing passwords disable the corresponding account; never ship board credentials.
AUDITBOX_ADMIN_PASSWORD = get_str("AUDITBOX_ADMIN_PASSWORD", "")
AUDITBOX_SUPER_ADMIN_USERNAME = get_str(
    "AUDITBOX_SUPER_ADMIN_USERNAME", "superadmin"
)
AUDITBOX_SUPER_ADMIN_PASSWORD = get_str(
    "AUDITBOX_SUPER_ADMIN_PASSWORD", ""
)
AUDITBOX_AUTH_SECRET = get_str("AUDITBOX_AUTH_SECRET", "")
AUDITBOX_AUTH_TOKEN_TTL = get_int("AUDITBOX_AUTH_TOKEN_TTL", 12 * 60 * 60)

AUDITBOX_GUARDIAN_REQUIRED = get_bool("AUDITBOX_GUARDIAN_REQUIRED", False)
AUDITBOX_GUARDIAN_MANAGEMENT_TRANSPORT = get_str(
    "AUDITBOX_GUARDIAN_MANAGEMENT_TRANSPORT", "usb_otg"
)
AUDITBOX_GUARDIAN_MANAGEMENT_NETWORK = get_str(
    "AUDITBOX_GUARDIAN_MANAGEMENT_NETWORK", "192.168.77.0/24"
)
AUDITBOX_GUARDIAN_MANAGEMENT_GATEWAY = get_str(
    "AUDITBOX_GUARDIAN_MANAGEMENT_GATEWAY", "192.168.77.1"
)
AUDITBOX_GUARDIAN_USB_VENDOR_ID = get_str("AUDITBOX_GUARDIAN_USB_VENDOR_ID", "")
AUDITBOX_GUARDIAN_USB_PRODUCT_ID = get_str("AUDITBOX_GUARDIAN_USB_PRODUCT_ID", "")
AUDITBOX_GUARDIAN_USB_ADAPTER_KEYWORDS = get_csv(
    "AUDITBOX_GUARDIAN_USB_ADAPTER_KEYWORDS",
    ["auditbox", "usb ncm", "usbncm", "remote ndis", "rndis"],
)
AUDITBOX_GUARDIAN_LOCAL_URL = get_str(
    "AUDITBOX_GUARDIAN_LOCAL_URL", "http://127.0.0.1:17890"
).rstrip("/")
AUDITBOX_GUARDIAN_DOWNLOAD_PATH = Path(
    get_str(
        "AUDITBOX_GUARDIAN_DOWNLOAD_PATH",
        "/opt/auditbox/downloads/AuditBoxGuardian-Setup.exe",
    )
).expanduser()
AUDITBOX_GUARDIAN_MANIFEST_PATH = Path(
    get_str(
        "AUDITBOX_GUARDIAN_MANIFEST_PATH",
        "/opt/auditbox/downloads/AuditBoxGuardian-Setup.json",
    )
).expanduser()
AUDITBOX_GUARDIAN_MIN_VERSION = get_str(
    "AUDITBOX_GUARDIAN_MIN_VERSION", "0.2.1"
)
AUDITBOX_GUARDIAN_CHALLENGE_TTL = get_int("AUDITBOX_GUARDIAN_CHALLENGE_TTL", 120)
AUDITBOX_GUARDIAN_ADMISSION_TTL = get_int(
    "AUDITBOX_GUARDIAN_ADMISSION_TTL",
    AUDITBOX_AUTH_TOKEN_TTL,
)
AUDITBOX_GUARDIAN_SECRET = get_str("AUDITBOX_GUARDIAN_SECRET", AUDITBOX_AUTH_SECRET)

AUDITBOX_DEFAULT_SSH_USERNAME = get_str("AUDITBOX_DEFAULT_SSH_USERNAME", "admin")
AUDITBOX_CONNECT_TIMEOUT = get_float("AUDITBOX_CONNECT_TIMEOUT", 10.0)
AUDITBOX_CONNECTION_WS_TTL = get_float("AUDITBOX_CONNECTION_WS_TTL", 120.0)
AUDITBOX_TARGET_PREPARE_SOCKET = get_str("AUDITBOX_TARGET_PREPARE_SOCKET", "")
AUDITBOX_TARGET_INTERFACE = get_str("AUDITBOX_TARGET_INTERFACE", "eth0")
AUDITBOX_MANAGEMENT_NETWORK = get_str(
    "AUDITBOX_MANAGEMENT_NETWORK", "192.168.77.0/24"
)
AUDITBOX_MANAGEMENT_NETWORKS = get_csv(
    "AUDITBOX_MANAGEMENT_NETWORKS",
    [AUDITBOX_MANAGEMENT_NETWORK],
)

AUDITBOX_INFERENCE_HOST = get_str("AUDITBOX_INFERENCE_HOST", "127.0.0.1")
AUDITBOX_INFERENCE_PORT = get_int("AUDITBOX_INFERENCE_PORT", 18091)
AUDITBOX_INFERENCE_PROFILES = get_csv("AUDITBOX_INFERENCE_PROFILES", ["linux"])
AUDITBOX_ASSISTANT_BACKEND_URL = get_str(
    "AUDITBOX_ASSISTANT_BACKEND_URL", "http://127.0.0.1:18081"
).rstrip("/")
AUDITBOX_REVIEW_BACKEND_URL = get_str(
    "AUDITBOX_REVIEW_BACKEND_URL", "http://127.0.0.1:18082"
).rstrip("/")
AUDITBOX_H3C_BACKEND_URL = get_str(
    "AUDITBOX_H3C_BACKEND_URL", "http://127.0.0.1:8083"
).rstrip("/")
AUDITBOX_LLM_URL = get_str(
    "AUDITBOX_LLM_URL", "http://127.0.0.1:18091/assistant"
).rstrip("/")
AUDITBOX_HISTORY_REVIEW_LLM_URL = get_str(
    "AUDITBOX_HISTORY_REVIEW_LLM_URL", "http://127.0.0.1:18091/review"
).rstrip("/")
AUDITBOX_LLM_MODEL = get_str("AUDITBOX_LLM_MODEL", "command-guard")
AUDITBOX_ASSISTANT_LLM_MODEL = get_str(
    "AUDITBOX_ASSISTANT_LLM_MODEL", "minicpm5-2b"
)
AUDITBOX_LINUX_REVIEW_LLM_MODEL = get_str(
    "AUDITBOX_LINUX_REVIEW_LLM_MODEL", AUDITBOX_LLM_MODEL
)
AUDITBOX_H3C_LLM_MODEL = get_str("AUDITBOX_H3C_LLM_MODEL", "h3c-risk")
AUDITBOX_LLM_TIMEOUT = get_float("AUDITBOX_LLM_TIMEOUT", 180.0)
AUDITBOX_LLM_MAX_TOKENS = get_int("AUDITBOX_LLM_MAX_TOKENS", 512)
AUDITBOX_AGENT_URL = get_str(
    "AUDITBOX_AGENT_URL", "http://127.0.0.1:18765"
).rstrip("/")
AUDITBOX_AGENT_TIMEOUT = get_float("AUDITBOX_AGENT_TIMEOUT", 180.0)
AUDITBOX_HISTORY_REVIEW_ENABLED = get_bool("AUDITBOX_HISTORY_REVIEW_ENABLED", True)
AUDITBOX_HISTORY_REVIEW_INTERVAL = get_float("AUDITBOX_HISTORY_REVIEW_INTERVAL", 2.0)
AUDITBOX_HISTORY_REVIEW_WINDOW = get_int("AUDITBOX_HISTORY_REVIEW_WINDOW", 20)
AUDITBOX_HISTORY_REVIEW_TIMEOUT = get_float("AUDITBOX_HISTORY_REVIEW_TIMEOUT", 240.0)
