"""轻量化调试推理工具浏览器客户端使用的 HTTP/WebSocket 网关。

浏览器通过 HTTP/WebSocket 连接本服务，输入通过协商后的带来源帧传给代理；
代理解帧后仍向目标转发原始终端字节。目标输出保持透明字节流。
"""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import ipaddress
import json
import logging
import re
import secrets
import socket
import time
from collections import deque
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import paramiko
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from starlette.websockets import WebSocketState

from auditbox.audit.logger import AUDIT_DIR
from auditbox.audit.store import AuditStore, TABLES, redact, safe_audit
from auditbox.branding import product_name
from auditbox.console import list_console_ports, validate_console_path
from auditbox.db import DB_PATH, init_db
from auditbox.db.models import RecentConnection, Target
from auditbox.db.repositories import RecentConnectionRepository, TargetRepository
from auditbox.device_profiles import DeviceProfileKey, require_device_profile
from auditbox.security.review_result import advisory_review, current_command_review, stale_review_update
from auditbox.env import (
    AUDITBOX_AGENT_TIMEOUT,
    AUDITBOX_AGENT_URL,
    AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED,
    AUDITBOX_CONNECT_TIMEOUT,
    AUDITBOX_CONNECTION_WS_TTL,
    AUDITBOX_DEFAULT_SSH_USERNAME,
    AUDITBOX_GATEWAY_CORS_ORIGINS,
    AUDITBOX_GATEWAY_HOST,
    AUDITBOX_GUARDIAN_ADMISSION_TTL,
    AUDITBOX_GUARDIAN_DOWNLOAD_PATH,
    AUDITBOX_GUARDIAN_LOCAL_URL,
    AUDITBOX_GUARDIAN_MANIFEST_PATH,
    AUDITBOX_GUARDIAN_MIN_VERSION,
    AUDITBOX_GUARDIAN_REQUIRED,
    AUDITBOX_NETWORK_SETTINGS_ENABLED,
    AUDITBOX_PROXY_HOST,
    AUDITBOX_PROXY_PORT,
    AUDITBOX_PUBLIC_URL,
    AUDITBOX_WEB_DIST_DIR,
)
from auditbox.gateway.auth import (
    Principal,
    authenticate_credentials,
    issue_access_token,
    require_authenticated,
    require_super_admin,
)
from auditbox.gateway.preflight import (
    GUARDIAN_PROTOCOL_VERSION,
    ChallengeStore,
    PreflightError,
    guardian_policy,
    issue_admission_token,
    require_guardian_version,
    synchronize_system_clock,
    validate_admission_token,
    verify_report,
)
from auditbox.network.client import (
    NetworkPreparationError,
    get_ethernet_settings,
    prepare_target_network,
    set_ethernet_settings,
)
from auditbox.recommendation import complete_commands
from auditbox.relay.input_frames import INPUT_FRAMING, encode_input
from auditbox.reference.catalog import search_reference

log = logging.getLogger(__name__)

PROXY_HOST = AUDITBOX_PROXY_HOST
PROXY_PORT = AUDITBOX_PROXY_PORT
DEFAULT_SSH_USERNAME = AUDITBOX_DEFAULT_SSH_USERNAME
CONNECT_TIMEOUT = AUDITBOX_CONNECT_TIMEOUT
CONNECTION_WS_TTL = AUDITBOX_CONNECTION_WS_TTL
GATEWAY_CORS_ORIGINS = AUDITBOX_GATEWAY_CORS_ORIGINS
GATEWAY_HOST = AUDITBOX_GATEWAY_HOST
PUBLIC_URL = AUDITBOX_PUBLIC_URL
WEB_DIST_DIR = AUDITBOX_WEB_DIST_DIR
AGENT_URL = AUDITBOX_AGENT_URL
AGENT_TIMEOUT = AUDITBOX_AGENT_TIMEOUT
_pending_ws_sessions: dict[str, tuple[float, ConnectRequest]] = {}
_active_terminal_sessions: dict[str, "ActiveTerminalSession"] = {}
_preflight_challenges = ChallengeStore()

_ANSI_ESCAPE_RE = re.compile(
    r"(?:\x1B\][^\x07]*(?:\x07|\x1B\\)|\x1B[P^_].*?\x1B\\|\x1B(?:\[[0-?]*[ -/]*[@-~]|[@-_]))",
    re.DOTALL,
)
_TERMINAL_PROMPT_RE = re.compile(r"(?m)(?:^|\n)[^\n]{0,160}(?:[$#>%]|[>#])\s*$")
_SESSION_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def _plain_terminal_text(value: str) -> str:
    text = _ANSI_ESCAPE_RE.sub("", value).replace("\x00", "")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class ActiveTerminalSession:
    """One live terminal shared by the browser and approval-gated Agent tools."""

    def __init__(self, session_id: str, request: "ConnectRequest", writer: asyncio.StreamWriter,
                 *, input_framing: bool = False, input_notifier=None):
        self.session_id = session_id
        self.device_id = request.device_id
        self.audit_context = {"login_id": request.login_id or None, "account": request.operator or "未登录",
                              "device_id": request.device_id, "device_name": request.device_name,
                              "session_id": session_id, "target": f"{request.host}:{request.port}",
                              "protocol": request.protocol, "device_profile": request.device_profile}
        self.protocol = request.protocol
        self.target = request.host
        self.port = request.port
        self.writer = writer
        self._input_framing = input_framing
        self._input_notifier = input_notifier
        self.connected = True
        self.created_at = time.time()
        self._write_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._output_condition = asyncio.Condition()
        self._output_chunks: deque[tuple[int, str]] = deque(maxlen=128)
        self._output_sequence = 0
        self._transcript_events: deque[dict[str, Any]] = deque(maxlen=256)
        self._transcript_sequence = 0
        self._known_command_sequences: set[int] = set()
        self._pending_agent_command = ""
        self._last_output_at = 0.0
        self._agent_running = False
        self._human_interrupt = asyncio.Event()

    def public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "protocol": self.protocol,
            "target": self.target,
            "port": self.port,
            "status": "connected" if self.connected else "disconnected",
            "agent_running": self._agent_running,
            "transcript_cursor": self._transcript_sequence,
            "created_at": self.created_at,
        }

    async def record_output(self, data: bytes) -> None:
        text = data.decode("utf-8", errors="replace")
        async with self._output_condition:
            self._output_sequence += 1
            self._transcript_sequence += 1
            self._last_output_at = time.monotonic()
            self._output_chunks.append((self._output_sequence, text))
            self._transcript_events.append({
                "cursor": self._transcript_sequence,
                "event": "terminal_output",
                "text": _plain_terminal_text(text),
            })
            self._output_condition.notify_all()

    async def record_review(self, payload: dict[str, Any]) -> None:
        if payload.get("type") != "operation_review":
            return
        history = payload.get("history")
        if not isinstance(history, list):
            return
        async with self._output_condition:
            for item in history:
                if not isinstance(item, dict):
                    continue
                sequence = int(item.get("seq") or 0)
                command = str(item.get("command") or "").strip()
                if sequence <= 0 or sequence in self._known_command_sequences or not command:
                    continue
                self._known_command_sequences.add(sequence)
                source = {"agent": "Agent", "guide": "指令速查", "human": "用户"}.get(
                    item.get("input_source"),
                    "Agent" if command == self._pending_agent_command else "用户",
                )
                command_status = str(item.get("status") or "executed")
                self._transcript_sequence += 1
                self._transcript_events.append({
                    "cursor": self._transcript_sequence,
                    "event": "command",
                    "source": source,
                    "status": command_status,
                    "text": f"\n[{source}命令/{command_status}] {command}\n",
                })
                if source == "Agent":
                    self._pending_agent_command = ""
            self._output_condition.notify_all()

    def _write_input(self, data: bytes, source: str, audit_context=None) -> None:
        # Source must not silently disappear when proxy/gateway versions differ.
        if not self._input_framing and source != "human":
            raise HTTPException(status_code=409, detail="代理输入来源协议未就绪，请同步更新并重新连接")
        if self._input_framing:
            for offset in range(0, len(data), 65536):
                self.writer.write(encode_input(data[offset:offset + 65536], source, audit_context))
        else:
            self.writer.write(data)

    async def write_browser(self, data: bytes, *, source: str = "human") -> None:
        if self._agent_running and data:
            self._human_interrupt.set()
        async with self._write_lock:
            self._write_input(data, source)
            await self.writer.drain()

    async def execute(self, command: str, timeout_seconds: float, audit_context=None) -> dict[str, Any]:
        async with self._command_lock:
            if not self.connected or self.writer.is_closing():
                raise HTTPException(status_code=409, detail="目标终端已经断开")
            self._human_interrupt.clear()
            self._agent_running = True
            self._pending_agent_command = command
            start_sequence = self._output_sequence
            started_at = time.monotonic()
            status_text = "timeout"
            try:
                async with self._write_lock:
                    if self._input_notifier:
                        await self._input_notifier({"type": "terminal_input_source", "input_source": "agent"})
                    # Submit a complete canonical input line.  A bare CR works
                    # with some PTYs but remains only as typed text through
                    # other SSH/Telnet proxy paths until the browser sends an
                    # additional Enter key.
                    self._write_input(command.encode("utf-8") + b"\n", "agent", audit_context)
                    await self.writer.drain()
                deadline = started_at + timeout_seconds
                while time.monotonic() < deadline:
                    if self._human_interrupt.is_set():
                        status_text = "interrupted_by_user"
                        break
                    remaining = deadline - time.monotonic()
                    async with self._output_condition:
                        try:
                            await asyncio.wait_for(
                                self._output_condition.wait(), timeout=min(0.75, remaining)
                            )
                        except (TimeoutError, asyncio.TimeoutError):
                            pass
                    output = self.output_after(start_sequence, 12_000)
                    quiet_for = time.monotonic() - self._last_output_at
                    if output and quiet_for >= 0.7 and _TERMINAL_PROMPT_RE.search(output):
                        status_text = "completed"
                        break
                    if output and quiet_for >= 1.5:
                        status_text = "completed_after_quiet"
                        break
                return {
                    "status": status_text,
                    "session_id": self.session_id,
                    "command": command,
                    "output": self.output_after(start_sequence, 12_000),
                    "output_cursor": self._output_sequence,
                    "elapsed_seconds": round(time.monotonic() - started_at, 3),
                    "connection_status": (
                        "connected"
                        if self.connected and not self.writer.is_closing()
                        else "disconnected"
                    ),
                }
            finally:
                self._agent_running = False

    def transcript_after(self, cursor: int, max_characters: int) -> dict[str, Any]:
        selected: list[dict[str, Any]] = []
        used = 0
        for event in self._transcript_events:
            if int(event["cursor"]) <= cursor:
                continue
            text = str(event.get("text") or "")
            if selected and used + len(text) > max_characters:
                break
            if len(text) > max_characters:
                text = text[-max_characters:]
            selected.append({**event, "text": text})
            used += len(text)
        next_cursor = int(selected[-1]["cursor"]) if selected else cursor
        oldest = int(self._transcript_events[0]["cursor"]) if self._transcript_events else 0
        return {
            "session_id": self.session_id,
            "cursor": next_cursor,
            "content": "".join(str(event["text"]) for event in selected),
            "events": selected,
            "truncated_before_cursor": bool(oldest and cursor and cursor < oldest - 1),
        }

    async def interrupt(self) -> dict[str, Any]:
        if not self.connected or self.writer.is_closing():
            raise HTTPException(status_code=409, detail="目标终端已经断开")
        self._human_interrupt.set()
        async with self._write_lock:
            self._write_input(b"\x03", "agent")
            await self.writer.drain()
        return {"status": "interrupt_sent", "session_id": self.session_id}

    def output_after(self, cursor: int, max_characters: int) -> str:
        raw = "".join(text for sequence, text in self._output_chunks if sequence > cursor)
        return _plain_terminal_text(raw)[-max_characters:]

    async def close(self) -> None:
        self.connected = False
        self._human_interrupt.set()
        async with self._output_condition:
            self._output_condition.notify_all()

app = FastAPI(title="AuditBox Gateway", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=GATEWAY_CORS_ORIGINS,
    allow_credentials="*" not in GATEWAY_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _audit_store():
    return AuditStore(DB_PATH)


def _actor(principal):
    return {"account": principal.username, "login_id": getattr(principal, "login_id", "") or None}


@app.middleware("http")
async def business_behavior(request: Request, call_next):
    path = request.url.path
    tracked = request.method in {"POST", "PATCH", "DELETE", "PUT"} and (
        path.startswith(("/api/devices", "/api/targets", "/api/connections", "/api/sessions", "/api/settings/ethernet", "/api/system/clock/"))
        or (request.method == "DELETE" and path.startswith("/api/assistant/"))
    )
    principal, details = None, {}
    if tracked:
        try:
            principal = require_authenticated(request)
        except HTTPException:
            pass
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
                details = {key: body[key] for key in (
                    "device_id", "address", "port", "protocol", "device_type", "device_profile",
                    "remark", "mode", "ip_address", "netmask", "client_time_ms"
                ) if key in body}
            except (ValueError, TypeError):
                pass
    response = await call_next(request)
    if tracked:
        safe_audit(lambda: _audit_store().behavior(
            f"{request.method} {path}", context=_actor(principal) if principal else {"account": "未认证"},
            result="success" if response.status_code < 400 else "failed", http_status=response.status_code,
            changes=details))
    return response


@app.get("/api/branding")
async def public_branding(response: Response) -> dict[str, str]:
    """Login can read this one public field; never expose the environment."""
    response.headers["Cache-Control"] = "no-store"
    return {"product_name": product_name()}


class ConnectRequest(BaseModel):
    protocol: str = Field(pattern="^(ssh|telnet|console)$")
    device_type: str
    device_profile: DeviceProfileKey
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=4_000_000)
    username: str | None = None
    password: str | None = None
    cols: int = Field(default=96, ge=20, le=300)
    rows: int = Field(default=28, ge=5, le=120)
    operator: str | None = Field(default=None, max_length=255)
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    login_id: str = Field(default="", max_length=128)
    device_name: str = Field(default="", max_length=255)

    @model_validator(mode="after")
    def validate_endpoint(self) -> "ConnectRequest":
        require_device_profile(self.device_type, self.device_profile)
        _validate_connection_endpoint(self.protocol, self.host, self.port)
        return self


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    admission_token: str = Field(default="", max_length=4096)
    client_time_ms: int | None = Field(default=None, ge=1704067200000, lt=4102444800000, strict=True)


class LoginResponse(BaseModel):
    token: str
    username: str
    role: Literal["admin", "super_admin"]
    clock: dict[str, Any] | None = None


class ClockSyncRequest(BaseModel):
    client_time_ms: int = Field(ge=1704067200000, lt=4102444800000, strict=True)


class EthernetSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "static"]
    ip_address: str = Field(default="", max_length=15)
    netmask: str = Field(default="", max_length=15)


class TargetPreset(BaseModel):
    id: str
    protocol: str = Field(pattern="^(ssh|telnet|console)$")
    device_type: str
    device_profile: DeviceProfileKey
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=4_000_000)
    remark: str = ""
    status: str = "template"

    @model_validator(mode="after")
    def validate_endpoint(self) -> "TargetPreset":
        require_device_profile(self.device_type, self.device_profile)
        self.host = _validate_connection_endpoint(self.protocol, self.host, self.port)
        return self


class ConnectionCreateRequest(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=4_000_000)
    protocol: str = Field(pattern="^(ssh|telnet|console)$")
    device_type: str
    device_profile: DeviceProfileKey
    remark: str = ""
    notes: str = Field(default="", max_length=500)
    target_password: str = Field(default="", min_length=0)
    username: str | None = None

    @model_validator(mode="after")
    def validate_endpoint(self) -> "ConnectionCreateRequest":
        require_device_profile(self.device_type, self.device_profile)
        self.address = _validate_connection_endpoint(self.protocol, self.address, self.port)
        return self


class DeviceWriteRequest(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=4_000_000)
    protocol: str = Field(pattern="^(ssh|telnet|console)$")
    device_type: str
    device_profile: DeviceProfileKey
    remark: str = Field(default="", max_length=255)
    notes: str = Field(default="", max_length=500)
    username: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_endpoint(self) -> "DeviceWriteRequest":
        require_device_profile(self.device_type, self.device_profile)
        self.address = _validate_connection_endpoint(self.protocol, self.address, self.port)
        return self


class SessionCreateRequest(ConnectionCreateRequest):
    device_id: str | None = None


class ConnectionResponse(BaseModel):
    id: str
    address: str
    port: int
    protocol: str
    device_type: str
    device_profile: DeviceProfileKey
    remark: str
    notes: str = ""
    username: str | None = None
    status: str
    last_connected_at: str | None = None
    websocket_url: str | None = None
    session_id: str | None = None
    device_id: str | None = None


class ConnectionListResponse(BaseModel):
    items: list[ConnectionResponse]
    total: int
    page: int
    page_size: int


class RecentConnectionResponse(BaseModel):
    id: str
    device_id: str | None = None
    address: str
    port: int
    protocol: str
    device_type: str
    device_profile: DeviceProfileKey
    remark: str
    notes: str = ""
    username: str | None = None
    last_opened_at: str
    last_operation_at: str | None = None
    operation_result: str = ""
    operation_risk: bool = False


class DeviceStatusItem(BaseModel):
    id: str
    status: str = Field(pattern="^(online|offline|probing)$")
    checked_at: str


class ConsolePortResponse(BaseModel):
    path: str
    device: str
    label: str
    description: str
    manufacturer: str
    product: str
    serial_number: str
    vid: int | None = None
    pid: int | None = None


class CommandSuggestion(BaseModel):
    command: str
    usage_count: int
    last_used_at: str | None = None
    source: Literal["catalog", "history", "prediction"] = "catalog"
    risk: Literal["normal", "confirm"] = "normal"
    behavior: str = ""
    behavior_label: str = ""
    view: str = ""
    models: str = ""
    variant_count: int = 0
    risk_reason: str = ""


class AssistantTargetContext(BaseModel):
    session_id: str = Field(default="", max_length=255)
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    protocol: str = Field(default="", max_length=32)
    address: str = Field(default="", max_length=255)
    port: int | None = Field(default=None, ge=1, le=4_000_000)
    remark: str = Field(default="", max_length=255)
    device_type: str = "server"
    device_profile: DeviceProfileKey = "linux"

    @model_validator(mode="after")
    def validate_device_profile(self) -> "AssistantTargetContext":
        require_device_profile(self.device_type, self.device_profile)
        return self


class AssistantConversationCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=255)
    device_id: str | None = Field(default=None, min_length=1, max_length=128)


AssistantMode = Annotated[
    Literal["standard", "enhanced"],
    BeforeValidator(lambda value: "enhanced" if value == "fast" else value),
]


class AssistantAgentMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    mode: AssistantMode = "standard"
    target: AssistantTargetContext | None = None


class AssistantRunApprovalRequest(BaseModel):
    mode: AssistantMode = "standard"
    tools: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    approved: bool


class AssistantCapabilityPatch(BaseModel):
    enabled: bool


class AssistantMcpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2_000)
    transport: Literal["streamable-http", "sse"] = "streamable-http"
    headers: dict[str, str] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True


class AgentTerminalCommandRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=16_384)
    purpose: str = Field(default="", max_length=1_000)
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    audit_context: dict[str, Any] = Field(default_factory=dict)


class AgentTerminalInterruptRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    _audit_store()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "proxy": {
            "host": PROXY_HOST,
            "port": PROXY_PORT,
        },
        "db": str(DB_PATH),
        "box": _audit_store().info(),
        "audit_schema_version": 2,
    }


def _guardian_installer_metadata() -> dict[str, Any]:
    path = AUDITBOX_GUARDIAN_DOWNLOAD_PATH
    metadata: dict[str, Any] = {
        "filename": path.name,
        "version": "",
        "sha256": "",
        "size": 0,
        "verified": False,
    }
    if not path.is_file() or not AUDITBOX_GUARDIAN_MANIFEST_PATH.is_file():
        return metadata
    try:
        digest = hashlib.sha256()
        with path.open("rb") as installer_file:
            for block in iter(lambda: installer_file.read(1024 * 1024), b""):
                digest.update(block)
        actual_sha256 = digest.hexdigest()
        actual_size = path.stat().st_size
        manifest = json.loads(
            AUDITBOX_GUARDIAN_MANIFEST_PATH.read_text(encoding="utf-8-sig")
        )
        manifest_filename = str(manifest.get("filename") or "").strip()
        manifest_version = str(manifest.get("version") or "").strip()
        require_guardian_version(manifest_version)
        manifest_sha256 = str(manifest.get("sha256") or "").strip().lower()
        manifest_size = int(manifest.get("size") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, PreflightError):
        return metadata
    metadata.update(
        version=manifest_version,
        sha256=actual_sha256,
        size=actual_size,
        verified=(
            manifest_filename == path.name
            and manifest_sha256 == actual_sha256
            and manifest_size == actual_size
        ),
    )
    return metadata


@app.get("/api/security/preflight")
async def security_preflight() -> dict[str, Any]:
    installer = _guardian_installer_metadata()
    return {
        "required": AUDITBOX_GUARDIAN_REQUIRED,
        "product": "智维卫士",
        "platform": "windows",
        "local_url": AUDITBOX_GUARDIAN_LOCAL_URL,
        "minimum_guardian_version": AUDITBOX_GUARDIAN_MIN_VERSION,
        "guardian_protocol_version": GUARDIAN_PROTOCOL_VERSION,
        "download_available": bool(installer.get("verified")),
        "download_url": "/api/security/guardian/download",
        "installer": installer,
    }


@app.get("/api/settings/ethernet")
async def read_ethernet_settings(request: Request) -> dict[str, Any]:
    require_authenticated(request)
    if not AUDITBOX_NETWORK_SETTINGS_ENABLED:
        return {"supported": False, "reason": "服务器版使用现有网络连接目标设备，不在此处修改服务器网口。"}
    try:
        return await asyncio.to_thread(get_ethernet_settings)
    except NetworkPreparationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.put("/api/settings/ethernet")
async def update_ethernet_settings(payload: EthernetSettingsRequest, request: Request) -> dict[str, Any]:
    principal = require_authenticated(request)
    if not AUDITBOX_NETWORK_SETTINGS_ENABLED:
        raise HTTPException(status_code=403, detail="服务器版不允许修改宿主机网口")
    try:
        result = await asyncio.to_thread(set_ethernet_settings, payload.mode, payload.ip_address, payload.netmask)
    except NetworkPreparationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log.info("Ethernet settings updated by %s: mode=%s address=%s mask=%s", principal.username, result["mode"], result["ip_address"], result["netmask"])
    return result


@app.post("/api/security/challenge")
async def security_challenge(request: Request) -> dict[str, Any]:
    challenge = _preflight_challenges.issue(_client_ip(request))
    return {
        "challenge_id": challenge.challenge_id,
        "nonce": challenge.nonce,
        "expires_at": challenge.expires_at,
        "policy": guardian_policy(),
        "policy_digest": challenge.policy_digest,
        "minimum_guardian_version": AUDITBOX_GUARDIAN_MIN_VERSION,
        "guardian_protocol_version": GUARDIAN_PROTOCOL_VERSION,
    }


@app.post("/api/security/verify")
async def security_verify(request: Request) -> dict[str, Any]:
    try:
        report = await request.json()
        if not isinstance(report, dict):
            raise PreflightError("检查报告格式无效")
        challenge_id = str(report.get("challenge_id") or "")
        challenge = _preflight_challenges.consume(challenge_id, _client_ip(request))
        device_id = verify_report(report, challenge)
        clock_synchronized = synchronize_system_clock(report.get("checked_at"))
    except PreflightError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="检查报告格式无效") from exc
    return {
        "admission_token": issue_admission_token(_client_ip(request), device_id),
        "expires_in": AUDITBOX_GUARDIAN_ADMISSION_TTL,
        "clock_synchronized": clock_synchronized,
    }


@app.get("/api/security/guardian/download")
async def guardian_download() -> FileResponse:
    installer = _guardian_installer_metadata()
    if not installer.get("verified"):
        raise HTTPException(
            status_code=503,
            detail="Windows 安装包缺失或未通过版本与完整性校验",
        )
    return FileResponse(
        AUDITBOX_GUARDIAN_DOWNLOAD_PATH,
        media_type="application/vnd.microsoft.portable-executable",
        filename="AuditBoxGuardian-Setup.exe",
        headers={
            "ETag": f'"sha256:{installer["sha256"]}"',
            "X-AuditBox-Guardian-Version": str(installer["version"]),
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request) -> LoginResponse:
    if AUDITBOX_GUARDIAN_REQUIRED:
        try:
            validate_admission_token(payload.admission_token, _client_ip(request))
        except PreflightError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    principal = authenticate_credentials(payload.username, payload.password)
    if not principal:
        safe_audit(lambda: _audit_store().put("logins", {
            "account": payload.username[:255], "success": False, "reason": "用户名或密码错误",
            "client_ip": _client_ip(request), "user_agent": request.headers.get("user-agent", "")[:512],
        }))
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # Authenticate first, calibrate second, issue the token last. Otherwise a
    # cold-boot clock jump immediately expires the newly issued login token.
    clock = _synchronize_browser_clock(payload.client_time_ms) if payload.client_time_ms else None
    login_record = _audit_store().put("logins", {
        "account": principal.username, "success": True, "role": principal.role,
        "client_ip": _client_ip(request), "user_agent": request.headers.get("user-agent", "")[:512],
    })
    principal = Principal(principal.username, principal.role, login_record["id"])
    return LoginResponse(
        token=issue_access_token(principal),
        username=principal.username,
        role=principal.role,
        clock=clock,
    )


def _synchronize_browser_clock(client_time_ms: int) -> dict[str, Any]:
    if not AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED:
        return {"synchronized": False, "server_time_ms": int(time.time() * 1000), "source": "server", "enabled": False}
    before = time.time()
    try:
        changed = synchronize_system_clock(
            datetime.fromtimestamp(client_time_ms / 1000, timezone.utc).isoformat()
        )
    except PreflightError as exc:
        log.warning("Browser clock synchronization failed: %s", exc)
        return {"synchronized": False, "error": "盒子时间校准失败，请检查服务校时权限"}
    if changed:
        log.info("Clock synchronized from authenticated browser: %.3f -> %.3f", before, time.time())
    return {"synchronized": changed, "server_time_ms": int(time.time() * 1000), "source": "browser"}


@app.post("/api/system/clock/sync")
async def synchronize_browser_clock(payload: ClockSyncRequest, request: Request) -> dict[str, Any]:
    principal = require_authenticated(request)
    result = _synchronize_browser_clock(payload.client_time_ms)
    if result.get("error"):
        raise HTTPException(status_code=503, detail=result["error"])
    if result["synchronized"]:
        # Validate the old token before changing the clock, then replace it so
        # a forward correction cannot log the connected browser out mid-use.
        result["token"] = issue_access_token(principal)
    return result


@app.post("/api/security/admission/refresh")
async def refresh_security_admission(request: Request) -> dict[str, Any]:
    """Keep the first trusted device check valid while the browser session remains open."""
    principal = require_authenticated(request)
    return {
        "admission_token": issue_admission_token(
            _client_ip(request),
            f"authenticated:{principal.username}",
        ),
        "expires_in": AUDITBOX_GUARDIAN_ADMISSION_TTL,
    }


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


# Keep the historical captive-probe routes so older Wi-Fi images remain
# upgrade-compatible. Current Type-C deployments are opened by the Windows
# Guardian after the USB management link becomes ready.
@app.get("/hotspot-detect.html", include_in_schema=False)
@app.get("/connecttest.txt", include_in_schema=False)
@app.get("/ncsi.txt", include_in_schema=False)
@app.get("/generate_204", include_in_schema=False)
@app.get("/gen_204", include_in_schema=False)
@app.get("/canonical.html", include_in_schema=False)
@app.get("/success.txt", include_in_schema=False)
@app.get("/redirect", include_in_schema=False)
async def captive_portal_probe() -> RedirectResponse:
    return RedirectResponse(url=PUBLIC_URL, status_code=302)


@app.get("/api/targets", response_model=list[TargetPreset])
async def list_targets() -> list[TargetPreset]:
    return [_target_to_response(target) for target in TargetRepository().list()]


@app.post("/api/targets", response_model=TargetPreset, status_code=201)
async def create_target(target: TargetPreset) -> TargetPreset:
    try:
        created = TargetRepository().create(_target_from_request(target))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    RecentConnectionRepository().sync_device(created)
    return _target_to_response(created)


@app.get("/api/devices", response_model=ConnectionListResponse)
async def list_devices(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    search: str = Query(default=""),
) -> ConnectionListResponse:
    result = TargetRepository().list_page(
        page=page,
        page_size=page_size,
        search=search,
        connected_only=False,
    )
    return ConnectionListResponse(
        items=[_connection_to_response(item, status="probing") for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@app.post("/api/devices", response_model=ConnectionResponse, status_code=201)
async def create_device(payload: DeviceWriteRequest) -> ConnectionResponse:
    target = _device_from_request(payload)
    try:
        created = TargetRepository().create(target)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    RecentConnectionRepository().sync_device(created)
    return _connection_to_response(created, status="probing")


@app.patch("/api/devices/{device_id}", response_model=ConnectionResponse)
async def update_device(device_id: str, payload: DeviceWriteRequest) -> ConnectionResponse:
    target = _device_from_request(payload, target_id=device_id)
    try:
        updated = TargetRepository().update(device_id, target)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    RecentConnectionRepository().sync_device(updated)
    return _connection_to_response(updated, status="probing")


@app.get("/api/recent-connections", response_model=list[RecentConnectionResponse])
async def list_recent_connections(
    limit: int = Query(default=4, ge=1, le=20),
) -> list[RecentConnectionResponse]:
    return [
        _recent_connection_to_response(item)
        for item in RecentConnectionRepository().list(limit=limit)
    ]


@app.get("/api/devices/status", response_model=list[DeviceStatusItem])
async def probe_device_statuses() -> list[DeviceStatusItem]:
    targets = TargetRepository().list()
    semaphore = asyncio.Semaphore(4)

    async def probe(target: Target) -> DeviceStatusItem:
        async with semaphore:
            online = await asyncio.to_thread(_probe_target_endpoint, target)
        return DeviceStatusItem(
            id=target.id,
            status="online" if online else "offline",
            checked_at=_utc_now(),
        )

    return list(await asyncio.gather(*(probe(target) for target in targets)))


@app.get("/api/console/ports", response_model=list[ConsolePortResponse])
async def console_ports(request: Request) -> list[ConsolePortResponse]:
    require_authenticated(request)
    return [
        ConsolePortResponse(**port.__dict__)
        for port in await asyncio.to_thread(list_console_ports)
    ]


@app.post("/api/sessions", response_model=ConnectionResponse, status_code=201)
async def create_session(payload: SessionCreateRequest, request: Request) -> ConnectionResponse:
    principal = require_authenticated(request)
    targets = TargetRepository()
    if payload.device_id:
        target = targets.get(payload.device_id)
        if not target:
            raise HTTPException(status_code=404, detail="设备不存在")
    else:
        # Quick-open/old direct records may not carry an ID. Resolve the saved
        # device before verification and ticket creation, not only in recents.
        target = targets.find_by_endpoint(payload.protocol, payload.address, payload.port)
    if target:
        payload = payload.model_copy(update={
            "device_id": target.id,
            "address": target.host,
            "port": target.port,
            "protocol": target.protocol,
            "device_type": target.device_type,
            "device_profile": target.device_profile,
            "remark": target.remark,
            "notes": target.notes,
            "username": target.username,
        })

    username = payload.username or (DEFAULT_SSH_USERNAME if payload.protocol == "ssh" else None)
    await _prepare_and_verify_target(payload, username)

    connected_at = _utc_now()
    if payload.device_id:
        target = targets.mark_connected(payload.device_id, connected_at) or target
    if not target:
        target = Target(
            id="",
            protocol=payload.protocol,
            device_type=payload.device_type,
            device_profile=payload.device_profile,
            host=payload.address,
            port=payload.port,
            remark=payload.remark,
            notes=payload.notes,
            status="online",
            username=username,
            last_connected_at=connected_at,
        )
    RecentConnectionRepository().remember(
        target,
        device_id=payload.device_id,
        opened_at=connected_at,
    )

    connect_request = ConnectRequest(
        protocol=payload.protocol,
        device_type=payload.device_type,
        device_profile=payload.device_profile,
        host=payload.address,
        port=payload.port,
        username=username,
        password=payload.target_password,
        cols=96,
        rows=28,
        operator=principal.username,
        login_id=getattr(principal, "login_id", ""),
        device_name=payload.remark,
        device_id=payload.device_id,
    )
    websocket_url, session_id = _create_connection_websocket_url(request, connect_request)
    return _connection_to_response(
        target,
        websocket_url=websocket_url,
        session_id=session_id,
        device_id=payload.device_id,
        status="online",
    )


@app.post("/api/connections", response_model=ConnectionResponse, status_code=201)
async def create_connection(payload: ConnectionCreateRequest, request: Request) -> ConnectionResponse:
    principal = require_authenticated(request)
    username = payload.username or (DEFAULT_SSH_USERNAME if payload.protocol == "ssh" else None)
    await _prepare_and_verify_target(payload, username)

    connected_at = _utc_now()
    target = Target(
        id="",
        protocol=payload.protocol,
        device_type=payload.device_type,
        device_profile=payload.device_profile,
        host=payload.address,
        port=payload.port,
        remark=payload.remark,
        notes=payload.notes,
        status="online",
        username=username,
        last_connected_at=connected_at,
    )
    saved = TargetRepository().save_connected(target)
    RecentConnectionRepository().remember(
        saved,
        device_id=saved.id,
        opened_at=connected_at,
    )
    connect_request = ConnectRequest(
        protocol=payload.protocol,
        device_type=payload.device_type,
        device_profile=payload.device_profile,
        host=payload.address,
        port=payload.port,
        username=username,
        password=payload.target_password,
        cols=96,
        rows=28,
        operator=principal.username,
        login_id=getattr(principal, "login_id", ""),
        device_name=payload.remark,
        device_id=saved.id,
    )
    websocket_url, session_id = _create_connection_websocket_url(request, connect_request)
    return _connection_to_response(
        saved,
        websocket_url=websocket_url,
        session_id=session_id,
        device_id=saved.id,
    )


@app.get("/api/connections", response_model=ConnectionListResponse)
async def list_connections(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str = Query(default=""),
) -> ConnectionListResponse:
    result = TargetRepository().list_page(
        page=page,
        page_size=page_size,
        search=search,
        connected_only=True,
    )
    return ConnectionListResponse(
        items=[_connection_to_response(item) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@app.get("/api/commands/guide/counts")
def device_command_guide_counts(request: Request) -> dict:
    require_authenticated(request)
    from auditbox.reference.guide import command_guide_counts
    try:
        return command_guide_counts()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="指令速查操作数量暂不可用") from exc


@app.get("/api/commands/guide")
def device_command_guide(request: Request, device_profile: DeviceProfileKey) -> dict:
    require_authenticated(request)
    from auditbox.reference.guide import command_guide
    try:
        return command_guide(device_profile)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="该设备指令速查资料暂不可用") from exc


@app.get("/api/commands/reference")
def command_reference(
    request: Request,
    device_profile: DeviceProfileKey,
    q: str = Query(default="", max_length=200),
    risk: Literal["all", "risk", "safe"] = "all",
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    command: str | None = Query(default=None, max_length=2048),
) -> dict:
    require_authenticated(request)
    try:
        return search_reference(device_profile, q=q, risk=risk, page=page, limit=limit, command=command)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/commands/suggestions", response_model=list[CommandSuggestion])
async def suggest_commands(
    prefix: str = Query(default="", min_length=0, max_length=255),
    target: str = Query(default="", max_length=255),
    protocol: str = Query(default="", max_length=32),
    device_type: str = Query(default="server"),
    device_profile: DeviceProfileKey = Query(default="linux"),
    limit: int = Query(default=8, ge=1, le=20),
) -> list[CommandSuggestion]:
    # target/protocol stay in the public API for client compatibility. Factory
    # completion is deliberately independent of mutable audit history.
    del target, protocol
    try:
        profile = require_device_profile(device_type, device_profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    items = complete_commands(prefix, device_profile=profile.key, limit=limit)
    return [
        CommandSuggestion(**item)
        for item in items
    ]


@app.get("/api/assistant/status")
async def assistant_status(request: Request) -> dict[str, Any]:
    require_authenticated(request)
    try:
        result = await _request_agent_json("GET", "/api/v1/health", timeout=4.0)
        ready = result.get("status") == "ok" and bool(result.get("model_ready"))
        return {
            "ready": ready,
            "service": result.get("service", "智维宝盒智能助手"),
            "knowledge_enabled": bool(result.get("knowledge_enabled")),
            "tool_approval": result.get("tool_approval"),
            "capabilities": result.get("capabilities") or {},
        }
    except HTTPException:
        return {"ready": False, "capabilities": {"skills": 0, "mcp_servers": 0}}


@app.get("/api/assistant/conversations")
async def assistant_conversations(
    request: Request,
    page: int = 1,
    limit: int = 20,
    device_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> dict[str, Any]:
    require_authenticated(request)
    _require_connected_device_scope(device_id)
    query = httpx.QueryParams({"page": page, "limit": limit})
    if device_id:
        query = query.set("device_id", device_id)
    return await _request_agent_json(
        "GET", f"/api/v1/conversations?{query}"
    )


@app.get("/api/assistant/capabilities")
async def assistant_capabilities(request: Request) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json("GET", "/api/v1/capabilities")


@app.patch("/api/assistant/capabilities/{capability_id:path}")
async def assistant_capability_patch(
    capability_id: str, payload: AssistantCapabilityPatch, request: Request
) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json(
        "PATCH", f"/api/v1/capabilities/{capability_id}", payload.model_dump()
    )


@app.delete("/api/assistant/capabilities/{capability_id:path}")
async def assistant_capability_delete(capability_id: str, request: Request) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json("DELETE", f"/api/v1/capabilities/{capability_id}")


@app.post("/api/assistant/capabilities/mcp", status_code=201)
async def assistant_mcp_create(
    payload: AssistantMcpServerCreate, request: Request
) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json(
        "POST", "/api/v1/capabilities/mcp", payload.model_dump()
    )


@app.post("/api/assistant/capabilities/mcp/probe")
async def assistant_mcp_probe(
    payload: AssistantMcpServerCreate, request: Request
) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json(
        "POST", "/api/v1/capabilities/mcp/probe", payload.model_dump()
    )


@app.post("/api/assistant/capabilities/{capability_id:path}/probe")
async def assistant_saved_mcp_probe(capability_id: str, request: Request) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json(
        "POST", f"/api/v1/capabilities/{capability_id}/probe", {}
    )


@app.post("/api/assistant/capabilities/skills", status_code=201)
async def assistant_skill_upload(
    request: Request,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
    enabled: Annotated[bool, Form()] = True,
) -> dict[str, Any]:
    require_authenticated(request)
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Skill 压缩包不能超过 10MB")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(AGENT_TIMEOUT, connect=5.0)
        ) as client:
            response = await client.post(
                f"{AGENT_URL}/api/v1/capabilities/skills",
                files={
                    "file": (
                        file.filename or "skill.zip",
                        content,
                        file.content_type or "application/zip",
                    )
                },
                data={"name": name or "", "enabled": str(enabled).lower()},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Agent 服务暂时不可用") from exc
    if response.status_code >= 400:
        raise _agent_http_error(response.status_code, response.content)
    result = response.json()
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Agent 返回了无法识别的结果")
    return result


@app.post("/api/assistant/conversations", status_code=201)
async def assistant_conversation_create(
    payload: AssistantConversationCreate, request: Request
) -> dict[str, Any]:
    require_authenticated(request)
    _require_connected_device_scope(payload.device_id)
    return await _request_agent_json(
        "POST", "/api/v1/conversations", payload.model_dump()
    )


@app.get("/api/assistant/conversations/{conversation_id}")
async def assistant_conversation_get(conversation_id: str, request: Request) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json("GET", f"/api/v1/conversations/{conversation_id}")


@app.get("/api/assistant/conversations/{conversation_id}/files")
async def assistant_conversation_files(
    conversation_id: str, request: Request
) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json(
        "GET", f"/api/v1/conversations/{conversation_id}/files"
    )


@app.post("/api/assistant/conversations/{conversation_id}/files", status_code=201)
async def assistant_conversation_file_upload(
    conversation_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    require_authenticated(request)
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单个文件不能超过 5MB")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(AGENT_TIMEOUT, connect=5.0)
        ) as client:
            response = await client.post(
                f"{AGENT_URL}/api/v1/conversations/{conversation_id}/files",
                files={
                    "file": (
                        file.filename or "document.txt",
                        content,
                        file.content_type or "application/octet-stream",
                    )
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Agent 服务暂时不可用") from exc
    if response.status_code >= 400:
        raise _agent_http_error(response.status_code, response.content)
    result = response.json()
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Agent 返回了无法识别的结果")
    return result


@app.delete(
    "/api/assistant/conversations/{conversation_id}/files/{file_id}", status_code=204
)
async def assistant_conversation_file_delete(
    conversation_id: str,
    file_id: str,
    request: Request,
) -> Response:
    require_authenticated(request)
    await _request_agent(
        "DELETE", f"/api/v1/conversations/{conversation_id}/files/{file_id}"
    )
    return Response(status_code=204)


@app.delete("/api/assistant/conversations/{conversation_id}", status_code=204)
async def assistant_conversation_delete(conversation_id: str, request: Request) -> Response:
    require_authenticated(request)
    await _request_agent("DELETE", f"/api/v1/conversations/{conversation_id}")
    return Response(status_code=204)


@app.post("/api/assistant/conversations/{conversation_id}/title")
async def assistant_conversation_title(
    conversation_id: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json(
        "POST", f"/api/v1/conversations/{conversation_id}/title", payload
    )


@app.post("/api/assistant/conversations/{conversation_id}/messages")
async def assistant_conversation_message(
    conversation_id: str, payload: AssistantAgentMessageRequest, request: Request
) -> StreamingResponse:
    principal = require_authenticated(request)
    agent_payload = payload.model_dump()
    agent_payload["metadata"] = {**agent_payload.get("metadata", {}), "audit_actor": _actor(principal)}
    agent_payload["stream"] = True
    client = httpx.AsyncClient(timeout=httpx.Timeout(AGENT_TIMEOUT, connect=5.0))
    try:
        agent_request = client.build_request(
            "POST",
            f"{AGENT_URL}/api/v1/conversations/{conversation_id}/messages",
            json=agent_payload,
            headers={"Accept": "text/event-stream"},
        )
        response = await client.send(agent_request, stream=True)
        if response.status_code >= 400:
            detail = await response.aread()
            await response.aclose()
            await client.aclose()
            raise _agent_http_error(response.status_code, detail)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=503, detail="Agent 服务暂时不可用") from exc

    async def proxy_events():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        proxy_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/api/assistant/runs/{run_id}/cancel")
async def assistant_run_cancel(run_id: str, request: Request) -> dict[str, Any]:
    require_authenticated(request)
    return await _request_agent_json("POST", f"/api/v1/runs/{run_id}/cancel", {})


@app.post("/api/assistant/conversations/{conversation_id}/runs/{run_id}/continue")
async def assistant_run_continue(
    conversation_id: str,
    run_id: str,
    payload: AssistantRunApprovalRequest,
    request: Request,
) -> StreamingResponse:
    principal = require_authenticated(request)
    agent_payload = {**payload.model_dump(), "audit_actor": _actor(principal)}
    return await _stream_agent_response(
        f"/api/v1/conversations/{conversation_id}/runs/{run_id}/continue",
        agent_payload,
    )


@app.get("/api/internal/agent/terminal/sessions")
async def agent_terminal_sessions(request: Request) -> dict[str, Any]:
    _require_loopback_agent(request)
    return {"data": [session.public() for session in _active_terminal_sessions.values()]}


@app.get("/api/internal/agent/terminal/sessions/{session_id}/transcript")
async def agent_terminal_transcript(
    session_id: str,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    max_characters: int = Query(default=4_000, ge=256, le=12_000),
) -> dict[str, Any]:
    _require_loopback_agent(request)
    return _require_active_terminal(session_id).transcript_after(cursor, max_characters)


@app.post("/api/internal/agent/terminal/execute")
async def agent_terminal_execute(
    payload: AgentTerminalCommandRequest, request: Request
) -> dict[str, Any]:
    _require_loopback_agent(request)
    await _require_agent_execution_enabled()
    session = _require_active_terminal(payload.session_id)
    return await session.execute(payload.command.strip(), payload.timeout_seconds, payload.audit_context)


async def _require_agent_execution_enabled() -> None:
    # Fail closed if the Agent is off, unavailable, or too old to expose the
    # switch. Manual browser terminal input uses a separate path.
    health = await _request_agent_json("GET", "/api/v1/health", timeout=4.0)
    if (health.get("capabilities") or {}).get("execution_enabled") is not True:
        raise HTTPException(status_code=409, detail="Agent 执行已关闭，当前仅支持模型问答")


@app.post("/api/internal/agent/audit")
async def agent_audit_event(request: Request):
    _require_loopback_agent(request)
    payload = await request.json()
    context = payload.get("context") or {}
    return _audit_store().behavior(str(payload.get("action") or "agent_approval")[:100],
                                  context=context, **redact(payload.get("details") or {}))


@app.get("/api/box/info")
async def box_info(request: Request):
    require_super_admin(request)
    return {**_audit_store().info(), "audit_schema_version": 2}


@app.get("/api/device-capabilities")
async def device_capabilities(request: Request):
    require_super_admin(request)
    from auditbox.capability_packages import inventory
    return {**inventory(), "box_id": _audit_store().info()["box_id"]}


@app.get('/api/device-profiles')
async def public_device_profiles():
    # Labels only, like public branding: no credentials, device instances or logs.
    from auditbox.device_profiles import DEVICE_PROFILES
    from auditbox.command_packages import profiles
    return {'profiles': [
        {'profile_id': p.key, 'label': p.label, 'device_type': p.device_type,
         'category_label': '服务器' if p.device_type == 'server' else '交换机'}
        for p in DEVICE_PROFILES.values()
    ] + [{k: p[k] for k in ('profile_id', 'label', 'device_type', 'category_label')}
         for p in profiles()]}


@app.post("/api/device-capabilities/install")
async def install_device_capability(request: Request, expected_box_id: str):
    principal = require_super_admin(request)
    from auditbox.capability_packages import MAX_PACKAGE, install
    import tarfile
    import zlib
    if expected_box_id != _audit_store().info()["box_id"]:
        raise HTTPException(409, "接入的盒子已变化，请重新确认盒子")
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_PACKAGE:
            raise HTTPException(413, "能力包超过大小限制")
    if _active_terminal_sessions:
        raise HTTPException(409, "请结束终端连接后再注入能力")
    try:
        result = install(bytes(payload))
    except (ValueError, TypeError, KeyError, OSError, EOFError, tarfile.TarError, zlib.error) as exc:
        raise HTTPException(422, "能力包校验或安装失败：" + str(exc)) from exc
    safe_audit(lambda: _audit_store().behavior("capability_installed", context=_actor(principal),
        profile_id=result["profile_id"], version=result["version"], already_installed=result["already_installed"]))
    return result


@app.get("/api/audit/v2/changes")
async def audit_changes(request: Request, cursor: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
    require_super_admin(request)
    try:
        return _audit_store().changes(cursor, limit)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/audit/v2/inputs/{input_id}/conversation")
async def audit_conversation(input_id: str, request: Request):
    require_super_admin(request)
    source = _audit_store().get("inputs", input_id)
    if not source:
        raise HTTPException(404, "输入详情不存在")
    cid = str(source.get("conversation_id") or "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", cid):
        return {"available": False, "snapshot": source, "reason": "历史记录未保存聊天标识"}
    try:
        conversation = await _request_agent_json("GET", f"/api/v1/conversations/{cid}")
    except HTTPException as exc:
        return {"available": False, "snapshot": source,
                "reason": "原会话已删除，显示留存来源" if exc.status_code == 404 else "会话暂不可访问，显示留存来源"}
    return {"available": True, "snapshot": source, "focus_run_id": source.get("run_id"), "conversation": redact(conversation)}


@app.get("/api/audit/v2/{kind}")
async def audit_records(kind: str, request: Request, login_id: str = "", execution_id: str = "",
                        page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200)):
    require_super_admin(request)
    if kind not in TABLES:
        raise HTTPException(404, "日志类型不存在")
    return _audit_store().records(kind, login_id=login_id, execution_id=execution_id,
                                  limit=limit, offset=(page - 1) * limit)


@app.post("/api/internal/agent/terminal/interrupt")
async def agent_terminal_interrupt(
    payload: AgentTerminalInterruptRequest, request: Request
) -> dict[str, Any]:
    _require_loopback_agent(request)
    await _require_agent_execution_enabled()
    return await _require_active_terminal(payload.session_id).interrupt()


@app.get("/api/audit/sessions")
async def list_audit_sessions(request: Request) -> list[dict[str, Any]]:
    require_super_admin(request)
    audit_dir = _audit_dir()
    if not audit_dir.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for path in sorted(audit_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True):
        summary = _read_session_summary(path)
        if summary:
            sessions.append(summary)
    return sessions


@app.get("/api/audit/sessions/{session_id}")
async def get_audit_session(session_id: str, request: Request) -> dict[str, Any]:
    require_super_admin(request)
    if not _is_safe_session_id(session_id):
        raise HTTPException(status_code=400, detail="会话标识无效")

    path = _audit_dir() / f"{session_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="未找到审计会话")

    records = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"event": "invalid_record", "raw": line})
    return {"session_id": session_id, "records": records}


@app.get("/api/audit/export")
async def export_audit_log(request: Request) -> StreamingResponse:
    require_super_admin(request)
    filename = f"auditbox-operation-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        _iter_audit_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.websocket("/ws/session")
async def terminal_session(websocket: WebSocket) -> None:
    await websocket.accept()
    request = await _receive_connect_request(websocket)
    await _bridge_terminal_session(websocket, request)


@app.websocket("/ws/connections/{token}")
async def connection_terminal_session(websocket: WebSocket, token: str) -> None:
    await websocket.accept()
    request = _pop_pending_ws_session(token)
    if not request:
        await websocket.send_json({"type": "error", "message": "连接会话已过期或不存在"})
        await websocket.close()
        return
    await _bridge_terminal_session(websocket, request)


async def _bridge_terminal_session(websocket: WebSocket, request: ConnectRequest) -> None:
    tcp_reader: asyncio.StreamReader | None = None
    tcp_writer: asyncio.StreamWriter | None = None
    predict_writer: asyncio.StreamWriter | None = None
    terminal_tasks: set[asyncio.Task] = set()
    background_tasks: set[asyncio.Task] = set()
    shared_session: ActiveTerminalSession | None = None

    try:
        tcp_reader, tcp_writer = await asyncio.open_connection(PROXY_HOST, PROXY_PORT)

        proxy_payload = request.model_dump(exclude_none=True)
        proxy_payload["input_framing"] = INPUT_FRAMING
        tcp_writer.write((json.dumps(proxy_payload) + "\n").encode())
        await tcp_writer.drain()

        response = await _read_json_line(tcp_reader)
        predict_port = int(response.get("predict_port") or 0)
        session_id = str(response.get("session_id") or "")
        if response.get("status") == "ok" and session_id:
            shared_session = ActiveTerminalSession(
                session_id, request, tcp_writer,
                input_framing=response.get("input_framing") == INPUT_FRAMING,
                input_notifier=websocket.send_json,
            )
            _active_terminal_sessions[session_id] = shared_session
            safe_audit(lambda: _audit_store().behavior("terminal_connected", context=shared_session.audit_context))
        await websocket.send_json({"type": "handshake", **response})

        if response.get("status") != "ok":
            return

        if predict_port and session_id:
            predict_task, predict_writer = await _start_prediction_bridge(
                websocket=websocket,
                session_id=session_id,
                predict_port=predict_port,
                request=request,
                shared_session=shared_session,
            )
            if predict_task:
                background_tasks.add(predict_task)

        terminal_tasks.add(asyncio.create_task(
            _tcp_to_websocket(tcp_reader, websocket, shared_session)
        ))
        terminal_tasks.add(asyncio.create_task(
            _websocket_to_tcp(websocket, tcp_writer, predict_writer, shared_session)
        ))

        done, pending = await asyncio.wait(terminal_tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.warning("gateway session task ended with error: %s", exc)
        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("gateway session failed: %s", exc)
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({"type": "error", "message": str(exc)})
    finally:
        if shared_session:
            safe_audit(lambda: _audit_store().behavior("terminal_disconnected", context=shared_session.audit_context))
            if _active_terminal_sessions.get(shared_session.session_id) is shared_session:
                _active_terminal_sessions.pop(shared_session.session_id, None)
            await shared_session.close()
        for task in terminal_tasks | background_tasks:
            task.cancel()
        if predict_writer:
            predict_writer.close()
            await _wait_closed(predict_writer)
        if tcp_writer:
            tcp_writer.close()
            await _wait_closed(tcp_writer)
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close()


async def _receive_connect_request(websocket: WebSocket) -> ConnectRequest:
    message = await websocket.receive()
    if "text" not in message or message["text"] is None:
        raise ValueError("终端首条消息必须是 JSON 文本")
    try:
        payload = json.loads(message["text"])
    except json.JSONDecodeError as exc:
        raise ValueError("连接参数不是有效的 JSON") from exc
    # The legacy WebSocket route has no authenticated HTTP setup request. Do
    # not let a direct client forge the operator recorded in the audit log.
    payload["operator"] = "未登录"
    payload["login_id"] = ""
    payload["device_id"] = None
    payload["device_name"] = ""
    return ConnectRequest.model_validate(payload)


async def _read_json_line(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await reader.readline()
    if not line:
        raise ConnectionError("代理在返回握手响应前关闭了连接")
    return json.loads(line.decode("utf-8"))


async def _tcp_to_websocket(
    reader: asyncio.StreamReader,
    websocket: WebSocket,
    shared_session: ActiveTerminalSession | None = None,
) -> None:
    while True:
        data = await reader.read(4096)
        if not data:
            break
        if shared_session:
            await shared_session.record_output(data)
        await websocket.send_bytes(data)


async def _websocket_to_tcp(
    websocket: WebSocket,
    writer: asyncio.StreamWriter,
    control_writer: asyncio.StreamWriter | None = None,
    shared_session: ActiveTerminalSession | None = None,
) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            break
        if message.get("bytes") is not None:
            data = message["bytes"]
        elif message.get("text") is not None:
            text = message["text"]
            # Programmatic input uses the same ordered transport as its bytes.
            try:
                input_payload = json.loads(text) if text.startswith("{") else None
            except json.JSONDecodeError:
                input_payload = None
            if isinstance(input_payload, dict) and input_payload.get("type") == "terminal_input":
                if (not isinstance(input_payload.get("source"), str)
                        or input_payload["source"] not in {"human", "guide"}
                        or not isinstance(input_payload.get("data"), str)
                        or len(input_payload["data"].encode()) > 262144):
                    await websocket.send_json({"type": "input_error", "message": "终端输入来源或内容无效"})
                    continue
                if not shared_session or not shared_session._input_framing:
                    await websocket.send_json({"type": "input_error", "message": "代理输入来源协议未就绪，请重新连接"})
                    continue
                await shared_session.write_browser(input_payload["data"].encode(), source=input_payload["source"])
                continue
            control = _terminal_control_message(text)
            if control:
                if control["type"] == "session_unlock_debug":
                    # Consume retired controls from cached clients; never send
                    # their JSON to the target shell or to an older lock handler.
                    continue
                if not control_writer:
                    if control["type"].startswith("command_confirmation"):
                        await websocket.send_json({
                            "type": "confirmation_error",
                            "message": "高危命令确认通道不可用",
                        })
                    continue
                control_writer.write((json.dumps(control) + "\n").encode())
                await control_writer.drain()
                continue
            data = text.encode()
        else:
            continue
        if shared_session:
            await shared_session.write_browser(data)
        else:
            writer.write(data)
            await writer.drain()


def _terminal_control_message(text: str) -> dict[str, Any] | None:
    if len(text) > 32_768 or not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    message_type = payload.get("type")
    if message_type == "guide_audit_event":
        event = str(payload.get("event") or "").strip()
        details = payload.get("details")
        if not event.startswith("guide_") or len(event) > 64 or not isinstance(details, dict):
            return None
        return {
            "type": message_type,
            "event": event,
            "details": details,
        }
    if message_type == "session_unlock_debug":
        return {"type": message_type}
    if message_type not in {"command_confirmation", "command_confirmation_cancel"}:
        return None
    confirmation_id = str(payload.get("confirmation_id") or "")
    if not confirmation_id or len(confirmation_id) > 128:
        return None
    return {
        "type": message_type,
        "confirmation_id": confirmation_id,
    }


async def _start_prediction_bridge(
    websocket: WebSocket,
    session_id: str,
    predict_port: int,
    request: ConnectRequest,
    shared_session: ActiveTerminalSession | None = None,
) -> tuple[asyncio.Task | None, asyncio.StreamWriter | None]:
    try:
        reader, writer = await asyncio.open_connection(PROXY_HOST, predict_port)
        writer.write((json.dumps({"session_id": session_id}) + "\n").encode())
        await writer.drain()
    except OSError as exc:
        log.warning("[%s] prediction bridge unavailable: %s", session_id, exc)
        return None, None

    return asyncio.create_task(
        _prediction_to_websocket(reader, websocket, request, shared_session)
    ), writer


async def _prediction_to_websocket(
    reader: asyncio.StreamReader,
    websocket: WebSocket,
    request: ConnectRequest,
    shared_session: ActiveTerminalSession | None = None,
) -> None:
    last_review = {}
    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            payload = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {"type": "prediction_raw", "data": line.decode("utf-8", errors="replace")}
        if payload.get("type") == "operation_review":
            payload = advisory_review(payload)
            if stale_review_update(payload, last_review):
                continue
            last_review = payload
        if shared_session:
            await shared_session.record_review(payload)
        _record_recent_operation(request, payload)
        await websocket.send_json(payload)


def _record_recent_operation(request: ConnectRequest, payload: dict[str, Any]) -> None:
    """Translate real review-channel events into the latest-device activity summary."""
    result = ""
    risk = False
    operated_at = _utc_now()
    if payload.get("type") == "operation_review":
        payload = advisory_review(payload)
        history = payload.get("history") if isinstance(payload.get("history"), list) else []
        latest = history[-1] if history and isinstance(history[-1], dict) else {}
        operated_at = str(latest.get("ts") or payload.get("reviewed_at") or operated_at)
        status = str(payload.get("status") or "idle")
        command_status = str(latest.get("status") or "")
        review_risk = str(current_command_review(payload).get("risk") or "unknown")
        if command_status == "cancelled":
            result, risk = "操作已取消", review_risk in {"medium", "high", "critical"}
        elif status == "queued":
            result = "正在智能审查"
        elif status == "error":
            result, risk = "智能审查未完成", True
        elif status == "ready":
            if review_risk == "critical":
                result, risk = "发现严重风险", True
            elif review_risk == "high":
                result, risk = "发现高风险", True
            elif review_risk == "medium":
                result, risk = "发现需关注风险", True
            elif review_risk == "unknown":
                result, risk = "风险待核验", True
            else:
                result = "未发现风险"
    elif payload.get("type") == "alert":
        if payload.get("action") == "confirm":
            result, risk = "高风险操作等待确认", True
        else:
            result, risk = "已拦截高风险操作", True
    if not result:
        return
    try:
        RecentConnectionRepository().record_operation(
            protocol=request.protocol,
            host=request.host,
            port=request.port,
            operated_at=operated_at,
            result=result,
            risk=risk,
        )
    except Exception as exc:
        log.warning("最近操作结果写入失败: %s", exc)


async def _wait_closed(writer: asyncio.StreamWriter) -> None:
    try:
        await writer.wait_closed()
    except Exception:
        pass


def _require_loopback_agent(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        client_address = ipaddress.ip_address(host)
        configured_address = ipaddress.ip_address(GATEWAY_HOST)
        if client_address.is_loopback or (
            not configured_address.is_unspecified and client_address == configured_address
        ):
            return
    except ValueError:
        pass
    raise HTTPException(status_code=403, detail="终端内部工具只允许本机 Agent 调用")


def _require_active_terminal(session_id: str) -> ActiveTerminalSession:
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="终端会话标识无效")
    session = _active_terminal_sessions.get(session_id)
    if not session or not session.connected:
        raise HTTPException(status_code=404, detail="终端会话不存在或已经断开")
    return session


def _require_connected_device_scope(device_id: str | None) -> None:
    """Only expose device-bound assistant history while that device is connected."""
    if device_id is None:
        return
    if any(
        session.connected and session.device_id == device_id
        for session in _active_terminal_sessions.values()
    ):
        return
    raise HTTPException(status_code=409, detail="设备连接成功后才能访问该设备的助手会话")


def _read_terminal_transcript(
    session_id: str, cursor: int, max_characters: int
) -> dict[str, Any]:
    path = _audit_dir() / f"{session_id}.jsonl"
    if not path.exists():
        return {"session_id": session_id, "cursor": cursor, "content": "", "events": []}
    events: list[dict[str, Any]] = []
    content_parts: list[str] = []
    next_cursor = cursor
    with path.open("r", encoding="utf-8") as file:
        for index, line in enumerate(file):
            if index < cursor:
                continue
            next_cursor = index + 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = str(record.get("event") or "")
            text = ""
            if event in {"command", "blocked_command", "command_confirmation_cancelled"}:
                command = str(record.get("command") or "").strip()
                if command:
                    label = {
                        "command": "命令",
                        "blocked_command": "已阻断命令",
                        "command_confirmation_cancelled": "已取消命令",
                    }[event]
                    text = f"\n[{label}] {command}\n"
            elif event == "terminal_output" and record.get("data_b64"):
                try:
                    raw = base64.b64decode(str(record["data_b64"]), validate=True)
                    text = _plain_terminal_text(raw.decode("utf-8", errors="replace"))
                except (ValueError, TypeError):
                    text = ""
            elif event == "terminal_input" and record.get("redacted"):
                text = "\n[敏感输入已隐藏]\n"
            if not text:
                continue
            events.append({"cursor": next_cursor, "event": event, "text": text})
            content_parts.append(text)
            if sum(len(item) for item in content_parts) >= max_characters:
                break
    content = "".join(content_parts)
    return {
        "session_id": session_id,
        "cursor": next_cursor,
        "content": content[:max_characters],
        "events": events,
    }


def _audit_dir() -> Path:
    return Path(AUDIT_DIR).expanduser().resolve()


def _iter_audit_csv(audit_dir: Path | None = None) -> Iterator[str]:
    """Yield an Excel-friendly CSV containing every JSONL audit record."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")

    def emit(values: list[Any]) -> str:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow([_safe_csv_value(value) for value in values])
        return buffer.getvalue()

    yield "\ufeff" + emit(
        ["账号", "时间", "会话 ID", "目标", "协议", "事件", "命令", "风险等级", "说明", "内容", "详情"]
    )

    directory = audit_dir or _audit_dir()
    if not directory.exists():
        return

    for path in sorted(directory.glob("*.jsonl")):
        context: dict[str, Any] = {
            "session_id": path.stem,
            "account": "历史记录",
            "target": "",
            "protocol": "",
        }
        try:
            file = path.open("r", encoding="utf-8")
        except OSError as exc:
            log.warning("cannot open audit log %s: %s", path, exc)
            continue
        with file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    record = {"event": "invalid_record", "reason": line}
                if not isinstance(record, dict):
                    record = {"event": "invalid_record", "reason": record}
                for key in ("session_id", "account", "target", "protocol"):
                    if record.get(key) not in (None, ""):
                        context[key] = record[key]
                yield emit(
                    [
                        _operator_display_name(record.get("account", context["account"])),
                        record.get("ts", ""),
                        record.get("session_id", context["session_id"]),
                        record.get("target", context["target"]),
                        record.get("protocol", context["protocol"]),
                        record.get("event", ""),
                        record.get("command", ""),
                        record.get("level", ""),
                        record.get("reason", ""),
                        record.get("data", record.get("data_b64", "")),
                        record.get("details", ""),
                    ]
                )


def _safe_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    # Prevent a command or remark from becoming an Excel formula when the CSV
    # is opened on Windows. Leading whitespace must be considered as well.
    stripped = text.lstrip()
    if text.startswith(("\t", "\r", "\n")) or stripped.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _operator_display_name(value: Any) -> str:
    username = str(value or "")
    if username.lower() in {"admin", "opsadmin"}:
        return "运维管理员"
    if username.lower() == "superadmin":
        return "系统管理员"
    return username


def _is_safe_session_id(session_id: str) -> bool:
    return bool(session_id) and all(char.isalnum() or char in "-_" for char in session_id)


def _target_from_request(target: TargetPreset) -> Target:
    return Target(
        id=target.id,
        protocol=target.protocol,
        device_type=target.device_type,
        device_profile=target.device_profile,
        host=target.host,
        port=target.port,
        remark=target.remark,
        status=target.status,
    )


def _target_to_response(target: Target) -> TargetPreset:
    return TargetPreset(
        id=target.id,
        protocol=target.protocol,
        device_type=target.device_type,
        device_profile=target.device_profile,
        host=target.host,
        port=target.port,
        remark=target.remark,
        status=target.status,
    )


def _device_from_request(payload: DeviceWriteRequest, target_id: str = "") -> Target:
    return Target(
        id=target_id,
        protocol=payload.protocol,
        device_type=payload.device_type,
        device_profile=payload.device_profile,
        host=payload.address,
        port=payload.port,
        remark=payload.remark.strip(),
        notes=payload.notes.strip(),
        status="saved",
        username=payload.username.strip() if payload.username else None,
        last_connected_at=None,
    )


def _connection_to_response(
    target: Target,
    websocket_url: str | None = None,
    session_id: str | None = None,
    device_id: str | None = None,
    status: str | None = None,
) -> ConnectionResponse:
    return ConnectionResponse(
        id=target.id or session_id or "",
        address=target.host,
        port=target.port,
        protocol=target.protocol,
        device_type=target.device_type,
        device_profile=target.device_profile,
        remark=target.remark,
        notes=target.notes,
        username=target.username,
        status=status or target.status,
        last_connected_at=target.last_connected_at,
        websocket_url=websocket_url,
        session_id=session_id,
        device_id=device_id,
    )


def _recent_connection_to_response(
    recent: RecentConnection,
) -> RecentConnectionResponse:
    return RecentConnectionResponse(
        id=recent.id,
        device_id=recent.device_id,
        address=recent.host,
        port=recent.port,
        protocol=recent.protocol,
        device_type=recent.device_type,
        device_profile=recent.device_profile,
        remark=recent.remark,
        notes="",
        username=recent.username,
        last_opened_at=recent.last_opened_at,
        last_operation_at=recent.last_operation_at,
        operation_result=recent.last_operation_result,
        operation_risk=recent.operation_risk,
    )


def _validate_connection_endpoint(protocol: str, address: str, port: int) -> str:
    normalized_protocol = str(protocol or "").lower()
    if normalized_protocol == "console":
        return validate_console_path(address)
    if port > 65_535:
        raise ValueError("网络端口必须是 1 到 65535 之间的整数")
    return _validate_ipv4_text(address)


def _validate_ipv4_text(value: str) -> str:
    text = value.strip()
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise ValueError("请输入有效的完整 IPv4 地址") from exc
    if not isinstance(address, ipaddress.IPv4Address):
        raise ValueError("目前仅支持 IPv4 地址")
    return str(address)


async def _prepare_and_verify_target(
    payload: ConnectionCreateRequest,
    username: str | None,
) -> None:
    if payload.protocol == "console":
        try:
            await asyncio.to_thread(
                validate_console_path,
                payload.address,
                require_connected=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return
    try:
        await asyncio.to_thread(prepare_target_network, payload.address)
    except NetworkPreparationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await asyncio.to_thread(_verify_target_connection, payload, username)


def _probe_target_endpoint(target: Target) -> bool:
    if target.protocol == "console":
        try:
            validate_console_path(target.host, require_connected=True)
            return True
        except ValueError:
            return False
    try:
        prepare_target_network(target.host)
        with socket.create_connection((target.host, target.port), timeout=1.5):
            return True
    except (NetworkPreparationError, OSError, TimeoutError):
        return False


def _verify_target_connection(request: ConnectionCreateRequest, username: str | None) -> None:
    if request.protocol == "ssh":
        _verify_ssh_connection(request, username)
        return
    if request.protocol == "telnet":
        _verify_tcp_connection(request.address, request.port)
        return
    if request.protocol == "console":
        return
    raise HTTPException(status_code=400, detail=f"不支持的协议：{request.protocol}")


def _verify_ssh_connection(request: ConnectionCreateRequest, username: str | None) -> None:
    if not username:
        raise HTTPException(status_code=400, detail="SSH 连接必须填写用户名")
    if not request.target_password:
        raise HTTPException(status_code=400, detail="SSH 连接必须填写登录密码")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=request.address,
            port=request.port,
            username=username,
            password=request.target_password,
            timeout=CONNECT_TIMEOUT,
            banner_timeout=CONNECT_TIMEOUT,
            auth_timeout=CONNECT_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
        )
        channel = ssh.invoke_shell(term="xterm", width=96, height=28)
        channel.close()
    except paramiko.AuthenticationException as exc:
        raise HTTPException(
            status_code=401,
            detail="SSH 用户名或密码错误，请重新输入",
        ) from exc
    except Exception as exc:
        log.warning("SSH target connection failed: %s", exc)
        raise HTTPException(status_code=502, detail="SSH 连接目标失败，请检查网线、地址和端口") from exc
    finally:
        ssh.close()


def _verify_tcp_connection(host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            return
    except OSError as exc:
        log.warning("TCP target connection failed: %s", exc)
        raise HTTPException(status_code=502, detail="TCP 连接目标失败，请检查网线、地址和端口") from exc


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _agent_http_error(status_code: int, body: bytes) -> HTTPException:
    detail = "Agent 请求失败"
    try:
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict) and parsed.get("detail"):
            detail = str(parsed["detail"])
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    return HTTPException(status_code=status_code, detail=detail)


async def _request_agent(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout or AGENT_TIMEOUT, connect=5.0)
        ) as client:
            response = await client.request(method, f"{AGENT_URL}{path}", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Agent 服务暂时不可用") from exc
    if response.status_code >= 400:
        raise _agent_http_error(response.status_code, response.content)
    return response


async def _request_agent_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    response = await _request_agent(method, path, payload, timeout=timeout)
    try:
        result = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Agent 返回了无法识别的结果") from exc
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Agent 返回了无法识别的结果")
    return result


async def _stream_agent_response(path: str, payload: dict[str, Any]) -> StreamingResponse:
    client = httpx.AsyncClient(timeout=httpx.Timeout(AGENT_TIMEOUT, connect=5.0))
    try:
        request = client.build_request(
            "POST",
            f"{AGENT_URL}{path}",
            json=payload,
            headers={"Accept": "text/event-stream"},
        )
        response = await client.send(request, stream=True)
        if response.status_code >= 400:
            detail = await response.aread()
            status_code = response.status_code
            await response.aclose()
            await client.aclose()
            raise _agent_http_error(status_code, detail)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=503, detail="Agent 服务暂时不可用") from exc

    async def events():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _create_connection_websocket_url(
    request: Request,
    connect_request: ConnectRequest,
) -> tuple[str, str]:
    _cleanup_pending_ws_sessions()
    token = secrets.token_urlsafe(24)
    _pending_ws_sessions[token] = (time.monotonic() + CONNECTION_WS_TTL, connect_request)
    scheme = "wss" if request.url.scheme == "https" else "ws"
    websocket_url = str(request.url_for("connection_terminal_session", token=token)).replace(
        f"{request.url.scheme}://",
        f"{scheme}://",
        1,
    )
    return websocket_url, token


def _pop_pending_ws_session(token: str) -> ConnectRequest | None:
    _cleanup_pending_ws_sessions()
    item = _pending_ws_sessions.pop(token, None)
    if not item:
        return None
    expires_at, request = item
    if expires_at < time.monotonic():
        return None
    return request


def _cleanup_pending_ws_sessions() -> None:
    now = time.monotonic()
    expired = [
        token
        for token, (expires_at, _) in _pending_ws_sessions.items()
        if expires_at < now
    ]
    for token in expired:
        _pending_ws_sessions.pop(token, None)


def _read_session_summary(path: Path) -> dict[str, Any] | None:
    first: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    command_count = 0

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            first = first or record
            last = record
            if record.get("event") == "command":
                command_count += 1

    if not first:
        return None

    return {
        "session_id": first.get("session_id", path.stem),
        "target": first.get("target"),
        "protocol": first.get("protocol"),
        "started_at": first.get("ts"),
        "ended_at": last.get("ts") if last else None,
        "last_event": last.get("event") if last else None,
        "command_count": command_count,
    }


@app.get("/", include_in_schema=False)
async def web_index() -> FileResponse:
    return _web_file_response("index.html")


@app.get("/{path:path}", include_in_schema=False)
async def web_static_or_index(path: str) -> FileResponse:
    first_segment = path.split("/", 1)[0]
    if first_segment in {"api", "ws", "health", "docs", "redoc", "openapi.json"}:
        raise HTTPException(status_code=404, detail="未找到页面")
    return _web_file_response(path)


def _web_file_response(path: str) -> FileResponse:
    dist_dir = WEB_DIST_DIR.expanduser().resolve()
    index_path = dist_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail=f"未找到网页资源：{dist_dir}")

    requested_path = (dist_dir / path).resolve()
    if _is_child_path(requested_path, dist_dir) and requested_path.is_file():
        return FileResponse(requested_path)
    return FileResponse(index_path)


def _is_child_path(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
