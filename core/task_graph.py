"""
AI Company — Task Graph（任务 DAG 数据结构）

Planner 生成的有向无环图，定义"应该做什么"。
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from enum import Enum


class TaskStatus(str, Enum):
    """任务状态机（完整闭环）

    PENDING → ASSIGNED → RUNNING → REPORTED_DONE → COMPLETED
                                    ↘ FAILED → RUNNING (重试)
    RUNNING ↔ BLOCKED (BLK / DIR 解除)
    """

    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    BLOCKED = "blocked"
    REPORTED_DONE = "reported_done"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskSpec(BaseModel):
    """Planner 输出的单个任务规范"""

    id: str
    description: str
    input: str = ""
    output_contract: str = ""
    priority: str = "P2"
    depends_on: List[str] = Field(default_factory=list)
    required_capabilities: List[str] = Field(default_factory=list)
    assigned_agent: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    rework_count: int = 0
    last_fail_reason: str = ""


class TaskGraph:
    """任务 DAG"""

    def __init__(self):
        self.tasks: Dict[str, TaskSpec] = {}

    def add_task(self, task: TaskSpec):
        self.tasks[task.id] = task

    def remove_task(self, task_id: str):
        self.tasks.pop(task_id, None)

    def get_ready_tasks(self) -> List[TaskSpec]:
        """返回所有依赖已 COMPLETED 且状态为 PENDING 的任务"""
        ready = []
        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            if self._dependencies_satisfied(task):
                ready.append(task)
        return ready

    def update_status(self, task_id: str, status: TaskStatus):
        """更新任务状态"""
        if task_id in self.tasks:
            self.tasks[task_id].status = status

    def assign_agent(self, task_id: str, agent_id: str):
        """分配 Agent 给任务"""
        if task_id in self.tasks:
            self.tasks[task_id].assigned_agent = agent_id
            self.tasks[task_id].status = TaskStatus.ASSIGNED

    def is_complete(self) -> bool:
        """所有任务 COMPLETED 返回 True"""
        if not self.tasks:
            return False
        return all(t.status == TaskStatus.COMPLETED for t in self.tasks.values())

    def get_failed_tasks(self) -> List[TaskSpec]:
        """获取所有失败的任务"""
        return [t for t in self.tasks.values() if t.status == TaskStatus.FAILED]

    def get_running_tasks(self) -> List[TaskSpec]:
        """获取所有运行中的任务"""
        return [t for t in self.tasks.values() if t.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED)]

    def get_blocked_tasks(self) -> List[TaskSpec]:
        """获取所有阻塞的任务"""
        return [t for t in self.tasks.values() if t.status == TaskStatus.BLOCKED]

    def has_cycles(self) -> bool:
        """检测 DAG 中是否有环（在添加任务前检测）"""
        visited = set()
        rec_stack = set()

        def dfs(node_id):
            if node_id in rec_stack:
                return True  # 有环
            if node_id in visited:
                return False
            visited.add(node_id)
            rec_stack.add(node_id)
            if node_id in self.tasks:
                for dep in self.tasks[node_id].depends_on:
                    if dfs(dep):
                        return True
            rec_stack.discard(node_id)
            return False

        for task_id in self.tasks:
            if dfs(task_id):
                return True
        return False

    def progress(self) -> dict:
        """返回任务进度统计"""
        total = len(self.tasks)
        if total == 0:
            return {"total": 0, "completed": 0, "percent": 0}
        completed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED)
        return {
            "total": total,
            "completed": completed,
            "failed": sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED),
            "running": sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING),
            "blocked": sum(1 for t in self.tasks.values() if t.status == TaskStatus.BLOCKED),
            "percent": round(completed / total * 100, 1),
        }

    def to_dict(self) -> dict:
        return {
            "tasks": [t.model_dump() for t in self.tasks.values()]
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskGraph":
        graph = cls()
        for task_data in data.get("tasks", []):
            task = TaskSpec(**task_data)
            graph.add_task(task)
        if graph.has_cycles():
            raise ValueError("Task graph contains cycles!")
        return graph

    def _dependencies_satisfied(self, task: TaskSpec) -> bool:
        """检查任务的所有依赖是否已完成"""
        for dep_id in task.depends_on:
            if dep_id not in self.tasks:
                continue  # 外部依赖视为已满足
            if self.tasks[dep_id].status != TaskStatus.COMPLETED:
                return False
        return True
