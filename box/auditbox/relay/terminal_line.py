"""Recover the editable command from a remote terminal redraw, not keystrokes."""

from __future__ import annotations

import codecs
import re

from auditbox.relay.command_cleaner import _ESC_RE

# Anchor network prompts at the start: a '>' in a shell command is not a prompt.
_NETWORK_PROMPT_RE = re.compile(r"^(?:<[^<>\r\n]{1,128}>|\[[^\[\]\r\n]{1,128}\])\s*")
_SHELL_PROMPT_RE = re.compile(r"^[^\r\n#>$]{0,256}[#>$]\s*")
_MAX_LINE = 16384


def _prompt_end(text: str) -> int | None:
    match = _NETWORK_PROMPT_RE.match(text) or _SHELL_PROMPT_RE.match(text)
    return match.end() if match else None


def extract_command_from_redraw(text: str) -> str:
    plain = _ESC_RE.sub("", text)
    end = _prompt_end(plain)
    return plain[end:].rstrip() if end is not None else plain.strip()


class TerminalLineSync:
    """Bounded VT line reader for a device redraw or cursor-probe snapshot.

    command is None without a recognized prompt or after an unsupported
    screen movement. Arbitrary output/pager text must not become a command.
    Printable output overwrites cells; BS moves the cursor (it is not DEL).
    """

    def __init__(self) -> None:
        self.reset()

    @property
    def line(self) -> str:
        return extract_command_from_redraw(self._line)

    @property
    def command(self) -> str | None:
        end = _prompt_end(self._line)
        if (not self._reliable or end is None or self._escape_buf is not None
                or self._control_string or self._decoder.getstate()[0]):
            return None
        if self._wrapped_prompt and not self._line.startswith(self._wrapped_prompt):
            return None
        return self._line[end:].rstrip()

    @property
    def redrawn(self) -> bool:
        return self._redrawn

    def reset(self) -> None:
        self._line = ""
        self._cursor = 0
        self._reliable = True
        self._redrawn = False
        self._wrapped_prompt: str | None = None
        self._escape_buf: str | None = None
        self._control_string = False
        self._control_string_escape = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def finalize_pending(self) -> None:
        # Kept for existing callers; CR/CSI now apply immediately.
        pass

    def feed(self, data: bytes) -> None:
        for ch in self._decoder.decode(data):
            self._feed_char(ch)

    def _feed_char(self, ch: str) -> None:
        if self._control_string:
            if ch == "\x07" or (self._control_string_escape and ch == "\\"):
                self._control_string = False
                self._control_string_escape = False
            else:
                self._control_string_escape = ch == "\x1b"
            return
        if self._escape_buf is not None:
            self._consume_escape(ch)
            return
        if ch == "\x1b":
            self._escape_buf = ""
        elif ch == "\r":
            self._cursor = 0
            self._redrawn = True
        elif ch in "\n\v\f":
            end = _prompt_end(self._line)
            if (end is not None and self._line[end:].strip()
                    and (self._wrapped_prompt is None or self._line.startswith(self._wrapped_prompt))):
                self._wrapped_prompt = self._line[:end]
            self._line = ""
            self._cursor = 0
            self._reliable = True
            self._redrawn = True
        elif ch in "\x08\x7f":
            self._cursor = max(0, self._cursor - 1)
        elif ch == "\t":
            self._cursor = min(_MAX_LINE, (self._cursor // 8 + 1) * 8)
        elif ch == "\ufffd" or "\x80" <= ch < "\xa0":
            self._reliable = False
        elif ch >= " ":
            if len(self._line) >= _MAX_LINE or self._cursor >= _MAX_LINE:
                self._reliable = False
                return
            self._line = self._line.ljust(self._cursor)
            self._line = self._line[:self._cursor] + ch + self._line[self._cursor + 1:]
            self._cursor += 1

    def _consume_escape(self, ch: str) -> None:
        assert self._escape_buf is not None
        self._escape_buf += ch
        seq = self._escape_buf
        if seq[0] in "]P_^":
            self._escape_buf = None
            self._control_string = True
        elif seq == "[":
            return
        elif seq[0] in "()*+-./":
            if len(seq) >= 2:
                self._escape_buf = None  # Character-set designation, e.g. ESC ( B.
        elif seq[0] == "[":
            if "@" <= ch <= "~":
                self._escape_buf = None
                self._handle_csi(seq)
            elif len(seq) > 64:
                self._escape_buf = None
                self._reliable = False
        else:
            self._escape_buf = None
            if seq not in {"=", ">"}:
                self._reliable = False

    def _handle_csi(self, seq: str) -> None:
        cmd, args = seq[-1], seq[1:-1]
        if cmd in "mhlsu":  # Colors, modes and cursor save/restore.
            if cmd in "su":
                self._reliable = False
            return
        if not re.fullmatch(r"\d*(?:;\d*)*", args):
            self._reliable = False
            return
        values = [int(value or "0") for value in args.split(";")]
        step = max(1, values[0])
        if cmd == "K":
            if values[0] == 0:
                self._line = self._line[:self._cursor]
            elif values[0] == 1:
                self._line = " " * (self._cursor + 1) + self._line[self._cursor + 1:]
            elif values[0] == 2:
                self._line = ""
        elif cmd == "C":
            self._cursor = min(_MAX_LINE, self._cursor + step)
        elif cmd == "D":
            self._cursor = max(0, self._cursor - step)
        elif cmd == "G":
            self._cursor = min(_MAX_LINE, step - 1)
        elif cmd == "P":
            self._line = self._line[:self._cursor] + self._line[self._cursor + step:]
        elif cmd == "@":
            self._line = (self._line[:self._cursor] + " " * min(step, _MAX_LINE) + self._line[self._cursor:])[:_MAX_LINE]
        elif cmd == "X":
            end = min(len(self._line), self._cursor + step)
            self._line = self._line[:self._cursor] + " " * (end - self._cursor) + self._line[end:]
        else:
            # Never guess from the last physical row of a multiline redraw.
            self._reliable = False
