# AI Company — Work Pool Communication Protocol v1.1

---

## 1. Design Principles

- **Maximum efficiency**: Each message targets 15–30 tokens; human readability is a secondary concern
- **Events, not content**: Messages carry only "what happened"; content lives in Shared State, messages carry `shared://` pointers
- **Precise wake-up**: Workers are silent by default and respond only when `@`-mentioned, eliminating wasteful token consumption
- **Immutability**: The work pool is append-only; events cannot be modified after writing
- **Guaranteed termination**: Every execution path must have a reachable terminal state; the system enforces all retry limits at the infrastructure layer

---

## 2. Message Format

```
VERB|AGENT|TASK|STATUS|PAYLOAD|@MENTIONS
```

| Field | Description | Example |
|-------|-------------|---------|
| `VERB` | Action verb — see verb set below | `UPD` |
| `AGENT` | Sender agent ID | `BE` |
| `TASK` | Task ID, prefixed with `#` | `#auth_api` |
| `STATUS` | Status or progress descriptor | `50%` / `WAIT` / `OK` |
| `PAYLOAD` | Key-value pairs carrying necessary metadata | `impl:jwt;next:refresh` |
| `@MENTIONS` | Agents to wake up, space-separated | `@HR @DIRECTOR` |

**Omission rules**: STATUS / PAYLOAD / @MENTIONS may be omitted if empty. The compact format is used for display and logging; events are actually stored as structured JSON in SQLite.

---

## 3. Verb Set

| Verb | Sender | Receiver | Meaning |
|------|--------|----------|---------|
| `ASN` | HR | Worker | Assign a task |
| `ACK` | Worker | System | Acknowledge task receipt (ACCEPT / REJECT) |
| `UPD` | Worker | System | Progress update |
| `BLK` | Worker | HR / dependency | Blocked, waiting on dependency |
| `REQ` | Worker | HR / Director | Request a decision or resource |
| `RES` | HR / Director | Worker | Respond to a REQ |
| `CON` | Worker | HR / Director | Report a conflict |
| `DON` | Worker | QA | Self-report execution complete (pending validation) |
| `VAL` | QA | System | Validation result: PASS / FAIL |
| `DIR` | Director | Any | Intervention instruction |
| `ALERT` | Observer | Director | Anomaly pattern warning |

---

## 4. Verb Specifications

### ASN — Assign Task
```
ASN|HR|#task_id|PRIORITY|agent:AGENT_ID;spec:shared://path @AGENT_ID
```
- `PRIORITY`: P0 / P1 / P2 / P3
- `spec`: Points to the task specification (inlined in payload for v1)

**Example**
```
ASN|HR|#auth_api|P1|agent:BE;spec:{id:#auth_api,...} @BE
```

---

### ACK — Acknowledge Receipt
```
ACK|AGENT_ID|#task_id|ACCEPT
ACK|AGENT_ID|#task_id|REJECT|reason:cannot_execute_reason @HR
```

**Examples**
```
ACK|BE|#auth_api|ACCEPT
ACK|BE|#auth_api|REJECT|reason:no_db_access @HR
```

---

### UPD — Progress Update
```
UPD|AGENT_ID|#task_id|PERCENT|key:value
```
- `PERCENT`: Current progress percentage or phase label
- PAYLOAD describes what was completed and what comes next

**Examples**
```
UPD|BE|#auth_api|50%|impl:jwt;next:token_refresh
UPD|FE|#login_ui|STYLING|impl:form+validation;pending:api_contract
```

---

### BLK — Blocked
```
BLK|AGENT_ID|#task_id|WAIT|dep:#blocked_by_task @BLOCKING_AGENT @HR
```
- `dep`: Declares the blocking source (task ID or resource path)
- Must `@` the blocking dependency and HR

**Examples**
```
BLK|BE|#auth_api|WAIT|dep:#db_schema @DB @HR
BLK|FE|#login_ui|WAIT|dep:shared://contracts/login_api @BE @HR
```

---

### REQ — Request Decision or Resource
```
REQ|AGENT_ID|#task_id|DECISION|question:shared://path @TARGET
REQ|AGENT_ID|#task_id|RESOURCE|need:resource_description @HR
```
- Worker enters BLOCKED state after sending REQ; awaits `RES` to resume

**Examples**
```
REQ|FE|#login_ui|DECISION|question:shared://req/login_storage @HR
REQ|BE|#auth_api|RESOURCE|need:db_write_permission @HR
```

---

### RES — Respond to REQ
```
RES|HR|#task_id|DECISION|answer:shared://path @AGENT_ID
RES|DIRECTOR|#task_id|RESOURCE|granted:resource_name @AGENT_ID
RES|HR|#task_id|DENIED|reason:description @AGENT_ID
```
- On `RES` arrival, HR transitions the task from BLOCKED back to RUNNING
- On `DENIED`, the Worker decides independently whether to re-issue REQ or escalate via CON

**Examples**
```
RES|HR|#login_ui|DECISION|answer:shared://res/login_storage @FE
RES|DIRECTOR|#auth_api|RESOURCE|granted:db_write_permission @BE
```

---

### CON — Report Conflict
```
CON|AGENT_ID|#task_id|CONFLICT|path:conflict_path;with:@counterpart @HR
```
- `path`: Location of the conflict (file path / API endpoint / resource name)

**Examples**
```
CON|FE|#ui_kit|CONFLICT|path:components/button.tsx;with:@DESIGN @HR
CON|BE|#auth_api|CONFLICT|path:POST/login;schema_mismatch:@FE @HR
```

---

### DON — Self-Report Completion
```
DON|AGENT_ID|#task_id|OK|out→shared://output_path @QA
```
- **`@QA` is mandatory** — QA activates validation upon receipt
- DON does not mean completion; task must await VAL PASS

**Examples**
```
DON|DB|#db_schema|OK|out→shared://outputs/db_schema @QA
DON|BE|#auth_api|OK|out→shared://outputs/auth_api @QA
```

---

### VAL — Validation Result
```
VAL|QA|#task_id|PASS
VAL|QA|#task_id|FAIL|reason:shared://qa/feedback @AGENT_ID
```
- On FAIL, failure details (reason, issues list) are included in the payload
- Main loop increments retry_count and either re-runs or marks FAILED

**Examples**
```
VAL|QA|#db_schema|PASS
VAL|QA|#auth_api|FAIL|reason:jwt_missing;issues:[token_expiry_not_implemented] @BE
```

---

### DIR — Intervention Instruction
```
DIR|DIRECTOR|#task_id|ACTION_TYPE|instruction:shared://path @TARGET
```

**ACTION_TYPE — Currently Implemented**

| Type | Meaning |
|------|---------|
| `INTERVENE` | General intervention with specific instructions |
| `REASSIGN` | Reassign to a different agent |
| `CANCEL` | Cancel task (not yet enforced in state machine) |

**Examples**
```
DIR|DIRECTOR|#auth_api|INTERVENE|instruction:fix_jwt @BE
DIR|DIRECTOR|#auth_api|REASSIGN|target:BE2;reason:retry_exhausted @HR
```

---

### ALERT — Anomaly Warning
```
ALERT|OBS|#task_id|ALERT_TYPE|evidence:event_ids;meta:key:value @DIRECTOR
```
- Only Observer may send ALERT
- All ALERTs are directed to Director only

**ALERT_TYPE — Complete List**

| Type | Trigger Condition | Suggested Director Action |
|------|------------------|--------------------------|
| `stall` | No UPD from Worker beyond idle threshold (5 min) | REASSIGN |
| `deadlock` | Circular dependency chain detected | CANCEL one node |
| `conflict_spike` | CON on same task ≥ 3 within 10 minutes | DIR arbitration |
| `overload` | Single agent concurrent tasks ≥ 5 | REASSIGN |
| `quality_failure` | Same task VAL FAIL ≥ 3 consecutive times | Early intervention |
| `MAX_RETRIES` | Task exhausted all retry attempts | DIR to HR for reassign or split |

**Examples**
```
ALERT|OBS|#auth_api|stall|evidence:evt_123,evt_124;idle:8min @DIRECTOR
ALERT|OBS|SYSTEM|deadlock|chain:#task_a→#task_b→#task_a @DIRECTOR
ALERT|OBS|#auth_api|MAX_RETRIES|retries:3;reason:persistent_test_failure @DIRECTOR
```

---

## 5. Event Data Structure

```json
{
  "id":        "uuid-v4",
  "timestamp": 1716000000000,
  "verb":      "UPD",
  "agent":     "BE",
  "task":      "#auth_api",
  "status":    "50%",
  "payload":   { "impl": "jwt", "next": "token_refresh" },
  "mentions":  []
}
```

| Field | Description |
|-------|-------------|
| `id` | UUID v4, auto-generated on creation |
| `timestamp` | Unix timestamp in milliseconds, set at event creation |
| `verb` | One of the 11 defined verbs |
| `agent` | Sender agent ID |
| `task` | Task ID this event relates to |
| `status` | Short status string (progress %, PASS/FAIL, WAIT, etc.) |
| `payload` | Arbitrary JSON dict for event metadata |
| `mentions` | List of agent IDs to notify |

Events are stored in SQLite (`workspace/events.db`) with the following schema:

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
);
```

---

## 6. Task State Machine

```
PENDING
  ↓ HR.assign_ready_tasks()
ASSIGNED
  ↓ Worker ACK ACCEPT
RUNNING
  ↓ Worker UPD (loop during execution)
  ↓ Worker BLK / REQ
BLOCKED ←─ HR resolves / RES received ─→ RUNNING
  ↓ Worker DON
REPORTED_DONE
  ↓ QA VAL PASS                ↓ QA VAL FAIL (retry < max)    ↓ QA VAL FAIL (retry == max)
COMPLETED                     RUNNING (re-execute)            FAILED
```

**Terminal States — no further transitions permitted**

| State | Entry Condition | Notes |
|-------|----------------|-------|
| `COMPLETED` | VAL PASS | Irreversible |
| `FAILED` | VAL FAIL with retry_count >= MAX_TASK_RETRIES (3) | Requires Director intervention |

Both terminal states trigger `task_graph.is_complete()` check (only COMPLETED counts as "done").

---

## 7. Termination Guarantees

These rules are enforced by the main execution loop in `main.py`.

**Rule T1 — Retry limit hard stop**
```
retry_count >= MAX_TASK_RETRIES
  → task enters FAILED
  → System emits ALERT MAX_RETRIES to Director
```

**Rule T2 — Deadlock detection**
```
Observer detects circular BLK dependency chain
  → ALERT deadlock sent to Director
  → Director must intervene
```

**Rule T3 — Stall detection**
```
No UPD event from RUNNING task for STALL_THRESHOLD_MS (5 min)
  → Observer triggers ALERT stall
  → Director may REASSIGN
```

---

## 8. Rules and Constraints

**Prohibited Actions**
- Workers must not modify historical events in the work pool
- Workers must not declare COMPLETED without a VAL PASS
- Only Observer may send ALERT
- Only Director may write to SharedMemory (LESSONs)
- HR must not create new agents without Director approval

**Performance Constraints**
- Target token count per message: 15–30
- All complex content (code, detailed descriptions) must be written to Shared State or project files; messages carry only `shared://` references
- Workers in BLOCKED state do not actively poll, conserving tokens
- Observer uses rule-based detection only — no LLM calls

**Security Constraints**
- All file paths are sanitized in `BaseAgent._write_files()` to prevent directory traversal
- Shared state paths are validated against the state directory root

---

## 9. System Architecture Integration

The protocol is implemented across these layers:

| Layer | Component | File |
|-------|-----------|------|
| Verbs & Events | `Verb` enum, `Event` model | `protocols/verbs.py` |
| Event persistence | `EventStream` (SQLite) | `core/event_stream.py` |
| State tracking | `TaskGraph`, `TaskStatus`, `TaskSpec` | `core/task_graph.py` |
| Content storage | `SharedState` (file-based) | `core/shared_state.py` |
| Policy memory | `SharedMemory` (LESSONs) | `core/memory.py` |
| Agent experience | `PersonalMemory` | `core/memory.py` |
| Protocol parsing | `parse_compact()` | `protocols/parser.py` |

---

*Protocol v1.1 — Corresponds to AI Company implementation*

**Changelog from v1.0**
- Added `RES` verb (response to REQ)
- `VAL` payload now includes `reason` and `issues` list for FAIL verdicts
- `DIR` target validation enforces only valid Worker/HR targets
- Event structure simplified: removed `seq`, `retry_count`, `rework_count` (tracked in TaskGraph instead)
- Observer detection rules: 5 types (stall, deadlock, conflict_spike, overload, quality_failure)
- Added MAX_RETRIES alert type for retry exhaustion
- Integrated `PersonalMemory` for per-agent experience tracking
- Integrated `SharedMemory` for cross-project LESSON accumulation
- Console UI for interactive management (`console.py`)
