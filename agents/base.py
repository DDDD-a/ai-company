"""
AI Company — Agent 基类

所有 Agent 的基类，提供 LLM 调用、Event Stream 读写、Shared State 访问、
文件创建/修改等能力。
"""

import re
import os
from pathlib import Path
from typing import Optional

from core.event_stream import EventStream
from core.shared_state import SharedState
from core.memory import PersonalMemory
from core.llm_provider import create_provider, LLMProvider
from protocols.verbs import Event, Verb

PROJECTS_DIR = Path("workspace/projects")


class BaseAgent:
    """所有 Agent 的基类"""

    def __init__(
        self,
        agent_id: str,
        system_prompt: str,
        event_stream: EventStream,
        shared_state: SharedState,
        llm: Optional[LLMProvider] = None,
        project_name: str = "default",
    ):
        self.id = agent_id
        self.system_prompt = system_prompt
        self.stream = event_stream
        self.state = shared_state
        self.memory = PersonalMemory(agent_id)
        self.llm = llm or create_provider()
        self.project_name = project_name
        self.project_dir = PROJECTS_DIR / project_name
        self.project_dir.mkdir(parents=True, exist_ok=True)

    async def call_llm(
        self,
        user_message: str,
        max_tokens: int = 8000,
        temperature: float = 0.7,
    ) -> str:
        """调用 LLM API，返回纯文本响应"""
        return await self.llm.chat(
            system_prompt=self.system_prompt,
            user_message=user_message,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def emit(
        self,
        verb: Verb,
        task: str,
        status: str = "",
        payload: dict = None,
        mentions: list = None,
    ) -> Event:
        """向工作池写入事件"""
        event = Event(
            verb=verb,
            agent=self.id,
            task=task,
            status=status,
            payload=payload or {},
            mentions=mentions or [],
        )
        await self.stream.append(event)
        return event

    async def get_context(self, task_id: str) -> str:
        """组装任务上下文：事件历史 + 个人记忆摘要 + 已有文件列表"""
        events = await self.stream.get_task_events(task_id)
        event_log = "\n".join(e.to_compact() for e in events[-30:])
        memory_summary = await self.memory.get_summary(max_entries=10)

        # 列出项目中已有的文件
        existing_files = self._list_project_files()

        return (
            f"## 任务事件历史（最近30条）\n{event_log}\n\n"
            f"## 项目已有文件\n{existing_files}\n\n"
            f"## 个人经验\n{memory_summary}"
        )

    async def run(self, task_spec: dict, **kwargs):
        """子类实现具体执行逻辑"""
        raise NotImplementedError(f"{self.id}.run() not implemented")

    # ═══════════════════════════════════════════════════════════════
    # 文件操作
    # ═══════════════════════════════════════════════════════════════

    def _list_project_files(self) -> str:
        """列出项目目录中的所有文件"""
        if not self.project_dir.exists():
            return "（项目目录为空）"
        files = []
        for f in sorted(self.project_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(self.project_dir)
                size = f.stat().st_size
                files.append(f"  {rel} ({self._fmt_size(size)})")
        if not files:
            return "（项目目录为空）"
        return "\n".join(files) if files else "（项目目录为空）"

    def _fmt_size(self, size: int) -> str:
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        return f"{size / (1024 * 1024):.1f}MB"

    def _extract_files(self, llm_output: str) -> tuple:
        """
        从 LLM 输出中提取 FILE: 块，返回 (cleaned_text, list_of_file_dicts)

        支持的格式：
            FILE:path/to/file.py
            ```python
            code content
            ```

            FILE:path/to/another.tsx
            ```typescript
            component code
            ```

            DONE: summary
        """
        files = []
        cleaned_parts = []
        last_end = 0

        # 匹配 FILE:path 后跟代码块
        pattern = r'FILE:(\S+)\s*\n\s*```(\w*)\n(.*?)```'
        for m in re.finditer(pattern, llm_output, re.DOTALL):
            filepath = m.group(1)
            lang = m.group(2)
            content = m.group(3)

            # 添加匹配之前的文本
            cleaned_parts.append(llm_output[last_end:m.start()])
            last_end = m.end()

            files.append({
                "path": filepath,
                "language": lang,
                "content": content.strip(),
            })

        # 添加剩余文本
        cleaned_parts.append(llm_output[last_end:])
        cleaned_text = "".join(cleaned_parts).strip()

        return cleaned_text, files

    async def _write_files(self, files: list) -> list[str]:
        """将提取的文件写入项目目录，返回写入的路径列表。
        自动清理 LLM 可能输出的绝对路径或逃逸路径。"""
        written = []
        for f in files:
            raw = f["path"].lstrip("/")

            # 清理绝对路径和系统路径：只保留相对路径部分
            # 例如: home/lxj/projects/text/package.json → package.json
            #       /home/lxj/projects/text/src/app.py → src/app.py
            dangerous_prefixes = ["home/", "root/", "etc/", "tmp/", "var/", "opt/", "usr/"]
            for prefix in dangerous_prefixes:
                if raw.startswith(prefix):
                    # 尝试提取项目名后的部分
                    parts = raw.split("/")
                    # 找到 "projects" 之后的路径
                    if "projects" in parts:
                        idx = parts.index("projects")
                        # projects 后面第一个是项目名，之后是实际文件路径
                        if len(parts) > idx + 2:
                            raw = "/".join(parts[idx + 2:])
                        elif len(parts) > idx + 1:
                            raw = "/".join(parts[idx + 1:])
                    else:
                        # 只取文件名
                        raw = parts[-1]
                    break

            # 再次确保安全
            raw = raw.lstrip("/")
            if not raw:
                raw = "output.txt"

            full_path = (self.project_dir / raw).resolve()
            # 路径穿越防护
            if not str(full_path).startswith(str(self.project_dir.resolve())):
                # 不安全，只用文件名
                full_path = (self.project_dir.resolve() / Path(raw).name).resolve()

            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(f["content"], encoding="utf-8")
            # 使用 resolve() 确保路径比较一致
            resolved_project = self.project_dir.resolve()
            resolved_full = full_path.resolve()
            written.append(str(resolved_full.relative_to(resolved_project)))
        return written

    async def _read_file(self, rel_path: str) -> Optional[str]:
        """读取项目目录中的文件"""
        full_path = (self.project_dir / rel_path.lstrip("/")).resolve()
        if not str(full_path).startswith(str(self.project_dir.resolve())):
            return None  # 路径穿越防护
        if not full_path.exists():
            return None
        return full_path.read_text(encoding="utf-8")

    async def _process_output(self, task_id: str, llm_output: str) -> dict:
        """
        处理 LLM 输出：提取文件 → 写入磁盘 → 保存到 Shared State → 返回结果

        返回：{"files": [...], "summary": "...", "shared_path": "..."}
        """
        cleaned_text, files = self._extract_files(llm_output)

        # 写入实际文件
        written_paths = []
        if files:
            written_paths = await self._write_files(files)

        # 保存到 Shared State（保留原文，方便 QA 验收）
        output_path = f"outputs/{task_id.strip('#')}"
        await self.state.write(
            output_path,
            {
                "summary": cleaned_text[:500],
                "files": [{"path": p, "language": f["language"]} for p, f in zip(written_paths, files)],
                "raw_output": llm_output[:3000],
                "agent": self.id,
            },
            written_by=self.id,
        )

        return {
            "files": written_paths,
            "summary": cleaned_text,
            "shared_path": self.state.ref(output_path),
        }
