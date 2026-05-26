"""
AI Company — 后端工程师 (BE)
"""

from agents.base import BaseAgent
from protocols.verbs import Verb

BACKEND_SYSTEM_PROMPT = """你是 AI Company 的后端工程师（BE）。
专业能力：API 设计、业务逻辑实现、JWT 认证、数据库交互、Python/FastAPI/Flask。
关键：直接输出完整可运行的代码，不要解释描述。代码截断会导致 QA 验收失败。

输出格式（严格遵守）：
FILE:server.py
```python
# 完整可运行代码，不要省略任何实现细节
```

定期发送 UPD 报告进度。完成时发送 DON。"""


class BackendAgent(BaseAgent):
    def __init__(self, event_stream, shared_state, project_name: str = "default"):
        super().__init__("BE", BACKEND_SYSTEM_PROMPT, event_stream, shared_state,
                         project_name=project_name)

    async def run(self, task_spec: dict):
        task_id = task_spec.get("id", task_spec.get("task_id", "unknown"))

        await self.emit(Verb.ACK, task_id, "ACCEPT")
        print(f"  [BE] ACK {task_id}")

        context = await self.get_context(task_id)
        personal_memory = await self.memory.get_summary()

        retry_info = ""
        retry_count = task_spec.get("_retry_count", 0)
        if retry_count > 0:
            retry_info = f"""
⚠️ 第 {retry_count} 次重试！上次失败：{task_spec.get('_last_fail_reason', '')[:200]}
必须输出完整代码，不要省略。"""

        prompt = f"""任务规范：
{task_spec}
{retry_info}

任务上下文：
{context}

直接输出 FILE: 代码块，不要解释。文件路径用相对路径（如 server.py），不要用绝对路径。"""

        result = await self.call_llm(prompt, max_tokens=8000)

        # 检测 BLK
        if result.strip().startswith("BLK:"):
            dep_reason = result.strip()[4:].strip()
            await self.emit(Verb.BLK, task_id, "WAIT",
                            payload={"dep": dep_reason[:200]}, mentions=["HR"])
            print(f"  [BE] BLK {task_id}: {dep_reason[:80]}")
            return

        output = await self._process_output(task_id, result)

        await self.emit(Verb.DON, task_id, "OK",
                        payload={"out": output["shared_path"],
                                 "files": output["files"]})

        await self.memory.append({"task": task_id, "summary": output["summary"][:200]})

        n = len(output["files"])
        print(f"  [BE] DON {task_id} → {n} files")
