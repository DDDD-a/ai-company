"""
AI Company — 全局配置
"""

from pathlib import Path

# ===== 路径 =====
WORKSPACE_DIR = Path("workspace")
EVENTS_DB = WORKSPACE_DIR / "events.db"
STATE_DIR = WORKSPACE_DIR / "state"
SHARED_MEMORY_DIR = WORKSPACE_DIR / "memory/shared"
PERSONAL_MEMORY_DIR = WORKSPACE_DIR / "memory/personal"
REGISTRY_PATH = Path("registry/agent_registry.json")

# ===== LLM 默认配置（可通过 .env 覆盖）=====
DEFAULT_PROVIDER = "openai"  # openai | anthropic
DEFAULT_OPENAI_MODEL = "deepseek-chat"
DEFAULT_OPENAI_BASE_URL = "https://api.deepseek.com"
MAX_TOKENS = 2000
TEMPERATURE = 0.7

# ===== Observer 阈值 =====
STALL_THRESHOLD_MS = 300_000  # 5分钟
CON_SPIKE_THRESHOLD = 3
CON_SPIKE_WINDOW_MS = 600_000  # 10分钟
OVERLOAD_THRESHOLD = 5  # 单个 Agent 最大 RUNNING 任务数
QUALITY_FAIL_THRESHOLD = 3  # 同一任务 VAL FAIL 次数

# ===== 系统限制 =====
MAX_TASK_RETRIES = 3
AGENT_CREATE_MIN_PRIORITY = "P1"
MAIN_LOOP_INTERVAL_SEC = 1  # 主循环轮询间隔
