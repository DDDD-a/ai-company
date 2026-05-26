"""
AI Company — Director（干预层）

仅在 ALERT 触发时运行，低频介入。
- 接收 Observer 的 ALERT，结合共享状态与任务图做决策
- 下达 DIR 干预指令
- 将反复出现的模式提炼为 LESSON，写入共同记忆
- 对自身决策质量进行复盘，迭代自身系统提示

Director 是唯一可以写入 Policy Layer（共同记忆）的角色。
"""

import json
import re
import time

from agents.base import BaseAgent
from core.task_graph import TaskGraph
from core.memory import SharedMemory, Lesson
from protocols.verbs import Verb

DIRECTOR_SYSTEM_PROMPT = """你是 AI Company 的 Director，负责系统监控、异常干预和策略学习。

职责：
1. 接收 Observer 的 ALERT，分析根因，下达 DIR 指令给具体 Worker Agent
2. 在模式反复出现时，提炼为 LESSON 写入共同记忆
3. 审批 HR 上报的新 Agent 创建请求

决策原则：
- 最小干预：只在必要时介入（质量失败 ≥ 3次、死锁、严重阻塞），不打扰正常执行的 Agent
- 根因导向：解决根本问题，不是表面症状
- 经验沉淀：发现系统性规律时主动写 LESSON
- 目标明确：DIR 必须发给具体的 Worker（FE/BE/DB/QA/OPS），不要发给 system/observer/planner

可用的 Agent ID：
- FE: 前端工程师（UI, React, CSS, TypeScript, API集成）
- BE: 后端工程师（API, 业务逻辑, JWT, 数据库交互, Python）
- DB: 数据库工程师（Schema设计, SQL, 查询优化, 迁移）
- QA: 质量验证（测试, 验收, VAL, 代码审查）
- OPS: 部署运维（Docker, CI/CD, 环境配置, 部署）
- HR: 调度层（任务分配、Agent匹配）

干预策略：
- quality_failure → DIR 给失败的 Worker，给出具体的修复指导
- stall → DIR 给 HR，要求重新分配或催促
- deadlock → DIR 给阻塞的 Worker，要求协调或拆分依赖
- overload → DIR 给 HR，要求暂缓分配或启用备用 Agent
- conflict_spike → DIR 给冲突的 Worker，要求对齐接口
- MAX_RETRIES → DIR 给 HR，要求更换 Agent 或拆分任务

输出格式（JSON）：
{
  "actions": [
    {"type": "DIR", "target": "BE", "task": "#task_id", "instruction": "具体的、可执行的修复指令"},
    {"type": "LESSON", "pattern": "模式描述", "trigger": "触发条件", "action": "推荐动作"}
  ]
}

规则：
- target 必须是上述 Agent ID 之一
- instruction 必须具体可执行，不要模糊描述（如"检查数据源"而不是"分析根因"）
- 如果 MAX_RETRIES 触发，target 必须为 HR，指令为更换 Agent 或拆分任务
- 控制 DIR 数量：每次最多 3 条 DIR，不要让系统陷入指令风暴
"""


class Director(BaseAgent):
    """干预层 Agent"""

    def __init__(self, event_stream, shared_state, shared_memory: SharedMemory, task_graph: TaskGraph):
        super().__init__(
            agent_id="DIRECTOR",
            system_prompt=DIRECTOR_SYSTEM_PROMPT,
            event_stream=event_stream,
            shared_state=shared_state,
        )
        self.shared_memory = shared_memory
        self.task_graph = task_graph

    async def handle_alerts(self, alerts: list[dict]):
        """处理 Observer 发出的 ALERT"""
        if not alerts:
            return

        prompt = self._build_decision_prompt(alerts)
        raw = await self.call_llm(prompt, max_tokens=2000)
        actions = self._parse_actions(raw)
        await self._execute_actions(actions)

    async def handle_agent_create_request(self, required_capabilities: list[str], task_id: str):
        """审批新 Agent 创建请求"""
        prompt = f"""HR 上报了 Agent 创建请求：
任务：{task_id}
所需能力：{required_capabilities}

当前已有 Agent：
{self._format_available_agents()}

请决定是否批准创建新 Agent（JSON）：
{{"approved": true/false, "agent_config": {{...}} or null, "reason": "..."}}
如果批准，请提供新 Agent 的完整配置（id, name, capabilities, tools）。"""

        raw = await self.call_llm(prompt, max_tokens=1000)
        decision = self._parse_json(raw)
        return decision

    def _build_decision_prompt(self, alerts: list[dict]) -> str:
        """构建决策 prompt"""
        alert_text = json.dumps(alerts, ensure_ascii=False, indent=2)
        progress = self.task_graph.progress()

        # 列出每个任务和其分配的 Agent
        task_info = []
        for task in self.task_graph.tasks.values():
            st = task.status.value if hasattr(task.status, 'value') else str(task.status)
            task_info.append(
                f"  {task.id} [{task.priority}] → {task.assigned_agent or '未分配'} "
                f"({st}) retries={task.retry_count} fail_reason={task.last_fail_reason[:60] if task.last_fail_reason else '无'}"
            )
        task_list = "\n".join(task_info)

        return f"""系统预警：
{alert_text}

当前任务状态：
{task_list}

进度统计：{json.dumps(progress, ensure_ascii=False)}

请分析并给出干预决策（JSON）。记住：target 只能是 FE/BE/DB/QA/OPS/HR，instruction 要具体可执行，每次最多 3 条 DIR。"""

    def _parse_actions(self, raw: str) -> list:
        """从 LLM 输出中提取 actions 列表"""
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
            data = json.loads(json_str)
            return data.get("actions", [])
        except json.JSONDecodeError:
            return []

    async def _execute_actions(self, actions: list):
        """执行决策动作，含目标验证"""
        # 有效的 Worker Agent ID 列表
        VALID_TARGETS = {"FE", "BE", "DB", "QA", "OPS", "HR"}

        for action in actions:
            action_type = action.get("type", "")

            if action_type == "DIR":
                target = action.get("target", "")
                task = action.get("task", "")
                instruction = action.get("instruction", "")

                # 验证 target 是否为有效 Agent
                if target not in VALID_TARGETS:
                    # 尝试从 task_id 推断 target
                    if task and task in self.task_graph.tasks:
                        inferred = self.task_graph.tasks[task].assigned_agent
                        if inferred and inferred in VALID_TARGETS:
                            target = inferred
                        else:
                            target = "HR"  # 兜底发给 HR
                    else:
                        target = "HR"
                    print(f"  [DIR] invalid target, → {target}")

                await self.emit(
                    verb=Verb.DIR,
                    task=task,
                    status="INTERVENE",
                    payload={
                        "instruction": instruction,
                        "target": target,
                    },
                    mentions=[target],
                )
                print(f"  [DIR] → {target}: {instruction[:80]}")

            elif action_type == "LESSON":
                # 写入共同记忆
                lesson = Lesson(
                    id=f"LESSON#{int(time.time())}",
                    pattern=action.get("pattern", ""),
                    trigger=action.get("trigger", ""),
                    action=action.get("action", ""),
                    scope=action.get("scope", "global"),
                    confidence=action.get("confidence", 0.7),
                )
                await self.shared_memory.write_lesson(lesson, written_by="DIRECTOR")
                print(f"  [DIR] LESSON {lesson.id}")

    def _format_available_agents(self) -> str:
        """格式化现有 Agent 列表"""
        lines = []
        for agent in self.task_graph.tasks.values():
            if agent.assigned_agent:
                lines.append(f"- {agent.assigned_agent}: {agent.id} ({agent.status.value})")
        return "\n".join(lines) if lines else "（无）"

    def _parse_json(self, raw: str) -> dict:
        """通用 JSON 解析"""
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
            return {"actions": []}
