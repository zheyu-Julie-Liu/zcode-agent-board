#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board.py — 文件型多 agent 任务看板 CLI（零第三方依赖，Python 3.9+）

看板数据位于项目根的 .agent-board/：
    tasks/T-NNNN.json   任务文件（原子写入）
    locks/T-NNNN.lock   认领锁（O_EXCL 原子创建，先抢到先得）
    events.jsonl        全部 agent 的操作流水（跨对话可见）
    inbox/<名字>.jsonl  @提及收件箱（谁 @ 了我、在哪个任务、说了什么）；<名字>.ack 记已读水位
    config.json         可选配置 {"stale_seconds": 1800, "notify_mentions": ["boss"],
                        "notify": true, "notify_sound": "default", "open_app": "ZCode",
                        "project_name": "显示名"}

看板定位：--root > 环境变量 AGENT_BOARD_ROOT > 向上找已有 .agent-board > git 根 > cwd。
多个项目共用一块板时，用前两者把看板固定指向同一个目录。

送达人类：文本里出现 @名字 会写入该名字的收件箱；名字在 notify_mentions（默认 boss）
里的还会弹 macOS 桌面通知（osascript，系统自带；非 macOS 静默跳过）。deliver 命令把
晨报/方案等交付物直接在桌面应用（默认 ZCode）里打开，并记录送达。

并发原理：认领 = 用 O_CREAT|O_EXCL 创建锁文件，POSIX 保证同一时刻
只有一个进程创建成功，因此同一项目下的多个对话/并行 worker 不会
抢到同一个任务；锁文件 mtime 超过 stale_seconds 视为原认领者已离开，
其他 agent 可以 steal 接管。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

VERSION = "0.2.4"
BOARD_DIR = ".agent-board"
DEFAULT_STALE_SECONDS = 1800
DEFAULT_NOTIFY_MENTIONS = ["boss"]
DEFAULT_OPEN_APP = "ZCode"
MENTION_RE = re.compile(r"@([A-Za-z0-9_][A-Za-z0-9_.\-]*)")
STATUS_ORDER = {"todo": 0, "in_progress": 1, "review": 2, "done": 3, "cancelled": 4}
STATUS_ZH = {
    "todo": "待办",
    "in_progress": "进行中",
    "review": "待审核",
    "done": "完成",
    "cancelled": "已取消",
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def atomic_write_json(path, obj):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def truncate(s, n):
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# 文本里的文件路径：~/…、/绝对路径、或 首段/… 形式的相对路径（相对看板项目根），
# 遇到空白与中英文标点即止；提取后逐个校验真实存在才当作文件
PATH_RE = re.compile(r"(?:~/|/|[A-Za-z0-9_.\-]+/)[^\s，。；、！？：（）()【】\[\]<>\"'`]+")
# 裸文件名（如 晨报_2026-09-08.md / AGENTS.md）：常见扩展名 + 到项目根核验存在
BARE_FILE_RE = re.compile(
    r"[^\s/，。；、！？：（）()【】\[\]<>\"'`]+\.(?:md|txt|py|json|gd|tscn|toml|cfg|ini|sh|html|js|ts|csv|log|pdf|png|jpg|jpeg)\b")


def extract_file_paths(text, base_dir=None, limit=8):
    """从任意文本里提取真实存在的文件路径（去重、保序），供看板视图生成可点击链接。"""
    out, seen = [], set()
    base = base_dir or os.getcwd()
    candidates = [(m, m.startswith(("~", "/"))) for m in PATH_RE.findall(text or "")]
    candidates += [(m, False) for m in BARE_FILE_RE.findall(text or "")]
    for m, is_abs in candidates:
        p = m.rstrip(".,;:、。，；：")
        if not p or p in seen:
            continue
        seen.add(p)
        cand = os.path.realpath(os.path.expanduser(p) if is_abs else os.path.join(base, p))
        if os.path.isfile(cand) and cand not in out:
            out.append(cand)
            if len(out) >= limit:
                break
    return out


def file_link(path):
    """markdown 链接（ZCode 聊天里可点击跳到文件）；只转义空格和括号，中文保持可读。"""
    url = path.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    return "[%s](%s)" % (os.path.basename(path), url)


def task_texts(task):
    """任务里所有可能提到文件的文本：标题/说明/结果/全部留言。"""
    parts = [task.get("title", ""), task.get("description", ""), task.get("result", "")]
    parts.extend(c.get("text", "") for c in task.get("comments", []))
    return "\n".join(p for p in parts if p)


def find_board_root(explicit=None):
    """--root / $AGENT_BOARD_ROOT 显式指定优先；否则向上找已有的 .agent-board；
    没有则放在 git 仓库根，再不行放 cwd。"""
    explicit = explicit or os.environ.get("AGENT_BOARD_ROOT")
    if explicit:
        d = os.path.realpath(os.path.expanduser(str(explicit)))
        if not os.path.isdir(d):
            die("错误：看板根目录不存在：%s（检查 --root 或 $AGENT_BOARD_ROOT）" % d)
        return d
    d = os.path.realpath(os.getcwd())
    while True:
        if os.path.isdir(os.path.join(d, BOARD_DIR)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    d = os.path.realpath(os.getcwd())
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.realpath(os.getcwd())
        d = parent


class Board:
    def __init__(self, root=None):
        self.root = find_board_root(root)
        self.dir = os.path.join(self.root, BOARD_DIR)
        self.tasks_dir = os.path.join(self.dir, "tasks")
        self.locks_dir = os.path.join(self.dir, "locks")
        self.inbox_dir = os.path.join(self.dir, "inbox")
        self.events_path = os.path.join(self.dir, "events.jsonl")
        os.makedirs(self.tasks_dir, exist_ok=True)
        os.makedirs(self.locks_dir, exist_ok=True)
        self.stale_seconds = DEFAULT_STALE_SECONDS
        self.notify_mentions = list(DEFAULT_NOTIFY_MENTIONS)
        self.notify_enabled = os.environ.get("AGENT_BOARD_NOTIFY", "1").lower() not in ("0", "false", "no", "off")
        self.notify_sound = "default"
        self.open_app = DEFAULT_OPEN_APP
        self.project_name = os.path.basename(self.root)
        cfg = os.path.join(self.dir, "config.json")
        if os.path.exists(cfg):
            try:
                with open(cfg, encoding="utf-8") as f:
                    c = json.load(f)
                self.stale_seconds = int(c.get("stale_seconds", DEFAULT_STALE_SECONDS))
                self.notify_mentions = [str(n).lstrip("@").lower()
                                        for n in c.get("notify_mentions", DEFAULT_NOTIFY_MENTIONS)]
                if c.get("notify") is False:
                    self.notify_enabled = False
                self.notify_sound = c.get("notify_sound", "default")
                self.open_app = c.get("open_app") or DEFAULT_OPEN_APP
                self.project_name = c.get("project_name") or self.project_name
            except (json.JSONDecodeError, ValueError, OSError, AttributeError, TypeError):
                pass

    # ---------- 基础 ----------
    def task_path(self, tid):
        return os.path.join(self.tasks_dir, "%s.json" % tid)

    def lock_path(self, tid):
        return os.path.join(self.locks_dir, "%s.lock" % tid)

    def log_event(self, agent, event, tid, detail=""):
        rec = {"ts": now_iso(), "agent": agent, "event": event, "task": tid, "detail": truncate(detail, 120)}
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------- 送达人类：@提及收件箱 + 桌面通知 + 打开交付物 ----------
    def inbox_path(self, name):
        return os.path.join(self.inbox_dir, "%s.jsonl" % name)

    def inbox_ack_path(self, name):
        return os.path.join(self.inbox_dir, "%s.ack" % name)

    def inbox_append(self, name, rec):
        os.makedirs(self.inbox_dir, exist_ok=True)
        with open(self.inbox_path(name), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def inbox_read(self, name, include_read=False):
        """读某个名字的收件箱；默认只返回已读水位（.ack 里的时间戳）之后的条目。"""
        p = self.inbox_path(name)
        if not os.path.exists(p):
            return []
        ack = ""
        if not include_read and os.path.exists(self.inbox_ack_path(name)):
            try:
                with open(self.inbox_ack_path(name), encoding="utf-8") as f:
                    ack = f.read().strip()
            except OSError:
                ack = ""
        out = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if include_read or r.get("ts", "") > ack:
                    out.append(r)
        return out

    def notify_desktop(self, title, subtitle, body):
        """macOS 桌面通知（osascript，系统自带）。非 macOS / 无 osascript / 已禁用则静默跳过，
        任何失败都不影响主流程。"""
        if not self.notify_enabled or sys.platform != "darwin":
            return False
        osa = shutil.which("osascript")
        if not osa:
            return False

        def esc(s):
            return (s or "").replace("\\", "\\\\").replace('"', '\\"')

        script = 'display notification "%s" with title "%s" subtitle "%s"' % (
            esc(truncate(body, 180)), esc(title), esc(truncate(subtitle, 60)))
        if self.notify_sound:
            script += ' sound name "%s"' % esc(str(self.notify_sound))
        try:
            subprocess.run([osa, "-e", script], capture_output=True, timeout=8)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def deliver_mentions(self, agent, tid, event, text, extra_names=()):
        """文本里 @到的名字各写一条收件箱；名字在通知名单（默认 boss）内的再弹桌面通知。
        作者 @ 自己不算。返回实际送达的名字列表。"""
        names = {m.rstrip(".").lower() for m in MENTION_RE.findall(text or "")}
        names.update(str(n).lstrip("@").lower() for n in extra_names if n)
        names.discard((agent or "").lower())
        for name in sorted(names):
            self.inbox_append(name, {"ts": now_iso(), "from": agent, "task": tid, "event": event, "text": text or ""})
            if name in self.notify_mentions:
                self.notify_desktop("agent-board · @%s" % name, "%s · %s · %s" % (agent, tid, event), text)
        return sorted(names)

    def open_file(self, path, app=None):
        """用桌面应用打开文件（默认 ZCode），失败回退系统默认方式。返回 (是否成功, 用的方式)。"""
        app = app or os.environ.get("AGENT_BOARD_OPEN_APP") or self.open_app
        try:
            if sys.platform == "darwin":
                if app:
                    r = subprocess.run(["open", "-a", app, path], capture_output=True, timeout=15)
                    if r.returncode == 0:
                        return True, app
                r = subprocess.run(["open", path], capture_output=True, timeout=15)
                return r.returncode == 0, "系统默认应用"
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: attribute only exists on Windows
                return True, "系统默认应用"
            opener = shutil.which("xdg-open")
            if opener:
                r = subprocess.run([opener, path], capture_output=True, timeout=15)
                return r.returncode == 0, "xdg-open"
        except (OSError, subprocess.SubprocessError, AttributeError):
            pass
        return False, app or "系统默认应用"

    def load_task(self, tid):
        if not os.path.exists(self.task_path(tid)):
            die("错误：任务 %s 不存在（用 board list 查看现有任务）" % tid)
        try:
            with open(self.task_path(tid), encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            die("错误：任务 %s 文件正被写入或已损坏，请稍后重试" % tid)

    def save_task(self, task):
        atomic_write_json(self.task_path(task["id"]), task)

    def all_tasks(self):
        out = []
        try:
            names = os.listdir(self.tasks_dir)
        except OSError:
            return out
        for name in sorted(names):
            if not (name.startswith("T-") and name.endswith(".json")):
                continue
            try:
                with open(os.path.join(self.tasks_dir, name), encoding="utf-8") as f:
                    out.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                continue  # 正在被写入/损坏的文件跳过
        return out

    def read_lock(self, tid):
        p = self.lock_path(tid)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"agent": "?", "host": "?", "pid": -1, "ts": ""}

    def lock_is_stale(self, tid):
        """锁 mtime 超过 stale_seconds → 原认领者视为已离开。"""
        p = self.lock_path(tid)
        if not os.path.exists(p):
            return True
        try:
            return (time.time() - os.path.getmtime(p)) > self.stale_seconds
        except OSError:
            return False

    def check_owner(self, task, agent, force):
        lock = self.read_lock(task["id"])
        if lock is None:
            if task["status"] == "in_progress" and not force:
                die("错误：任务 %s 处于进行中但没有锁（认领者可能异常退出）。"
                    "确认无人处理后可用 --force 强制操作。" % task["id"])
            return
        if lock.get("agent") != agent and not force:
            tip = ""
            if self.lock_is_stale(task["id"]):
                tip = "该认领已超过 %d 秒未活动，可以用 steal 接管。" % self.stale_seconds
            die("错误：任务 %s 当前由「%s」认领，你不是该任务的持有人。%s"
                "（确认对方已放弃可用 --force 或 steal）" % (task["id"], lock.get("agent"), tip))

    # ---------- 命令 ----------
    def cmd_add(self, args):
        task = {
            "id": None,
            "title": args.title,
            "description": args.desc or "",
            "status": "todo",
            "priority": args.priority,
            "parent": args.parent,
            "created_by": args.agent,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "claimed_by": None,
            "claimed_at": None,
            "result": None,
            "comments": [],
        }
        for n in range(1, 10000):
            tid = "T-%04d" % n
            try:
                fd = os.open(self.task_path(tid), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                continue  # ID 已被占用（含并发添加的情况）
            task["id"] = tid
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(task, f, ensure_ascii=False, indent=2)
            break
        else:
            die("错误：任务 ID 已用尽")
        self.log_event(args.agent, "add", tid, task["title"])
        self.deliver_mentions(args.agent, tid, "add",
                              task["title"] + ("：" + task["description"] if task["description"] else ""))
        if args.json:
            print(json.dumps(task, ensure_ascii=False, indent=2))
        else:
            print("已创建 %s「%s」（优先级 %d，状态：待办）" % (tid, task["title"], task["priority"]))

    def _try_claim(self, tid, agent):
        """原子创建锁 → 成功则认领；失败返回 False。"""
        lock = {"task": tid, "agent": agent, "host": socket.gethostname(),
                "pid": os.getpid(), "ts": now_iso()}
        try:
            fd = os.open(self.lock_path(tid), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lock, f, ensure_ascii=False)
        task = self.load_task(tid)
        task["status"] = "in_progress"
        task["claimed_by"] = agent
        task["claimed_at"] = now_iso()
        task["updated_at"] = now_iso()
        self.save_task(task)
        return True

    def cmd_claim(self, args):
        agent = args.agent
        if args.task_id:
            task = self.load_task(args.task_id)
            if task["status"] in ("done", "cancelled"):
                die("错误：任务 %s 已是终态（%s），无法认领" % (task["id"], STATUS_ZH[task["status"]]))
            if not self._try_claim(task["id"], agent):
                lock = self.read_lock(task["id"]) or {}
                stale = self.lock_is_stale(task["id"])
                tip = "原认领已超时，可用 steal 接管。" if stale else "若确认对方已离开也可用 steal 接管。"
                die("错误：任务 %s 已被「%s」认领。%s"
                    % (task["id"], lock.get("agent", "?"), tip if task["status"] == "in_progress" else tip))
            self.log_event(agent, "claim", task["id"], task["title"])
        else:
            # 自动挑任务：todo 优先级最小者；review 任务也可被重新认领（返工）
            candidates = [t for t in self.all_tasks() if t.get("status") == "todo"]
            candidates.sort(key=lambda t: (t.get("priority", 2), t.get("created_at", ""), t.get("id", "")))
            picked = None
            for t in candidates:
                if self._try_claim(t["id"], agent):
                    picked = t
                    break
            if picked is None:
                if candidates:
                    die("错误：看板上还有 %d 个待办任务，但都刚被其他 agent 抢先，请稍后重试或用 list 查看。" % len(candidates))
                die("错误：看板上没有可认领的待办任务（用 board add 添加，或用 list --all 查看全部）。")
            task = picked
            self.log_event(agent, "claim", task["id"], task["title"])
        if args.json:
            print(json.dumps(self.load_task(task["id"]), ensure_ascii=False, indent=2))
        else:
            t = self.load_task(task["id"])
            print("已认领 %s「%s」（优先级 %d）" % (t["id"], t["title"], t["priority"]))
            if t.get("description"):
                print("说明：%s" % t["description"])
            print("完成后执行：board done %s --result \"结果摘要\"" % t["id"])

    def cmd_done(self, args):
        task = self.load_task(args.task_id)
        if task["status"] in ("done", "cancelled"):
            die("错误：任务 %s 已是终态（%s）" % (task["id"], STATUS_ZH[task["status"]]))
        self.check_owner(task, args.agent, args.force)
        task["status"] = "done"
        task["result"] = args.result or ""
        task["updated_at"] = now_iso()
        self.save_task(task)
        lock = self.lock_path(task["id"])
        if os.path.exists(lock):
            os.remove(lock)
        self.log_event(args.agent, "done", task["id"], task["result"] or task["title"])
        self.deliver_mentions(args.agent, task["id"], "done", task["result"])
        print("任务 %s「%s」已完成%s" % (task["id"], task["title"], ("，结果：%s" % task["result"]) if task["result"] else ""))

    def cmd_review(self, args):
        task = self.load_task(args.task_id)
        if task["status"] != "in_progress":
            die("错误：只有进行中的任务可以提交审核（当前：%s）" % STATUS_ZH[task["status"]])
        self.check_owner(task, args.agent, args.force)
        task["status"] = "review"
        task["updated_at"] = now_iso()
        self.save_task(task)
        lock = self.lock_path(task["id"])
        if os.path.exists(lock):
            os.remove(lock)
        self.log_event(args.agent, "review", task["id"], task["title"])
        print("任务 %s 已提交审核。其他人可用 board done %s 验收，或 board claim %s 打回返工。" % (task["id"], task["id"], task["id"]))

    def cmd_release(self, args):
        task = self.load_task(args.task_id)
        if task["status"] not in ("in_progress", "review"):
            die("错误：任务 %s 当前状态为「%s」，无需释放" % (task["id"], STATUS_ZH[task["status"]]))
        self.check_owner(task, args.agent, args.force)
        task["status"] = "todo"
        task["claimed_by"] = None
        task["claimed_at"] = None
        task["updated_at"] = now_iso()
        self.save_task(task)
        lock = self.lock_path(task["id"])
        if os.path.exists(lock):
            os.remove(lock)
        self.log_event(args.agent, "release", task["id"], args.reason or "")
        self.deliver_mentions(args.agent, task["id"], "release", args.reason)
        print("任务 %s 已释放回待办池%s" % (task["id"], ("（原因：%s）" % args.reason) if args.reason else ""))

    def cmd_cancel(self, args):
        task = self.load_task(args.task_id)
        if task["status"] == "cancelled":
            die("错误：任务 %s 已取消" % task["id"])
        lock = self.read_lock(task["id"])
        if lock and lock.get("agent") != args.agent and not args.force:
            die("错误：任务 %s 由「%s」认领中，取消需 --force 或让对方 release" % (task["id"], lock.get("agent")))
        task["status"] = "cancelled"
        task["updated_at"] = now_iso()
        self.save_task(task)
        lockp = self.lock_path(task["id"])
        if os.path.exists(lockp):
            os.remove(lockp)
        self.log_event(args.agent, "cancel", task["id"], args.reason or "")
        self.deliver_mentions(args.agent, task["id"], "cancel", args.reason)
        print("任务 %s「%s」已取消%s" % (task["id"], task["title"], ("（原因：%s）" % args.reason) if args.reason else ""))

    def cmd_comment(self, args):
        task = self.load_task(args.task_id)
        task.setdefault("comments", []).append({"agent": args.agent, "ts": now_iso(), "text": args.text})
        task["updated_at"] = now_iso()
        self.save_task(task)
        if os.path.exists(self.lock_path(task["id"])):
            os.utime(self.lock_path(task["id"]))  # 留言同时续约锁
        self.log_event(args.agent, "comment", task["id"], args.text)
        delivered = self.deliver_mentions(args.agent, task["id"], "comment", args.text)
        print("已在任务 %s 上留言。%s" % (task["id"], ("已送达 @" + " @".join(delivered) + "。") if delivered else ""))

    def cmd_steal(self, args):
        task = self.load_task(args.task_id)
        if task["status"] in ("done", "cancelled"):
            die("错误：任务 %s 已是终态（%s）" % (task["id"], STATUS_ZH[task["status"]]))
        if not os.path.exists(self.lock_path(task["id"])):
            die("错误：任务 %s 没有认领锁，直接用 board claim %s 即可" % (task["id"], task["id"]))
        if not self.lock_is_stale(task["id"]):
            die("错误：任务 %s 的认领尚未超时（%d 秒内有活动），不能抢。"
                "如果确认对方已离开，可让 TA release，或由你 --force 操作。" % (task["id"], self.stale_seconds))
        old = self.read_lock(task["id"]) or {}
        # 原子替换锁：先写临时文件再 rename 覆盖
        tmp = self.lock_path(task["id"]) + ".steal.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"task": task["id"], "agent": args.agent, "host": socket.gethostname(),
                       "pid": os.getpid(), "ts": now_iso()}, f, ensure_ascii=False)
        os.replace(tmp, self.lock_path(task["id"]))
        task["status"] = "in_progress"
        task["claimed_by"] = args.agent
        task["claimed_at"] = now_iso()
        task["updated_at"] = now_iso()
        self.save_task(task)
        self.log_event(args.agent, "steal", task["id"], "从「%s」接管" % old.get("agent", "?"))
        print("已从「%s」接管任务 %s「%s」。" % (old.get("agent", "?"), task["id"], task["title"]))

    def cmd_touch(self, args):
        task = self.load_task(args.task_id)
        lockp = self.lock_path(task["id"])
        if not os.path.exists(lockp):
            die("错误：任务 %s 没有进行中的认领，无需续约" % task["id"])
        self.check_owner(task, args.agent, args.force)
        os.utime(lockp)
        print("已续约任务 %s 的认领锁（%d 秒内不会被接管）。" % (task["id"], self.stale_seconds))

    def cmd_show(self, args):
        task = self.load_task(args.task_id)
        if args.json:
            print(json.dumps(task, ensure_ascii=False, indent=2))
            return
        print("任务 %s   [%s]  优先级 %d  创建人 %s  %s" % (
            task["id"], STATUS_ZH.get(task["status"], task["status"]), task["priority"],
            task.get("created_by", "?"), task.get("created_at", "")))
        print("标题：" + task["title"])
        if task.get("description"):
            print("说明：" + task["description"])
        if task.get("parent"):
            print("父任务：" + task["parent"])
        if task.get("claimed_by"):
            print("认领者：%s（%s 起）" % (task["claimed_by"], task.get("claimed_at", "?")))
        lock = self.read_lock(task["id"])
        if lock:
            print("持锁：%s @ %s（%d 秒无活动%s）" % (
                lock.get("agent"), lock.get("host"), self._lock_idle(task["id"]),
                "，已超时可被 steal" if self.lock_is_stale(task["id"]) else ""))
        if task.get("result"):
            print("结果：" + task["result"])
        for c in task.get("comments", []):
            print("留言 [%s] %s：%s" % (c.get("ts", ""), c.get("agent", "?"), c.get("text", "")))
        files = extract_file_paths(task_texts(task), base_dir=self.root)
        if files:
            print("📎 相关文件：" + "  ".join(file_link(p) for p in files))

    def _lock_idle(self, tid):
        try:
            return int(time.time() - os.path.getmtime(self.lock_path(tid)))
        except OSError:
            return -1

    def cmd_list(self, args):
        tasks = self.all_tasks()
        if not args.all:
            tasks = [t for t in tasks if t.get("status") in ("todo", "in_progress", "review")]
        if args.status:
            tasks = [t for t in tasks if t.get("status") == args.status]
        if args.mine:
            tasks = [t for t in tasks if t.get("claimed_by") == args.agent]
        tasks.sort(key=lambda t: (STATUS_ORDER.get(t.get("status"), 9), t.get("priority", 2), t.get("id", "")))
        if args.json:
            print(json.dumps(tasks, ensure_ascii=False, indent=2))
            return
        if not tasks:
            print("（看板上没有匹配的任务）")
            return
        print("%-9s %-8s %-4s %-14s %s" % ("ID", "状态", "优先", "认领者", "标题"))
        for t in tasks:
            print("%-9s %-8s %-4d %-14s %s" % (
                t["id"], STATUS_ZH.get(t.get("status"), t.get("status", "?")), t.get("priority", 2),
                truncate(t.get("claimed_by") or "-", 14), truncate(t.get("title", ""), 60)))
        n = {"todo": 0, "in_progress": 0, "review": 0, "done": 0, "cancelled": 0}
        for t in tasks:
            n[t.get("status")] = n.get(t.get("status"), 0) + 1
        print("--- 待办 %d | 进行中 %d | 待审核 %d%s" % (
            n["todo"], n["in_progress"], n["review"],
            (" | 完成 %d | 已取消 %d" % (n["done"], n["cancelled"])) if args.all else ""))

    def cmd_feed(self, args):
        if not os.path.exists(self.events_path):
            print("（还没有任何操作记录）")
            return
        with open(self.events_path, encoding="utf-8") as f:
            lines = f.readlines()
        recent = lines[-args.limit:]
        if args.json:
            print(json.dumps([json.loads(l) for l in recent if l.strip()], ensure_ascii=False, indent=2))
            return
        for l in recent:
            try:
                r = json.loads(l)
            except json.JSONDecodeError:
                continue
            print("%s  %-16s %-8s %-9s %s" % (r.get("ts", ""), truncate(r.get("agent", "?"), 16),
                                              r.get("event", "?"), r.get("task", ""), r.get("detail", "")))

    def cmd_whoami(self, args):
        info = {"agent": args.agent, "root": self.root, "board_dir": self.dir, "project_name": self.project_name,
                "stale_seconds": self.stale_seconds, "notify_mentions": self.notify_mentions,
                "notify": self.notify_enabled, "open_app": self.open_app, "version": VERSION}
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return
        print("身份：%s" % args.agent)
        print("看板目录：%s" % self.dir)
        print("认领超时：%d 秒" % self.stale_seconds)
        print("通知名单：%s（桌面通知%s）" % (" ".join("@" + n for n in self.notify_mentions),
                                          "开" if self.notify_enabled else "关"))
        print("交付打开应用：%s" % self.open_app)

    def cmd_inbox(self, args):
        name = (args.agent or "").lstrip("@").lower()
        if not name:
            die("错误：请用 --agent 指定要查看收件箱的身份名（如 --agent boss）")
        unread = self.inbox_read(name)
        if args.ack:
            everything = self.inbox_read(name, include_read=True)
            os.makedirs(self.inbox_dir, exist_ok=True)
            with open(self.inbox_ack_path(name), "w", encoding="utf-8") as f:
                f.write((everything[-1]["ts"] if everything else now_iso()) + "\n")
            if args.json:
                print(json.dumps({"acked": len(unread)}, ensure_ascii=False))
            else:
                print("已将 @%s 的 %d 条未读提及标记为已读。" % (name, len(unread)))
            return
        entries = self.inbox_read(name, include_read=True) if args.all else unread
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
            return
        if not entries:
            print("📭 @%s 没有%s提及。" % (name, "" if args.all else "未读"))
            return
        print("📬 @%s %s %d 条（看完用 board inbox --agent %s --ack 标记已读）"
              % (name, "全部" if args.all else "未读", len(entries), name))
        for r in entries:
            print("%s  %-12s %-7s %-8s %s" % (r.get("ts", ""), truncate(r.get("from", "?"), 12),
                                            r.get("task", "-"), r.get("event", ""), truncate(r.get("text", ""), 110)))

    def cmd_deliver(self, args):
        path = os.path.realpath(os.path.expanduser(args.file))
        if not os.path.exists(path):
            die("错误：文件不存在：%s" % path)
        to = (args.to or "boss").lstrip("@").lower()
        ok, how = self.open_file(path, args.app)
        note = (" — " + args.note) if args.note else ""
        name = os.path.basename(path)
        if ok:
            text = "【交付 @%s】📄 %s 已由 %s 用 %s 打开%s（路径：%s）" % (to, name, args.agent, how, note, path)
        else:
            text = "【交付 @%s】📄 %s 打开失败（%s），请手动查看%s（路径：%s）" % (to, name, how, note, path)
        tid = args.task_id or "-"
        if args.task_id:
            task = self.load_task(args.task_id)
            task.setdefault("comments", []).append({"agent": args.agent, "ts": now_iso(), "text": text})
            task["updated_at"] = now_iso()
            self.save_task(task)
            if os.path.exists(self.lock_path(task["id"])):
                os.utime(self.lock_path(task["id"]))
        self.log_event(args.agent, "deliver", tid, name + note)
        self.deliver_mentions(args.agent, tid, "deliver", text, extra_names=[to])
        notified = ok and to in self.notify_mentions and self.notify_enabled and sys.platform == "darwin"
        if args.json:
            print(json.dumps({"ok": ok, "path": path, "opened_with": how, "to": to, "task": tid,
                              "notified": bool(notified)}, ensure_ascii=False))
            return
        print(("已用 %s 打开：%s" % (how, path)) if ok else ("打开失败（%s）：%s" % (how, path)))
        print("已送达 @%s：收件箱已记录%s%s" % (to, "，桌面通知已发" if notified else "",
                                          ("，并已在 %s 留言" % tid) if args.task_id else ""))


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--agent", default=os.environ.get("AGENT_BOARD_AGENT") or getpass.getuser(),
                        help="agent 身份名（默认取 $AGENT_BOARD_AGENT 或系统用户名）；同一对话/同一 worker 全程保持一致")
    common.add_argument("--json", action="store_true", help="以 JSON 输出（供程序/agent 解析）")
    # 子命令侧用 SUPPRESS：没写时不覆盖顶层 --root 的值，这样 --root 放子命令前后都行
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help="看板所在项目根目录（默认取 $AGENT_BOARD_ROOT，再向上查找 .agent-board / git 根）；"
                             "多个项目共用一块板时用它固定指向")

    p = argparse.ArgumentParser(prog="board", description="agent-board：跨对话/并行 worker 的文件型任务看板（零依赖）")
    p.add_argument("--version", action="version", version="agent-board %s" % VERSION)
    p.add_argument("--root", default=None, help="看板所在项目根目录（也可放在子命令之后）")
    sub = p.add_subparsers(dest="cmd")

    def attach(name, help_, fn, **kw):
        sp = sub.add_parser(name, help=help_, parents=[common])
        sp.set_defaults(fn=fn)
        return sp

    sp = attach("add", "创建任务", Board.cmd_add)
    sp.add_argument("title", help="任务标题")
    sp.add_argument("-d", "--desc", help="任务详细说明")
    sp.add_argument("-p", "--priority", type=int, default=2, help="优先级：0 最高 1 高 2 中（默认） 3 低")
    sp.add_argument("--parent", help="父任务 ID（用于大任务拆子任务）")

    sp = attach("claim", "认领任务（不带 ID 时自动挑优先级最高的待办）", Board.cmd_claim)
    sp.add_argument("task_id", nargs="?", help="任务 ID，缺省自动选取")

    sp = attach("done", "完成任务并提交结果", Board.cmd_done)
    sp.add_argument("task_id")
    sp.add_argument("-r", "--result", help="结果摘要（其他 agent 会看到）")
    sp.add_argument("--force", action="store_true", help="非持锁人强制操作")

    sp = attach("review", "提交审核（保留结果待验收）", Board.cmd_review)
    sp.add_argument("task_id")
    sp.add_argument("--force", action="store_true")

    sp = attach("release", "放弃任务，放回待办池", Board.cmd_release)
    sp.add_argument("task_id")
    sp.add_argument("--reason", help="放弃原因")
    sp.add_argument("--force", action="store_true")

    sp = attach("cancel", "取消任务", Board.cmd_cancel)
    sp.add_argument("task_id")
    sp.add_argument("--reason", help="取消原因")
    sp.add_argument("--force", action="store_true")

    sp = attach("comment", "在任务上留言（交接/汇报），同时续约锁", Board.cmd_comment)
    sp.add_argument("task_id")
    sp.add_argument("text", help="留言内容")

    sp = attach("steal", "接管超时未活动的认领", Board.cmd_steal)
    sp.add_argument("task_id")

    sp = attach("touch", "续约认领锁（长任务防被接管）", Board.cmd_touch)
    sp.add_argument("task_id")
    sp.add_argument("--force", action="store_true")

    sp = attach("show", "查看任务详情", Board.cmd_show)
    sp.add_argument("task_id")

    sp = attach("list", "查看看板", Board.cmd_list)
    sp.add_argument("--all", action="store_true", help="包含完成/已取消")
    sp.add_argument("--status", choices=list(STATUS_ORDER), help="按状态过滤")
    sp.add_argument("--mine", action="store_true", help="只看我认领的")

    sp = attach("feed", "查看所有 agent 的最近操作流水", Board.cmd_feed)
    sp.add_argument("-n", "--limit", type=int, default=20)

    sp = attach("inbox", "查看 @我 的提及收件箱（默认只看未读；boss 用它看漏掉的 @boss）", Board.cmd_inbox)
    sp.add_argument("--all", action="store_true", help="包含已读")
    sp.add_argument("--ack", action="store_true", help="把未读全部标记为已读")

    sp = attach("deliver", "把交付物（晨报/方案/报告）在桌面应用里打开给人看，并记录送达", Board.cmd_deliver)
    sp.add_argument("file", help="要打开的文件路径")
    sp.add_argument("--task", dest="task_id", help="关联任务 ID（会在该任务下留言记录送达）")
    sp.add_argument("--to", default="boss", help="送达对象身份名（默认 boss）")
    sp.add_argument("--note", help="附言（出现在留言与桌面通知里）")
    sp.add_argument("--app", help="打开用的应用名（默认 $AGENT_BOARD_OPEN_APP / config.json 的 open_app / ZCode）")

    attach("whoami", "查看当前身份、看板位置与通知设置", Board.cmd_whoami)
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "cmd", None):
        parser.print_help()
        sys.exit(0)
    Board(root=getattr(args, "root", None)).__getattribute__("cmd_%s" % args.cmd)(args)


if __name__ == "__main__":
    main()
