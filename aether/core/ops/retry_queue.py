"""RetryQueue – persistent retry mechanism with exponential backoff.

Failed delivery attempts are stored in a SQLite database (``retry_queue.db``) in
the project's ``data`` directory.  A background coroutine processes pending rows
and retries the associated adapter.  The queue survives process restarts.
"""

import asyncio
import pathlib
import json
import time
import sqlite3
from typing import Dict, Any

from ..adapters.registry import AdapterRegistry

DB_NAME = "retry_queue.db"


class RetryQueue:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self.project_root = pathlib.Path(__file__).resolve().parents[3]
        self.db_path = self.project_root / "data" / DB_NAME
        self._ensure_db()
        self._task: asyncio.Task | None = None
        self._adapter_registry: AdapterRegistry | None = None

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS retries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adapter TEXT NOT NULL,
                payload TEXT NOT NULL,
                next_attempt REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()

    async def enqueue(self, adapter_name: str, payload: Dict[str, Any]) -> None:
        """Add a failed delivery to the persistent queue.
        ``payload`` is JSON‑serialisable.
        """
        now = time.time()
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO retries (adapter, payload, next_attempt, attempts) VALUES (?, ?, ?, 0)",
            (adapter_name, json.dumps(payload), now),
        )
        conn.commit()
        conn.close()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _process_loop(self) -> None:
        while True:
            await self._process_pending()
            await asyncio.sleep(5)  # poll interval

    async def _process_pending(self) -> None:
        now = time.time()
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, adapter, payload, attempts FROM retries WHERE next_attempt <= ?",
            (now,),
        )
        rows = cur.fetchall()
        for row in rows:
            row_id, adapter_name, payload_json, attempts = row
            payload = json.loads(payload_json)
            # Resolve the adapter instance.
            if not self._adapter_registry:
                print(f"[RetryQueue] adapter registry not injected; skipping retry for row {row_id}")
                continue
            try:
                adapter = self._adapter_registry.get(adapter_name)
                success = await adapter.send_signal(payload)
                if success:
                    cur.execute("DELETE FROM retries WHERE id = ?", (row_id,))
                else:
                    # Schedule next attempt with exponential backoff.
                    attempts += 1
                    delay = min(30 * (2 ** attempts), 3600)  # cap at 1h
                    next_attempt = now + delay
                    cur.execute(
                        "UPDATE retries SET attempts = ?, next_attempt = ? WHERE id = ?",
                        (attempts, next_attempt, row_id),
                    )
            except Exception as exc:
                # Adapter failure – treat as a temporary error.
                attempts += 1
                delay = min(30 * (2 ** attempts), 3600)
                next_attempt = now + delay
                cur.execute(
                    "UPDATE retries SET attempts = ?, next_attempt = ? WHERE id = ?",
                    (attempts, next_attempt, row_id),
                )
                print(f"[RetryQueue] error processing row {row_id}: {exc}")
        conn.commit()
        conn.close()

__all__ = ["RetryQueue"]
