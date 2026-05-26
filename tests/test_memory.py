"""测试记忆系统"""
import pytest
import asyncio
import tempfile
import time
from pathlib import Path
from core.memory import Lesson, SharedMemory, PersonalMemory


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestLesson:
    def test_creation(self):
        lesson = Lesson(
            id="L001",
            pattern="test pattern",
            trigger="x > 1",
            action="do Y",
        )
        assert lesson.id == "L001"
        assert lesson.confidence == 0.8
        assert lesson.created_at > 0

    def test_effective_confidence(self):
        lesson = Lesson(
            id="L001",
            pattern="test",
            trigger="x > 1",
            action="fix",
            confidence=0.9,
            decay=0.001,
        )
        now = lesson.created_at
        eff = lesson.effective_confidence(now)
        assert eff > 0.89  # 基本没衰减

        # 100天后
        later = now + 100 * 86400
        eff = lesson.effective_confidence(later)
        assert eff < 0.81  # 0.9 - 0.001*100 = 0.8

    def test_confidence_floor(self):
        lesson = Lesson(id="L003", pattern="test", trigger="x", action="y",
                        confidence=0.5, decay=0.01)
        later = lesson.created_at + 100 * 86400  # 100天后，0.5 - 1.0 = -0.5
        assert lesson.effective_confidence(later) == 0.0


class TestSharedMemory:
    @pytest.mark.asyncio
    async def test_write_and_read(self, temp_dir):
        sm = SharedMemory(temp_dir)
        lesson = Lesson(id="L001", pattern="test", trigger="x", action="y")
        await sm.write_lesson(lesson, written_by="DIRECTOR")

        lessons = await sm.get_lessons()
        assert len(lessons) == 1
        assert lessons[0].id == "L001"

    @pytest.mark.asyncio
    async def test_write_permission_denied(self, temp_dir):
        sm = SharedMemory(temp_dir)
        lesson = Lesson(id="L001", pattern="test", trigger="x", action="y")
        with pytest.raises(AssertionError, match="Director"):
            await sm.write_lesson(lesson, written_by="HR")

    @pytest.mark.asyncio
    async def test_get_relevant_lessons(self, temp_dir):
        sm = SharedMemory(temp_dir)
        await sm.write_lesson(
            Lesson(id="L001", pattern="API conflicts", trigger="CON>2",
                   action="add contract phase"),
            written_by="DIRECTOR",
        )
        await sm.write_lesson(
            Lesson(id="L002", pattern="database migration", trigger="migration failed",
                   action="test first"),
            written_by="DIRECTOR",
        )

        relevant = await sm.get_relevant_lessons("API contract")
        assert len(relevant) >= 1
        assert relevant[0].id == "L001"


class TestPersonalMemory:
    @pytest.mark.asyncio
    async def test_append_and_read(self, temp_dir):
        pm = PersonalMemory("test_agent", temp_dir)
        await pm.append({"task": "#t1", "summary": "Built login API"})
        await pm.append({"task": "#t2", "summary": "Fixed JWT bug"})

        entries = await pm.get_all()
        assert len(entries) == 2
        assert entries[0]["task"] == "#t1"
        assert entries[1]["task"] == "#t2"

    @pytest.mark.asyncio
    async def test_summary(self, temp_dir):
        pm = PersonalMemory("test_agent", temp_dir)
        await pm.append({"task": "#t1", "summary": "Built login API"})

        summary = await pm.get_summary()
        assert "#t1" in summary
        assert "Built login API" in summary
