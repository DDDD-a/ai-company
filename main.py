"""
AI Company — 系统入口

主循环：串联所有模块，实现事件流驱动的多 Agent 协作。
支持两种使用方式：
  1. 命令行直接运行：python main.py "需求描述"
  2. 被 console.py 导入调用：create_infrastructure() + run_project_loop()
"""

import asyncio
from dotenv import load_dotenv

from core.event_stream import EventStream
from core.shared_state import SharedState
from core.memory import SharedMemory
from core.task_graph import TaskGraph, TaskStatus
from agents.planner import Planner
from agents.observer import Observer
from agents.director import Director
from agents.hr import HR
from agents.workers.backend import BackendAgent
from agents.workers.frontend import FrontendAgent
from agents.workers.qa import QAAgent
from agents.workers.ops import OpsAgent
from agents.workers.db import DBAgent
from registry.registry import AgentRegistry
from protocols.verbs import Verb, Event
import config

load_dotenv()


async def create_infrastructure():
    """初始化所有基础设施组件，返回组件字典"""
    stream = EventStream(config.EVENTS_DB)
    await stream.init()
    state = SharedState(config.STATE_DIR)
    shared_memory = SharedMemory(config.SHARED_MEMORY_DIR)
    registry = AgentRegistry(config.REGISTRY_PATH)

    return {
        "stream": stream,
        "state": state,
        "shared_memory": shared_memory,
        "registry": registry,
    }


def create_workers(stream, state, project_name: str = "default"):
    """创建所有 Worker Agent 池"""
    return {
        "FE": FrontendAgent(stream, state, project_name=project_name),
        "BE": BackendAgent(stream, state, project_name=project_name),
        "DB": DBAgent(stream, state, project_name=project_name),
        "QA": QAAgent(stream, state, project_name=project_name),
        "OPS": OpsAgent(stream, state, project_name=project_name),
    }


async def plan_project(user_requirement: str, stream, state, shared_memory) -> TaskGraph:
    """调用 Planner 生成任务图"""
    planner = Planner(stream, state, shared_memory)
    task_graph = await planner.plan(user_requirement)
    return task_graph


async def run_project_loop(
    task_graph: TaskGraph,
    stream: EventStream,
    state: SharedState,
    shared_memory: SharedMemory,
    registry: AgentRegistry,
    workers: dict,
    max_iterations: int = 100,
    loop_interval: float = None,
    on_iteration: callable = None,
    on_complete: callable = None,
):
    """
    运行 AI Company 主执行循环。

    参数：
      on_iteration: 可选回调，每轮循环后调用，接收 (iteration, task_graph, stream) 作为参数
      on_complete: 可选回调，项目完成时调用
    返回：最终的 TaskGraph
    """
    if loop_interval is None:
        loop_interval = config.MAIN_LOOP_INTERVAL_SEC

    hr = HR(stream, state, registry, task_graph)
    observer = Observer(stream, task_graph)
    director = Director(stream, state, shared_memory, task_graph)

    # 记录启动时间，过滤掉旧 run 的残留事件
    import time
    start_time_ms = int(time.time() * 1000)

    iteration = 0

    while not task_graph.is_complete() and iteration < max_iterations:
        iteration += 1

        # HR 分配就绪任务
        await hr.assign_ready_tasks()

        # 获取当前的 RUNNING 任务并执行
        running_tasks = task_graph.get_running_tasks()
        for task in running_tasks:
            agent_id = task.assigned_agent
            if agent_id and agent_id in workers:
                spec = task.model_dump()
                # 注入重试上下文
                if task.retry_count > 0:
                    spec["_retry_count"] = task.retry_count
                    spec["_last_fail_reason"] = task.last_fail_reason
                try:
                    await workers[agent_id].run(spec)
                    task_graph.update_status(task.id, TaskStatus.REPORTED_DONE)
                except Exception as e:
                    print(f"  [ERR] {agent_id} {task.id}: {e}")
                    task.retry_count += 1
                    if task.retry_count >= config.MAX_TASK_RETRIES:
                        task_graph.update_status(task.id, TaskStatus.FAILED)
                    else:
                        task_graph.update_status(task.id, TaskStatus.RUNNING)

        # QA 验收 REPORTED_DONE 的任务（只处理本项目启动后的事件）
        done_events = await stream.query(verb=Verb.DON, since_timestamp=start_time_ms, limit=50)
        for event in done_events:
            task = task_graph.tasks.get(event.task)
            if task and task.status == TaskStatus.REPORTED_DONE:
                qa_result = await workers["QA"].validate(task.model_dump(), event)
                if qa_result.get("verdict") == "PASS":
                    task_graph.update_status(task.id, TaskStatus.COMPLETED)
                    task.retry_count = 0  # 重置
                else:
                    task.retry_count += 1
                    task.last_fail_reason = qa_result.get("reason", "")[:200]
                    if task.retry_count < config.MAX_TASK_RETRIES:
                        task_graph.update_status(task.id, TaskStatus.RUNNING)
                        print(f"  [RETRY] {task.id} #{task.retry_count}: {task.last_fail_reason[:80]}")
                    else:
                        task_graph.update_status(task.id, TaskStatus.FAILED)
                        print(f"  [FAIL] {task.id} retries exhausted: {task.last_fail_reason[:80]}")
                        # 上报给 Director 做最终决策
                        await stream.append(Event(
                            verb=Verb.ALERT, agent="SYSTEM", task=task.id, status="MAX_RETRIES",
                            payload={"reason": task.last_fail_reason, "retries": task.retry_count},
                            mentions=["DIRECTOR"],
                        ))

        # Observer 扫描 + Director 干预
        alerts = await observer.scan()
        if alerts:
            alert_types = set(a["type"] for a in alerts)
            print(f"  [ALERT] {', '.join(alert_types)}")
            await director.handle_alerts(alerts)

        # DIR 事件处理：将 DIR 指向的 FAILED 任务重置为 RUNNING（允许 rework）
        dir_events = await stream.query(verb=Verb.DIR, since_timestamp=start_time_ms, limit=20)
        for e in dir_events:
            task = task_graph.tasks.get(e.task)
            if task and task.status == TaskStatus.FAILED:
                if task.rework_count < config.MAX_TASK_RETRIES:
                    task.rework_count += 1
                    task.retry_count = 0
                    task.last_fail_reason = ""
                    task_graph.update_status(task.id, TaskStatus.RUNNING)
                    print(f"  [REWORK] {task.id} rework #{task.rework_count} by DIR")

        # progress
        progress = task_graph.progress()
        print(f"  [{iteration}] {progress['completed']}/{progress['total']} "
              f"({progress['percent']}%) "
              f"run={progress['running']} block={progress['blocked']} fail={progress['failed']}")

        # 外部回调（供 console.py 使用）
        if on_iteration:
            await on_iteration(iteration, task_graph, stream)

        if task_graph.is_complete():
            break

        await asyncio.sleep(loop_interval)

    if on_complete:
        await on_complete(task_graph)

    return task_graph


async def main(user_requirement: str):
    """主入口：接收用户需求，驱动整个 AI Company 完成项目"""

    print(f"  AI Company  {user_requirement[:60]}")
    print()

    # init
    infra = await create_infrastructure()
    stream = infra["stream"]
    state = infra["state"]
    shared_memory = infra["shared_memory"]
    registry = infra["registry"]

    # plan
    print("  planning...")
    task_graph = await plan_project(user_requirement, stream, state, shared_memory)
    print(f"  {len(task_graph.tasks)} tasks generated")
    for task in task_graph.tasks.values():
        deps = f" ← [{', '.join(task.depends_on)}]" if task.depends_on else ""
        print(f"    {task.id} [{task.priority}] {task.description[:60]}{deps}")

    # workers
    workers = create_workers(stream, state)
    print(f"  workers: {', '.join(workers.keys())}")
    print()

    # main loop
    await run_project_loop(task_graph, stream, state, shared_memory, registry, workers)

    # result
    print()
    if task_graph.is_complete():
        print(f"  done — {task_graph.progress()['completed']}/{task_graph.progress()['total']} tasks completed")
    else:
        print(f"  stopped — {task_graph.progress()['percent']}% complete, max iterations reached")

    print()
    for task in task_graph.tasks.values():
        icon = "✓" if task.status == TaskStatus.COMPLETED else "✗" if task.status == TaskStatus.FAILED else "○"
        print(f"  {icon} {task.id} [{task.status.value}] {task.description[:60]}")

    await stream.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        requirement = " ".join(sys.argv[1:])
    else:
        requirement = input("请输入项目需求：")

    asyncio.run(main(requirement))
