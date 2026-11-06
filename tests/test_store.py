"""JSONL task store roundtrip."""

from relay.store import TaskStore


def test_roundtrip_latest_line_wins(tmp_path):
    path = tmp_path / "nested" / "tasks.jsonl"
    store = TaskStore(str(path))
    created = store.upsert(
        {
            "task_id": "abc",
            "run_uuid": "run-1",
            "status": "submitted",
            "created_ts": 1000.0,
        }
    )
    assert created["status"] == "submitted"
    updated = store.upsert(
        {
            "task_id": "abc",
            "status": "done",
            "summary": "煮好飯",
            "artifact_url": "https://example.test/a",
            "updated_ts": 1001.5,
        }
    )
    assert updated["run_uuid"] == "run-1"
    assert updated["created_ts"] == 1000.0
    assert updated["status"] == "done"
    assert updated["summary"] == "煮好飯"
    assert updated["artifact_url"] == "https://example.test/a"

    reloaded = TaskStore(str(path))
    assert reloaded.get("abc") == updated
    assert reloaded.get("missing") is None
    assert reloaded.count() == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_corrupt_lines_are_skipped(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        "\nnot-json\n"
        '{"task_id":"a","status":"submitted"}\n'
        '{"nope":1}\n'
        '{"task_id":"b","status":"failed","summary":"x"}\n',
        encoding="utf-8",
    )
    store = TaskStore(str(path))
    assert store.count() == 2
    assert store.get("a")["status"] == "submitted"
    assert store.get("b")["summary"] == "x"
