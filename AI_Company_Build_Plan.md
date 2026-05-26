# AI Company — Claude Code 执行计划书

> 本文档是交给 Claude Code 的施工任务书。按阶段顺序执行，每个阶段完成验收后再进入下一阶段。

---

## 执行前须知

**技术栈**
- 语言：Python 3.11+
- 并发：asyncio
- LLM：Anthropic SDK（直接调用，不经过任何框架）
- 持久化：SQLite（Event Stream） + JSON 文件（Shared State / Memory）
- 依赖管理：pip + requirements.txt

**核心原则**
- 不引入 LangChain / LangGraph / CrewAI 等框架
- 每个 Agent = 一个独立的 Anthropic API 调用 + 专属系统提示
- 所有通信经过 Event Stream，禁止 Agent 直接互相调用
- 保持每个模块可独立测试

---

## 项目目录结构（目标态）

```
ai_company/
├── core/
│   ├── __init__.py
│   ├── event_stream.py      # 工作池：Append-Only 事件日志
│   ├── shared_state.py      # 共享状态层
│   ├── task_graph.py        # 任务 DAG 数据结构
│   └── memory.py            # 记忆系统（个人 + 共同）
├── agents/
│   ├── __init__.py
│   ├── base.py              # Agent 基类
│   ├── planner.py           # 规划层
│   ├── observer.py          # 感知层
│   ├── director.py          # 干预层
│   ├── hr.py                # 调度层
│   └── workers/
│       ├── __init__.py
│       ├── frontend.py
│       ├── backend.py
│       ├── qa.py
│       └── ops.py
├── protocols/
│   ├── __init__.py
│   ├── verbs.py             # 动词枚举与消息格式
│   └── parser.py            # 消息解析器
├── registry/
│   ├── agent_registry.json  # Agent 库配置
│   └── registry.py          # 注册表读写接口
├── workspace/               # 运行时数据（gitignore）
│   ├── events.db            # SQLite Event Stream
│   ├── state/               # Shared State JSON 文件
│   └── memory/
│       ├── shared/          # 共同记忆 LESSON 文件
│       └── personal/        # 各 Agent 个人记忆
├── tests/
│   ├── test_event_stream.py
│   ├── test_protocols.py
│   ├── test_planner.py
│   └── test_minimal_loop.py
├── config.py                # 全局配置
├── main.py                  # 系统入口
└── requirements.txt
```

---

## 阶段一：基础设施层

**目标**：搭好系统的骨架，不涉及任何 LLM 调用。

---

### Task 1.1 — 初始化项目

创建上述完整目录结构，生成所有 `__init__.py`，创建 `requirements.txt`：

```
anthropic>=0.25.0
asyncio
aiosqlite
python-dotenv
pydantic>=2.0
```

创建 `.env.example`：
```
ANTHROPIC_API_KEY=your_key_here
```

创建 `.gitignore`，忽略 `workspace/`、`.env`、`__pycache__`、`*.pyc`。

**验收**：`python -c "import anthropic"` 无报错。

---

### Task 1.2 — 通信协议层（`protocols/`）

**`protocols/verbs.py`** — 定义所有合法动词和消息结构：

```python
from enum import Enum
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import time, uuid

class Verb(str, Enum):
    ASN   = "ASN"    # HR → Worker：分配任务
    ACK   = "ACK"    # Worker → 系统：确认接收
    UPD   = "UPD"    # Worker → 系统：进度更新
    BLK   = "BLK"    # Worker → 系统：阻塞等待
    REQ   = "REQ"    # Worker → HR/Director：请求决策
    CON   = "CON"    # Worker → 系统：冲突上报
    DON   = "DON"    # Worker → 系统：自报完成（待验收）
    VAL   = "VAL"    # QA → 系统：验收结果
    DIR   = "DIR"    # Director → 任意：干预指令
    ALERT = "ALERT"  # Observer → Director：异常预警

class Event(BaseModel):
    id:        str = ""
    timestamp: int = 0
    verb:      Verb
    agent:     str
    task:      str
    status:    str = ""
    payload:   Dict[str, Any] = {}
    mentions:  List[str] = []

    def model_post_init(self, __context):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = int(time.time() * 1000)

    def to_compact(self) -> str:
        """序列化为工作池紧凑格式：VERB|AGENT|TASK|STATUS|..."""
        mention_str = " ".join(f"@{m}" for m in self.mentions)
        return f"{self.verb}|{self.agent}|{self.task}|{self.status}|{self.payload}|{mention_str}".strip("|")
```

**`protocols/parser.py`** — 消息解析器，将紧凑格式解析回 Event 对象：

```python
from .verbs import Event, Verb
from typing import Optional

def parse_compact(raw: str) -> Optional[Event]:
    """解析紧凑消息格式，解析失败返回 None"""
    # 实现解析逻辑
    ...

def extract_mentions(payload_str: str) -> list[str]:
    """从 payload 字符串中提取 @mentions"""
    ...
```

**验收**：
```python
e = Event(verb=Verb.UPD, agent="BE_1", task="#auth_api", status="50%", payload={"impl": "jwt"})
assert e.id != ""
assert "UPD" in e.to_compact()
```

---

### Task 1.3 — Event Stream（`core/event_stream.py`）

使用 SQLite 实现 Append-Only 事件日志：

```python
import aiosqlite
import json
from pathlib import Path
from protocols.verbs import Event, Verb
from typing import List, Optional

DB_PATH = Path("workspace/events.db")

class EventStream:
    """
    Append-Only 事件日志。
    - 只允许 append，不允许修改或删除。
    - 支持按 task_id、agent、verb 过滤查询。
    - 支持监听新事件（用于 Observer）。
    """

    async def init(self): ...
    # 建表 SQL：
    # CREATE TABLE IF NOT EXISTS events (
    #   id TEXT PRIMARY KEY,
    #   timestamp INTEGER,
    #   verb TEXT,
    #   agent TEXT,
    #   task TEXT,
    #   status TEXT,
    #   payload TEXT,   -- JSON
    #   mentions TEXT   -- JSON array
    # )

    async def append(self, event: Event) -> str:
        """写入一条事件，返回 event.id"""
        ...

    async def query(
        self,
        task: Optional[str] = None,
        agent: Optional[str] = None,
        verb: Optional[Verb] = None,
        since_timestamp: Optional[int] = None,
        limit: int = 100
    ) -> List[Event]:
        """按条件查询事件，按 timestamp 升序返回"""
        ...

    async def tail(self, n: int = 50) -> List[Event]:
        """返回最新 n 条事件"""
        ...

    async def get_task_events(self, task_id: str) -> List[Event]:
        """返回某任务的全部事件历史"""
        ...
```

**验收**：
```python
stream = EventStream()
await stream.init()
e = Event(verb=Verb.UPD, agent="test", task="#t1", status="50%")
await stream.append(e)
results = await stream.query(task="#t1")
assert len(results) == 1
```

---

### Task 1.4 — Shared State（`core/shared_state.py`）

基于文件系统的共享状态层：

```python
from pathlib import Path
import json
from typing import Any, Optional

STATE_DIR = Path("workspace/state")

class SharedState:
    """
    所有 Agent 可读写的结构化数据区。
    键为路径格式：contracts/login_api、schemas/user_v2
    值为任意 JSON 可序列化对象。
    """

    async def write(self, path: str, data: Any, written_by: str) -> str:
        """写入数据，返回 shared:// 格式的引用指针"""
        ...

    async def read(self, path: str) -> Optional[Any]:
        """读取数据，不存在返回 None"""
        ...

    async def list(self, prefix: str = "") -> list[str]:
        """列出所有键（可按前缀过滤）"""
        ...

    def ref(self, path: str) -> str:
        """生成引用指针字符串，格式：shared://path"""
        return f"shared://{path}"
```

**验收**：
```python
ss = SharedState()
await ss.write("contracts/login", {"method": "POST", "path": "/login"}, written_by="BE_1")
data = await ss.read("contracts/login")
assert data["method"] == "POST"
```

---

### Task 1.5 — Task Graph（`core/task_graph.py`）

任务 DAG 数据结构：

```python
from pydantic import BaseModel
from typing import List, Dict, Optional
from enum import Enum

class TaskStatus(str, Enum):
    PENDING       = "pending"
    ASSIGNED      = "assigned"
    RUNNING       = "running"
    BLOCKED       = "blocked"
    REPORTED_DONE = "reported_done"
    COMPLETED     = "completed"
    FAILED        = "failed"

class TaskSpec(BaseModel):
    id:              str
    description:     str
    input:           str
    output_contract: str
    priority:        str = "P2"
    depends_on:      List[str] = []
    assigned_agent:  Optional[str] = None
    status:          TaskStatus = TaskStatus.PENDING

class TaskGraph:
    def __init__(self):
        self.tasks: Dict[str, TaskSpec] = {}

    def add_task(self, task: TaskSpec): ...
    def get_ready_tasks(self) -> List[TaskSpec]:
        """返回所有依赖已 COMPLETED 且状态为 PENDING 的任务"""
        ...
    def update_status(self, task_id: str, status: TaskStatus): ...
    def is_complete(self) -> bool:
        """所有任务 COMPLETED 返回 True"""
        ...
    def to_dict(self) -> dict: ...

    @classmethod
    def from_dict(cls, data: dict) -> "TaskGraph": ...
```

**验收**：构造含3个任务（其中一个依赖另外两个）的 DAG，验证 `get_ready_tasks()` 在依赖未完成时只返回无依赖的任务。

---

### Task 1.6 — 记忆系统（`core/memory.py`）

```python
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional
import json, time

SHARED_MEMORY_DIR = Path("workspace/memory/shared")
PERSONAL_MEMORY_DIR = Path("workspace/memory/personal")

class Lesson(BaseModel):
    id:          str
    pattern:     str
    trigger:     str
    action:      str
    scope:       str = "global"
    confidence:  float = 0.8
    decay:       float = 0.01
    version:     int = 1
    created_at:  int = 0

    def model_post_init(self, __context):
        if not self.created_at:
            self.created_at = int(time.time())

    def effective_confidence(self, now: Optional[int] = None) -> float:
        """随时间衰减的置信度"""
        elapsed_days = ((now or int(time.time())) - self.created_at) / 86400
        return max(0.0, self.confidence - self.decay * elapsed_days)

class SharedMemory:
    """共同记忆，仅 Director 可写"""

    async def write_lesson(self, lesson: Lesson, written_by: str):
        assert written_by == "DIRECTOR", "只有 Director 可写共同记忆"
        ...

    async def get_lessons(self, min_confidence: float = 0.3) -> List[Lesson]:
        """返回置信度高于阈值的 LESSON，按有效置信度降序"""
        ...

    async def get_relevant_lessons(self, context: str) -> List[Lesson]:
        """根据上下文关键词返回相关 LESSON（简单关键词匹配，v1）"""
        ...

class PersonalMemory:
    """Agent 个人记忆"""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.path = PERSONAL_MEMORY_DIR / f"{agent_id}.json"

    async def append(self, entry: dict): ...
    async def get_all(self) -> list: ...
    async def get_summary(self) -> str:
        """返回适合注入系统提示的摘要文本"""
        ...
```

**验收**：写入一条 LESSON，用非 DIRECTOR 身份写入应抛出 AssertionError。

---

### Task 1.7 — Agent 注册表（`registry/`）

**`registry/agent_registry.json`** — 初始 Agent 库：

```json
{
  "agents": [
    {
      "id": "FE",
      "name": "前端工程师",
      "capabilities": ["UI", "React", "CSS", "TypeScript", "API集成"],
      "tools": ["write_file", "read_file"],
      "prompt_version": "v1.0",
      "system_prompt_file": "agents/workers/frontend.py"
    },
    {
      "id": "BE",
      "name": "后端工程师",
      "capabilities": ["API", "业务逻辑", "JWT", "数据库交互", "Python"],
      "tools": ["write_file", "read_file", "execute_code"],
      "prompt_version": "v1.0",
      "system_prompt_file": "agents/workers/backend.py"
    },
    {
      "id": "DB",
      "name": "数据库工程师",
      "capabilities": ["Schema设计", "SQL", "查询优化", "迁移"],
      "tools": ["write_file", "read_file"],
      "prompt_version": "v1.0",
      "system_prompt_file": "agents/workers/db.py"
    },
    {
      "id": "QA",
      "name": "质量验证",
      "capabilities": ["测试", "验收", "VAL", "代码审查"],
      "tools": ["read_file", "execute_code"],
      "prompt_version": "v1.0",
      "system_prompt_file": "agents/workers/qa.py"
    },
    {
      "id": "OPS",
      "name": "部署运维",
      "capabilities": ["Docker", "CI/CD", "环境配置", "部署"],
      "tools": ["write_file", "execute_code"],
      "prompt_version": "v1.0",
      "system_prompt_file": "agents/workers/ops.py"
    }
  ]
}
```

**`registry/registry.py`**：

```python
from typing import Optional, List
import json
from pathlib import Path

class AgentRegistry:
    def __init__(self, registry_path: str = "registry/agent_registry.json"):
        ...

    def find_by_capabilities(self, required: List[str]) -> Optional[dict]:
        """按能力标签匹配，返回最优 Agent 配置"""
        ...

    def get_by_id(self, agent_id: str) -> Optional[dict]:
        ...

    def register(self, agent_config: dict):
        """新增 Agent 到注册表（需 Director 审批后调用）"""
        ...

    def list_all(self) -> List[dict]:
        ...
```

---

## 阶段二：Agent 层

**目标**：实现所有 Agent 的核心逻辑，可以独立调用 Anthropic API。

---

### Task 2.1 — Agent 基类（`agents/base.py`）

```python
import anthropic
from core.event_stream import EventStream
from core.shared_state import SharedState
from core.memory import PersonalMemory
from protocols.verbs import Event, Verb
from typing import Optional
import os

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-sonnet-4-20250514"

class BaseAgent:
    """所有 Agent 的基类"""

    def __init__(
        self,
        agent_id: str,
        system_prompt: str,
        event_stream: EventStream,
        shared_state: SharedState,
    ):
        self.id = agent_id
        self.system_prompt = system_prompt
        self.stream = event_stream
        self.state = shared_state
        self.memory = PersonalMemory(agent_id)

    async def call_llm(self, user_message: str, max_tokens: int = 2000) -> str:
        """调用 Anthropic API，返回纯文本响应"""
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )
        return response.content[0].text

    async def emit(self, verb: Verb, task: str, status: str = "",
                   payload: dict = {}, mentions: list = []) -> Event:
        """向工作池写入事件"""
        event = Event(
            verb=verb, agent=self.id, task=task,
            status=status, payload=payload, mentions=mentions
        )
        await self.stream.append(event)
        return event

    async def get_context(self, task_id: str) -> str:
        """组装任务上下文：任务历史 + 相关共享状态"""
        events = await self.stream.get_task_events(task_id)
        event_log = "\n".join(e.to_compact() for e in events)
        return f"任务事件历史：\n{event_log}"

    async def run(self, task_spec: dict, **kwargs):
        """子类实现具体执行逻辑"""
        raise NotImplementedError
```

---

### Task 2.2 — Planner（`agents/planner.py`）

Planner 的系统提示要明确其职责：分析用户需求，结合共同记忆，输出标准 JSON 格式的 Task Graph。

```python
PLANNER_SYSTEM_PROMPT = """
你是 AI Company 的 Planner，负责将用户需求转化为可执行的任务图（Task Graph）。

职责：
1. 分析用户需求，识别核心目标与约束
2. 将项目拆解为原子任务，每个任务有明确的输入、输出合约、验收标准
3. 识别任务间的依赖关系，构建 DAG（确保无环）
4. 为每个任务设定优先级（P0/P1/P2/P3）

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
    def __init__(self, event_stream, shared_state, shared_memory):
        super().__init__("PLANNER", PLANNER_SYSTEM_PROMPT, event_stream, shared_state)
        self.shared_memory = shared_memory

    async def plan(self, user_requirement: str) -> TaskGraph:
        """接收用户需求，返回 TaskGraph"""
        lessons = await self.shared_memory.get_relevant_lessons(user_requirement)
        lesson_context = self._format_lessons(lessons)

        prompt = f"""
用户需求：
{user_requirement}

历史教训（请在规划时参考，避免重蹈覆辙）：
{lesson_context}

请生成任务图。
        """

        raw = await self.call_llm(prompt)
        return self._parse_task_graph(raw)

    def _format_lessons(self, lessons) -> str: ...
    def _parse_task_graph(self, raw: str) -> TaskGraph: ...
```

---

### Task 2.3 — HR（`agents/hr.py`）

```python
HR_SYSTEM_PROMPT = """
你是 AI Company 的 HR，负责任务调度和 Agent 匹配。

职责：
1. 根据任务所需能力，从 Agent 库中匹配最合适的 Agent
2. 发送 ASN 指令分配任务
3. 维护所有任务的状态
4. 处理 Worker 的 REQ 和 CON 上报
5. 执行 Director 的 DIR 指令

匹配原则：优先有相关个人记忆的 Agent；能力覆盖率高的优先。
无匹配时：上报 Director，不自行创建。

输出格式：只输出 JSON 决策，不要其他文字。
"""

class HR(BaseAgent):
    def __init__(self, event_stream, shared_state, registry, task_graph):
        super().__init__("HR", HR_SYSTEM_PROMPT, event_stream, shared_state)
        self.registry = registry
        self.task_graph = task_graph

    async def assign_ready_tasks(self):
        """将所有就绪任务（依赖已满足）分配给合适的 Agent"""
        ready = self.task_graph.get_ready_tasks()
        for task in ready:
            agent_config = self.registry.find_by_capabilities(
                task.get("required_capabilities", [])
            )
            if agent_config:
                await self.emit(
                    verb=Verb.ASN,
                    task=task["id"],
                    status=task["priority"],
                    payload={"agent": agent_config["id"], "spec": task},
                    mentions=[agent_config["id"]]
                )
                self.task_graph.update_status(task["id"], TaskStatus.ASSIGNED)
            else:
                # 无匹配，上报 Director
                await self.emit(
                    verb=Verb.REQ,
                    task=task["id"],
                    status="NO_AGENT",
                    payload={"required_capabilities": task.get("required_capabilities")},
                    mentions=["DIRECTOR"]
                )

    async def process_events(self):
        """处理工作池中发给 HR 的事件"""
        events = await self.stream.query(mentions=["HR"])
        for event in events:
            await self._handle_event(event)

    async def _handle_event(self, event: Event): ...
```

---

### Task 2.4 — Observer（`agents/observer.py`）

Observer 不调用 LLM，纯规则检测，节省 token：

```python
from dataclasses import dataclass
from typing import List
import time

STALL_THRESHOLD_MS = 300_000   # 5分钟无 UPD 视为 stall
CON_SPIKE_THRESHOLD = 3        # 同一任务10分钟内3次 CON 视为 spike

class Observer:
    """纯规则检测，不调用 LLM，不写 Event Stream（只产生 ALERT 对象）"""

    def __init__(self, event_stream: EventStream):
        self.stream = event_stream

    async def scan(self) -> List[dict]:
        """扫描 Event Stream，返回所有检测到的 ALERT 列表"""
        alerts = []
        alerts += await self._detect_stalls()
        alerts += await self._detect_conflict_spikes()
        alerts += await self._detect_deadlocks()
        return alerts

    async def _detect_stalls(self) -> List[dict]:
        """检测长时间无 UPD 的 RUNNING 任务"""
        now = int(time.time() * 1000)
        recent = await self.stream.tail(200)
        # 找出 RUNNING 任务中最后一次 UPD 超过阈值的
        ...

    async def _detect_conflict_spikes(self) -> List[dict]:
        """检测短时间内 CON 频发的任务"""
        ...

    async def _detect_deadlocks(self) -> List[dict]:
        """检测 BLK 循环依赖"""
        ...
```

---

### Task 2.5 — Director（`agents/director.py`）

```python
DIRECTOR_SYSTEM_PROMPT = """
你是 AI Company 的 Director，负责系统监控、异常干预和策略学习。

职责：
1. 接收 Observer 的 ALERT，分析根因，下达 DIR 指令
2. 在模式反复出现时，提炼为 LESSON 写入共同记忆
3. 审批 HR 上报的新 Agent 创建请求

决策原则：
- 最小干预：只在必要时介入，不打扰正常执行的 Agent
- 根因导向：解决根本问题，不是表面症状
- 经验沉淀：发现系统性规律时主动写 LESSON

输出格式（JSON）：
{
  "actions": [
    {"type": "DIR", "target": "agent_id", "task": "#task_id", "instruction": "..."},
    {"type": "LESSON", "pattern": "...", "trigger": "...", "action": "..."}
  ]
}
"""

class Director(BaseAgent):
    def __init__(self, event_stream, shared_state, shared_memory, task_graph):
        super().__init__("DIRECTOR", DIRECTOR_SYSTEM_PROMPT, event_stream, shared_state)
        self.shared_memory = shared_memory
        self.task_graph = task_graph

    async def handle_alerts(self, alerts: List[dict]):
        if not alerts:
            return
        prompt = self._build_decision_prompt(alerts)
        raw = await self.call_llm(prompt)
        actions = self._parse_actions(raw)
        await self._execute_actions(actions)

    async def _execute_actions(self, actions: list):
        for action in actions:
            if action["type"] == "DIR":
                await self.emit(
                    verb=Verb.DIR,
                    task=action["task"],
                    status=action.get("action_type", ""),
                    payload={"instruction": action["instruction"]},
                    mentions=[action["target"]]
                )
            elif action["type"] == "LESSON":
                lesson = Lesson(
                    id=f"LESSON#{int(time.time())}",
                    pattern=action["pattern"],
                    trigger=action["trigger"],
                    action=action["action"]
                )
                await self.shared_memory.write_lesson(lesson, written_by="DIRECTOR")

    def _build_decision_prompt(self, alerts: list) -> str: ...
    def _parse_actions(self, raw: str) -> list: ...
```

---

### Task 2.6 — Worker Agents（`agents/workers/`）

每个 Worker 继承 BaseAgent，实现自己的 `run()` 方法。以 `backend.py` 为例：

```python
BACKEND_SYSTEM_PROMPT = """
你是 AI Company 的后端工程师（BE）。

专业能力：API 设计、业务逻辑实现、JWT 认证、数据库交互、Python/FastAPI。

工作规则：
1. 收到任务后先读取任务规范（TASK_SPEC）和相关 Shared State
2. 执行过程中定期发送 UPD 报告进度
3. 遇到依赖未就绪时发送 BLK 并明确说明等待什么
4. 发现冲突（如接口定义不一致）立即发送 CON 上报
5. 认为完成时发送 DON，等待 QA 验收，不自行宣告完成

输出时：将产出写入 Shared State，在 DON 的 payload 中给出路径引用（shared://...）
"""

class BackendAgent(BaseAgent):
    def __init__(self, event_stream, shared_state):
        super().__init__("BE", BACKEND_SYSTEM_PROMPT, event_stream, shared_state)

    async def run(self, task_spec: dict):
        task_id = task_spec["id"]
        await self.emit(Verb.ACK, task_id, "ACCEPT")

        context = await self.get_context(task_id)
        personal_memory = await self.memory.get_summary()

        prompt = f"""
任务规范：{task_spec}
任务上下文：{context}
个人经验：{personal_memory}

请执行任务。执行过程中若需要阻塞或上报冲突，请明确说明。
最终输出代码/文档后，说明写入了哪个 shared:// 路径。
        """

        result = await self.call_llm(prompt, max_tokens=4000)
        output_path = await self._save_output(task_id, result)

        await self.emit(Verb.DON, task_id, "OK",
                        payload={"out": self.state.ref(output_path)})
        await self.memory.append({"task": task_id, "summary": result[:200]})

    async def _save_output(self, task_id: str, content: str) -> str:
        path = f"outputs/{task_id.strip('#')}"
        await self.state.write(path, {"content": content}, written_by=self.id)
        return path
```

QA Agent 需要特别实现 VAL 逻辑：读取 DON 事件中的输出路径，验证是否符合 output_contract，发送 VAL PASS 或 VAL FAIL。

---

## 阶段三：系统集成

**目标**：将所有模块串联为可运行的主循环。

---

### Task 3.1 — 主循环（`main.py`）

```python
import asyncio
from dotenv import load_dotenv
from core.event_stream import EventStream
from core.shared_state import SharedState
from core.memory import SharedMemory
from core.task_graph import TaskGraph, TaskStatus
from agents.planner import Planner
from agents.observer import Observer
from agents.director import Director
from agents.hr import HR
from agents.workers.backend import BackendAgent
from agents.workers.frontend import FrontendAgent
from agents.workers.qa import QAAgent
from registry.registry import AgentRegistry
from protocols.verbs import Verb

load_dotenv()

async def main(user_requirement: str):
    # 1. 初始化基础设施
    stream = EventStream()
    await stream.init()
    state = SharedState()
    shared_memory = SharedMemory()
    registry = AgentRegistry()

    # 2. Planner 生成任务图
    print("🧠 Planner 规划中...")
    planner = Planner(stream, state, shared_memory)
    task_graph = await planner.plan(user_requirement)
    print(f"✅ 生成 {len(task_graph.tasks)} 个任务")

    # 3. 初始化 HR 和 Workers
    hr = HR(stream, state, registry, task_graph)
    workers = {
        "FE": FrontendAgent(stream, state),
        "BE": BackendAgent(stream, state),
        "QA": QAAgent(stream, state),
    }
    observer = Observer(stream)
    director = Director(stream, state, shared_memory, task_graph)

    # 4. 主执行循环
    print("🚀 开始执行...")
    while not task_graph.is_complete():
        # HR 分配就绪任务
        await hr.assign_ready_tasks()

        # 并发执行被分配的任务
        assigned_events = await stream.query(verb=Verb.ASN)
        worker_tasks = []
        for event in assigned_events:
            agent_id = event.payload.get("agent")
            if agent_id in workers:
                spec = event.payload.get("spec", {})
                worker_tasks.append(workers[agent_id].run(spec))

        if worker_tasks:
            await asyncio.gather(*worker_tasks, return_exceptions=True)

        # Observer 扫描 + Director 干预
        alerts = await observer.scan()
        if alerts:
            await director.handle_alerts(alerts)

        # 等待下一轮
        await asyncio.sleep(1)

    print("🎉 项目完成")

if __name__ == "__main__":
    requirement = input("请输入项目需求：")
    asyncio.run(main(requirement))
```

---

### Task 3.2 — 配置文件（`config.py`）

```python
from pathlib import Path

# 路径
WORKSPACE_DIR     = Path("workspace")
EVENTS_DB         = WORKSPACE_DIR / "events.db"
STATE_DIR         = WORKSPACE_DIR / "state"
SHARED_MEMORY_DIR = WORKSPACE_DIR / "memory/shared"
PERSONAL_MEMORY_DIR = WORKSPACE_DIR / "memory/personal"
REGISTRY_PATH     = Path("registry/agent_registry.json")

# 模型
DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS    = 2000

# Observer 阈值
STALL_THRESHOLD_MS    = 300_000   # 5分钟
CON_SPIKE_THRESHOLD   = 3
CON_SPIKE_WINDOW_MS   = 600_000   # 10分钟

# 系统限制
MAX_TASK_RETRIES = 3
AGENT_CREATE_MIN_PRIORITY = "P1"
```

---

## 阶段四：测试验收

**目标**：验证系统每个层次的正确性。

---

### Task 4.1 — 单元测试

**`tests/test_event_stream.py`**：验证 append / query / tail 正确性，验证 Append-Only（无 delete 接口）。

**`tests/test_protocols.py`**：验证 Event 序列化/反序列化，验证 to_compact() 格式，验证非法 verb 被拦截。

**`tests/test_planner.py`**：Mock LLM 返回，验证 Task Graph 解析正确，验证依赖关系无环检测。

**`tests/test_memory.py`**：验证 LESSON 写入权限控制，验证 effective_confidence 衰减计算。

---

### Task 4.2 — 最小闭环集成测试（`tests/test_minimal_loop.py`）

用一个最简单的真实场景验证端到端流程：

```
需求："创建一个返回 Hello World 的 API 接口"

预期流程：
1. Planner 生成 2 个任务（#design_api, #implement_api）
2. HR 分配 #design_api 给 BE
3. BE 执行，发 DON
4. QA 验收，发 VAL PASS
5. #implement_api 解锁，HR 分配给 BE
6. BE 执行，发 DON
7. QA 验收，发 VAL PASS
8. task_graph.is_complete() == True

验收标准：
- Event Stream 中有完整的 ASN→ACK→UPD→DON→VAL 链路
- Shared State 中有产出物
- 无异常退出
```

---

## 执行顺序总结

```
阶段一（基础设施）
  Task 1.1  初始化项目结构
  Task 1.2  通信协议层
  Task 1.3  Event Stream（SQLite）
  Task 1.4  Shared State（文件系统）
  Task 1.5  Task Graph（DAG）
  Task 1.6  记忆系统
  Task 1.7  Agent 注册表

阶段二（Agent 层）
  Task 2.1  BaseAgent 基类
  Task 2.2  Planner
  Task 2.3  HR
  Task 2.4  Observer（纯规则，不调 LLM）
  Task 2.5  Director
  Task 2.6  Worker Agents（FE / BE / QA / OPS）

阶段三（系统集成）
  Task 3.1  主循环 main.py
  Task 3.2  配置文件 config.py

阶段四（测试验收）
  Task 4.1  单元测试
  Task 4.2  最小闭环集成测试
```

---

## 给 Claude Code 的执行指令

> 按照以上任务顺序执行。每完成一个 Task，运行对应验收检查后再继续。如遇到接口定义冲突，以本文档为准。所有 LLM 调用统一使用 `claude-sonnet-4-20250514` 模型，不使用其他模型。Observer 在 v1 阶段禁止调用 LLM，只做规则检测。共同记忆的写入权限严格限制为 DIRECTOR，其他角色写入应抛出异常。

---

*Build Plan v1.0 | 对应 AI Company Spec v1.0*
