"""
AI Company — Shared State（共享状态层）

所有 Agent 可读写的结构化数据区。
基于文件系统，键为路径格式（contracts/login_api、schemas/user_v2）。
值为任意 JSON 可序列化对象。
"""

from pathlib import Path
import json
from typing import Any, Optional

STATE_DIR = Path("workspace/state")


class SharedState:
    """所有 Agent 可读写的结构化数据区"""

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = state_dir or STATE_DIR

    async def write(self, path: str, data: Any, written_by: str) -> str:
        """
        写入数据，返回 shared:// 格式的引用指针。

        示例:
            ref = await state.write("contracts/login", {...}, written_by="BE")
            # ref == "shared://contracts/login"
        """
        file_path = self._resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        content = {
            "data": data,
            "written_by": written_by,
            "path": path,
        }
        file_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.ref(path)

    async def read(self, path: str) -> Optional[Any]:
        """读取数据，不存在返回 None"""
        file_path = self._resolve_path(path)
        if not file_path.exists():
            return None
        try:
            content = json.loads(file_path.read_text(encoding="utf-8"))
            return content.get("data")
        except (json.JSONDecodeError, FileNotFoundError):
            return None

    async def read_meta(self, path: str) -> Optional[dict]:
        """读取完整元数据（含 written_by）"""
        file_path = self._resolve_path(path)
        if not file_path.exists():
            return None
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return None

    async def list(self, prefix: str = "") -> list[str]:
        """列出所有键（可按前缀过滤）"""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        keys = []
        for json_file in self.state_dir.rglob("*.json"):
            rel_path = json_file.relative_to(self.state_dir).with_suffix("")
            key = str(rel_path).replace("\\", "/")
            if not prefix or key.startswith(prefix):
                keys.append(key)
        return sorted(keys)

    async def delete(self, path: str) -> bool:
        """删除指定路径的数据"""
        file_path = self._resolve_path(path)
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def ref(self, path: str) -> str:
        """生成引用指针字符串，格式：shared://path"""
        return f"shared://{path}"

    def _resolve_path(self, path: str) -> Path:
        """将逻辑路径解析为文件系统路径，防止路径穿越"""
        # 规范化路径，防止 ../ 穿越
        safe = path.replace("\\", "/").lstrip("/")
        resolved = (self.state_dir / safe).resolve()
        if not str(resolved).startswith(str(self.state_dir.resolve())):
            raise ValueError(f"路径穿越检测: {path}")
        return resolved.with_suffix(".json")
