"""
AI Company — 管理终端 (Management Console) v2.1

CC-style clean terminal UI.
- 自然语言 + / 命令双模式
- 实时任务状态、事件流监控
- 简洁配色、最小化视觉噪音
"""

import asyncio
import json
import re
import os
import sys
import time
import shutil
from pathlib import Path

from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, Completion, CompleteEvent
from prompt_toolkit.styles import Style, merge_styles
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.document import Document

from core.llm_provider import create_provider
from core.task_graph import TaskStatus

load_dotenv()

# ═══════════════════════════════════════════════════
# Style — minimal, CC-inspired
# ═══════════════════════════════════════════════════

class C:
    """Minimal color palette. Prefer dim/bright over many hues."""
    R = "\033[31m"
    G = "\033[32m"
    Y = "\033[33m"
    B = "\033[34m"
    C = "\033[36m"
    D = "\033[2m"     # dim
    W = "\033[1m"     # bright/bold
    Z = "\033[0m"     # reset

# Status colors — subtle
S = {
    "pending":       C.D,
    "assigned":      C.B,
    "running":       C.C,
    "blocked":       C.Y,
    "reported_done": C.G,
    "completed":     C.G + C.W,
    "failed":        C.R,
}

_I = {  # status icons
    "pending": "○", "assigned": "▸", "running": "◉",
    "blocked": "▣", "reported_done": "✓", "completed": "✓", "failed": "✗",
}

# ═══════════════════════════════════════════════════
# LLM instruction parser
# ═══════════════════════════════════════════════════

CONSOLE_PROMPT = """你是 AI Company 管理终端的指令解析器。分析用户输入，输出 JSON（只输出 JSON）：
{"intent": "start|status|pool|agents|lessons|task|intervene|output|stop|clear|help|exit|unknown", "params": {}}

意图说明：
- start: 启动新项目, params.requirement = 需求描述
- status: 查看任务进度
- pool: 查看事件流, params.n = 条数(默认20)
- agents: 查看 Agent 状态
- lessons: 查看共同记忆
- task: 查看任务详情, params.task_id = 任务ID
- intervene: 干预, params.instruction = 指令, params.target = 目标Agent
- output: 查看产出, params.task_id = 任务ID
- stop: 停止项目
- clear: 清屏
- help: 帮助
- exit: 退出
- unknown: 无法识别"""


# ═══════════════════════════════════════════════════
# Tab completer
# ═══════════════════════════════════════════════════

CMDS = {
    "/start", "/status", "/pool", "/agents", "/lessons",
    "/task", "/intervene", "/output", "/stop", "/watch",
    "/clear", "/help", "/exit",
}

ALIASES = {
    "/s": "/status", "/p": "/pool", "/a": "/agents",
    "/l": "/lessons", "/t": "/task", "/i": "/intervene",
    "/o": "/output", "/h": "/help", "/q": "/exit", "/w": "/watch",
}

ALL_CMDS = set(CMDS) | set(ALIASES.keys())


class AICCompleter(Completer):
    def __init__(self, console):
        self.console = console

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        text = document.text_before_cursor
        word = document.get_word_before_cursor()

        if text.lstrip().startswith("/"):
            for cmd in sorted(ALL_CMDS):
                if cmd.startswith(word):
                    yield Completion(cmd, start_position=-len(word),
                                     display=f"{cmd} → {ALIASES[cmd]}" if cmd in ALIASES else cmd)
            return

        if word.startswith("#") and self.console.task_graph:
            for tid in self.console.task_graph.tasks:
                if tid.startswith(word):
                    yield Completion(tid, start_position=-len(word))
            return

        for aid in ["FE", "BE", "DB", "QA", "OPS", "DIRECTOR", "HR", "PLANNER"]:
            if aid.startswith(word.upper()):
                yield Completion(aid, start_position=-len(word))

        if self.console.project_name:
            pd = Path(f"workspace/projects/{self.console.project_name}")
            if pd.exists():
                for f in pd.rglob("*"):
                    if f.is_file():
                        rel = str(f.relative_to(pd))
                        if rel.startswith(word):
                            yield Completion(rel, start_position=-len(word))


# ═══════════════════════════════════════════════════
# Console
# ═══════════════════════════════════════════════════

class Console:

    def __init__(self):
        self.project_name = None
        self.task_graph = None
        self.infra = None
        self.workers = None
        self.project_task = None
        self.iteration = 0
        self.last_event_count = 0
        self.llm = create_provider()
        self.width = shutil.get_terminal_size().columns

    # ═══════════════════════════════════════════════════
    # entry
    # ═══════════════════════════════════════════════════

    async def run(self):
        self._header()

        from main import create_infrastructure
        self.infra = await create_infrastructure()

        model = ""
        if hasattr(self.llm, 'model'):
            model = f" · {self.llm.model}"
        self._dim(f"  provider: {type(self.llm).__name__}{model}")

        await self._repl()

        if self.infra and self.infra["stream"]:
            await self.infra["stream"].close()

    def _header(self):
        print(f"\n  {C.W}AI Company{C.Z} {C.D}v2{C.Z}")
        print(f"  {C.D}multi-agent collaboration system{C.Z}")
        print()

    # ═══════════════════════════════════════════════════
    # REPL
    # ═══════════════════════════════════════════════════

    async def _repl(self):
        history_file = Path("workspace/.console_history")
        history_file.parent.mkdir(parents=True, exist_ok=True)

        session = PromptSession(
            history=FileHistory(str(history_file)),
            style=self._prompt_style(),
            bottom_toolbar=self._toolbar,
            completer=AICCompleter(self),
            complete_while_typing=True,
        )

        while True:
            try:
                line = await session.prompt_async(self._prompt_text, multiline=False)
                line = line.strip()
                if not line:
                    continue
                if line in ("/exit", "/q", "exit"):
                    await self._exit()
                    break
                await self._dispatch(line)
            except (EOFError, KeyboardInterrupt):
                await self._exit()
                break

        print(f"\n{C.D}  shutdown complete.{C.Z}")

    async def _exit(self):
        if self.project_task and not self.project_task.done():
            self._dim("  stopping project...")
            self.project_task.cancel()
            await asyncio.sleep(0.3)

    def _prompt_text(self):
        if self.project_name:
            return FormattedText([
                ("", "\n"),
                ("class:prompt", "  ❯ "),
                ("class:project", f"{self.project_name} "),
            ])
        return FormattedText([
            ("", "\n"),
            ("class:prompt", "  ❯ "),
        ])

    def _toolbar(self):
        if not self.task_graph:
            return FormattedText([
                ("class:toolbar",
                 " /help  type a requirement to start "),
            ])

        p = self.task_graph.progress()
        total = p["total"]
        done = p["completed"]
        r = p["running"]
        b = p["blocked"]
        f = p["failed"]

        bar_w = 16
        filled = int(done / max(total, 1) * bar_w)
        bar = f"{'━' * filled}{'━' * (bar_w - filled)}"

        parts = [bar, f"{done}/{total}"]
        if r > 0: parts.append(f"run:{r}")
        if b > 0: parts.append(f"block:{b}")
        if f > 0: parts.append(f"fail:{f}")
        parts.append(f"loop {self.iteration}")

        return FormattedText([
            ("class:toolbar", "  " + "  ".join(parts) + " "),
        ])

    def _prompt_style(self):
        return Style.from_dict({
            "prompt": "#00d700 bold",
            "project": "#ffffff",
            "toolbar": "#888888",
        })

    # ═══════════════════════════════════════════════════
    # dispatch
    # ═══════════════════════════════════════════════════

    async def _dispatch(self, line: str):
        if line.startswith("/"):
            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            if cmd in ALIASES:
                cmd = ALIASES[cmd]
            await self._handle_cmd(cmd, args)
        else:
            await self._handle_nl(line)

    async def _handle_cmd(self, cmd: str, args: str):
        handlers = {
            "/start": self._cmd_start, "/status": self._cmd_status,
            "/pool": self._cmd_pool, "/agents": self._cmd_agents,
            "/lessons": self._cmd_lessons, "/task": self._cmd_task,
            "/intervene": self._cmd_intervene, "/output": self._cmd_output,
            "/stop": self._cmd_stop, "/watch": self._cmd_watch,
            "/clear": self._cmd_clear, "/help": self._cmd_help,
        }
        handler = handlers.get(cmd)
        if handler:
            await handler(args)
        else:
            self._err(f"unknown: {cmd}")

    async def _handle_nl(self, line: str):
        intent = self._rule_match(line)
        params = {}

        if not intent:
            try:
                raw = await self.llm.chat(
                    system_prompt=CONSOLE_PROMPT,
                    user_message=line, max_tokens=200, temperature=0.1,
                )
                parsed = self._json(raw)
                intent = parsed.get("intent", "unknown")
                params = parsed.get("params", {})
            except Exception:
                intent = "unknown"

        if intent == "unknown":
            if len(line) > 10:
                intent = "start"
                params["requirement"] = line
            else:
                self._err("cannot understand — try /help")
                return

        await self._dispatch_intent(intent, params)

    def _rule_match(self, line: str) -> str | None:
        text = line.lower()
        if any(w in text for w in ["进度", "状态", "怎么样了", "如何了", "情况", "看看"]):
            if not any(w in text for w in ["开始", "创建", "新建", "启动", "做", "改", "修"]):
                return "status"
        if any(w in text for w in ["重新", "修改", "改一下", "重做", "修复", "调整"]):
            return "intervene"
        if any(w in text for w in ["停止", "停掉", "结束"]):
            return "stop"
        if any(w in text for w in ["最近", "日志", "事件", "发生了什么", "记录"]):
            return "pool"
        if any(w in text for w in ["团队", "人员", "谁在", "有哪些"]) and not any(
            w in text for w in ["做", "改", "修", "开始"]
        ):
            return "agents"
        if text in ["清屏", "清理屏幕", "cls"]:
            return "clear"
        return None

    async def _dispatch_intent(self, intent: str, params: dict):
        map_intent = {
            "start": lambda: self._cmd_start(params.get("requirement", "")),
            "status": lambda: self._cmd_status(""),
            "pool": lambda: self._cmd_pool(str(params.get("n", 20))),
            "agents": lambda: self._cmd_agents(""),
            "lessons": lambda: self._cmd_lessons(""),
            "task": lambda: self._cmd_task(params.get("task_id", "")),
            "intervene": lambda: self._cmd_intervene(
                params.get("instruction", "") +
                (f" @{params['target']}" if params.get("target") else "")),
            "output": lambda: self._cmd_output(params.get("task_id", "")),
            "stop": lambda: self._cmd_stop(""),
            "clear": lambda: self._cmd_clear(""),
            "help": lambda: self._cmd_help(""),
            "exit": lambda: self._exit(),
        }
        fn = map_intent.get(intent)
        if fn:
            await fn()

    # ═══════════════════════════════════════════════════
    # /start
    # ═══════════════════════════════════════════════════

    async def _cmd_start(self, args: str):
        if not args:
            self._err("usage: /start <requirement>")
            return
        if self.project_task and not self.project_task.done():
            self._err("a project is already running — /stop first")
            return

        from main import create_workers, plan_project, run_project_loop

        self.project_name = re.sub(r'[^\w一-鿿\s-]', '', args)[:30].strip()
        stream = self.infra["stream"]
        state = self.infra["state"]
        shared_memory = self.infra["shared_memory"]
        registry = self.infra["registry"]

        self._dim("  planning...")
        try:
            self.task_graph = await plan_project(args, stream, state, shared_memory)
        except Exception as e:
            self._err(f"plan failed: {e}")
            self.project_name = None
            return

        n = len(self.task_graph.tasks)
        print(f"\n  {C.W}{n} tasks{C.Z}\n")

        for task in self.task_graph.tasks.values():
            deps = f" {C.D}← {', '.join(task.depends_on)}{C.Z}" if task.depends_on else ""
            cap = ", ".join(task.required_capabilities[:3]) if task.required_capabilities else ""
            cap_str = f"  {C.D}[{cap}]{C.Z}" if cap else ""
            print(f"  {task.id} {C.D}[{task.priority}]{C.Z} {task.description[:55]}{deps}{cap_str}")

        self.workers = create_workers(stream, state, project_name=self.project_name)

        print(f"\n  {C.D}running in background — /status to view{C.Z}")
        print()

        self.project_task = asyncio.ensure_future(
            run_project_loop(
                task_graph=self.task_graph,
                stream=stream, state=state, shared_memory=shared_memory,
                registry=registry, workers=self.workers,
                max_iterations=100, loop_interval=2.0,
                on_iteration=self._on_iteration,
                on_complete=self._on_complete,
            )
        )

    async def _on_iteration(self, iteration, task_graph, stream):
        self.iteration = iteration
        self.task_graph = task_graph

    async def _on_complete(self, task_graph):
        self.task_graph = task_graph
        self._ok(f"project complete — {task_graph.progress()['completed']}/{task_graph.progress()['total']} tasks done")

    # ═══════════════════════════════════════════════════
    # /status
    # ═══════════════════════════════════════════════════

    async def _cmd_status(self, args: str):
        if not self.task_graph:
            self._dim("  no active project — use /start <requirement>")
            return

        p = self.task_graph.progress()
        print(f"\n  {C.W}tasks{C.Z}  "
              f"{C.G}✓{p['completed']}{C.Z}/{p['total']}  "
              f"{C.C}◉{p['running']}{C.Z}  "
              f"{C.Y}▣{p['blocked']}{C.Z}  "
              f"{C.R}✗{p['failed']}{C.Z}  "
              f"{p['percent']}%")
        print()

        prio = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        tasks = sorted(
            self.task_graph.tasks.values(),
            key=lambda t: prio.get(t.priority, 99)
        )

        for task in tasks:
            st = task.status.value if hasattr(task.status, 'value') else str(task.status)
            c = S.get(st, "")
            icon = _I.get(st, " ")
            agent = task.assigned_agent or "-"
            desc = task.description[:52]
            retry = f" {C.R}r{task.retry_count}{C.Z}" if task.retry_count > 0 else ""

            print(f"  {c}{icon} {task.id:<14}{C.Z} "
                  f"{desc:<52} "
                  f"{C.D}{task.priority:<4} {agent:<5}{C.Z}{retry}")
        print()

    # ═══════════════════════════════════════════════════
    # /pool
    # ═══════════════════════════════════════════════════

    async def _cmd_pool(self, args: str):
        n = 20
        if args:
            try: n = min(int(args.split()[0]), 100)
            except: pass

        events = await self.infra["stream"].tail(n)
        if not events:
            self._dim("  event pool is empty")
            return

        print(f"\n  {C.W}events{C.Z} {C.D}(last {len(events)}){C.Z}\n")

        vc = {"ASN": C.B, "ACK": C.D, "UPD": C.C,
              "BLK": C.Y, "REQ": C.Y, "CON": C.R,
              "DON": C.G, "VAL": C.G, "DIR": C.R, "ALERT": C.R + C.W}

        for e in events:
            ts = time.strftime("%H:%M:%S", time.localtime(e.timestamp / 1000))
            v = e.verb if hasattr(e.verb, 'value') else str(e.verb)
            c = vc.get(v, "")
            print(f"  {C.D}{ts}{C.Z}  {c}{e.to_compact()}{C.Z}")
        print()

    # ═══════════════════════════════════════════════════
    # /watch
    # ═══════════════════════════════════════════════════

    async def _cmd_watch(self, args: str):
        print(f"\n  {C.W}watching events{C.Z} {C.D}(Ctrl+C to stop){C.Z}\n")

        shown = await self.infra["stream"].count()

        try:
            while True:
                new_events = await self.infra["stream"].tail(200)
                if len(new_events) > shown:
                    for e in new_events[shown:]:
                        ts = time.strftime("%H:%M:%S", time.localtime(e.timestamp / 1000))
                        v = e.verb if hasattr(e.verb, 'value') else str(e.verb)
                        vc = {"ASN": C.B, "ACK": C.D, "UPD": C.C,
                              "BLK": C.Y, "DON": C.G, "VAL": C.G,
                              "DIR": C.R, "ALERT": C.R + C.W}.get(v, "")
                        print(f"  {C.D}{ts}{C.Z}  {vc}{e.to_compact()}{C.Z}")
                    shown = len(new_events)
                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            print(f"\n  {C.D}watch stopped{C.Z}\n")

    # ═══════════════════════════════════════════════════
    # /agents
    # ═══════════════════════════════════════════════════

    async def _cmd_agents(self, args: str):
        agents = self.infra["registry"].list_all()
        if not agents:
            self._dim("  no agents registered")
            return

        load = {}
        if self.task_graph:
            for task in self.task_graph.tasks.values():
                aid = task.assigned_agent
                if aid:
                    load.setdefault(aid, {"r": 0, "d": 0, "b": 0})
                    st = task.status.value if hasattr(task.status, 'value') else str(task.status)
                    if st in ("running", "assigned"): load[aid]["r"] += 1
                    elif st == "blocked": load[aid]["b"] += 1
                    elif st == "completed": load[aid]["d"] += 1

        print(f"\n  {C.W}agents{C.Z}\n")
        print(f"  {'id':<6} {'name':<10} {'capabilities':<44} {'run':<5} {'block':<5} {'done':<5}")
        print(f"  {C.D}{'─' * 76}{C.Z}")

        for a in agents:
            aid = a["id"]
            l = load.get(aid, {"r": 0, "d": 0, "b": 0})
            caps = ", ".join(a.get("capabilities", [])[:5])
            print(f"  {C.W}{aid:<6}{C.Z} {a['name']:<10} "
                  f"{C.D}{caps:<44}{C.Z} "
                  f"{C.C}{l['r']:<5}{C.Z} "
                  f"{C.Y}{l['b']:<5}{C.Z} "
                  f"{C.G}{l['d']:<5}{C.Z}")
        print()

    # ═══════════════════════════════════════════════════
    # /lessons
    # ═══════════════════════════════════════════════════

    async def _cmd_lessons(self, args: str):
        lessons = await self.infra["shared_memory"].get_lessons(min_confidence=0.1)
        if not lessons:
            self._dim("  no lessons yet — they accumulate as Director learns")
            return

        print(f"\n  {C.W}lessons{C.Z} {C.D}({len(lessons)}){C.Z}\n")

        for l in lessons:
            eff = l.effective_confidence()
            eff_c = C.G if eff > 0.7 else C.Y if eff > 0.3 else C.D
            print(f"  {C.Y}◆{C.Z} {l.id}  {eff_c}{eff:.0%}{C.Z}")
            print(f"  {C.D}pattern{C.Z} {l.pattern[:65]}")
            print(f"  {C.D}action{C.Z}  {l.action[:65]}")
            print()
        print()

    # ═══════════════════════════════════════════════════
    # /task
    # ═══════════════════════════════════════════════════

    async def _cmd_task(self, args: str):
        if not args:
            self._err("usage: /task <id>")
            return

        tid = args.split()[0]
        if not tid.startswith("#"): tid = "#" + tid

        if not self.task_graph or tid not in self.task_graph.tasks:
            self._err(f"task not found: {tid}")
            return

        task = self.task_graph.tasks[tid]
        st = task.status.value if hasattr(task.status, 'value') else str(task.status)

        print(f"\n  {C.W}{tid}{C.Z}  {S.get(st, '')}{_I.get(st, '')} {st}{C.Z}")
        print(f"  {C.D}description{C.Z}  {task.description}")
        print(f"  {C.D}priority{C.Z}    {task.priority}")
        print(f"  {C.D}depends on{C.Z}  {', '.join(task.depends_on) if task.depends_on else '(none)'}")
        print(f"  {C.D}capabilities{C.Z} {', '.join(task.required_capabilities) if task.required_capabilities else '(none)'}")
        print(f"  {C.D}contract{C.Z}    {task.output_contract or '-'}")
        print(f"  {C.D}assigned{C.Z}   {task.assigned_agent or '(unassigned)'}")
        if task.retry_count > 0:
            print(f"  {C.R}retries{C.Z}    {task.retry_count} — {task.last_fail_reason[:80] if task.last_fail_reason else '-'}")

        events = await self.infra["stream"].get_task_events(tid)
        if events:
            print(f"\n  {C.W}history{C.Z} {C.D}({len(events)}){C.Z}")
            for e in events:
                v = e.verb if hasattr(e.verb, 'value') else str(e.verb)
                print(f"  {C.D}{v:<6}{C.Z} ← {e.agent:<6} {e.status}")

        if self.project_name:
            pd = Path(f"workspace/projects/{self.project_name}")
            if pd.exists():
                files = [f for f in pd.rglob("*") if f.is_file()]
                if files:
                    print(f"\n  {C.W}files{C.Z} {C.D}({len(files)}){C.Z}")
                    for f in sorted(files):
                        rel = f.relative_to(pd)
                        size = f.stat().st_size
                        print(f"  {C.D}{rel}{C.Z}  {self._size(size)}")
        print()

    # ═══════════════════════════════════════════════════
    # /intervene
    # ═══════════════════════════════════════════════════

    async def _cmd_intervene(self, args: str):
        if not self.task_graph:
            self._err("no active project")
            return
        if not args:
            self._err("usage: /intervene <instruction>")
            return

        from protocols.verbs import Verb, Event

        target = None
        for aid in ["FE", "BE", "DB", "QA", "OPS"]:
            if aid.lower() in args.lower():
                target = aid
                break

        running = self.task_graph.get_running_tasks()
        tid = running[0].id if running else "#intervention"

        event = Event(
            verb=Verb.DIR, agent="MANAGEMENT", task=tid, status="INTERVENE",
            payload={"instruction": args, "target": target},
            mentions=[target] if target else [],
        )
        await self.infra["stream"].append(event)
        self._ok(f"DIR → {target or 'all'} — {args[:60]}")

    # ═══════════════════════════════════════════════════
    # /output
    # ═══════════════════════════════════════════════════

    async def _cmd_output(self, args: str):
        if not args:
            self._err("usage: /output <task_id>")
            return

        tid = args.split()[0]
        if not tid.startswith("#"): tid = "#" + tid

        path = f"outputs/{tid.strip('#')}"
        data = await self.infra["state"].read(path)
        if data:
            print(f"\n  {C.W}{tid} output{C.Z}\n")
            summary = data.get('summary', str(data)[:500])
            for line in summary.split("\n")[:20]:
                print(f"  {C.D}{line}{C.Z}")

        if self.project_name:
            pd = Path(f"workspace/projects/{self.project_name}")
            if pd.exists():
                files = [f for f in pd.rglob("*") if f.is_file()]
                if files:
                    print(f"\n  {C.W}files{C.Z} {C.D}({len(files)}){C.Z}\n")
                    for f in sorted(files):
                        if f.is_file():
                            rel = f.relative_to(pd)
                            content = f.read_text(encoding="utf-8")[:300]
                            print(f"  {C.Y}{rel}{C.Z}")
                            for line in content.split("\n")[:8]:
                                print(f"  {C.D}{line}{C.Z}")
                            if len(content) > 300:
                                print(f"  {C.D}... (truncated){C.Z}")
                            print()
        print()

    # ═══════════════════════════════════════════════════
    # /stop, /clear, /help
    # ═══════════════════════════════════════════════════

    async def _cmd_stop(self, args: str):
        if not self.project_task or self.project_task.done():
            self._dim("  no running project")
            return
        self.project_task.cancel()
        self._dim("  project stopped")

    async def _cmd_clear(self, args: str):
        print("\033[2J\033[H")
        self._header()

    async def _cmd_help(self, args: str):
        print(f"""
{C.W}  commands{C.Z}

  {C.D}natural language{C.Z} — just type in Chinese
    "做一个博客系统"       start a project
    "进度怎么样了"         check progress
    "让前端重新做登录页面"  intervene

  {C.D}/ commands{C.Z}

  {C.G}/start{C.Z}     <req>   start new project
  {C.G}/status{C.Z}            task board
  {C.G}/pool{C.Z}      [n]     event stream
  {C.G}/watch{C.Z}             live event monitor
  {C.G}/agents{C.Z}            agent status + load
  {C.G}/lessons{C.Z}           shared memory (LESSONs)
  {C.G}/task{C.Z}      <id>    task detail + files
  {C.G}/intervene{C.Z} <ins>   director intervention
  {C.G}/output{C.Z}    <id>    output + file contents
  {C.G}/stop{C.Z}              stop current project
  {C.G}/clear{C.Z}             clear screen
  {C.G}/help{C.Z}              this help

  {C.D}aliases{C.Z}  /s /p /a /l /t /i /o /w /h /q
""")

    # ═══════════════════════════════════════════════════
    # output helpers
    # ═══════════════════════════════════════════════════

    def _ok(self, msg):    print(f"  {C.G}✓{C.Z} {msg}")
    def _err(self, msg):   print(f"  {C.R}✗{C.Z} {msg}")
    def _dim(self, msg):   print(f"  {C.D}{msg}{C.Z}")

    def _size(self, s: int) -> str:
        if s < 1024: return f"{s}B"
        if s < 1024 * 1024: return f"{s / 1024:.1f}KB"
        return f"{s / (1024 * 1024):.1f}MB"

    def _json(self, raw: str) -> dict:
        t = raw.strip()
        if "```json" in t:
            m = re.search(r"```json\s*(.*?)\s*```", t, re.DOTALL)
            if m: t = m.group(1)
        s = t.find("{"); e = t.rfind("}")
        if s >= 0 and e > s: t = t[s:e + 1]
        try: return json.loads(t)
        except: return {"intent": "unknown"}


# ═══════════════════════════════════════════════════
# entry
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    console = Console()
    try:
        asyncio.run(console.run())
    except KeyboardInterrupt:
        print()
    except Exception as e:
        print(f"{C.R}error: {e}{C.Z}")
        import traceback
        traceback.print_exc()
