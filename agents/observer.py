"""
AI Company — Observer（感知层）

纯规则检测，不调用 LLM（v1 阶段禁止调 LLM）。
只读 Event Stream，不写 Event Stream，只产生 ALERT 对象供 Director 消费。

检测类型：deadlock / stall / conflict_spike / overload / quality_failure
"""

import time
from typing import List, Optional

from core.event_stream import EventStream
from protocols.verbs import Verb

# 阈值配置
STALL_THRESHOLD_MS = 300_000  # 5分钟无 UPD 视为 stall
CON_SPIKE_THRESHOLD = 3  # 同一任务10分钟内3次 CON 视为 spike
CON_SPIKE_WINDOW_MS = 600_000  # 10分钟窗口
OVERLOAD_THRESHOLD = 5  # 单个 Agent 超过5个 RUNNING 任务视为 overload
QUALITY_FAIL_THRESHOLD = 3  # 同一任务3次 VAL FAIL 视为质量问题


class Observer:
    """
    纯规则检测，不调用 LLM，不写 Event Stream（只产生 ALERT dict）。
    """

    def __init__(self, event_stream: EventStream, task_graph=None):
        self.stream = event_stream
        self.task_graph = task_graph

    def set_task_graph(self, task_graph):
        """注入 task_graph 引用，用于过滤已终止的任务"""
        self.task_graph = task_graph

    async def scan(self) -> List[dict]:
        """扫描 Event Stream，返回所有检测到的 ALERT 列表"""
        alerts = []
        alerts += await self._detect_stalls()
        alerts += await self._detect_conflict_spikes()
        alerts += await self._detect_deadlocks()
        alerts += await self._detect_overload()
        alerts += await self._detect_quality_failures()
        return alerts

    async def _detect_stalls(self) -> List[dict]:
        """检测长时间无 UPD 的 RUNNING 任务"""
        alerts = []
        now = int(time.time() * 1000)
        recent = await self.stream.tail(200)

        # 找出所有在 RUNNING 状态的任务
        running_tasks = {}
        for event in recent:
            if event.verb in (Verb.ACK, Verb.ASN):
                running_tasks[event.task] = running_tasks.get(event.task, 0)
            elif event.verb == Verb.UPD:
                running_tasks[event.task] = event.timestamp
            elif event.verb in (Verb.DON, Verb.VAL, Verb.BLK):
                running_tasks.pop(event.task, None)

        # 检查每个 RUNNING 任务的最后更新时间
        for task_id, last_update in running_tasks.items():
            last_ts = last_update if last_update > 0 else 0
            if last_ts == 0 or (now - last_ts) > STALL_THRESHOLD_MS:
                # 首次 ACK 也算，从 ACK 时间算起
                stall_ms = now - last_ts if last_ts > 0 else now
                alerts.append({
                    "type": "stall",
                    "targets": [task_id],
                    "severity": "high" if stall_ms > STALL_THRESHOLD_MS * 2 else "medium",
                    "evidence": [task_id],
                    "metadata": {"stall_ms": stall_ms},
                })

        return alerts

    async def _detect_conflict_spikes(self) -> List[dict]:
        """检测短时间内 CON 频发的任务"""
        alerts = []
        now = int(time.time() * 1000)
        recent = await self.stream.tail(200)

        # 按任务统计最近窗口内的 CON 数量
        con_counts = {}
        for event in recent:
            if event.verb == Verb.CON and (now - event.timestamp) <= CON_SPIKE_WINDOW_MS:
                con_counts[event.task] = con_counts.get(event.task, 0) + 1

        for task_id, count in con_counts.items():
            if count >= CON_SPIKE_THRESHOLD:
                alerts.append({
                    "type": "conflict_spike",
                    "targets": [task_id],
                    "severity": "high" if count >= CON_SPIKE_THRESHOLD * 2 else "medium",
                    "evidence": [task_id],
                    "metadata": {"conflict_count": count, "window_ms": CON_SPIKE_WINDOW_MS},
                })

        return alerts

    async def _detect_deadlocks(self) -> List[dict]:
        """检测 BLK 循环依赖"""
        alerts = []
        recent = await self.stream.tail(100)

        # 收集所有 BLK 事件
        blocked_tasks = {}
        for event in recent:
            if event.verb == Verb.BLK:
                blocked_tasks[event.task] = {
                    "agent": event.agent,
                    "timestamp": event.timestamp,
                    "depends_on": event.payload.get("dep", []),
                }

        # 简化检测：检查 A 阻塞在 B，同时 B 阻塞在 A
        # v1 版本：如果有多个 BLK 且相互引用，标记为潜在死锁
        if len(blocked_tasks) >= 2:
            for task_a, info_a in blocked_tasks.items():
                for dep in info_a.get("depends_on", []):
                    if dep in blocked_tasks:
                        for dep_b in blocked_tasks[dep].get("depends_on", []):
                            if dep_b == task_a:
                                alerts.append({
                                    "type": "deadlock",
                                    "targets": [task_a, dep],
                                    "severity": "high",
                                    "evidence": [task_a, dep],
                                    "metadata": {},
                                })
                                return alerts  # 一个死锁就够

        return alerts

    async def _detect_overload(self) -> List[dict]:
        """检测单 Agent 任务堆积"""
        alerts = []
        recent = await self.stream.tail(200)

        # 统计每个 Agent 的 RUNNING 任务数
        agent_tasks = {}
        task_status = {}
        for event in recent:
            if event.verb == Verb.ACK:
                task_status[event.task] = event.agent
                agent_tasks[event.agent] = agent_tasks.get(event.agent, 0) + 1
            elif event.verb in (Verb.DON, Verb.VAL):
                if event.task in task_status:
                    agent = task_status[event.task]
                    agent_tasks[agent] = max(0, agent_tasks.get(agent, 0) - 1)

        for agent_id, count in agent_tasks.items():
            if count >= OVERLOAD_THRESHOLD:
                alerts.append({
                    "type": "overload",
                    "targets": [agent_id],
                    "severity": "medium",
                    "evidence": [agent_id],
                    "metadata": {"task_count": count},
                })

        return alerts

    async def _detect_quality_failures(self) -> List[dict]:
        """检测 VAL FAIL 重复出现的任务（只统计最近一次 PASS 之后的 FAIL）"""
        alerts = []
        recent = await self.stream.tail(200)

        # 跳过已经终止的任务（FAILED / COMPLETED）
        terminal_tasks = set()
        if self.task_graph:
            from core.task_graph import TaskStatus
            for t in self.task_graph.tasks.values():
                if t.status in (TaskStatus.FAILED, TaskStatus.COMPLETED):
                    terminal_tasks.add(t.id)

        # 先找到每个任务最后一次 PASS 的时间
        last_pass = {}
        for event in recent:
            if event.verb == Verb.VAL and event.status == "PASS":
                if event.task not in last_pass or event.timestamp > last_pass[event.task]:
                    last_pass[event.task] = event.timestamp

        # 只统计 PASS 之后的 FAIL（如果从未 PASS，则统计所有 FAIL）
        fail_counts = {}
        for event in recent:
            if event.verb == Verb.VAL and event.status == "FAIL":
                task = event.task
                # 如果存在 PASS 且当前 FAIL 在 PASS 之前，跳过
                if task in last_pass and event.timestamp <= last_pass[task]:
                    continue
                fail_counts[task] = fail_counts.get(task, 0) + 1

        for task_id, count in fail_counts.items():
            if task_id in terminal_tasks:
                continue
            if count >= QUALITY_FAIL_THRESHOLD:
                alerts.append({
                    "type": "quality_failure",
                    "targets": [task_id],
                    "severity": "high",
                    "evidence": [task_id],
                    "metadata": {"fail_count": count},
                })

        return alerts
