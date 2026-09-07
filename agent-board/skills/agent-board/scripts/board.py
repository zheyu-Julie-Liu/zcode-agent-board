#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board.py — 文件型多 agent 任务看板 CLI（零第三方依赖，Python 3.9+）

看板数据位于项目根的 .agent-board/：
    tasks/T-NNNN.json   任务文件（原子写入）
    locks/T-NNNN.lock   认领锁（O_EXCL 原子创建，先抢到先得）
    events.jsonl        全部 agent 的操作流水（跨对话可见）
    config.json         可选配置 {"stale_seconds": 1800}

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
import socket
import sys
import time
from datetime import datetime, timezone

VERSION = "0.1.0"
BOARD_DIR = ".agent-board"
DEFAULT_STALE_SECONDS = 1800
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


def find_board_root():
    """向上找已有的 .agent-board；没有则放在 git 仓库根，再不行放 cwd。"""
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
    def __init__(self):
        self.root = find_board_root()
        self.dir = os.path.join(self.root, BOARD_DIR)
        self.tasks_dir = os.path.join(self.dir, "tasks")
        self.locks_dir = os.path.join(self.dir, "locks")
        self.events_path = os.path.join(self.dir, "events.jsonl")
        os.makedirs(self.tasks_dir, exist_ok=True)
        os.makedirs(self.locks_dir, exist_ok=True)
        self.stale_seconds = DEFAULT_STALE_SECONDS
        cfg = os.path.join(self.dir, "config.json")
        if os.path.exists(cfg):
            try:
                with open(cfg, encoding="utf-8") as f:
                    self.stale_seconds = int(json.load(f).get("stale_seconds", DEFAULT_STALE_SECONDS))
            except (json.JSONDecodeError, ValueError, OSError):
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
        print("任务 %s「%s」已取消%s" % (task["id"], task["title"], ("（原因：%s）" % args.reason) if args.reason else ""))

    def cmd_comment(self, args):
        task = self.load_task(args.task_id)
        task.setdefault("comments", []).append({"agent": args.agent, "ts": now_iso(), "text": args.text})
        task["updated_at"] = now_iso()
        self.save_task(task)
        if os.path.exists(self.lock_path(task["id"])):
            os.utime(self.lock_path(task["id"]))  # 留言同时续约锁
        self.log_event(args.agent, "comment", task["id"], args.text)
        print("已在任务 %s 上留言。" % task["id"])

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
        print("身份：%s" % args.agent)
        print("看板目录：%s" % self.dir)
        print("认领超时：%d 秒" % self.stale_seconds)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--agent", default=os.environ.get("AGENT_BOARD_AGENT") or getpass.getuser(),
                        help="agent 身份名（默认取 $AGENT_BOARD_AGENT 或系统用户名）；同一对话/同一 worker 全程保持一致")
    common.add_argument("--json", action="store_true", help="以 JSON 输出（供程序/agent 解析）")

    p = argparse.ArgumentParser(prog="board", description="agent-board：跨对话/并行 worker 的文件型任务看板（零依赖）")
    p.add_argument("--version", action="version", version="agent-board %s" % VERSION)
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

    attach("whoami", "查看当前身份与看板位置", Board.cmd_whoami)
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "cmd", None):
        parser.print_help()
        sys.exit(0)
    Board().__getattribute__("cmd_%s" % args.cmd)(args)


if __name__ == "__main__":
    main()
