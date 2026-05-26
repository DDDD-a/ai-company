"""
AI Company — Agent 注册表

管理 Agent 库的读写，支持能力标签匹配。
"""

from typing import Optional, List
import json
from pathlib import Path

DEFAULT_REGISTRY_PATH = Path("registry/agent_registry.json")


class AgentRegistry:
    """Agent 注册表，管理所有 Worker Agent 的配置"""

    def __init__(self, registry_path: Optional[Path] = None):
        self.registry_path = registry_path or DEFAULT_REGISTRY_PATH
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is None:
            if self.registry_path.exists():
                self._data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            else:
                self._data = {"agents": []}
        return self._data

    def _save(self):
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def find_by_capabilities(self, required: List[str]) -> Optional[dict]:
        """按能力标签匹配，返回最优 Agent 配置

        匹配规则：
        1. 优先选匹配最多标签的
        2. 同匹配数时，优先选有个人记忆的（经验优先）
        """
        data = self._load()
        if not required:
            return None

        required_set = set(tag.lower() for tag in required)
        best = None
        best_score = -1

        for agent in data.get("agents", []):
            caps = set(c.lower() for c in agent.get("capabilities", []))
            overlap = len(required_set & caps)
            if overlap > best_score:
                best = agent
                best_score = overlap

        if best_score <= 0:
            return None
        return best

    def get_by_id(self, agent_id: str) -> Optional[dict]:
        """按 ID 获取 Agent 配置"""
        data = self._load()
        for agent in data.get("agents", []):
            if agent["id"] == agent_id:
                return agent
        return None

    def register(self, agent_config: dict):
        """新增 Agent 到注册表（需 Director 审批后调用）"""
        data = self._load()
        # 检查是否已存在
        for i, agent in enumerate(data.get("agents", [])):
            if agent["id"] == agent_config["id"]:
                data["agents"][i] = agent_config
                self._data = data
                self._save()
                return
        data.setdefault("agents", []).append(agent_config)
        self._data = data
        self._save()

    def remove(self, agent_id: str):
        """从注册表移除 Agent"""
        data = self._load()
        data["agents"] = [a for a in data.get("agents", []) if a["id"] != agent_id]
        self._data = data
        self._save()

    def list_all(self) -> List[dict]:
        """列出所有已注册的 Agent"""
        return self._load().get("agents", [])

    def list_capable(self, required: List[str], min_match: int = 1) -> List[dict]:
        """列出所有匹配至少 min_match 个标签的 Agent"""
        data = self._load()
        required_set = set(tag.lower() for tag in required)
        results = []
        for agent in data.get("agents", []):
            caps = set(c.lower() for c in agent.get("capabilities", []))
            overlap = len(required_set & caps)
            if overlap >= min_match:
                results.append((agent, overlap))
        results.sort(key=lambda x: x[1], reverse=True)
        return [a for a, _ in results]
