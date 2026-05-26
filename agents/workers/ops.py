"""
AI Company — 部署运维 (OPS)
"""

from agents.base import BaseAgent
from protocols.verbs import Verb

OPS_SYSTEM_PROMPT = """你是 AI Company 的部署运维工程师（OPS）。
专业能力：项目初始化、Docker、CI/CD、环境配置、部署。
关键：直接输出完整的文件内容，不要解释。代码截断会导致 QA 验收失败。

输出格式（严格遵守）：
FILE:filename
```type
完整内容，不要省略
```

定期发送 UPD 报告进度。完成时发送 DON。"""


class OpsAgent(BaseAgent):
    def __init__(self, event_stream, shared_state, project_name: str = "default"):
        super().__init__("OPS", OPS_SYSTEM_PROMPT, event_stream, shared_state,
                         project_name=project_name)

    async def run(self, task_spec: dict):
        task_id = task_spec.get("id", task_spec.get("task_id", "unknown"))

        await self.emit(Verb.ACK, task_id, "ACCEPT")
        print(f"  [OPS] ACK {task_id}")

        context = await self.get_context(task_id)
        prompt = f"""任务规范：
{task_spec}

任务上下文：
{context}

直接输出 FILE: 代码块，不要解释。文件路径用相对路径。"""

        result = await self.call_llm(prompt, max_tokens=8000)

        cleaned, files = self._extract_files(result)
        if not files:
            retry_prompt = f"""FILE:output.txt
```
{task_spec}
```
直接输出完整代码，不要解释。"""
            result = await self.call_llm(retry_prompt, max_tokens=8000)

        output = await self._process_output(task_id, result)

        await self.emit(Verb.DON, task_id, "OK",
                        payload={"out": output["shared_path"],
                                 "files": output["files"]})

        await self.memory.append({"task": task_id, "summary": output["summary"][:200]})

        n = len(output["files"])
        print(f"  [OPS] DON {task_id} → {n} files")
