"""
AI Company — Event Stream（工作池）

Append-Only 事件日志，基于 SQLite。
- 只允许 append，不允许修改或删除
- 支持按 task_id、agent、verb 过滤查询
- 支持监听新事件（用于 Observer）
"""

import aiosqlite
import json
from pathlib import Path
from typing import List, Optional

from protocols.verbs import Event, Verb

DB_PATH = Path("workspace/events.db")


class EventStream:
    """Append-Only 事件日志"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db: Optional[aiosqlite.Connection] = None

    async def init(self):
        """初始化数据库，创建事件表"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(str(self.db_path))
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id        TEXT PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                verb      TEXT NOT NULL,
                agent     TEXT NOT NULL,
                task      TEXT NOT NULL DEFAULT '',
                status    TEXT NOT NULL DEFAULT '',
                payload   TEXT NOT NULL DEFAULT '{}',
                mentions  TEXT NOT NULL DEFAULT '[]'
            )
        """)
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_task ON events(task)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_verb ON events(verb)"
        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)"
        )
        await self.db.commit()

    async def close(self):
        """关闭数据库连接"""
        if self.db:
            await self.db.close()

    async def append(self, event: Event) -> str:
        """写入一条事件，返回 event.id"""
        if not self.db:
            raise RuntimeError("EventStream not initialized. Call init() first.")
        await self.db.execute(
            """INSERT INTO events (id, timestamp, verb, agent, task, status, payload, mentions)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.timestamp,
                event.verb,
                event.agent,
                event.task,
                event.status,
                json.dumps(event.payload, ensure_ascii=False),
                json.dumps(event.mentions, ensure_ascii=False),
            ),
        )
        await self.db.commit()
        return event.id

    async def query(
        self,
        task: Optional[str] = None,
        agent: Optional[str] = None,
        verb: Optional[Verb] = None,
        mentions: Optional[str] = None,
        since_timestamp: Optional[int] = None,
        limit: int = 100,
    ) -> List[Event]:
        """按条件查询事件，按 timestamp 升序返回"""
        if not self.db:
            raise RuntimeError("EventStream not initialized. Call init() first.")
        conditions = []
        params = []

        if task:
            conditions.append("task = ?")
            params.append(task)
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if verb:
            conditions.append("verb = ?")
            params.append(verb.value)
        if mentions:
            conditions.append("mentions LIKE ?")
            params.append(f'%"{mentions}"%')
        if since_timestamp:
            conditions.append("timestamp > ?")
            params.append(since_timestamp)

        where = " AND ".join(conditions) if conditions else "1=1"
        query_str = f"SELECT * FROM events WHERE {where} ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query_str, params)
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def tail(self, n: int = 50) -> List[Event]:
        """返回最新 n 条事件"""
        if not self.db:
            raise RuntimeError("EventStream not initialized. Call init() first.")
        cursor = await self.db.execute(
            "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (n,)
        )
        rows = await cursor.fetchall()
        events = [self._row_to_event(row) for row in rows]
        events.reverse()
        return events

    async def get_task_events(self, task_id: str) -> List[Event]:
        """返回某任务的全部事件历史"""
        return await self.query(task=task_id, limit=1000)

    async def get_last_event_for_task(self, task_id: str, verb: Optional[Verb] = None) -> Optional[Event]:
        """获取某任务的最后一条事件（可指定 verb）"""
        if not self.db:
            raise RuntimeError("EventStream not initialized. Call init() first.")
        if verb:
            cursor = await self.db.execute(
                "SELECT * FROM events WHERE task = ? AND verb = ? ORDER BY timestamp DESC LIMIT 1",
                (task_id, verb.value),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM events WHERE task = ? ORDER BY timestamp DESC LIMIT 1",
                (task_id,),
            )
        row = await cursor.fetchone()
        return self._row_to_event(row) if row else None

    async def clear(self):
        """清空所有事件（新项目启动时调用）"""
        if not self.db:
            raise RuntimeError("EventStream not initialized. Call init() first.")
        await self.db.execute("DELETE FROM events")
        await self.db.commit()

    async def count(self, task: Optional[str] = None,
                    verb: Optional[Verb] = None) -> int:
        """统计事件数量"""
        if not self.db:
            raise RuntimeError("EventStream not initialized. Call init() first.")
        conditions = []
        params = []
        if task:
            conditions.append("task = ?")
            params.append(task)
        if verb:
            conditions.append("verb = ?")
            params.append(verb.value)
        where = " AND ".join(conditions) if conditions else "1=1"
        cursor = await self.db.execute(f"SELECT COUNT(*) FROM events WHERE {where}", params)
        row = await cursor.fetchone()
        return row[0] if row else 0

    def _row_to_event(self, row) -> Event:
        """将数据库行转为 Event 对象"""
        return Event(
            id=row["id"],
            timestamp=row["timestamp"],
            verb=Verb(row["verb"]),
            agent=row["agent"],
            task=row["task"],
            status=row["status"],
            payload=json.loads(row["payload"]),
            mentions=json.loads(row["mentions"]),
        )
