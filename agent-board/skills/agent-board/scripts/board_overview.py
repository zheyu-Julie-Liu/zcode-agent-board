#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看板全项目进度视图（人类专用，固定口径）
=========================================
boss 指示 2026-09-07：每个 agent 查询口径不同（feed 滚动窗口 / list 默认滤掉完成单），
导致人类在不同对话里看到的板"不一样"。本脚本输出唯一权威视图：

    python3 tools/board_overview.py            # 全量：在飞 + 待办 + 已完成(一行摘要)
    python3 tools/board_overview.py --todo     # 只看未完结（在飞+待办）

只读，不认领、不留言、不改任何状态。数据源 = 本仓根 .agent-board/。
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 看板根与 board.py 定位（可移植）：
# 优先本脚本同目录的 board.py（插件安装形态：skills/agent-board/scripts/），
# 其次本仓布局（tools/ 与 agent-board-plugin/ 同级）。看板数据目录
# 由 board.py 自行向上查找（.agent-board / git 根），与脚本位置解耦。
import os
HERE = Path(__file__).resolve().parent
BOARD_CANDIDATES = [
    HERE / "board.py",                                   # 插件形态
    HERE.parent / "skills" / "agent-board" / "scripts" / "board.py",
    HERE.parent / "agent-board-plugin" / "skills" / "agent-board" / "scripts" / "board.py",
]
BOARD_PY = next((p for p in BOARD_CANDIDATES if p.exists()), BOARD_CANDIDATES[-1])
CWD = str(BOARD_PY.parent.parent.parent.parent)  # 从 scripts/ 上溯到看板项目根（board.py 自会再找）
BOARD = subprocess.run(
    ["python3", str(BOARD_PY), "list", "--all", "--json", "--agent", "human-view"],
    cwd=CWD if os.path.isdir(os.path.join(CWD, ".agent-board")) else os.getcwd(),
    capture_output=True, text=True)
if BOARD.returncode != 0 or not BOARD.stdout.strip():
    sys.exit("看板读取失败：未找到 .agent-board（请在含看板的项目内运行）\n" + BOARD.stderr)
TASKS = json.loads(BOARD.stdout)

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
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    # mktime 把 UTC 时刻按本地解析，差值多出 8 小时（CST），减回
    mins = int((time.time() - time.mktime(t.timetuple())) / 60) - 480  # UTC→本地
    if mins < 60:
        return f"{max(mins,0)}分钟前"
    if mins < 1440:
        return f"{mins // 60}小时前"
    return f"{mins // 1440}天前"


def render(todo_only: bool) -> str:
    total = len(TASKS)
    by = {}
    for t in TASKS:
        by.setdefault(t["status"], []).append(t)
    done = len(by.get("done", []))
    lines = []
    lines.append("=" * 62)
    lines.append(f" 芦苇荡巡护模拟器 · 看板全项目进度（生成于 {time.strftime('%m-%d %H:%M')}）")
    lines.append(f" 总进度：{done}/{total} 完成 | "
                 + " ".join(f"{STATUS_LABEL[s]}{len(by.get(s, []))}"
                            for s in ["in_progress", "review", "todo", "done", "cancelled"]
                            if by.get(s)))
    lines.append("=" * 62)

    # 在飞 agent 一览
    flying = [(t["claimed_by"], t["id"] + " " + t["title"])
              for t in TASKS if t["status"] == "in_progress" and t.get("claimed_by")]
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
    lines.append("")
    lines.append("（本视图为固定口径全量快照；操作看板请仍用 board.py / board_* 工具）")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(render(todo_only="--todo" in sys.argv))
