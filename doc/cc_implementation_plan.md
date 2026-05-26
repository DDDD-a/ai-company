# Implementation Plan: AI Company Work Pool

## Goal
Build a multi-agent work pool system where agents communicate via a structured event protocol.
Agents use LLM prompt-based output (FILE: blocks) to create files — they never write protocol format directly.

---

## Project Structure

```
ai_company/
├── main.py                    # Entry point — infra init + main execution loop
├── console.py                 # Interactive terminal UI (prompt_toolkit)
├── config.py                  # Global config: paths, LLM defaults, thresholds
├── core/
│   ├── __init__.py
│   ├── event_stream.py        # Append-only event log (SQLite via aiosqlite)
│   ├── llm_provider.py        # LLM abstraction: OpenAI-compatible + Anthropic
│   ├── shared_state.py        # File-based key-value store (shared:// paths)
│   ├── memory.py              # SharedMemory (Director-writable) + PersonalMemory
│   └── task_graph.py          # Task DAG: TaskSpec, TaskStatus, cycle detection
├── agents/
│   ├── __init__.py
│   ├── base.py                # BaseAgent: LLM call, emit(), file I/O, context builder
│   ├── planner.py             # Planner: requirement → TaskGraph (one-shot)
│   ├── hr.py                  # HR: assigns tasks via capability matching
│   ├── observer.py            # Observer: rule-based anomaly detection (no LLM)
│   ├── director.py            # Director: handles ALERTs, writes LESSONs
│   └── workers/
│       ├── __init__.py
│       ├── backend.py         # BE: API, business logic, JWT, Python
│       ├── frontend.py        # FE: UI, React, CSS, TypeScript
│       ├── db.py              # DB: Schema design, SQL, migrations
│       ├── qa.py              # QA: validates DON output against contract
│       └── ops.py             # OPS: project init, Docker, CI/CD, deployment
├── protocols/
│   ├── __init__.py
│   ├── verbs.py               # Verb enum + Event pydantic model
│   └── parser.py              # Parse compact format back to Event objects
├── registry/
│   ├── __init__.py
│   ├── registry.py            # AgentRegistry: capability-based matching
│   └── agent_registry.json    # Static agent definitions
├── workspace/                  # Runtime data (gitignored)
│   ├── events.db
│   ├── state/
│   ├── memory/
│   └── projects/
├── requirements.txt
└── .env
```

---

## Architecture Overview

### No Tool-Use Loop
Unlike the Anthropic SDK tool-use pattern, this system uses **prompt-based output parsing**:
- Each agent has a detailed system prompt with output format instructions
- Agents call LLM → receive text → parse FILE: blocks → write files to disk
- This works with any OpenAI-compatible API (DeepSeek, Qwen, GLM, etc.)

### Event-Driven Communication
- All communication goes through the append-only Event Stream (SQLite)
- Agents `emit()` events and `query()` for context
- Observer polls the stream periodically for anomaly patterns
- Director handles ALERTs by issuing DIR instructions

### Execution Flow
```
User Requirement
  → Planner (LLM: requirement → TaskGraph JSON)
  → HR assigns tasks to Workers by capability matching
  → Main Loop iterates:
      Workers run tasks (LLM: task → FILE: blocks → disk)
      QA validates outputs
      Observer scans for anomalies
      Director handles ALERTs
  → All tasks COMPLETED or max iterations reached
```

---

## Step 1: LLM Provider (`core/llm_provider.py`)

Abstracts the LLM API behind a simple `chat()` interface.

```python
class LLMProvider(ABC):
    async def chat(system_prompt, user_message, max_tokens, temperature) -> str: ...

class OpenAICompatibleProvider(LLMProvider):
    # Uses openai.AsyncOpenAI — works with DeepSeek, Qwen, GLM, etc.
    # Configure via: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

class AnthropicProvider(LLMProvider):
    # Uses anthropic.Anthropic — optional alternative
```

**Default**: `OpenAICompatibleProvider` with `deepseek-chat` model.

---

## Step 2: Protocol Layer (`protocols/`)

### Verbs (`verbs.py`)
11 verbs covering all agent communication:

| Verb | Sender | Meaning |
|------|--------|---------|
| `ASN` | HR | Assign task |
| `ACK` | Worker | Acknowledge receipt |
| `UPD` | Worker | Progress update |
| `BLK` | Worker | Blocked on dependency |
| `REQ` | Worker | Request decision/resource |
| `RES` | HR/Director | Respond to REQ |
| `CON` | Worker | Report conflict |
| `DON` | Worker | Self-report completion |
| `VAL` | QA | Validation result (PASS/FAIL) |
| `DIR` | Director | Intervention instruction |
| `ALERT` | Observer | Anomaly warning |

### Event Model (`verbs.py`)
```python
class Event(BaseModel):
    id: str              # uuid4
    timestamp: int       # unix ms
    verb: Verb
    agent: str
    task: str
    status: str          # e.g. "50%", "PASS", "WAIT"
    payload: dict        # arbitrary metadata
    mentions: list[str]  # agents to notify
```

### Compact Format
`VERB|AGENT|TASK|STATUS|payload_key:value|@MENTIONS`

### Parser (`parser.py`)
`parse_compact(raw) → Event` — parses compact format back to Event objects.

---

## Step 3: Event Stream (`core/event_stream.py`)

Append-only SQLite event log using `aiosqlite`.

```sql
CREATE TABLE events (
    id        TEXT PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    verb      TEXT NOT NULL,
    agent     TEXT NOT NULL,
    task      TEXT NOT NULL DEFAULT '',
    status    TEXT NOT NULL DEFAULT '',
    payload   TEXT NOT NULL DEFAULT '{}',
    mentions  TEXT NOT NULL DEFAULT '[]'
)
```

Key methods:
- `append(event) → event_id`
- `query(task, agent, verb, mentions, since, limit) → list[Event]`
- `tail(n) → list[Event]` — latest n events
- `get_task_events(task_id) → list[Event]`
- `count() → int`

---

## Step 4: Task Graph (`core/task_graph.py`)

Planner outputs a DAG of TaskSpec nodes.

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    BLOCKED = "blocked"
    REPORTED_DONE = "reported_done"
    COMPLETED = "completed"
    FAILED = "failed"

class TaskSpec(BaseModel):
    id: str                     # e.g. "#design_api"
    description: str
    input: str
    output_contract: str        # acceptance criteria
    priority: str               # P0/P1/P2/P3
    depends_on: list[str]       # task IDs this depends on
    required_capabilities: list[str]
    assigned_agent: Optional[str]
    status: TaskStatus
    retry_count: int
    last_fail_reason: str
```

Key methods:
- `get_ready_tasks()` — tasks whose deps are satisfied and status is PENDING
- `assign_agent(task_id, agent_id)` — PENDING → ASSIGNED
- `update_status(task_id, status)`
- `has_cycles()` — DFS cycle detection
- `progress()` — task count summary

---

## Step 5: Shared State & Memory (`core/shared_state.py`, `core/memory.py`)

### SharedState
File-based key-value store. Keys like `contracts/login_api` → `workspace/state/contracts/login_api.json`.

- `write(path, data, written_by) → "shared://path"`
- `read(path) → data`
- Agents reference outputs via `shared://` URIs in event payloads

### SharedMemory (Policy Layer)
Only Director can write LESSON entries. Pattern → trigger → action, with confidence decay.
- `write_lesson(lesson, "DIRECTOR")`
- `get_lessons(min_confidence) → list[Lesson]`
- `get_relevant_lessons(context) → list[Lesson]` — keyword matching for Planner

### PersonalMemory
Per-agent experience log (`workspace/memory/personal/{agent_id}.json`).
- `append(entry)` — capped at 200 entries
- `get_summary()` — injected into Worker context prompts

---

## Step 6: Base Agent (`agents/base.py`)

All agents inherit from this. Provides:

```python
class BaseAgent:
    id: str                    # e.g. "BE", "HR", "QA"
    system_prompt: str         # injected into every LLM call
    stream: EventStream        # for emit() and query()
    state: SharedState         # for read/write shared state
    memory: PersonalMemory     # agent's private experience
    llm: LLMProvider           # configured via env
    project_dir: Path          # workspace/projects/{name}/

    # Core methods:
    async call_llm(user_message, max_tokens, temperature) → str
    async emit(verb, task, status, payload, mentions) → Event
    async get_context(task_id) → str   # event history + files + memory
    async run(task_spec)               # implemented by subclasses

    # File operations:
    _extract_files(llm_output) → (cleaned_text, file_dicts)
    _write_files(file_dicts) → written_paths
    _process_output(task_id, llm_output) → {"files": [...], "summary": "...", "shared_path": "..."}
```

### How Workers Execute Tasks

1. `run(task_spec)` is called by the main loop
2. Agent emits `ACK ACCEPT`
3. Builds context: event history + existing project files + personal memory
4. Calls `call_llm()` with: task spec + context + output format instructions
5. LLM returns text with `FILE:path\n```lang\ncode\n``` blocks
6. `_process_output()` extracts FILE: blocks, writes them to `workspace/projects/{name}/`, stores summary to SharedState
7. Agent emits `DON` with `out=shared://outputs/{task_id}`
8. PersonalMemory is updated with task summary

### Output Format (understood by all Workers)
```
FILE:src/app.py
```python
code content
```

FILE:src/components/Button.tsx
```tsx
component code
```

DONE: summary of what was created
```

Security: Path sanitization prevents directory traversal. Absolute paths and dangerous prefixes (`home/`, `root/`, `etc/`) are stripped.

---

## Step 7: Planner (`agents/planner.py`)

One-shot generator. Converts user requirement → TaskGraph.

```
User requirement
  + relevant LESSONs from SharedMemory (keyword match)
  → LLM call with PLANNER_SYSTEM_PROMPT
  → JSON parse → TaskSpec list → TaskGraph
```

Output format:
```json
{
  "tasks": [
    {
      "id": "#design_schema",
      "description": "Design database schema for users and posts",
      "input": "User requirement specification",
      "output_contract": "schema.sql with CREATE TABLE statements",
      "priority": "P1",
      "depends_on": [],
      "required_capabilities": ["Schema设计", "SQL"]
    }
  ]
}
```

---

## Step 8: HR (`agents/hr.py`)

Task assignment by capability matching.

- `assign_ready_tasks()`: for each PENDING task with satisfied deps:
  1. Query `AgentRegistry.find_by_capabilities(required_caps)` → best match
  2. `task_graph.assign_agent(task_id, agent_id)` → ASSIGNED
  3. `emit(ASN, ...)` with task spec in payload, @mention the worker
  4. If no match → emit `REQ` escalated to DIRECTOR

- Handles DIR instructions: re-assigns tasks per Director's orders

---

## Step 9: Worker Agents (`agents/workers/`)

| Agent | ID | Capabilities |
|-------|-----|-------------|
| BackendAgent | BE | API, business logic, JWT, database interaction, Python |
| FrontendAgent | FE | UI, React, CSS, TypeScript, API integration |
| DBAgent | DB | Schema design, SQL, query optimization, migrations |
| QAAgent | QA | Testing, acceptance, VAL, code review |
| OpsAgent | OPS | Docker, CI/CD, environment config, deployment |

Each worker:
1. Receives `task_spec` dict (may include `_retry_count` and `_last_fail_reason`)
2. Sends `ACK ACCEPT`
3. Calls LLM with task spec + context
4. Parses output for FILE: blocks, writes files
5. Sends `DON` with output path
6. Updates personal memory

DB and OPS agents include a retry-if-no-files-extracted guard.

### QA Validation (`qa.py`)
Triggered when main loop sees a DON event:
1. Reads task output from SharedState
2. Reads actual file contents created
3. Calls LLM with: task spec + output_contract + file contents
4. Returns `{"verdict": "PASS"|"FAIL", "reason": "...", "issues": [...]}`
5. Emits `VAL PASS` or `VAL FAIL`

---

## Step 10: Observer (`agents/observer.py`)

**Pure rule-based — no LLM calls.** Detects anomalies by scanning the event stream:

| Detection | Rule |
|-----------|------|
| `stall` | RUNNING task with no UPD in 5 minutes |
| `conflict_spike` | ≥3 CON events on same task in 10 minutes |
| `deadlock` | Two tasks with circular BLK dependencies |
| `overload` | Single agent with ≥5 concurrent RUNNING tasks |
| `quality_failure` | ≥3 VAL FAILs on same task since last PASS |

`scan()` returns a list of ALERT dicts consumed by Director.

---

## Step 11: Director (`agents/director.py`)

Triggered when Observer produces ALERTs. The only agent that can write LESSONs.

```python
async handle_alerts(alerts):
    1. Build prompt: alert details + current task statuses + progress stats
    2. LLM call → parse actions JSON
    3. For each DIR action: validate target (must be FE/BE/DB/QA/OPS/HR), emit DIR event
    4. For each LESSON action: write to SharedMemory
```

ALERT → intervention mapping:
- `quality_failure` → DIR to worker with specific fix instructions
- `stall` → DIR to HR to reassign
- `deadlock` → DIR to blocking agents
- `overload` → DIR to HR to redistribute
- `conflict_spike` → DIR to conflicting agents to align interfaces
- `MAX_RETRIES` → DIR to HR to reassign or split task

---

## Step 12: Main Loop (`main.py`)

```python
async def main(requirement):
    1. infra = create_infrastructure()     # EventStream, SharedState, SharedMemory, Registry
    2. task_graph = plan_project()         # Planner generates TaskGraph
    3. workers = create_workers()          # FE, BE, DB, QA, OPS
    4. run_project_loop()                  # Main execution loop

async def run_project_loop():
    while not task_graph.is_complete():
        hr.assign_ready_tasks()               # HR assigns PENDING tasks
        for task in running_tasks:
            worker.run(task_spec)             # Workers execute
        for done_task in REPORTED_DONE tasks:
            qa.validate(task_spec, don_event) # QA validates
        alerts = observer.scan()              # Observer detects anomalies
        director.handle_alerts(alerts)        # Director intervenes
        sleep(config.MAIN_LOOP_INTERVAL_SEC)
```

---

## Step 13: Console UI (`console.py`)

Interactive terminal using `prompt_toolkit`. Supports:

- **Natural language**: type requirements directly, LLM parser determines intent
- **`/` commands**: `/start`, `/status`, `/pool`, `/agents`, `/lessons`, `/task`, `/intervene`, `/output`, `/stop`, `/watch`, `/help`
- **Tab completion**: commands, task IDs (#xxx), agent IDs, file paths
- **Status toolbar**: progress bar, task counts, iteration counter
- **Real-time event monitoring**: `/watch` command

---

## Step 14: Config (`config.py`)

```python
# Paths
WORKSPACE_DIR = Path("workspace")
EVENTS_DB = WORKSPACE_DIR / "events.db"
STATE_DIR = WORKSPACE_DIR / "state"
SHARED_MEMORY_DIR = WORKSPACE_DIR / "memory/shared"
PERSONAL_MEMORY_DIR = WORKSPACE_DIR / "memory/personal"

# LLM (defaults, overridable via .env)
DEFAULT_PROVIDER = "openai"
DEFAULT_OPENAI_MODEL = "deepseek-chat"
DEFAULT_OPENAI_BASE_URL = "https://api.deepseek.com"

# Observer thresholds
STALL_THRESHOLD_MS = 300_000        # 5 minutes
CON_SPIKE_THRESHOLD = 3
CON_SPIKE_WINDOW_MS = 600_000       # 10 minutes
OVERLOAD_THRESHOLD = 5
QUALITY_FAIL_THRESHOLD = 3

# System limits
MAX_TASK_RETRIES = 3
MAIN_LOOP_INTERVAL_SEC = 1
```

---

## Requirements

```
openai>=1.0.0
aiosqlite>=0.19.0
python-dotenv>=1.0.0
pydantic>=2.0.0
prompt_toolkit>=3.0.0
```

Optional: `anthropic>=0.25.0` (only if switching to Claude).

---

## Key Invariants

- Agents NEVER write protocol format (pipe-delimited strings) — that's handled by `Event.to_compact()` for display only
- All work pool communication goes through `emit()` method only
- Observer uses **rule-based detection only** — no LLM calls in `observer.scan()`
- Only Director writes to SharedMemory (LESSONs)
- `MAX_TASK_RETRIES` is enforced in the main loop — tasks exceed limit go to FAILED
- All large content (code, specs, feedback) is stored as files on disk; events carry only `shared://` paths and metadata
- Workers create files via LLM output parsing (FILE: blocks), not via tool-use loops
- Path sanitization in `BaseAgent._write_files()` prevents directory traversal

---

## Implementation Order

Built and tested in this sequence:

1. `core/llm_provider.py` → verify DeepSeek API connectivity
2. `protocols/verbs.py` → Event model validation
3. `core/event_stream.py` → append + query cycle
4. `core/task_graph.py` → DAG building + cycle detection
5. `core/shared_state.py` + `core/memory.py` → read/write/decay
6. `agents/base.py` → FILE: extraction + file writing
7. `agents/planner.py` → requirement → TaskGraph
8. `agents/hr.py` + `registry/` → capability matching
9. `agents/workers/*.py` → single task execution
10. `agents/observer.py` → scan + alert generation
11. `agents/director.py` → alert → intervention + LESSON writing
12. `main.py` → full end-to-end loop
13. `console.py` → interactive terminal UI
