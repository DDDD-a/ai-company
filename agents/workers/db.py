"""
AI Company — 数据库工程师 (DB)
"""

from agents.base import BaseAgent
from protocols.verbs import Verb

DB_SYSTEM_PROMPT = """你是 AI Company 的数据库工程师（DB）。
专业能力：Schema 设计、SQL 编写、查询优化、数据库迁移。
关键：直接输出完整的 SQL 文件，不要解释。代码截断会导致 QA 验收失败。

输出格式（严格遵守）：
FILE:schema.sql
```sql
-- 完整的 CREATE TABLE 语句，不要省略任何字段和约束
```

定期发送 UPD 报告进度。完成时发送 DON。"""


class DBAgent(BaseAgent):
    def __init__(self, event_stream, shared_state, project_name: str = "default"):
        super().__init__("DB", DB_SYSTEM_PROMPT, event_stream, shared_state,
                         project_name=project_name)

    async def run(self, task_spec: dict):
        task_id = task_spec.get("id", task_spec.get("task_id", "unknown"))

        await self.emit(Verb.ACK, task_id, "ACCEPT")
        print(f"  [DB] ACK {task_id}")

        context = await self.get_context(task_id)
        prompt = f"""任务规范：
{task_spec}

任务上下文：
{context}

直接输出 FILE:schema.sql 代码块，不要解释。文件路径用相对路径。"""

        result = await self.call_llm(prompt, max_tokens=8000)

        cleaned, files = self._extract_files(result)
        if not files:
            retry_prompt = f"""FILE:schema.sql
```sql
{task_spec}
```
直接输出完整 SQL 代码，不要解释。"""
            result = await self.call_llm(retry_prompt, max_tokens=8000)

        output = await self._process_output(task_id, result)

        await self.emit(Verb.DON, task_id, "OK",
                        payload={"out": output["shared_path"],
                                 "files": output["files"]})

        await self.memory.append({"task": task_id, "summary": output["summary"][:200]})

        n = len(output["files"])
        print(f"  [DB] DON {task_id} → {n} files")
