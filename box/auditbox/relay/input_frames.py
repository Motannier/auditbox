"""Optional gateway-to-proxy framing; target terminal bytes remain unchanged.

Source travels on the same ordered stream as the bytes, not a racy side channel.
Legacy TCP clients retain raw human input unless framing was negotiated.
"""
import base64
import binascii
import json

INPUT_FRAMING = "source-jsonl-v1"
INPUT_SOURCES = {"human", "guide", "agent"}
MAX_FRAME = 1_048_576


def encode_input(data: bytes, source: str = "human", audit_context: dict | None = None) -> bytes:
    if source not in INPUT_SOURCES:
        raise ValueError("Invalid terminal input source")
    payload = {"source": source, "data": base64.b64encode(data).decode("ascii")}
    if audit_context:
        payload["audit_context"] = audit_context
    return (json.dumps(payload) + "\n").encode()


class InputDecoder:
    def __init__(self, framed: bool = False):
        self.framed = framed
        self.pending = bytearray()

    def feed(self, data: bytes) -> list[tuple[bytes, str]]:
        return [(data, source) for data, source, _ in self.feed_with_context(data)]

    def feed_with_context(self, data: bytes) -> list[tuple[bytes, str, dict]]:
        if not self.framed:
            return [(data, "human", {})]
        self.pending.extend(data)
        frames = []
        while b"\n" in self.pending:
            line, _, rest = self.pending.partition(b"\n")
            if len(line) > MAX_FRAME:
                raise ValueError("Terminal input frame too large")
            self.pending = bytearray(rest)
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict) or payload.get("source") not in INPUT_SOURCES:
                    raise ValueError("Invalid terminal input source")
                decoded = base64.b64decode(payload["data"], validate=True)
            except (TypeError, KeyError, binascii.Error) as exc:
                raise ValueError("Invalid terminal input frame") from exc
            context = payload.get("audit_context") or {}
            if not isinstance(context, dict):
                raise ValueError("Invalid audit context")
            frames.append((decoded, payload["source"], context))
        if len(self.pending) > MAX_FRAME:
            raise ValueError("Terminal input frame too large")
        return frames
