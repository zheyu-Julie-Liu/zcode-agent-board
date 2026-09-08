#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看板全项目进度视图（人类专用，固定口径）
=========================================
每个 agent 查询口径不同（feed 滚动窗口 / list 默认滤掉完成单），导致人类在不同对话里
看到的板"不一样"。本脚本输出唯一权威视图：

    python3 board_overview.py            # 全量：@我未读 + 在飞 + 待办 + 已完成(一行摘要)
    python3 board_overview.py --todo     # 只看未完结（在飞+待办）
    python3 board_overview.py --me boss  # 顶部「未读提及」看谁的收件箱（默认 boss）

只读，不认领、不留言、不改任何状态。看板位置由 board.py 决定
（$AGENT_BOARD_ROOT > 向上找 .agent-board > git 根）；标题用 config.json 的
project_name，没有则用看板所在目录名。
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# board.py 定位（可移植）：优先本脚本同目录（插件安装形态：skills/agent-board/scripts/），
# 其次仓内布局（tools/ 与 agent-board-plugin/ 同级）。
HERE = Path(__file__).resolve().parent
BOARD_CANDIDATES = [
    HERE / "board.py",
    HERE.parent / "skills" / "agent-board" / "scripts" / "board.py",
    HERE.parent / "agent-board-plugin" / "skills" / "agent-board" / "scripts" / "board.py",
]
BOARD_PY = next((p for p in BOARD_CANDIDATES if p.exists()), BOARD_CANDIDATES[-1])
_UP = BOARD_PY.parent.parent.parent.parent  # 从 scripts/ 上溯到插件所在项目根
CWD = str(_UP) if (_UP / ".agent-board").is_dir() else os.getcwd()

# 复用 board.py 的文件路径提取与链接渲染，保证 show 与总览口径一致
import importlib.util
_spec = importlib.util.spec_from_file_location("agent_board_core", str(BOARD_PY))
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)


def board_json(*cmd, agent="human-view"):
    """调用 board.py 子命令并解析 JSON；失败返回 None（附 stderr）。"""
    r = subprocess.run([sys.executable, str(BOARD_PY), *cmd, "--json", "--agent", agent],
                       cwd=CWD, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None, r.stderr.strip()
    try:
        return json.loads(r.stdout), ""
    except json.JSONDecodeError:
        return None, r.stdout.strip()


STATUS_ORDER = {"in_progress": 0, "review": 1, "todo": 2, "done": 3, "cancelled": 4}
STATUS_LABEL = {"in_progress": "进行中", "review": "待审核",
                "todo": "待办", "done": "完成", "cancelled": "已取消"}
STATUS_ICON = {"in_progress": "🔄", "review": "🔎",
               "todo": "📋", "done": "✅", "cancelled": "❌"}


def last_activity(t: dict) -> str:
    """最新动态：done 用 result，其余用最后一条留言，都没有用创建时间。"""
    if t.get("result"):
        return "结果：" + one_line(t["result"])
    if t.get("comments"):
        c = t["comments"][-1]
        return f"最新留言[{c['agent']}]：" + one_line(c["text"])
    return "（无留言）"


def one_line(s: str, limit: int = 90) -> str:
    s = " ".join(str(s).split())
    return s[:limit] + "…" if len(s) > limit else s


def ago(iso: str) -> str:
    try:
        t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return "?"
    mins = max(int((time.time() - t.timestamp()) / 60), 0)
    if mins < 60:
        return f"{mins}分钟前"
    if mins < 1440:
        return f"{mins // 60}小时前"
    return f"{mins // 1440}天前"


def file_line(t: dict, root: str) -> str:
    """任务里提到过的真实文件 → 可点击 markdown 链接行；没有则空串。"""
    files = _core.extract_file_paths(_core.task_texts(t), base_dir=root, limit=5)
    if not files:
        return ""
    return "      📎 " + "  ".join(_core.file_link(p) for p in files)


def render(tasks: list, info: dict, inbox: list, me: str, todo_only: bool) -> str:
    total = len(tasks)
    by = {}
    for t in tasks:
        by.setdefault(t["status"], []).append(t)
    done = len(by.get("done", []))
    root = info.get("root", "") or CWD
    title = info.get("project_name") or os.path.basename(root) or "项目"
    lines = []
    lines.append("=" * 62)
    lines.append(f" {title} · 看板全项目进度（生成于 {time.strftime('%m-%d %H:%M')}）")
    lines.append(f" 总进度：{done}/{total} 完成 | "
                 + " ".join(f"{STATUS_LABEL[s]}{len(by.get(s, []))}"
                            for s in ["in_progress", "review", "todo", "done", "cancelled"]
                            if by.get(s)))
    lines.append("=" * 62)

    # 给人看的第一件事：谁 @ 了我还没看
    if inbox:
        lines.append("")
        lines.append(f"📬 @{me} 未读提及 {len(inbox)} 条"
                     f"（看完执行 board.py inbox --agent {me} --ack 清零）")
        for r in inbox[-12:]:
            lines.append(f"  {ago(r.get('ts', ''))} [{r.get('from', '?')}] {r.get('task', '-')} "
                         f"{r.get('event', '')}：{one_line(r.get('text', ''), 84)}")
            links = _core.extract_file_paths(r.get("text", ""), base_dir=root, limit=3)
            if links:
                lines.append("      📎 " + "  ".join(_core.file_link(p) for p in links))
        if len(inbox) > 12:
            lines.append(f"  …另有 {len(inbox) - 12} 条更早的，用 board.py inbox --agent {me} 查看全部")

    # 在飞 agent 一览
    flying = [(t["claimed_by"], t["id"] + " " + t["title"])
              for t in tasks if t["status"] == "in_progress" and t.get("claimed_by")]
    if flying:
        lines.append("")
        lines.append("👥 在飞 agent：" + "；".join(f"{a} → {tid}" for a, tid in flying))

    for status in ["in_progress", "review", "todo", "done", "cancelled"]:
        group = sorted(by.get(status, []), key=lambda t: (t["priority"], t["id"]))
        if not group or (todo_only and status in ("done", "cancelled")):
            continue
        lines.append("")
        lines.append(f"{STATUS_ICON[status]} {STATUS_LABEL[status]}（{len(group)}）"
                     + ("——只列标题" if status == "done" and not todo_only else ""))
        for t in group:
            owner = f"👤{t['claimed_by']}" if t.get("claimed_by") else "🈚无人认领"
            lines.append(f"  {t['id']} [P{t['priority']}] {t['title']}  （{owner}）")
            if status != "done":
                lines.append(f"      └ {ago(t['updated_at'])} | {last_activity(t)}")
            elif t.get("result"):
                lines.append(f"      └ {one_line(t['result'], 70)}")
            fl = file_line(t, root)
            if fl:
                lines.append(fl)
    lines.append("")
    lines.append("（本视图为固定口径全量快照；操作看板请仍用 board.py / board_* 工具）")
    return "\n".join(lines)


def main(argv):
    me = "boss"
    if "--me" in argv:
        i = argv.index("--me")
        if i + 1 < len(argv):
            me = argv[i + 1].lstrip("@")
    tasks, err = board_json("list", "--all")
    if tasks is None:
        sys.exit("看板读取失败：未找到 .agent-board（请在含看板的项目内运行，或设置 $AGENT_BOARD_ROOT）\n" + err)
    info, _ = board_json("whoami")
    inbox, _ = board_json("inbox", agent=me)
    print(render(tasks, info or {}, inbox or [], me, todo_only="--todo" in argv))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
