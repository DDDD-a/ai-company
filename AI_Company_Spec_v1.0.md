# AI Company — 系统规格说明书 v1.0

> 一个基于事件流驱动的多智能体协作系统，具备调度、感知、干预与策略学习能力，通过有限策略记忆实现渐进式自我优化。

---

## 目录

1. [系统本质](#一系统本质)
2. [整体架构](#二整体架构)
3. [角色体系](#三角色体系)
4. [工作池与通信协议](#四工作池与通信协议)
5. [共享状态层](#五共享状态层)
6. [任务生命周期](#六任务生命周期)
7. [记忆体系](#七记忆体系)
8. [系统运行闭环](#八系统运行闭环)
9. [系统边界（v1）](#九系统边界v1)
10. [待解决的开放问题](#十待解决的开放问题)

---

## 一、系统本质

### 最高抽象定义

AI Company 是一个**事件流驱动（Event-Driven）的多智能体协作系统**，以人类组织结构为隐喻构建，核心能力为：

- **调度**：将复杂任务拆解并分配给专业化 Agent
- **感知**：持续监控系统运行状态，识别异常模式
- **干预**：在必要时精准介入，纠正偏差
- **进化**：将经验沉淀为策略记忆，指导未来决策

### 三个核心层（不可再拆）

```
Task Graph    → 定义"应该做什么"
Event Stream  → 记录"正在发生什么"
Policy Layer  → 决定"未来怎么做更好"
```

三层关系：Task Graph 驱动执行，Event Stream 推进状态，Policy Layer 影响未来决策。

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────┐
│              Policy Layer（策略记忆层）                 │
│         共同记忆 LESSONS / ANTI-PATTERNS               │
└───────────┬──────────────────────────────────────────┘
            │ 读取 / 写入（仅 Director 可写）
┌───────────▼──────────────────────────────────────────┐
│              Orchestration Layer（编排层）              │
│   Planner（规划）  Observer（感知）  Director（干预）   │
│                       HR（调度）                       │
└───────────┬──────────────────────────────────────────┘
            │ 指令写入 / 持续监听
┌───────────▼──────────────────────────────────────────┐
│              Event Stream（工作池）                     │
│        Append-Only 事件日志，所有角色读写              │
└───────────┬──────────────────────────────────────────┘
            │ 写入事件 / 被 @ 唤醒
┌───────────▼──────────────────────────────────────────┐
│              Worker Agents（执行层）                    │
│        个人记忆 + 专属工具 + 专属系统提示               │
└──────────────────────────────────────────────────────┘
            │ 读写
┌───────────▼──────────────────────────────────────────┐
│              Shared State（共享状态层）                  │
│          所有 Agent 可读写的结构化数据区                 │
└──────────────────────────────────────────────────────┘
```

---

## 三、角色体系

### 3.1 Planner（规划层）

**性质**：一次性静态生成器，不参与运行期。

**职责**：
- 解析用户需求，识别核心目标与约束
- 构建任务有向无环图（DAG），明确依赖关系
- 为每个任务定义规范（输入、输出合约、验收标准）
- 读取共同记忆，将历史教训融入初始规划

**输出格式**：

```json
TASK_GRAPH {
  "nodes": [Task],
  "edges": [Dependency]
}

TASK_SPEC {
  "id": "string",
  "description": "string",
  "input": "描述",
  "output_contract": "验收标准",
  "priority": "P0~P3",
  "depends_on": ["task_id"]
}
```

**关键约束**：Planner 完成后退出，不监听 Event Stream，不参与运行期决策。

---

### 3.2 Observer（感知层）

**性质**：只读 Event Stream，不做决策，持续运行。

**职责**：扫描 Event Stream，检测系统异常模式，生成 ALERT。

**检测类型**：

| 类型 | 触发条件 | 说明 |
|------|---------|------|
| `deadlock` | BLK 循环依赖 | A 等 B，B 等 A |
| `stall` | 超时无 UPD | 任务长时间静默 |
| `conflict_spike` | CON 频发 | 冲突密度超阈值 |
| `overload` | 单 Agent 任务堆积 | 负载不均衡 |
| `quality_failure` | VAL FAIL 重复出现 | 产出质量问题 |

**输出格式**：

```json
ALERT {
  "type": "deadlock | stall | conflict_spike | overload | quality_failure",
  "targets": ["task_id"],
  "severity": "low | medium | high",
  "evidence": ["event_id"],
  "metadata": {}
}
```

**关键约束**：Observer 不写 Event Stream，不发 DIR，只产生 ALERT 供 Director 消费。

---

### 3.3 Director（干预层）

**性质**：仅在 ALERT 触发时运行，低频介入。

**职责**：
- 接收 Observer 的 ALERT，结合共享状态与任务图做决策
- 下达 DIR 干预指令
- 将反复出现的模式提炼为 LESSON，写入共同记忆
- 对自身决策质量进行复盘，迭代自身系统提示

**决策流程**：

```
ALERT 到达
  → 读取相关任务状态（Shared State）
  → 读取相关历史教训（Policy Layer）
  → 生成 DIR 指令 → 写入 Event Stream
  → 判断是否需要写入新 LESSON
```

**关键约束**：Director 是唯一可以写入 Policy Layer（共同记忆）的角色。

---

### 3.4 HR（调度层）

**性质**：智能调度 Agent，具备能力匹配判断力，但不参与高层决策。

**职责**：
- 接收 Planner 的 Task Graph，按依赖顺序发送 ASN 指令
- 从 Agent 库匹配能力标签，选择合适 Agent
- 接收 Director 的 DIR 指令并执行调度调整
- 维护所有任务的状态机（TASK_STATE）
- 在 Agent 库无匹配时，上报 Director 审批后新建 Agent

**Agent 匹配规则**：

```
能力标签匹配（capabilities tags）
  → 优先选已有个人记忆的 Agent（经验优先）
  → 次选库中最新版本 Agent
  → 无匹配 → 上报 Director → 审批通过后创建
```

**内部状态**：

```json
TASK_STATE {
  "task_id": "string",
  "status": "pending | assigned | running | blocked | reported_done | completed | failed",
  "assigned_agent": "agent_id",
  "started_at": "timestamp",
  "updated_at": "timestamp"
}
```

**关键约束**：HR 不自主创建 Agent，不修改 Policy Layer，不参与 LESSON 写入。

---

### 3.5 Worker Agents（执行层）

**性质**：最小智能执行单元，默认静默。

**组成**：

```json
Agent {
  "id": "string",
  "name": "string",
  "capabilities": ["tag1", "tag2"],
  "tools": ["tool_id"],
  "system_prompt": "专属角色定义",
  "prompt_version": "v1.0",
  "memory": "私有记忆引用"
}
```

**行为规则**：
- 默认静默，不消耗 token
- 被 `@` 提及或收到 ASN 时才激活
- 执行过程中主动写入 Event Stream（UPD / BLK / CON / DON）
- 完成任务后写 DON，等待 QA 进行 VAL，不自行宣告完成

**初始 Agent 库（最小集合）**：

| Agent ID | 名称 | 能力标签 |
|----------|------|---------|
| `FE` | 前端工程师 | UI, React, CSS, API集成 |
| `BE` | 后端工程师 | API, 业务逻辑, JWT, 数据库交互 |
| `DB` | 数据库工程师 | Schema设计, 查询优化, 迁移 |
| `QA` | 质量验证 | 测试, 验收, VAL |
| `OPS` | 部署运维 | Docker, CI/CD, 环境配置 |
| `SEC` | 安全审计 | 漏洞扫描, 权限审查 |

**Agent 创建规则**：

```
IF 无匹配 Agent AND task_priority >= P1
  → HR 上报 Director
  → Director 审批
  → 按规范创建新 AgentProfile
  → 写入 Agent 库
```

---

## 四、工作池与通信协议

### 4.1 工作池本质

工作池（Event Stream）是系统的**神经中枢**：

- **Append-Only**：事件只能追加，不可修改，保证历史可溯
- **全局可见**：所有角色均可读取，但 Worker 默认不主动监听
- **@ 唤醒机制**：Worker 只在 `mentions` 字段包含自身 ID 时才激活响应，类比工作群的 @ 机制

### 4.2 事件数据结构

```json
EVENT {
  "id": "uuid",
  "timestamp": "int（Unix ms）",
  "verb": "string",
  "agent": "agent_id",
  "task": "task_id",
  "status": "string",
  "payload": {},
  "mentions": ["agent_id"]
}
```

### 4.3 通信动词集合（最小完备集）

| 动词 | 发送方 | 含义 |
|------|-------|------|
| `ASN` | HR | 分配任务给 Agent |
| `ACK` | Worker | 确认接收任务 |
| `UPD` | Worker | 进度更新 |
| `BLK` | Worker | 阻塞，等待依赖 |
| `REQ` | Worker | 请求决策或资源 |
| `CON` | Worker | 上报冲突 |
| `DON` | Worker | 自报执行完成（待验收）|
| `VAL` | QA | 验收结果（PASS/FAIL）|
| `DIR` | Director | 干预指令 |
| `ALERT` | Observer | 异常模式预警 |

### 4.4 通信语言格式

**设计原则**：追求极致效率与准确性，忽略人类可读性。消息只传递事件指针，内容存于 Shared State，不在消息中重复搬运。

**格式**：

```
VERB|AGENT|TASK|STATUS|PAYLOAD|@MENTIONS
```

**示例**：

```
ASN|HR|#auth_api|P1|agent:BE_1 @BE_1
ACK|BE_1|#auth_api|ACCEPT
UPD|BE_1|#auth_api|50%|impl:jwt;next:token_refresh
BLK|BE_1|#auth_api|WAIT|dep:#db_schema @DB @HR
DON|DB|#db_schema|OK|out→shared:schema/v2
VAL|QA|#db_schema|PASS
CON|FE|#ui_kit|CONFLICT|path:components/button.tsx @DESIGN @HR
ALERT|OBS|#ui_kit|conflict_spike|count:3;window:10min
DIR|DIRECTOR|FE|REWORK|fix:component_path_isolation @FE
REQ|FE|#login_ui|DECISION|JWT_storage:cookie?localStorage? @HR
```

每条消息约 **15-30 token**，完整传递机器可解析的事件信息。

---

## 五、共享状态层

### 5.1 定义

共享状态（Shared State）是所有 Agent 可读写的**结构化数据区**，独立于 Event Stream 存在。

- Event Stream 记录**发生了什么**（事件历史）
- Shared State 存储**现在是什么**（当前状态）

消息中的 `out→shared:schema/v2` 即为指向 Shared State 的指针，避免在消息中重复传递内容。

### 5.2 数据分区

```
shared/
  ├── contracts/       # 接口契约（FE/BE 对齐用）
  ├── schemas/         # 数据库 Schema
  ├── components/      # 共享 UI 组件
  ├── specs/           # 任务规范文档
  └── outputs/         # 各 Agent 产出物
```

### 5.3 写入规则

- 任何 Agent 均可写入，但写入时必须发一条 `UPD` 或 `DON` 事件记录路径
- 同一路径的写入冲突需通过 `CON` 上报，由 HR/Director 仲裁

---

## 六、任务生命周期

### 6.1 状态机（闭环定义）

```
PENDING
  ↓ ASN（HR分配）
ASSIGNED
  ↓ ACK（Worker确认）
RUNNING
  ↓ UPD（持续更新）
BLOCKED ←→ RUNNING（BLK / DIR解除）
  ↓ DON（Worker自报完成）
REPORTED_DONE
  ↓ VAL（QA验收）
COMPLETED（VAL PASS）
FAILED（VAL FAIL → 回到 RUNNING）
```

### 6.2 关键原则

> **DON ≠ 完成**

Worker 写 DON 只代表"我认为做完了"，必须经过 QA 的 VAL PASS 才能转为 COMPLETED。VAL FAIL 时任务回到 RUNNING，QA 将失败原因写入 Shared State，Worker 修复后再次 DON。

### 6.3 超时处理

```
RUNNING 状态超过阈值无 UPD
  → Observer 检测到 stall
  → 发出 ALERT
  → Director 决定：催促 / 重分配 / 拆分任务
```

---

## 七、记忆体系

### 7.1 三层记忆架构

```
共同记忆（Policy Layer）  →  全局持久，跨项目积累，仅 Director 写入
      ↑ 提炼
工作池（Event Stream）    →  当前项目事件流，短暂存在
      ↑ 积累
个人记忆（Agent Memory）  →  每个 Agent 私有，自身经验与偏好
```

### 7.2 个人记忆

- 每个 Agent 私有，其他角色不可访问
- 存储内容：历史踩坑、擅长模式、技术偏好、常用输出格式
- 在 Agent 启动时注入上下文，提升执行质量

### 7.3 共同记忆（LESSON）

**数据结构**：

```json
LESSON {
  "id": "LESSON#xxx",
  "pattern": "描述触发场景",
  "trigger": "触发条件（可量化）",
  "action": "推荐动作",
  "scope": "global | project_type | domain",
  "confidence": "0.0~1.0",
  "version": "int",
  "decay": "float（随时间降低权重）",
  "written_by": "DIRECTOR",
  "created_at": "timestamp"
}
```

**示例**：

```json
{
  "id": "LESSON#003",
  "pattern": "FE 和 BE 并行启动时频繁发生 API 契约冲突",
  "trigger": "CON.type=api_contract count > 2 in same sprint",
  "action": "HR 在分配 FE/BE 前强制插入 CONTRACT_DEFINE 阶段",
  "scope": "global",
  "confidence": 0.85,
  "decay": 0.01,
  "version": 1
}
```

**使用规则**：

| 角色 | 权限 |
|------|------|
| Planner | 可读，融入初始规划 |
| Director | 可读写，唯一写入方 |
| HR | 可读，影响调度策略 |
| Worker | 不可访问 |

### 7.4 Agent 启动上下文

```
Agent 激活时注入：
  共同记忆中与本任务相关的 LESSON
  + 个人记忆（自身经验）
  + 当前任务 TASK_SPEC
  + 相关 Shared State 路径
  = 完整启动上下文
```

---

## 八、系统运行闭环

### 8.1 主执行循环

```
1. 用户输入需求
      ↓
2. Planner 读取共同记忆 → 生成 Task Graph + TASK_SPEC
      ↓
3. HR 按依赖顺序发送 ASN，Worker ACK 后进入 RUNNING
      ↓
4. Worker 并行执行，持续写入 Event Stream（UPD/BLK/CON/DON）
      ↓
5. Observer 持续扫描 → 检测异常 → 发出 ALERT
      ↓
6. Director 消费 ALERT → 下达 DIR → HR 执行调度调整
      ↓
7. QA 对 DON 任务进行 VAL → PASS 转 COMPLETED，FAIL 返回 RUNNING
      ↓
8. 所有任务 COMPLETED → 项目收敛
```

### 8.2 进化闭环

```
Event Stream 中的异常模式
      ↓ Observer 检测
ALERT（有证据链）
      ↓ Director 分析
识别系统性漏洞
      ↓
写入 LESSON → 共同记忆
      ↓
下次 Planner 规划时读取
      ↓
提前规避，不再重蹈
```

### 8.3 自迭代机制

Director 定期对自身决策质量进行复盘：

```
复盘触发：项目完成 或 VAL FAIL 累计超阈值
复盘内容：
  - 哪些 ALERT 被正确处理了？
  - 哪些 DIR 指令没有效果？
  - Planner 的初始 Task Graph 有哪些盲点？
复盘产出：
  - 更新 Director 自身系统提示版本
  - 补充或修正 LESSON
```

---

## 九、系统边界（v1）

### v1 负责

- 多 Agent 并行协作与调度
- 基于事件流的状态追踪
- 异常检测与低频干预
- 基础策略记忆（LESSON 写入与读取）
- Agent 库管理与动态扩充
- 任务验收闭环（VAL 机制）

### v1 不负责

- 强化学习或神经网络训练
- 全自动 Prompt 进化（需 Director 审批）
- 完全自主 Agent 创建（需 Director 审批）
- 长期复杂记忆推理
- 跨公司 / 跨系统的 Agent 通信（留给 v2）

---

## 十、待解决的开放问题

以下问题在 v1 规格中暂时搁置，需在实现过程中探索：

| 问题 | 说明 |
|------|------|
| **LESSON 污染防控** | 错误的 LESSON 如何识别和撤销？confidence + decay 是否足够？ |
| **Observer 扫描频率** | 实时扫描 vs 批量扫描的成本权衡 |
| **共同记忆规模控制** | LESSON 积累过多后如何聚合、归并、淘汰 |
| **Agent 注意力控制精度** | @ 机制在高并发时的 token 开销如何量化 |
| **Director 自迭代安全边界** | 自修改系统提示的范围如何约束，防止漂移 |
| **通信语言的 LLM 可靠性** | 极简格式在无训练情况下的生成准确率需要实测验证 |

---

## 附录：系统一句话定义

> **AI Company = 一个基于事件流的多 Agent 调度操作系统，通过感知-干预-收敛的编排机制，以及跨项目积累的策略记忆，实现具备自我进化能力的智能协作组织。**

---

*Spec v1.0 | 状态：草稿 | 下一步：技术选型与原型实现*
