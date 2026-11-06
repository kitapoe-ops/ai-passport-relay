"""Stdlib HTTP server for the muse relay.

Routes:
  POST /ask       device WAV in, chunked muse frames out (RELAY_TOKEN)
  POST /task      fire-and-forget Cursor automation (RELAY_TOKEN)
  POST /callback  task result webhook (X-Callback-Secret)
  GET  /task/{id} current task record
  GET  /health    {"ok": true, "tasks": N}
  GET  /last      last /ask debug turn
"""

from __future__ import annotations

import json
import logging
import re
import socket
import time
import urllib.parse
import uuid
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from relay.auth import relay_authorized, secret_authorized
from relay.config import (
    MAX_ARTIFACT_URL_CHARS,
    MAX_JSON_BYTES,
    MAX_SUMMARY_CHARS,
    MAX_TASK_INPUT_CHARS,
    MAX_WAV_BYTES,
    Config,
)
from relay.pipeline import TurnLog, iter_ask_frames
from relay.store import TaskStore
from relay.upstream import HttpUpstream, UpstreamError
from relay.wav import validate_wav

log = logging.getLogger("relay")

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_CALLBACK_STATUS = frozenset({"done", "failed"})


class RelayHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ai-passport-relay/0.1"

    def __init__(self, request, client_address, server, *, config: Config, upstream, store: TaskStore, turns: TurnLog):
        self.config = config
        self.upstream = upstream
        self.store = store
        self.turns = turns
        super().__init__(request, client_address, server)

    def setup(self) -> None:
        super().setup()
        self.request.settimeout(180)
        try:
            self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def address_string(self) -> str:
        # Skip reverse DNS; it stalls the request path and is unused.
        return self.client_address[0]

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            self._json(200, {"ok": True, "tasks": self.store.count()})
            return
        if path == "/last":
            self._json(200, self.turns.snapshot())
            return
        if path.startswith("/task/"):
            task_id = urllib.parse.unquote(path[len("/task/") :])
            if not _TASK_ID.fullmatch(task_id):
                self._json(404, {"error": "not found"})
                return
            record = self.store.get(task_id)
            if record is None:
                self._json(404, {"error": "not found"})
                return
            self._json(200, record)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/ask":
            self._ask()
            return
        if path == "/task":
            self._submit_task()
            return
        if path == "/callback":
            self._callback()
            return
        self._json(404, {"error": "not found"})

    def _ask(self) -> None:
        if not self._require_relay():
            return
        body = self._read_body(MAX_WAV_BYTES)
        if body is None:
            return
        try:
            validate_wav(body)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._start_stream()
        frames = iter_ask_frames(body, self.upstream, self.turns)
        try:
            for frame in frames:
                self._write_chunk(frame)
            self._end_chunks()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        finally:
            frames.close()

    def _submit_task(self) -> None:
        if not self._require_relay():
            return
        obj = self._read_json()
        if obj is None:
            return
        if not isinstance(obj, dict) or not isinstance(obj.get("input"), str):
            self._json(400, {"error": "input must be a string"})
            return
        text = obj["input"].strip()
        if not text:
            self._json(400, {"error": "input must be a string"})
            return
        if len(text) > MAX_TASK_INPUT_CHARS:
            self._json(400, {"error": "input too long"})
            return
        try:
            run_uuid = self.upstream.submit_cursor(text)
        except UpstreamError as exc:
            log.warning("task upstream status=%s kind=%s", exc.status, exc.kind)
            if exc.kind == "unconfigured":
                self._json(503, {"error": "cursor automation is not configured"})
            elif exc.token_expired:
                self._json(503, {"error": "upstream busy"})
            else:
                self._json(502, {"error": "upstream failed"})
            return
        task_id = uuid.uuid4().hex
        self.store.upsert(
            {
                "task_id": task_id,
                "run_uuid": run_uuid,
                "status": "submitted",
                "created_ts": time.time(),
            }
        )
        self._json(202, {"task_id": task_id})

    def _callback(self) -> None:
        presented = self.headers.get("X-Callback-Secret")
        if not secret_authorized(presented, self.config.callback_secret):
            self._json(401, {"error": "unauthorized"})
            return
        obj = self._read_json()
        if obj is None:
            return
        if not isinstance(obj, dict):
            self._json(400, {"error": "invalid json"})
            return
        task_id = obj.get("task_id")
        status = obj.get("status")
        summary = obj.get("summary")
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            self._json(400, {"error": "invalid task_id"})
            return
        if status not in _CALLBACK_STATUS:
            self._json(400, {"error": "invalid status"})
            return
        if not isinstance(summary, str) or len(summary) > MAX_SUMMARY_CHARS:
            self._json(400, {"error": "invalid summary"})
            return
        fields: dict = {
            "task_id": task_id,
            "status": status,
            "summary": summary,
            "updated_ts": time.time(),
        }
        if "artifact_url" in obj and obj["artifact_url"] is not None:
            artifact = obj["artifact_url"]
            if not isinstance(artifact, str) or len(artifact) > MAX_ARTIFACT_URL_CHARS:
                self._json(400, {"error": "invalid artifact_url"})
                return
            fields["artifact_url"] = artifact
        existing = self.store.get(task_id)
        if existing is None:
            fields["created_ts"] = fields["updated_ts"]
            fields["run_uuid"] = None
        self.store.upsert(fields)
        self._json(200, {"ok": True})

    def _require_relay(self) -> bool:
        if not relay_authorized(self.headers.get("Authorization"), self.config.relay_token):
            self._json(401, {"error": "unauthorized"})
            return False
        return True

    def _read_body(self, max_len: int) -> bytes | None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            self._json(411, {"error": "content-length required"})
            return None
        try:
            length = int(raw)
        except ValueError:
            self._json(400, {"error": "bad content-length"})
            return None
        if length < 0:
            self._json(400, {"error": "bad content-length"})
            return None
        if length > max_len:
            self._json(413, {"error": "body too large"})
            return None
        return self.rfile.read(length)

    def _read_json(self) -> object | None:
        body = self._read_body(MAX_JSON_BYTES)
        if body is None:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "invalid json"})
            return None

    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()

    def _start_stream(self) -> None:
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()

    def _write_chunk(self, data: bytes) -> None:
        if not data:
            return
        self.wfile.write(f"{len(data):x}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _end_chunks(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def build_server(
    config: Config,
    upstream=None,
    store: TaskStore | None = None,
    turns: TurnLog | None = None,
) -> RelayHTTPServer:
    upstream = upstream if upstream is not None else HttpUpstream(config)
    store = store if store is not None else TaskStore(config.task_store_path)
    turns = turns if turns is not None else TurnLog()
    handler = partial(
        RelayHandler,
        config=config,
        upstream=upstream,
        store=store,
        turns=turns,
    )
    return RelayHTTPServer((config.host, config.port), handler)
