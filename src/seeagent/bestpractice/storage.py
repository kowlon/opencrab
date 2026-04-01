"""BPStorage — BP 实例的 SQLite 持久化层。

使用 aiosqlite 异步读写，复用 settings.db_full_path 同一数据库文件。
所有 UPDATE 方法均显式设置 updated_at = time.time()。
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS bp_instances (
    instance_id           TEXT PRIMARY KEY,
    bp_id                 TEXT NOT NULL,
    session_id            TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'active',
    run_mode              TEXT NOT NULL DEFAULT 'manual',
    current_subtask_index INTEGER DEFAULT 0,
    created_at            REAL NOT NULL,
    completed_at          REAL,
    suspended_at          REAL,
    context_summary       TEXT DEFAULT '',
    subtask_statuses      TEXT DEFAULT '{}',
    initial_input         TEXT DEFAULT '{}',
    subtask_outputs       TEXT DEFAULT '{}',
    supplemented_inputs   TEXT DEFAULT '{}',
    updated_at            REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bp_inst_session ON bp_instances(session_id);
CREATE INDEX IF NOT EXISTS idx_bp_inst_status  ON bp_instances(status);
CREATE INDEX IF NOT EXISTS idx_bp_inst_bp_id   ON bp_instances(bp_id);
CREATE INDEX IF NOT EXISTS idx_bp_inst_created ON bp_instances(created_at);
"""


class BPStorage:
    """BP 实例 SQLite 持久化层（async）。"""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._initialized = False

    # ── Init ──────────────────────────────────────────────────────

    async def _ensure_init(self, conn: aiosqlite.Connection) -> None:
        if not self._initialized:
            await conn.executescript(_DDL)
            await conn.commit()
            self._initialized = True

    @asynccontextmanager
    async def _conn(self):
        """正确的 aiosqlite 连接上下文管理器。每次调用开新连接，用完自动关闭。"""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await self._ensure_init(conn)
            yield conn

    # ── Full CRUD ─────────────────────────────────────────────────

    async def save_instance(self, snap: Any) -> None:
        """INSERT OR REPLACE 全量写入实例。snap 为 BPInstanceSnapshot。"""
        data = snap.serialize()
        now = time.time()
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO bp_instances (
                    instance_id, bp_id, session_id, status, run_mode,
                    current_subtask_index, created_at, completed_at, suspended_at,
                    context_summary, subtask_statuses, initial_input,
                    subtask_outputs, supplemented_inputs, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["instance_id"],
                    data["bp_id"],
                    data["session_id"],
                    data["status"],
                    data["run_mode"],
                    data["current_subtask_index"],
                    data["created_at"],
                    data.get("completed_at"),
                    data.get("suspended_at"),
                    data.get("context_summary", ""),
                    json.dumps(data.get("subtask_statuses", {}), ensure_ascii=False),
                    json.dumps(data.get("initial_input", {}), ensure_ascii=False),
                    json.dumps(data.get("subtask_outputs", {}), ensure_ascii=False),
                    json.dumps(data.get("supplemented_inputs", {}), ensure_ascii=False),
                    now,
                ),
            )
            await conn.commit()

    async def load_instance(self, instance_id: str) -> dict[str, Any] | None:
        """返回可直接传给 BPInstanceSnapshot.deserialize() 的 dict。"""
        async with self._conn() as conn, conn.execute(
            "SELECT * FROM bp_instances WHERE instance_id = ?", (instance_id,)
        ) as cur:
            row = await cur.fetchone()
            return self._row_to_dict(row) if row else None

    async def load_instances_by_session(self, session_id: str) -> list[dict[str, Any]]:
        """按 session 查询所有实例（含已完成/已取消）。"""
        async with self._conn() as conn, conn.execute(
            "SELECT * FROM bp_instances WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [self._row_to_dict(r) for r in rows]

    async def load_instances_by_status(
        self, status: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """按状态查询。"""
        async with self._conn() as conn, conn.execute(
            "SELECT * FROM bp_instances WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [self._row_to_dict(r) for r in rows]

    async def load_instances_by_bp_id(
        self, bp_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """按 BP 模板 ID 查询。"""
        async with self._conn() as conn, conn.execute(
            "SELECT * FROM bp_instances WHERE bp_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (bp_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [self._row_to_dict(r) for r in rows]

    async def load_instances_by_status_and_bp_id(
        self, status: str, bp_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """按状态 + BP 模板联合查询（SQL 层 WHERE 联合条件 + 分页）。"""
        async with self._conn() as conn, conn.execute(
            "SELECT * FROM bp_instances WHERE status = ? AND bp_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, bp_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [self._row_to_dict(r) for r in rows]

    async def load_all_instances(
        self, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """分页查询所有实例。"""
        async with self._conn() as conn, conn.execute(
            "SELECT * FROM bp_instances ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [self._row_to_dict(r) for r in rows]

    async def delete_instance(self, instance_id: str) -> None:
        """删除实例记录。"""
        async with self._conn() as conn:
            await conn.execute(
                "DELETE FROM bp_instances WHERE instance_id = ?", (instance_id,)
            )
            await conn.commit()

    async def count_instances(
        self,
        session_id: str | None = None,
        status: str | None = None,
        bp_id: str | None = None,
    ) -> int:
        """条件统计。"""
        conditions = []
        params: list[Any] = []
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if bp_id is not None:
            conditions.append("bp_id = ?")
            params.append(bp_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        async with self._conn() as conn, conn.execute(
            f"SELECT COUNT(*) FROM bp_instances {where}", params
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def count_by_status(
        self,
        session_id: str | None = None,
        bp_id: str | None = None,
    ) -> dict[str, int]:
        """按状态分组统计实例数。"""
        result = {"active": 0, "suspended": 0, "completed": 0, "cancelled": 0}
        conditions: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        if bp_id is not None:
            conditions.append("bp_id = ?")
            params.append(bp_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        async with self._conn() as conn, conn.execute(
            f"SELECT status, COUNT(*) FROM bp_instances {where} GROUP BY status",
            params,
        ) as cur:
            for row in await cur.fetchall():
                if row[0] in result:
                    result[row[0]] = row[1]
        return result

    # ── Field-level updates ───────────────────────────────────────

    async def update_instance_status(
        self,
        instance_id: str,
        status: str,
        completed_at: float | None = None,
        suspended_at: float | None = None,
    ) -> None:
        """更新状态标量字段（suspend/resume/complete/cancel）。"""
        now = time.time()
        async with self._conn() as conn:
            await conn.execute(
                """
                UPDATE bp_instances
                SET status = ?, completed_at = ?, suspended_at = ?, updated_at = ?
                WHERE instance_id = ?
                """,
                (status, completed_at, suspended_at, now, instance_id),
            )
            await conn.commit()

    async def update_subtask_progress(
        self,
        instance_id: str,
        current_subtask_index: int,
        subtask_statuses: dict[str, str],
    ) -> None:
        """更新进度（current_subtask_index + subtask_statuses）。"""
        now = time.time()
        async with self._conn() as conn:
            await conn.execute(
                """
                UPDATE bp_instances
                SET current_subtask_index = ?, subtask_statuses = ?, updated_at = ?
                WHERE instance_id = ?
                """,
                (
                    current_subtask_index,
                    json.dumps(subtask_statuses, ensure_ascii=False),
                    now,
                    instance_id,
                ),
            )
            await conn.commit()

    async def update_subtask_output(
        self, instance_id: str, subtask_id: str, output: dict[str, Any]
    ) -> None:
        """合并更新 subtask_outputs 中指定 key。读取现有值，合并后写回。"""
        now = time.time()
        async with self._conn() as conn:
            async with conn.execute(
                "SELECT subtask_outputs FROM bp_instances WHERE instance_id = ?",
                (instance_id,),
            ) as cur:
                row = await cur.fetchone()
                if row is None:
                    return
                existing: dict[str, Any] = json.loads(row[0] or "{}")
            existing[subtask_id] = output
            await conn.execute(
                """
                UPDATE bp_instances
                SET subtask_outputs = ?, updated_at = ?
                WHERE instance_id = ?
                """,
                (json.dumps(existing, ensure_ascii=False), now, instance_id),
            )
            await conn.commit()

    async def update_context_summary(
        self, instance_id: str, summary: str
    ) -> None:
        """更新 context_summary（switch 挂起压缩完成后调用）。"""
        now = time.time()
        async with self._conn() as conn:
            await conn.execute(
                """
                UPDATE bp_instances
                SET context_summary = ?, updated_at = ?
                WHERE instance_id = ?
                """,
                (summary, now, instance_id),
            )
            await conn.commit()

    async def update_supplemented_input(
        self, instance_id: str, subtask_id: str, data: dict[str, Any]
    ) -> None:
        """合并更新 supplemented_inputs 中指定 key（bp_answer 补充参数）。"""
        now = time.time()
        async with self._conn() as conn:
            async with conn.execute(
                "SELECT supplemented_inputs FROM bp_instances WHERE instance_id = ?",
                (instance_id,),
            ) as cur:
                row = await cur.fetchone()
                if row is None:
                    return
                existing: dict[str, Any] = json.loads(row[0] or "{}")
            existing[subtask_id] = data
            await conn.execute(
                """
                UPDATE bp_instances
                SET supplemented_inputs = ?, updated_at = ?
                WHERE instance_id = ?
                """,
                (json.dumps(existing, ensure_ascii=False), now, instance_id),
            )
            await conn.commit()

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        """将 SQLite Row 转换为 BPInstanceSnapshot.deserialize() 兼容的 dict。"""
        d = dict(row)
        for key in ("subtask_statuses", "initial_input", "subtask_outputs", "supplemented_inputs"):
            val = d.get(key)
            d[key] = json.loads(val) if val else {}
        return d
