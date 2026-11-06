"""Local relay fixture. Upstream calls are fakes; the socket stays on localhost."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest

from relay.config import Config
from relay.pipeline import TurnLog
from relay.server import build_server
from relay.store import TaskStore
from tests.support import FakeUpstream


@dataclass
class Running:
    base: str
    host: str
    port: int
    config: Config
    store: TaskStore
    turns: TurnLog
    upstream: FakeUpstream


@pytest.fixture
def make_server(tmp_path):
    running: list[tuple] = []

    def _make(
        upstream: FakeUpstream | None = None,
        token: str = "test-relay-token",
        secret: str = "test-callback-secret",
    ) -> Running:
        fake = upstream if upstream is not None else FakeUpstream()
        cfg = Config(
            stt_url="https://stt.test/api/stt",
            llm_base_url="https://llm.test",
            tts_url="https://tts.test/api/tts",
            llm_model="hkgai-v3",
            cursor_automation_url="https://cursor.test/hook",
            cursor_automation_token="test-cursor-token",
            relay_token=token,
            callback_secret=secret,
            task_store_path=str(tmp_path / f"tasks-{len(running)}.jsonl"),
            host="127.0.0.1",
            port=0,
        )
        store = TaskStore(cfg.task_store_path)
        turns = TurnLog()
        httpd = build_server(cfg, upstream=fake, store=store, turns=turns)
        thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        thread.start()
        host, port = httpd.server_address[:2]
        state = Running(
            base=f"http://{host}:{port}",
            host=host,
            port=port,
            config=cfg,
            store=store,
            turns=turns,
            upstream=fake,
        )
        running.append((httpd, thread, state))
        return state

    yield _make

    for httpd, thread, _state in running:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
