"""Priority-aware, device-profile-aware proxy for local llama.cpp services.

The web gateway and terminal proxy are separate processes.  This small local
HTTP service keeps the operation-review model behind a stable local endpoint.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import BinaryIO, Callable, Literal

from auditbox.device_profiles import LINUX
from auditbox.env import AUDITBOX_INFERENCE_HOST, AUDITBOX_INFERENCE_PORT
from auditbox.inference.prompts import (
    assistant_warmup_payload,
    review_warmup_payload,
)
from auditbox.inference.routing import (
    DEVICE_PROFILE_HEADER,
    enabled_inference_profiles,
    resolve_inference_route,
)

log = logging.getLogger(__name__)

RequestKind = Literal["assistant", "review"]


class _InferenceLease:
    def __init__(self, gate: "_PriorityGate", kind: RequestKind):
        self._gate = gate
        self.kind = kind
        self.cancelled = threading.Event()
        self._cancel_callback: Callable[[], None] | None = None
        self._released = False

    def set_cancel_callback(self, callback) -> None:
        run_now = False
        with self._gate.condition:
            self._cancel_callback = callback
            run_now = self.cancelled.is_set()
        if run_now:
            callback()

    def request_cancel(self) -> None:
        callback = None
        with self._gate.condition:
            if self.cancelled.is_set():
                return
            self.cancelled.set()
            callback = self._cancel_callback
        if callback is not None:
            try:
                callback()
            except OSError:
                pass

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._gate.release(self)


class _PriorityGate:
    """One active inference; assistant waiters always precede review waiters."""

    def __init__(self):
        self.condition = threading.Condition()
        self._active: _InferenceLease | None = None
        self._assistant_waiters = 0
        self._review_waiters = 0

    def acquire(self, kind: RequestKind) -> _InferenceLease:
        started_at = time.monotonic()
        with self.condition:
            if kind == "assistant":
                self._assistant_waiters += 1
                active_review = (
                    self._active
                    if self._active is not None and self._active.kind == "review"
                    else None
                )
            else:
                self._review_waiters += 1
                active_review = None

        if active_review is not None:
            log.info("assistant queued; preempting active operation review")
            active_review.request_cancel()

        with self.condition:
            while self._active is not None or (
                kind == "review" and self._assistant_waiters > 0
            ):
                self.condition.wait()
            if kind == "assistant":
                self._assistant_waiters -= 1
            else:
                self._review_waiters -= 1
            lease = _InferenceLease(self, kind)
            self._active = lease

        wait_seconds = time.monotonic() - started_at
        if wait_seconds >= 0.05:
            log.info("%s inference waited %.3fs for the priority gate", kind, wait_seconds)
        return lease

    def release(self, lease: _InferenceLease) -> None:
        with self.condition:
            if self._active is lease:
                self._active = None
                self.condition.notify_all()

    def snapshot(self) -> dict[str, object]:
        with self.condition:
            return {
                "active": self._active.kind if self._active is not None else None,
                "assistant_waiters": self._assistant_waiters,
                "review_waiters": self._review_waiters,
            }


_gate = _PriorityGate()


class _InferenceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AuditBoxInference/1.0"

    def log_message(self, format: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "scheduler": _gate.snapshot(),
                    "models": {
                        profile: {
                            kind: resolve_inference_route(profile).model_for(kind)
                            for kind in ("assistant", "review")
                        }
                        for profile in enabled_inference_profiles()
                    },
                },
            )
            return
        try:
            route = _route(
                self.path,
                self.headers.get(DEVICE_PROFILE_HEADER, LINUX),
            )
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if route is None or route[1] != "/health":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        kind, upstream_path, backend_url, model = route
        del kind, model
        try:
            result = _request_json(backend_url, upstream_path, None, timeout=3.0)
        except (OSError, ValueError, TimeoutError) as exc:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "unavailable", "detail": str(exc)},
            )
            return
        self._send_json(HTTPStatus.OK, result)

    def do_POST(self) -> None:
        if _parse_route(self.path) is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 2 * 1024 * 1024:
                raise ValueError("invalid request length")
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        try:
            kind, upstream_path, backend_url, model = _route(
                self.path,
                self.headers.get(DEVICE_PROFILE_HEADER, LINUX),
            ) or (None, None, None, None)
            if kind is None:
                raise ValueError("unsupported inference route")
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        # The scheduler owns model selection. Do not let a caller accidentally
        # send the Linux alias to the H3C backend or vice versa.
        payload["model"] = model
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        lease = _gate.acquire(kind)
        try:
            self._forward_inference(
                lease,
                backend_url,
                upstream_path,
                body,
                stream=payload.get("stream") is True,
            )
        finally:
            lease.release()

    def _forward_inference(
        self,
        lease: _InferenceLease,
        backend_url: str,
        upstream_path: str,
        body: bytes,
        *,
        stream: bool,
    ) -> None:
        request = urllib.request.Request(
            f"{backend_url}{upstream_path}",
            data=body,
            headers={
                "Accept": "text/event-stream" if stream else "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=240.0)
        except urllib.error.HTTPError as exc:
            detail = exc.read()
            self._send_bytes(
                exc.code,
                detail,
                exc.headers.get("Content-Type", "application/json"),
            )
            return
        except (OSError, TimeoutError) as exc:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"message": f"local model unavailable: {exc}"}},
            )
            return

        with response:
            if lease.kind == "review":
                lease.set_cancel_callback(response.close)
            if stream:
                self._stream_response(response, lease)
                return
            data = response.read()
            self._send_bytes(
                response.status,
                data,
                response.headers.get("Content-Type", "application/json"),
            )

    def _stream_response(
        self,
        response: BinaryIO,
        lease: _InferenceLease,
    ) -> None:
        self.send_response(getattr(response, "status", HTTPStatus.OK))
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while not lease.cancelled.is_set():
                line = response.readline()
                if not line:
                    break
                self.wfile.write(line)
                self.wfile.flush()
        except (
            AttributeError,
            BrokenPipeError,
            ConnectionResetError,
            OSError,
            ValueError,
        ):
            pass
        finally:
            if lease.cancelled.is_set() and lease.kind == "review":
                event = json.dumps(
                    {
                        "error": {
                            "code": "review_preempted",
                            "message": "operation review yielded to assistant",
                        }
                    },
                    separators=(",", ":"),
                )
                try:
                    self.wfile.write(f"data: {event}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                    pass
            self.close_connection = True

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, data, "application/json; charset=utf-8")

    def _send_bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


def _parse_route(path: str) -> tuple[RequestKind, str] | None:
    if path == "/assistant" or path.startswith("/assistant/"):
        upstream_path = path.removeprefix("/assistant") or "/"
        return "assistant", upstream_path
    if path == "/review" or path.startswith("/review/"):
        upstream_path = path.removeprefix("/review") or "/"
        return "review", upstream_path
    return None


def _route(
    path: str,
    device_profile: str = LINUX,
) -> tuple[RequestKind, str, str, str] | None:
    parsed = _parse_route(path)
    if parsed is None:
        return None
    kind, upstream_path = parsed
    model_route = resolve_inference_route(device_profile)
    return (
        kind,
        upstream_path,
        model_route.backend_url(kind),
        model_route.model_for(kind),
    )


def _request_json(
    backend_url: str,
    path: str,
    payload: dict | None,
    *,
    timeout: float,
) -> dict:
    request = urllib.request.Request(
        f"{backend_url}{path}",
        data=(
            None
            if payload is None
            else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OSError(f"llama.cpp HTTP {exc.code}: {detail[:300]}") from exc
    if not isinstance(result, dict):
        raise ValueError("llama.cpp returned a non-object response")
    return result


def _warm_backend(name: str, backend_url: str, payload: dict) -> None:
    last_error: Exception | None = None
    for attempt in range(30):
        if attempt:
            time.sleep(2)
        try:
            health = _request_json(backend_url, "/health", None, timeout=3.0)
            if health.get("status") != "ok":
                raise OSError("health check is not ready")
            _request_json(
                backend_url,
                "/v1/chat/completions",
                payload,
                timeout=90.0,
            )
        except (OSError, ValueError, TimeoutError) as exc:
            last_error = exc
            continue
        log.info("%s model and business prompt cache warmed", name)
        return
    raise RuntimeError(f"{name} model warmup failed: {last_error}")


def warm_configured_backends() -> None:
    profiles = enabled_inference_profiles()
    for kind, payload_factory in (("assistant", assistant_warmup_payload), ("review", review_warmup_payload)):
        for profile in profiles:
            route = resolve_inference_route(profile)
            _warm_backend(
                f"{profile} {kind}", route.backend_url(kind),
                payload_factory(route.model_for(kind), profile),
            )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    warm_configured_backends()
    server = ThreadingHTTPServer(
        (AUDITBOX_INFERENCE_HOST, AUDITBOX_INFERENCE_PORT),
        _InferenceHandler,
    )
    log.info(
        "priority inference scheduler listening on %s:%d",
        AUDITBOX_INFERENCE_HOST,
        AUDITBOX_INFERENCE_PORT,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
