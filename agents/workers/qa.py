"""
AI Company — 质量验证 (QA)

负责验收 Worker 的 DON 产出物，发送 VAL PASS/FAIL。
"""

import json
import re

from agents.base import BaseAgent
from protocols.verbs import Verb

QA_SYSTEM_PROMPT = """你是 AI Company 的质量验证工程师（QA）。

职责：验收 Worker 提交的产出物，判断是否符合 output_contract。

验收规则：
1. 读取 DON 事件中 payload.out 指向的 Shared State 内容
2. 对照任务规范中的 output_contract 逐项检查
3. 检查创建的文件内容是否符合规范
4. 符合 → 写 VAL PASS
5. 不符合 → 写 VAL FAIL，含具体失败原因
6. 无法判断 → 写 VAL FAIL，说明缺少哪些信息

输出格式（JSON）：
{"verdict": "PASS"|"FAIL", "reason": "验收说明", "issues": ["问题列表"]}
"""


class QAAgent(BaseAgent):
    def __init__(self, event_stream, shared_state, project_name: str = "default"):
        super().__init__("QA", QA_SYSTEM_PROMPT, event_stream, shared_state,
                         project_name=project_name)

    async def validate(self, task_spec: dict, don_event) -> dict:
        """
        验证任务的输出是否符合合约。

        返回验收结果 dict: {"verdict": "PASS"|"FAIL", "reason": "...", "issues": [...]}
        """
        task_id = task_spec.get("id", don_event.task if don_event else "unknown")
        output_contract = task_spec.get("output_contract", "")

        # 1. 读取 Shared State 中的产出物
        output_path = don_event.payload.get("out", "") if don_event else ""
        output_content = None
        if output_path:
            path = output_path.replace("shared://", "")
            output_content = await self.state.read(path)

        # 2. 读取实际创建的文件内容
        files_created = don_event.payload.get("files", []) if don_event else []
        file_contents = {}
        for fpath in files_created:
            content = await self._read_file(fpath)
            if content:
                file_contents[fpath] = content[:2000]

        file_listing = ""
        if file_contents:
            file_listing = "\n## 创建的文件内容（主要验收依据）：\n"
            for fpath, content in file_contents.items():
                file_listing += f"\n=== {fpath} ===\n{content}\n"
        else:
            file_listing = "\n（警告：未能读取任何文件内容）\n"

        # 3. 构建验收 prompt
        prompt = f"""你是 QA 验收工程师。以下内容中，**文件内容**是主要验收依据，产出物摘要仅为辅助参考。

请按以下优先级验收：
1. 首先检查「创建的文件内容」是否完整满足 output_contract
2. 「产出物摘要」只是辅助信息，如果文件内容已满足合约，不要因为摘要不完整而判 FAIL

任务规范：
{task_spec}

验收合约：
{output_contract}

{file_listing}

辅助参考 — 产出物摘要：
{str(output_content.get('summary', ''))[:500] if output_content else '(无)'}

请验收。输出 JSON：{{"verdict": "PASS"|"FAIL", "reason": "...", "issues": []}}"""

        raw = await self.call_llm(prompt, max_tokens=1000)
        result = self._parse_result(raw)

        # 4. 发送 VAL 事件
        verdict = result.get("verdict", "FAIL")
        await self.emit(
            verb=Verb.VAL,
            task=task_id,
            status=verdict,
            payload={
                "reason": result.get("reason", ""),
                "issues": result.get("issues", []),
                "output_path": output_path,
            },
        )

        print(f"  [QA] VAL {verdict} {task_id}")
        return result

    def _parse_result(self, raw: str) -> dict:
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
            return {"verdict": "FAIL", "reason": "parse_error", "issues": ["JSON解析失败"]}
