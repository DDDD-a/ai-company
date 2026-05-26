# AI Company

多智能体协作开发系统 — 基于 LLM 的 Multi-Agent 协作引擎。

给定一个自然语言需求，AI Company 自动完成：需求分析 → 任务拆分 → 多 Agent 协作开发 → QA 验收 → 产出项目文件。

## 架构

```
User Requirement
  → Planner     # LLM 分析需求，生成 Task DAG
  → HR          # 按能力标签匹配 Worker Agent
  → Workers     # FE / BE / DB / QA / OPS 各司其职
  → Observer    # 纯规则检测异常（不调 LLM）
  → Director    # 告警介入 + 经验沉淀（LESSON）
  → QA          # 验收产出物，PASS/FAIL
```

所有 Agent 通过 **Event Stream**（SQLite Append-Only 日志）通信，协议格式 `VERB|AGENT|TASK|STATUS|PAYLOAD`。

详见 [`doc/ai_company_protocol_v1.1.md`](doc/ai_company_protocol_v1.1.md)。

## 快速开始

### 环境

- Python 3.12+
- DeepSeek API Key（[获取](https://platform.deepseek.com)）

### 安装

```bash
git clone https://github.com/DDDD-a/ai-company.git
cd ai-company
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入 DeepSeek API Key
```

### 运行

**命令行模式：**
```bash
python main.py "创建一个登录页面，包含HTML和CSS"
```

**交互终端模式（推荐）：**
```bash
python console.py
# 或创建别名: alias aic='cd ~/ai-company && python console.py'
```

终端内输入需求即可启动项目，`/help` 查看命令列表。

## 终端命令

| 命令 | 说明 |
|------|------|
| `/start <需求>` | 启动新项目 |
| `/status` | 任务看板 |
| `/pool [n]` | 事件流 |
| `/watch` | 实时事件监控 |
| `/agents` | Agent 状态与负载 |
| `/task <id>` | 任务详情 |
| `/intervene <指令>` | 手动干预 |
| `/output <id>` | 查看产出 |
| `/lessons` | 共同记忆 |

也支持自然语言，直接输入"进度怎么样了"、"让前端重新做登录页"。

## 项目结构

```
ai_company/
├── main.py                  # 命令行入口
├── console.py               # 交互终端
├── config.py                # 全局配置
├── core/
│   ├── event_stream.py      # SQLite 事件流
│   ├── llm_provider.py      # LLM 抽象层（DeepSeek / Claude）
│   ├── shared_state.py      # 共享状态（shared:// 路径）
│   ├── task_graph.py        # 任务 DAG
│   └── memory.py            # 共同记忆 + 个人记忆
├── agents/
│   ├── base.py              # Agent 基类
│   ├── planner.py           # 规划层
│   ├── hr.py                # 调度层
│   ├── director.py          # 干预层
│   ├── observer.py          # 感知层
│   └── workers/
│       ├── backend.py       # 后端工程师
│       ├── frontend.py      # 前端工程师
│       ├── db.py            # 数据库工程师
│       ├── qa.py            # 质量验证
│       └── ops.py           # 部署运维
├── protocols/
│   ├── verbs.py             # 通信动词 + Event 模型
│   └── parser.py            # 消息解析器
├── registry/
│   ├── registry.py          # Agent 注册表
│   └── agent_registry.json  # Agent 能力定义
├── tests/                   # 测试用例
└── doc/
    └── ai_company_protocol_v1.1.md
```

## License

MIT
