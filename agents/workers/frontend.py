"""
AI Company — 前端工程师 (FE)
"""

from agents.base import BaseAgent
from protocols.verbs import Verb

FRONTEND_SYSTEM_PROMPT = """你是 AI Company 的前端工程师（FE）。
专业能力：UI 设计、React/HTML/CSS/TypeScript、API 集成。
关键：直接输出完整可运行的代码，不要解释描述。代码截断会导致 QA 验收失败。

输出格式（严格遵守）：
FILE:src/index.html
```html
完整页面代码，不要省略任何标签
```

FILE:src/style.css
```css
完整样式，不要省略
```

定期发送 UPD 报告进度。完成时发送 DON。"""


class FrontendAgent(BaseAgent):
    def __init__(self, event_stream, shared_state, project_name: str = "default"):
        super().__init__("FE", FRONTEND_SYSTEM_PROMPT, event_stream, shared_state,
                         project_name=project_name)

    async def run(self, task_spec: dict):
        task_id = task_spec.get("id", task_spec.get("task_id", "unknown"))

        await self.emit(Verb.ACK, task_id, "ACCEPT")
        print(f"  [FE] ACK {task_id}")

        context = await self.get_context(task_id)
        personal_memory = await self.memory.get_summary()

        prompt = f"""任务规范：
{task_spec}

任务上下文：
{context}

直接输出 FILE: 代码块，不要解释。文件路径用相对路径（如 index.html），不要用绝对路径。"""

        result = await self.call_llm(prompt, max_tokens=8000)

        output = await self._process_output(task_id, result)

        await self.emit(Verb.DON, task_id, "OK",
                        payload={"out": output["shared_path"],
                                 "files": output["files"]})

        await self.memory.append({"task": task_id, "summary": output["summary"][:200]})

        n = len(output["files"])
        print(f"  [FE] DON {task_id} → {n} files")
