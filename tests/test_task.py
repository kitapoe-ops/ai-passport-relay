"""POST /task fires the Cursor webhook and stores a submitted record."""

import json
import re

from relay.upstream import UpstreamError
from tests.support import FakeUpstream, expired, http_request


def test_post_task_stores_run_and_is_readable(make_server):
    fake = FakeUpstream(run_uuid="uuid-123")
    srv = make_server(fake)
    body = json.dumps({"input": "  summarise today  "}).encode("utf-8")

    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/task",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    assert status == 401
    assert fake.cursor_inputs == []

    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/task",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer wrong",
        },
    )
    assert status == 401

    status, payload, headers = http_request(
        "POST",
        srv.base + "/task",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "bearer test-relay-token",
        },
    )
    assert status == 202
    assert headers["content-type"].startswith("application/json")
    parsed = json.loads(payload)
    assert re.fullmatch(r"[0-9a-f]{32}", parsed["task_id"])
    assert fake.cursor_inputs == ["summarise today"]

    record = srv.store.get(parsed["task_id"])
    assert record["run_uuid"] == "uuid-123"
    assert record["status"] == "submitted"
    assert isinstance(record["created_ts"], float)
    assert "summary" not in record

    status, got, _headers = http_request("GET", srv.base + "/task/" + parsed["task_id"])
    assert status == 200
    assert json.loads(got)["run_uuid"] == "uuid-123"

    status, health, _headers = http_request("GET", srv.base + "/health")
    assert json.loads(health) == {"ok": True, "tasks": 1}

    status, _missing, _headers = http_request("GET", srv.base + "/task/does-not-exist")
    assert status == 404


def test_task_rejects_bad_input_and_upstream_errors(make_server):
    fake = FakeUpstream(cursor_error=expired("cursor"))
    srv = make_server(fake)
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer test-relay-token",
    }
    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/task",
        body=b'{"input":""}',
        headers=headers,
    )
    assert status == 400

    status, _payload, _headers = http_request(
        "POST",
        srv.base + "/task",
        body=b'{"input": 1}',
        headers=headers,
    )
    assert status == 400

    status, payload, _headers = http_request(
        "POST",
        srv.base + "/task",
        body=b'{"input":"go"}',
        headers=headers,
    )
    assert status == 503
    assert json.loads(payload) == {"error": "upstream busy"}
    assert srv.store.count() == 0

    broken = FakeUpstream(cursor_error=UpstreamError("cursor", 500, "nope"))
    srv = make_server(broken)
    status, payload, _headers = http_request(
        "POST",
        srv.base + "/task",
        body=b'{"input":"go"}',
        headers=headers,
    )
    assert status == 502
    assert "nope" not in payload.decode()

    unconfigured = FakeUpstream()

    def refuse(text: str) -> str:
        raise UpstreamError("cursor", 0, "not configured", kind="unconfigured")

    unconfigured.submit_cursor = refuse  # type: ignore[method-assign]
    srv = make_server(unconfigured)
    status, payload, _headers = http_request(
        "POST",
        srv.base + "/task",
        body=b'{"input":"go"}',
        headers=headers,
    )
    assert status == 503
    assert json.loads(payload)["error"] == "cursor automation is not configured"
