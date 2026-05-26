"""
AI Company — HR（调度层）

智能调度 Agent，负责：
- 按依赖顺序分配任务
- 从 Agent 库匹配能力标签
- 执行 Director 的 DIR 指令
- 维护任务状态机
- 在无匹配 Agent 时上报 Director
"""

import json
import re

from agents.base import BaseAgent
from core.task_graph import TaskGraph, TaskStatus
from registry.registry import AgentRegistry
from protocols.verbs import Verb

HR_SYSTEM_PROMPT = """你是 AI Company 的 HR，负责任务调度和 Agent 匹配。

职责：
1. 根据任务所需能力，从 Agent 库中匹配最合适的 Agent
2. 发送 ASN 指令分配任务
3. 维护所有任务的状态
4. 处理 Worker 的 REQ 和 CON 上报
5. 执行 Director 的 DIR 指令

匹配原则：
- 优先选有相关个人记忆的 Agent（经验优先）
- 次选库中最新版本 Agent
- 无匹配时：上报 Director，不自行创建

当你收到 REQ 事件时，请给出 JSON 格式的决策：
{"action": "assign|escalate|resolve", "agent_id": "...", "task_id": "#...", "reason": "..."}

当收到 DIR 指令时，执行调度调整并发送 ASN。
"""


class HR(BaseAgent):
    """调度层 Agent"""

    def __init__(self, event_stream, shared_state, registry: AgentRegistry, task_graph: TaskGraph):
        super().__init__(
            agent_id="HR",
            system_prompt=HR_SYSTEM_PROMPT,
            event_stream=event_stream,
            shared_state=shared_state,
        )
        self.registry = registry
        self.task_graph = task_graph

    async def assign_ready_tasks(self):
        """将所有就绪任务分配给合适的 Agent"""
        ready = self.task_graph.get_ready_tasks()
        for task in ready:
            task_dict = task.model_dump() if hasattr(task, 'model_dump') else task
            agent_config = self.registry.find_by_capabilities(
                task_dict.get("required_capabilities", [])
            )

            if agent_config:
                agent_id = agent_config["id"]
                self.task_graph.assign_agent(task.id, agent_id)

                await self.emit(
                    verb=Verb.ASN,
                    task=task.id,
                    status=task.priority,
                    payload={
                        "agent": agent_id,
                        "spec": task_dict,
                    },
                    mentions=[agent_id],
                )

                print(f"  [HR] {task.id} → {agent_id}")
            else:
                # 无匹配，上报 Director
                await self.emit(
                    verb=Verb.REQ,
                    task=task.id,
                    status="NO_AGENT",
                    payload={
                        "reason": "no_matching_agent",
                        "required_capabilities": task_dict.get("required_capabilities", []),
                    },
                    mentions=["DIRECTOR"],
                )
                print(f"  [HR] {task.id} → NO MATCH, escalated")

    async def process_events(self):
        """处理工作池中发给 HR 的事件"""
        # 获取最近需要 HR 处理的事件
        recent = await self.stream.tail(50)
        for event in recent:
            if event.mentions_agent("HR"):
                await self._handle_event(event)

    async def _handle_event(self, event):
        """处理单个事件"""
        if event.verb == Verb.REQ:
            await self._handle_request(event)
        elif event.verb == Verb.CON:
            await self._handle_conflict(event)
        elif event.verb == Verb.DIR:
            await self._handle_directive(event)

    async def _handle_request(self, event):
        """处理 Worker 的 REQ 请求"""
        prompt = f"""Worker 发起了请求：
事件：{event.to_compact()}
Payload：{json.dumps(event.payload, ensure_ascii=False)}

当前任务状态：{self.task_graph.progress()}

请给出决策（JSON）：
{{"action": "assign|escalate", "agent_id": "...", "reason": "..."}}"""

        raw = await self.call_llm(prompt, max_tokens=500)
        decision = self._parse_json(raw)

        action = decision.get("action", "escalate")
        if action == "assign":
            agent_id = decision.get("agent_id")
            if agent_id:
                await self.emit(
                    verb=Verb.ASN,
                    task=event.task,
                    status="REASSIGN",
                    payload={"agent": agent_id, "reason": decision.get("reason")},
                    mentions=[agent_id],
                )
        else:
            await self.emit(
                verb=Verb.REQ,
                task=event.task,
                status="ESCALATED",
                payload={"reason": decision.get("reason", "HR cannot resolve")},
                mentions=["DIRECTOR"],
            )

    async def _handle_conflict(self, event):
        """处理 Worker 的 CON 冲突上报"""
        # 冲突默认上报 Director
        await self.emit(
            verb=Verb.REQ,
            task=event.task,
            status="CONFLICT_ESCALATED",
            payload={"original_conflict": event.payload},
            mentions=["DIRECTOR"],
        )

    async def _handle_directive(self, event):
        """执行 Director 的 DIR 指令"""
        target = event.mentions[0] if event.mentions else event.payload.get("target")
        if target:
            await self.emit(
                verb=Verb.ASN,
                task=event.task,
                status="DIR_EXECUTED",
                payload={"agent": target, "instruction": event.payload.get("instruction", "")},
                mentions=[target],
            )
            print(f"  [HR] DIR {event.task} → {target}")

    def _parse_json(self, raw: str) -> dict:
        """从 LLM 输出中提取 JSON"""
        json_str = raw.strip()
        if "```json" in json_str:
            m = re.search(r"```json\s*(.*?)\s*```", json_str, re.DOTALL)
            if m:
                json_str = m.group(1)
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start >= 0 and end > start:
            json_str = json_str[start : end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {"action": "escalate", "reason": "parse_error"}
