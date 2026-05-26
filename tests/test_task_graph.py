"""测试 Task Graph"""
import pytest
from core.task_graph import TaskGraph, TaskSpec, TaskStatus


class TestTaskGraph:
    def test_add_task(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="Task 1"))
        assert "#t1" in tg.tasks

    def test_ready_tasks_no_deps(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="Task 1"))
        ready = tg.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "#t1"

    def test_ready_tasks_with_deps(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="Task 1"))
        tg.add_task(TaskSpec(id="#t2", description="Task 2", depends_on=["#t1"]))
        ready = tg.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "#t1"

    def test_dependencies_block(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="Task 1"))
        tg.add_task(TaskSpec(id="#t2", description="Task 2", depends_on=["#t1"]))
        tg.add_task(TaskSpec(id="#t3", description="Task 3", depends_on=["#t1", "#t2"]))

        ready = tg.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "#t1"

    def test_unlock_after_dep_complete(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="Task 1"))
        tg.add_task(TaskSpec(id="#t2", description="Task 2", depends_on=["#t1"]))

        tg.update_status("#t1", TaskStatus.COMPLETED)
        ready = tg.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "#t2"

    def test_is_complete(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="Task 1"))
        assert not tg.is_complete()
        tg.update_status("#t1", TaskStatus.COMPLETED)
        assert tg.is_complete()

    def test_cycle_detection(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#a", description="A", depends_on=["#b"]))
        tg.add_task(TaskSpec(id="#b", description="B", depends_on=["#a"]))
        assert tg.has_cycles()

    def test_no_cycle(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#a", description="A"))
        tg.add_task(TaskSpec(id="#b", description="B", depends_on=["#a"]))
        tg.add_task(TaskSpec(id="#c", description="C", depends_on=["#b"]))
        assert not tg.has_cycles()

    def test_progress(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="T1"))
        tg.add_task(TaskSpec(id="#t2", description="T2"))
        tg.update_status("#t1", TaskStatus.COMPLETED)
        p = tg.progress()
        assert p["total"] == 2
        assert p["completed"] == 1
        assert p["percent"] == 50.0

    def test_to_from_dict(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="Task 1"))
        tg.add_task(TaskSpec(id="#t2", description="Task 2", depends_on=["#t1"]))
        d = tg.to_dict()
        tg2 = TaskGraph.from_dict(d)
        assert len(tg2.tasks) == 2
        assert "#t2" in tg2.tasks
        assert tg2.tasks["#t2"].depends_on == ["#t1"]

    def test_from_dict_with_cycles_raises(self):
        with pytest.raises(ValueError):
            TaskGraph.from_dict({
                "tasks": [
                    {"id": "#a", "description": "A", "depends_on": ["#b"]},
                    {"id": "#b", "description": "B", "depends_on": ["#a"]},
                ]
            })

    def test_assign_agent(self):
        tg = TaskGraph()
        tg.add_task(TaskSpec(id="#t1", description="Task 1"))
        tg.assign_agent("#t1", "BE")
        assert tg.tasks["#t1"].assigned_agent == "BE"
        assert tg.tasks["#t1"].status == TaskStatus.ASSIGNED
