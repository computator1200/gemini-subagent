"""Tiny line-protocol wrapper around `gemini --acp` for real-time steering.

Spawns gemini in ACP (Agent Client Protocol) mode and exposes a simple
line-oriented command interface on its stdin, while streaming agent
progress (thoughts, tool calls, content chunks) to stdout one line at a
time. Designed to be spawned from a Claude Code session via the
`desktop-commander` MCP `start_process` / `interact_with_process` /
`read_process_output` trio, so the parent agent can:

  - SEE what gemini is doing in real time (every session/update event
    surfaces as a single readable line on stdout)
  - SEND steering commands at any moment (PROMPT, CANCEL, STATUS, QUIT)

Commands accepted on stdin (one per line):
  INIT                   handshake + create session (do this first)
  PROMPT <free text>     send a session/prompt
  CANCEL                 send a session/cancel for the active turn
  STATUS                 print current turn state + last update summary
  QUIT                   terminate gemini and exit

By default this auto-approves every session/request_permission with
allow_once, so the wrapper never blocks. Set AUTO_PERMISSION=False to
make it reject-by-default, or edit `_handle_agent_request` to plug in
your own per-tool decision logic — that's the cleanest place to inject
fine-grained mid-turn steering.

Path note: On Windows, the gemini launcher is `gemini.cmd`. Adjust
GEMINI_BIN to match whatever `where gemini` reports for your install.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from queue import Queue, Empty

# UTF-8 stdout/stderr so non-ASCII content from gemini doesn't crash the reader.
# This bites on Windows (cp1252 default) — without it, the first emoji or
# typographic character in an agent_thought_chunk kills the reader thread.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Override with GEMINI_BIN env var if your install lives elsewhere.
GEMINI_BIN = os.environ.get("GEMINI_BIN") or ("gemini.cmd" if os.name == "nt" else "gemini")

GEMINI_ARGS = [
    "--skip-trust",
    "--acp",
    # Even in ACP mode, gemini's own approval_mode affects which tool calls
    # trigger session/request_permission. yolo = no permission asks; default
    # = every tool call surfaces as a permission request the client must
    # answer (a per-tool steering opportunity).
    "--approval-mode", "yolo",
]

AUTO_PERMISSION = True
CWD = os.environ.get("ACP_CWD") or os.getcwd()


class AcpClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            [GEMINI_BIN, *GEMINI_ARGS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._req_id = 0
        self._pending: dict[int, Queue] = {}
        self._lock = threading.Lock()
        self.session_id: str | None = None
        self.active_turn = False
        self.last_update_summary: str | None = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _send(self, msg: dict):
        line = (json.dumps(msg) + "\n").encode("utf-8")
        assert self.proc.stdin is not None
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def _send_request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        rid = self._next_id()
        q: Queue = Queue()
        self._pending[rid] = q
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return q.get(timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    def _send_notification(self, method: str, params: dict):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _read_loop(self):
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                emit(f"[parse-error] {raw[:200]!r}")
                continue
            self._handle(msg)
        emit("[reader] gemini stdout closed")

    def _handle(self, msg: dict):
        if "id" in msg and ("result" in msg or "error" in msg):
            q = self._pending.get(msg["id"])
            if q is not None:
                q.put(msg)
            return
        if "id" in msg and "method" in msg:
            self._handle_agent_request(msg)
            return
        if "method" in msg:
            self._handle_notification(msg)
            return
        emit(f"[unhandled] {msg}")

    def _handle_agent_request(self, msg: dict):
        method = msg["method"]
        params = msg.get("params") or {}
        if method == "session/request_permission":
            tool = (params.get("toolCall") or {}).get("toolName", "?")
            options = params.get("options") or []
            if AUTO_PERMISSION and options:
                pick = next((o for o in options if o.get("kind") == "allow_once"), options[0])
                emit(f"[permission] auto-allowing tool={tool} via {pick.get('optionId')}")
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "result": {"outcome": {"outcome": "selected",
                                                   "optionId": pick["optionId"]}}})
            else:
                pick = next((o for o in options if o.get("kind") == "reject_once"), None)
                if pick is None and options:
                    pick = options[-1]
                emit(f"[permission] rejecting tool={tool} via {pick.get('optionId') if pick else 'none'}")
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "result": {"outcome": {"outcome": "selected",
                                                   "optionId": pick["optionId"] if pick else "reject_once"}}})
        elif method.startswith("fs/"):
            # We declared no fs capability in initialize → reject if asked.
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601, "message": "fs not supported by client"}})
        else:
            emit(f"[agent-request:{method}] {json.dumps(params)[:300]}")
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601, "message": f"method {method} not supported"}})

    def _handle_notification(self, msg: dict):
        method = msg["method"]
        params = msg.get("params") or {}
        if method == "session/update":
            update = params.get("update") or {}
            utype = update.get("sessionUpdate") or update.get("type") or "?"
            summary = self._summarize_update(utype, update)
            self.last_update_summary = summary
            emit(f"[update:{utype}] {summary}")
        else:
            emit(f"[notif:{method}] {json.dumps(params)[:300]}")

    def _summarize_update(self, utype: str, update: dict) -> str:
        # Keep summary ASCII-safe; non-ASCII glyphs in summary delimiters
        # crash Windows cp1252 stdout in some shells.
        if utype in ("content_chunk", "agent_message_chunk", "agent_thought_chunk"):
            content = update.get("content") or {}
            text = content.get("text") if isinstance(content, dict) else None
            if text is None:
                text = json.dumps(content)[:200]
            return text.replace("\n", " | ")[:300]
        if utype == "tool_call":
            tc = update.get("toolCall") or update
            tool = tc.get("toolName", tc.get("title"))
            inp = tc.get("toolInput") or tc.get("rawInput") or {}
            return f"tool={tool} input={json.dumps(inp)[:200]}"
        if utype == "tool_call_update":
            tc = update.get("toolCall") or update
            return f"id={tc.get('toolCallId')} status={tc.get('status')}"
        if utype == "plan":
            entries = (update.get("plan") or {}).get("entries") or []
            return " | ".join(f"[{e.get('status')}] {e.get('content')}" for e in entries)
        return json.dumps(update)[:300]

    # ---- public commands ----

    def initialize(self):
        emit("[init] sending initialize")
        resp = self._send_request("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "claude-acp-bridge", "version": "0.1"},
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
        })
        emit(f"[init] response: {json.dumps(resp.get('result') or resp)[:400]}")
        emit("[init] creating session")
        resp = self._send_request("session/new", {"cwd": CWD, "mcpServers": []})
        self.session_id = (resp.get("result") or {}).get("sessionId")
        emit(f"[init] session_id={self.session_id}")

    def prompt(self, text: str):
        if not self.session_id:
            emit("[prompt] no session — call INIT first")
            return
        emit(f"[prompt] >>> {text[:200]}")
        self.active_turn = True
        rid = self._next_id()
        q: Queue = Queue()
        self._pending[rid] = q
        self._send({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                    "params": {"sessionId": self.session_id,
                               "prompt": [{"type": "text", "text": text}]}})

        def waiter():
            try:
                resp = q.get(timeout=900)
                result = resp.get("result") or resp.get("error")
                emit(f"[prompt-done] {json.dumps(result)[:300]}")
            except Empty:
                emit("[prompt-done] timeout waiting for turn to end")
            finally:
                self._pending.pop(rid, None)
                self.active_turn = False

        threading.Thread(target=waiter, daemon=True).start()

    def cancel(self):
        if not self.session_id:
            emit("[cancel] no session")
            return
        emit("[cancel] sending session/cancel")
        self._send_notification("session/cancel", {"sessionId": self.session_id})

    def quit(self):
        emit("[quit] terminating gemini")
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass


def emit(line: str):
    ts = time.strftime("%H:%M:%S")
    print(f"{ts} {line}", flush=True)


def main():
    client = AcpClient()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        if line == "INIT":
            client.initialize()
        elif line.startswith("PROMPT "):
            client.prompt(line[len("PROMPT "):])
        elif line == "CANCEL":
            client.cancel()
        elif line == "STATUS":
            emit(f"[status] active_turn={client.active_turn} "
                 f"session={client.session_id} last_update={client.last_update_summary}")
        elif line == "QUIT":
            client.quit()
            break
        else:
            emit(f"[unknown-cmd] {line!r}")


if __name__ == "__main__":
    main()
