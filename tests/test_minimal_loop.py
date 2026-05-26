"""
最小闭环集成测试（不调用真实 LLM API）

场景：模拟 "创建一个 Hello World API" 的完整流程。
验证：
1. Planner 生成任务图
2. HR 分配任务
3. Worker 执行并写 Event Stream
4. QA 验收
5. 任务状态正确流转
"""

import pytest
import asyncio
import tempfile
from pathlib import Path

from core.event_stream import EventStream
from core.shared_state import SharedState
from core.memory import SharedMemory
from core.task_graph import TaskGraph, TaskSpec, TaskStatus
from protocols.verbs import Event, Verb
from registry.registry import AgentRegistry


@pytest.fixture
def temp_workspace():
    """创建临时工作区"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "state").mkdir()
        (root / "memory" / "shared").mkdir(parents=True)
        (root / "memory" / "personal").mkdir(parents=True)
        yield root


@pytest.mark.asyncio
async def test_full_task_lifecycle(temp_workspace):
    """
    最小闭环测试：不调用 LLM，手动模拟完整任务生命周期。

    ASN → ACK → UPD → DON → VAL(PASS) → COMPLETED
    """
    # 初始化基础设施
    db_path = temp_workspace / "events.db"
    stream = EventStream(db_path)
    await stream.init()

    state = SharedState(temp_workspace / "state")

    # 创建任务图（模拟 Planner 输出）
    task_graph = TaskGraph()
    task_graph.add_task(TaskSpec(
        id="#api_design",
        description="设计 Hello World API 接口",
        input="需求文档",
        output_contract="输出 API 接口文档（含 method, path, response format）",
        priority="P1",
        depends_on=[],
        required_capabilities=["API", "Python"],
    ))

    # 模拟 HR 分配
    task_graph.assign_agent("#api_design", "BE")
    await stream.append(Event(
        verb=Verb.ASN,
        agent="HR",
        task="#api_design",
        status="P1",
        payload={"agent": "BE", "spec": task_graph.tasks["#api_design"].model_dump()},
        mentions=["BE"],
    ))

    # 模拟 BE 执行
    await stream.append(Event(verb=Verb.ACK, agent="BE", task="#api_design", status="ACCEPT"))
    await stream.append(Event(verb=Verb.UPD, agent="BE", task="#api_design", status="50%",
                              payload={"impl": "defined_endpoint"}))
    # BE 将产出物写入 Shared State
    await state.write("outputs/api_design", {
        "content": "GET /hello → {message: 'Hello World'}",
        "agent": "BE"
    }, written_by="BE")
    await stream.append(Event(
        verb=Verb.DON,
        agent="BE",
        task="#api_design",
        status="OK",
        payload={"out": "shared://outputs/api_design"},
    ))

    # 验证 Event Stream 中有完整链路
    events = await stream.get_task_events("#api_design")
    verbs = [e.verb for e in events]
    assert Verb.ASN in verbs
    assert Verb.ACK in verbs
    assert Verb.UPD in verbs
    assert Verb.DON in verbs
    print(f"  Event chain: {' → '.join(v.value for v in verbs)}")

    # 验证 Shared State 中有产出物
    output = await state.read("outputs/api_design")
    assert output is not None
    assert "Hello World" in str(output)
    print(f"  Output: {output['content'][:60]}")

    # 模拟 QA 验收
    task_graph.update_status("#api_design", TaskStatus.REPORTED_DONE)
    await stream.append(Event(
        verb=Verb.VAL,
        agent="QA",
        task="#api_design",
        status="PASS",
        payload={"reason": "API 接口定义完整，验收通过"},
    ))
    task_graph.update_status("#api_design", TaskStatus.COMPLETED)

    # 验证任务完成
    assert task_graph.is_complete()
    assert task_graph.tasks["#api_design"].status == TaskStatus.COMPLETED
    print(f"  Status: {task_graph.tasks['#api_design'].status.value}")

    await stream.close()


@pytest.mark.asyncio
async def test_val_fail_and_retry(temp_workspace):
    """测试 VAL FAIL → 重试 → VAL PASS 流程"""
    db_path = temp_workspace / "events.db"
    stream = EventStream(db_path)
    await stream.init()
    state = SharedState(temp_workspace / "state")

    task_graph = TaskGraph()
    task_graph.add_task(TaskSpec(
        id="#fix_bug",
        description="修复登录 bug",
        output_contract="登录成功率 > 99%",
        priority="P1",
    ))
    task_graph.assign_agent("#fix_bug", "BE")

    # 第一次执行
    await state.write("outputs/fix_bug", {"content": "patch v1"}, written_by="BE")
    await stream.append(Event(verb=Verb.DON, agent="BE", task="#fix_bug",
                              status="OK", payload={"out": "shared://outputs/fix_bug"}))
    task_graph.update_status("#fix_bug", TaskStatus.REPORTED_DONE)

    # QA FAIL
    await stream.append(Event(verb=Verb.VAL, agent="QA", task="#fix_bug",
                              status="FAIL", payload={"reason": "仍有漏洞"}))
    task_graph.update_status("#fix_bug", TaskStatus.RUNNING)

    # 第二次执行（修复）
    await state.write("outputs/fix_bug", {"content": "patch v2 (fixed)"}, written_by="BE")
    await stream.append(Event(verb=Verb.DON, agent="BE", task="#fix_bug",
                              status="OK", payload={"out": "shared://outputs/fix_bug", "retries": 1}))
    task_graph.update_status("#fix_bug", TaskStatus.REPORTED_DONE)

    # QA PASS
    await stream.append(Event(verb=Verb.VAL, agent="QA", task="#fix_bug",
                              status="PASS", payload={"reason": "验收通过"}))
    task_graph.update_status("#fix_bug", TaskStatus.COMPLETED)

    assert task_graph.is_complete()

    # 验证完整事件链：DON → VAL(FAIL) → VAL(PASS)
    events = await stream.get_task_events("#fix_bug")
    val_events = [e for e in events if e.verb == Verb.VAL]
    assert len(val_events) == 2
    assert val_events[0].status == "FAIL"
    assert val_events[1].status == "PASS"
    print(f"  VAL sequence: FAIL → PASS (retry worked)")

    await stream.close()


@pytest.mark.asyncio
async def test_observer_detects_stall(temp_workspace):
    """测试 Observer 检测 stall"""
    from agents.observer import Observer

    db_path = temp_workspace / "events.db"
    stream = EventStream(db_path)
    await stream.init()

    # 模拟一个 RUNNING 但长时间无更新的任务
    await stream.append(Event(verb=Verb.ASN, agent="HR", task="#t1"))
    await stream.append(Event(verb=Verb.ACK, agent="BE", task="#t1"))
    # 没有 UPD 事件 → 应该检测到 stall

    observer = Observer(stream)
    alerts = await observer.scan()
    stall_alerts = [a for a in alerts if a["type"] == "stall"]
    assert len(stall_alerts) >= 1
    print(f"  Stall detected for: {stall_alerts[0]['targets']}")

    await stream.close()


@pytest.mark.asyncio
async def test_multi_task_dependency_chain(temp_workspace):
    """测试多任务依赖链"""
    db_path = temp_workspace / "events.db"
    stream = EventStream(db_path)
    await stream.init()
    state = SharedState(temp_workspace / "state")

    # 3个任务，链式依赖: #t1 → #t2 → #t3
    task_graph = TaskGraph()
    task_graph.add_task(TaskSpec(id="#t1", description="Task 1"))
    task_graph.add_task(TaskSpec(id="#t2", description="Task 2", depends_on=["#t1"]))
    task_graph.add_task(TaskSpec(id="#t3", description="Task 3", depends_on=["#t2"]))

    # 初始只有 #t1 就绪
    assert len(task_graph.get_ready_tasks()) == 1

    # 依次完成
    for tid in ["#t1", "#t2", "#t3"]:
        task_graph.assign_agent(tid, "BE")
        await stream.append(Event(verb=Verb.DON, agent="BE", task=tid, status="OK"))
        await stream.append(Event(verb=Verb.VAL, agent="QA", task=tid, status="PASS"))
        task_graph.update_status(tid, TaskStatus.COMPLETED)

    assert task_graph.is_complete()
    print(f"  3-task chain completed in order")

    await stream.close()
