"""
AI Company — Planner（规划层）

一次性静态生成器，不参与运行期。
解析用户需求 → 读取共同记忆 → 生成 Task Graph。
"""

import json
import re

from agents.base import BaseAgent
from core.task_graph import TaskGraph, TaskSpec
from core.memory import SharedMemory

PLANNER_SYSTEM_PROMPT = """你是 AI Company 的 Planner，负责将用户需求转化为可执行的任务图（Task Graph）。

职责：
1. 分析用户需求，识别核心目标与约束
2. 将项目拆解为原子任务，每个任务有明确的输入、输出合约、验收标准
3. 识别任务间的依赖关系，构建 DAG（确保无环）
4. 为每个任务设定优先级（P0/P1/P2/P3）
5. 为每个任务标注所需能力标签（从以下选用：HTML, 前端, 页面, Python, API, 业务逻辑, JWT, 数据库交互, UI, React, CSS, TypeScript, API集成, Schema设计, SQL, 查询优化, 迁移, 测试, 验收, Docker, CI/CD, 环境配置, 部署）

输出格式（严格 JSON，不要有任何其他文字）：
{
  "tasks": [
    {
      "id": "#task_id",
      "description": "任务描述",
      "input": "输入描述",
      "output_contract": "输出合约/验收标准",
      "priority": "P1",
      "depends_on": ["#other_task_id"],
      "required_capabilities": ["Python", "API"]
    }
  ]
}

规则：
- 任务粒度：单个 Agent 在合理时间内可完成
- 依赖声明要完整，避免隐式依赖
- output_contract 要可验证，不能模糊
- 优先级 P0=阻塞性, P1=核心, P2=重要, P3=可选
"""


class Planner(BaseAgent):
    """规划层：将用户需求转化为 Task Graph"""

    def __init__(self, event_stream, shared_state, shared_memory: SharedMemory):
        super().__init__(
            agent_id="PLANNER",
            system_prompt=PLANNER_SYSTEM_PROMPT,
            event_stream=event_stream,
            shared_state=shared_state,
        )
        self.shared_memory = shared_memory

    async def plan(self, user_requirement: str) -> TaskGraph:
        """接收用户需求，返回 TaskGraph"""
        lessons = await self.shared_memory.get_relevant_lessons(user_requirement)
        lesson_context = self._format_lessons(lessons)

        prompt = f"""用户需求：
{user_requirement}

历史教训（请在规划时参考，避免重蹈覆辙）：
{lesson_context or '（无相关教训）'}

请生成任务图。只输出 JSON，不要任何其他文字。"""

        raw = await self.call_llm(prompt, max_tokens=3000)
        return self._parse_task_graph(raw)

    def _format_lessons(self, lessons) -> str:
        if not lessons:
            return ""
        lines = []
        for l in lessons[:5]:
            eff = l.effective_confidence()
            lines.append(f"- [{l.id}] (置信度 {eff:.0%}) {l.pattern} → {l.action}")
        return "\n".join(lines)

    def _parse_task_graph(self, raw: str) -> TaskGraph:
        """从 LLM 输出中提取 JSON 并构建 TaskGraph"""
        json_str = raw.strip()

        # 尝试提取 JSON 块
        if "```json" in json_str:
            m = re.search(r"```json\s*(.*?)\s*```", json_str, re.DOTALL)
            if m:
                json_str = m.group(1)
        elif "```" in json_str:
            m = re.search(r"```\s*(.*?)\s*```", json_str, re.DOTALL)
            if m:
                json_str = m.group(1)

        # 找到第一个 { 到最后一个 }
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start >= 0 and end > start:
            json_str = json_str[start : end + 1]

        data = json.loads(json_str)
        graph = TaskGraph()
        for task_data in data.get("tasks", []):
            task = TaskSpec(**task_data)
            graph.add_task(task)

        if graph.has_cycles():
            raise ValueError("Task graph contains cycles - invalid plan")

        return graph
