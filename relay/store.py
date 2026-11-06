"""Append-only JSONL task store. The latest line for a task_id wins."""

from __future__ import annotations

import json
import os
import threading
from typing import Any


class TaskStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        records: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("task_id"), str):
                    records.append(obj)
        return records

    def _latest_map(self, records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for record in records:
            latest[record["task_id"]] = record
        return latest

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()

    def upsert(self, fields: dict[str, Any]) -> dict[str, Any]:
        task_id = fields.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id is required")
        with self._lock:
            current = dict(self._latest_map(self._load()).get(task_id, {}))
            current.update(fields)
            current["task_id"] = task_id
            self._append(current)
            return dict(current)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._latest_map(self._load()).get(task_id)
            return dict(record) if record else None

    def count(self) -> int:
        with self._lock:
            return len(self._latest_map(self._load()))
