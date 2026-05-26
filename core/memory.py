"""
AI Company — 记忆系统

三层记忆架构：
  共同记忆（Policy Layer） → 全局持久，跨项目积累，仅 Director 写入
  个人记忆（Agent Memory） → 每个 Agent 私有，自身经验与偏好
"""

from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import json
import time

SHARED_MEMORY_DIR = Path("workspace/memory/shared")
PERSONAL_MEMORY_DIR = Path("workspace/memory/personal")


class Lesson(BaseModel):
    """共同记忆条目（LESSON），仅 Director 可写"""

    id: str
    pattern: str  # 描述触发场景
    trigger: str  # 触发条件（可量化）
    action: str  # 推荐动作
    scope: str = "global"  # global | project_type | domain
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    decay: float = Field(default=0.01, ge=0.0, le=1.0)
    version: int = 1
    created_at: int = 0

    def model_post_init(self, __context):
        if not self.created_at:
            self.created_at = int(time.time())

    def effective_confidence(self, now: Optional[int] = None) -> float:
        """随时间衰减的置信度"""
        elapsed_days = ((now or int(time.time())) - self.created_at) / 86400
        return max(0.0, self.confidence - self.decay * elapsed_days)


class SharedMemory:
    """共同记忆（Policy Layer），仅 Director 可写，Planner / HR 可读"""

    def __init__(self, memory_dir: Optional[Path] = None):
        self.memory_dir = memory_dir or SHARED_MEMORY_DIR

    async def write_lesson(self, lesson: Lesson, written_by: str):
        """写入 LESSON，仅 DIRECTOR 可写"""
        assert written_by == "DIRECTOR", "只有 Director 可写共同记忆"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.memory_dir / f"{lesson.id}.json"
        file_path.write_text(
            json.dumps(lesson.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def get_lessons(self, min_confidence: float = 0.3) -> List[Lesson]:
        """返回置信度高于阈值的 LESSON，按有效置信度降序"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        lessons = []
        now = int(time.time())
        for f in self.memory_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                lesson = Lesson(**data)
                if lesson.effective_confidence(now) >= min_confidence:
                    lessons.append(lesson)
            except (json.JSONDecodeError, Exception):
                continue
        lessons.sort(key=lambda l: l.effective_confidence(now), reverse=True)
        return lessons

    async def get_relevant_lessons(self, context: str) -> List[Lesson]:
        """根据上下文关键词返回相关 LESSON（v1 使用简单关键词匹配）"""
        all_lessons = await self.get_lessons(min_confidence=0.1)
        if not context:
            return all_lessons[:10]
        keywords = set(context.lower().split())
        scored = []
        for lesson in all_lessons:
            score = 0
            text = f"{lesson.pattern} {lesson.trigger} {lesson.action}".lower()
            for kw in keywords:
                if kw in text:
                    score += 1
            if score > 0:
                scored.append((lesson, score))
        scored.sort(key=lambda x: (x[1], x[0].effective_confidence()), reverse=True)
        return [l for l, _ in scored[:10]]

    async def get_lesson_by_id(self, lesson_id: str) -> Optional[Lesson]:
        """按 ID 获取 LESSON"""
        file_path = self.memory_dir / f"{lesson_id}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return Lesson(**data)
        except (json.JSONDecodeError, Exception):
            return None

    async def delete_lesson(self, lesson_id: str, deleted_by: str):
        """删除 LESSON，仅 DIRECTOR 可删"""
        assert deleted_by == "DIRECTOR", "只有 Director 可删除共同记忆"
        file_path = self.memory_dir / f"{lesson_id}.json"
        if file_path.exists():
            file_path.unlink()


class PersonalMemory:
    """Agent 个人记忆（私有，其他角色不可访问）"""

    def __init__(self, agent_id: str, memory_dir: Optional[Path] = None):
        self.agent_id = agent_id
        self.memory_dir = memory_dir or PERSONAL_MEMORY_DIR
        self.path = self.memory_dir / f"{agent_id}.json"

    async def append(self, entry: dict):
        """追加一条记忆"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        entries = await self.get_all()
        entry["timestamp"] = int(time.time())
        entries.append(entry)
        # 只保留最近 200 条
        if len(entries) > 200:
            entries = entries[-200:]
        self.path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def get_all(self) -> list:
        """获取所有记忆条目"""
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    async def get_summary(self, max_entries: int = 20) -> str:
        """返回适合注入系统提示的摘要文本"""
        entries = await self.get_all()
        if not entries:
            return "暂无个人记忆"

        recent = entries[-max_entries:]
        lines = []
        for e in recent:
            task = e.get("task", "?")
            summary = e.get("summary", e.get("content", ""))[:120]
            lines.append(f"- [{task}] {summary}")
        return "个人经验：\n" + "\n".join(lines)

    async def clear(self):
        """清空个人记忆"""
        if self.path.exists():
            self.path.unlink()
