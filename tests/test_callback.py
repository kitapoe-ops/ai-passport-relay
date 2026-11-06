"""POST /callback is guarded by X-Callback-Secret."""

import json

from relay.auth import const_eq, secret_authorized
from tests.support import http_request


def test_const_eq_uses_compare_digest(monkeypatch):
    import relay.auth as auth

    calls = []
    real = auth.hmac.compare_digest

    def wrapped(left, right):
        calls.append((left, right))
        assert len(left) == len(right)
        return real(left, right)

    monkeypatch.setattr(auth.hmac, "compare_digest", wrapped)
    assert const_eq("same-secret", "same-secret") is True
    assert const_eq("same-secret", "other-secret") is False
    assert const_eq("short", "much-longer-secret") is False
    assert const_eq("whatever", "") is False
    assert len(calls) == 3  # empty expected returns before compare
    assert secret_authorized(None, "same-secret") is False


def test_callback_rejects_bad_secret_and_accepts_good_one(make_server):
    srv = make_server()
    srv.store.upsert(
        {
            "task_id": "task123",
            "run_uuid": "run-1",
            "status": "submitted",
            "created_ts": 10.0,
        }
    )
    body = json.dumps(
        {
            "task_id": "task123",
            "status": "done",
            "summary": "煮好了",
            "artifact_url": "https://example.test/out",
        }
    ).encode("utf-8")

    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/callback",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    assert status == 401
    assert srv.store.get("task123")["status"] == "submitted"

    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/callback",
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-Callback-Secret": "not-the-secret",
        },
    )
    assert status == 401
    assert srv.store.get("task123")["status"] == "submitted"
    assert len(open(srv.config.task_store_path, encoding="utf-8").read().splitlines()) == 1

    status, payload, _headers = http_request(
        "POST",
        srv.base + "/callback",
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-Callback-Secret": "test-callback-secret",
        },
    )
    assert status == 200
    assert json.loads(payload) == {"ok": True}
    record = srv.store.get("task123")
    assert record["status"] == "done"
    assert record["summary"] == "煮好了"
    assert record["artifact_url"] == "https://example.test/out"
    assert record["run_uuid"] == "run-1"
    assert record["created_ts"] == 10.0
    assert isinstance(record["updated_ts"], float)

    status, got, _headers = http_request("GET", srv.base + "/task/task123")
    assert status == 200
    assert json.loads(got)["summary"] == "煮好了"


def test_callback_validation_and_empty_secret(make_server):
    srv = make_server()
    srv.store.upsert(
        {
            "task_id": "task123",
            "run_uuid": "run-9",
            "status": "submitted",
            "created_ts": 1.0,
        }
    )
    headers = {
        "Content-Type": "application/json",
        "X-Callback-Secret": "test-callback-secret",
    }

    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/callback",
        body=json.dumps(
            {"task_id": "task123", "status": "Done", "summary": "x"}
        ).encode(),
        headers=headers,
    )
    assert status == 400
    assert srv.store.get("task123")["status"] == "submitted"

    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/callback",
        body=json.dumps(
            {"task_id": "task123", "status": "failed", "summary": "x" * 501}
        ).encode(),
        headers=headers,
    )
    assert status == 400
    assert srv.store.get("task123")["status"] == "submitted"

    closed = make_server(secret="")
    status, _payload, _headers = http_request(
        "POST",
        closed.base + "/callback",
        body=json.dumps(
            {"task_id": "task123", "status": "done", "summary": "x"}
        ).encode(),
        headers={"X-Callback-Secret": "", "Content-Type": "application/json"},
    )
    assert status == 401


def test_callback_for_unknown_task_is_stored(make_server):
    srv = make_server()
    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/callback",
        body=json.dumps(
            {"task_id": "newtask", "status": "failed", "summary": "nope"}
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Callback-Secret": "test-callback-secret",
        },
    )
    assert status == 200
    record = srv.store.get("newtask")
    assert record["status"] == "failed"
    assert record["summary"] == "nope"
    assert record["run_uuid"] is None
    assert "created_ts" in record
