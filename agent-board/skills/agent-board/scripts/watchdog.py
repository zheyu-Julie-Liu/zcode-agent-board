#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watchdog.py — 看板看门狗（事件驱动叫醒，替代定时巡逻；规格 2026-09-25-board-watchdog-spec.md）

由 launchd WatchPaths 在 events.jsonl 变化时拉起：每次运行 = 去抖 → 处理新事件 → 退出。
急活（0 级任务 add / 留言 @值守身份）→ macOS 通知（+可选 wake_command / 镜像卡）；
普通事项零动作——看板本身就是存储，晨报 agent 自然会接。

设计约束：纯标准库；偏移量记忆（重启不重响）；文件变小视为轮转（重置偏移+哈希去重）；
watchdog 自己与 quiet 身份的 add/done 不触发（防回环）。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

DEFAULT_CONFIG_DIR = "~/.config/board-watchdog"
CONFIG_NAME = "config.json"
STATE_NAME = "state.json"
LOG_NAME = "watchdog.log"
SELF = "watchdog"
LOG_MAX_BYTES = 1024 * 1024
HASH_KEEP = 300


def default_config():
    home_board = "/Users/user/Documents/trae_projects/Reed-birdwatching-simulater/Reed-Bird-watching-simulator-exhibit/.agent-board/events.jsonl"
    return {
        "boards": [home_board] if os.path.exists(home_board) else [],
        "watch_identities": ["doc-audit", "plugin-dev"],
        "quiet_identities": ["doc-audit"],
        "self_identity": SELF,
        "urgent_priority": 0,
        "urgent_keywords": [],
        "wake_command": "",
        "mirror_urgent_card": False,
        "notify": True,
        "debounce_seconds": 60,
        "state_file": os.path.join(DEFAULT_CONFIG_DIR, STATE_NAME),
        "log_file": os.path.join(DEFAULT_CONFIG_DIR, LOG_NAME),
    }


def expand(p):
    return os.path.realpath(os.path.expanduser(p or ""))


def load_config(path):
    cfg = default_config()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if v is not None:
                    cfg[k] = v
        except (json.JSONDecodeError, OSError) as e:
            print("配置读取失败（用默认配置继续）：%s" % e, file=sys.stderr)
    return cfg


def load_state(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"streams": {}, "hashes": []}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def log(cfg, action, **kv):
    path = expand(cfg.get("log_file", ""))
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".old")
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "action": action}
        rec.update(kv)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def line_hash(raw):
    return hashlib.md5(raw.encode("utf-8", "replace")).hexdigest()[:16]


def notify(title, body):
    if sys.platform != "darwin":
        return False
    def esc(s):
        return str(s or "").replace("\\", "\\\\").replace('"', '\\"')
    script = 'display notification "%s" with title "%s" subtitle "%s"' % (
        esc(body), esc(title), esc("watchdog"))
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=8)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def read_task_priority(board_root, task_id):
    p = os.path.join(board_root, "tasks", "%s.json" % task_id)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("priority")
    except (OSError, json.JSONDecodeError):
        return None


def process_stream(cfg, stream, st):
    """处理一条事件流：按偏移读新行 → 分类 → 动作。返回 (新事件数, 急活数, 备注列表)。"""
    notes = []
    urgent = 0
    if not os.path.exists(stream):
        return 0, 0, ["流不存在，跳过"]
    board_root = os.path.dirname(stream)
    size = os.path.getsize(stream)
    entry = st["streams"].setdefault(stream, {"offset": 0, "hashes": []})
    offset = entry.get("offset", 0)
    known = set(entry.get("hashes", []))
    self_l = str(cfg.get("self_identity", SELF)).lower()
    quiet = [str(q).lower() for q in cfg.get("quiet_identities", [])]
    watchers = [str(w).lower() for w in cfg.get("watch_identities", [])]

    if size < offset:
        # 轮转/重建：偏移归零 + 哈希去重防全量重响
        notes.append("检测到文件变小（%d→%d），偏移重置并用哈希去重" % (offset, size))
        offset = 0

    new_lines = []
    with open(stream, encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        for raw in f:
            new_lines.append(raw)
    new_offset = offset
    seen = 0
    for raw in new_lines:
        new_offset += len(raw.encode("utf-8", "replace"))
        h = line_hash(raw)
        seen += 1
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ev_hash = line_hash(raw)
        agent = str(ev.get("agent", ""))
        event = ev.get("event", "")
        task = ev.get("task", "-")
        detail = str(ev.get("detail") or "")
        if offset == 0 and h in known:
            continue  # 轮转去重：旧事件不再触发
        entry.setdefault("hashes", []).append(ev_hash)
        if len(entry["hashes"]) > HASH_KEEP:
            entry["hashes"] = entry["hashes"][-HASH_KEEP:]

        disposition = "忽略"
        if agent.lower() == self_l:
            disposition = "自己的事件，忽略"
        elif agent.lower() in quiet and event in ("add", "done"):
            disposition = "quiet 身份的 %s，忽略" % event
        elif event == "add":
            pri = read_task_priority(board_root, task) if task.startswith("T-") else None
            if pri == cfg.get("urgent_priority", 0):
                urgent += 1
                disposition = "急活：0 级任务 %s" % task
                do_urgent(cfg, task, detail, board_root)
            elif any(kw in detail for kw in cfg.get("urgent_keywords", [])):
                urgent += 1
                disposition = "急活：命中关键词"
                do_urgent(cfg, task, detail, board_root)
        elif any(("@%s" % w) in detail for w in watchers):
            urgent += 1
            disposition = "急活：@值守身份（%s → %s）" % (agent, task)
            do_urgent(cfg, task, detail, board_root)
        notes.append("%s %s %s %s → %s" % (agent, event, task, one_line(detail, 60), disposition))
    entry["offset"] = new_offset
    return seen, urgent, notes


def do_urgent(cfg, task, detail, board_root):
    body = one_line("%s %s" % (task, detail), 140)
    notified = False
    if cfg.get("notify", True):
        notified = notify("看板看门狗 · 急活", body)
    wake = str(cfg.get("wake_command", "") or "")
    if wake:
        try:
            subprocess.run(wake.replace("{task}", task).replace("{detail}", one_line(detail, 80)),
                           shell=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
    if cfg.get("mirror_urgent_card"):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board.py")
        if os.path.exists(script):
            try:
                subprocess.run([sys.executable, script, "add", "[watchdog] 急活镜像 %s" % task,
                                "-d", one_line(detail, 200), "-p", "0", "--agent", SELF,
                                "--root", board_root], capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
    log(cfg, "urgent", task=task, notified=notified, detail=one_line(detail, 120))


def one_line(s, n):
    s = " ".join(str(s or "").split())
    return s[:n] + "…" if len(s) > n else s


def run_round(cfg):
    state_path = expand(cfg.get("state_file", ""))
    st = load_state(state_path)
    st.setdefault("streams", {})
    total, urgent_all, notes_all = 0, 0, []
    for stream in cfg.get("boards", []):
        s_path = expand(stream)
        n, u, notes = process_stream(cfg, s_path, st)
        total += n
        urgent_all += u
        notes_all.extend(notes)
    save_state(state_path, st)
    log(cfg, "round", events=total, urgent=urgent_all, notes=notes_all)
    return total, urgent_all, notes_all


def main():
    ap = argparse.ArgumentParser(description="看板看门狗：事件驱动的急活叫醒（launchd WatchPaths 拉起）")
    ap.add_argument("--once", action="store_true", help="立即处理一轮（跳过去抖等待）")
    ap.add_argument("--config", default=os.path.join(expand(DEFAULT_CONFIG_DIR), CONFIG_NAME))
    ap.add_argument("--debounce", type=int, default=None, help="覆盖去抖秒数（测试用）")
    a = ap.parse_args()

    cfg = load_config(a.config)
    if a.debounce is not None:
        cfg["debounce_seconds"] = a.debounce
    if not cfg.get("boards"):
        print("看门狗：配置里没有要监视的看板（config.json 的 boards 为空），无事可做。")
        return
    if not a.once:
        time.sleep(max(int(cfg.get("debounce_seconds", 60)), 0))
    total, urgent, notes = run_round(cfg)
    print("看门狗：处理 %d 条新事件，急活 %d 件。" % (total, urgent))


if __name__ == "__main__":
    main()
