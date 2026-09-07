#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board_mcp.py — agent-board 的 MCP server（stdio，零第三方依赖）

供 ZCode/Claude 等客户端注册后，让每个对话里的 agent 通过 MCP 工具
直接读写任务看板（比走 CLI 子进程更快、输出结构化）。

协议：newline-delimited JSON-RPC 2.0（MCP stdio 传输）。
身份：每个对话的 agent 在调用工具时传稳定的 agent 参数
     （如 main / worker-1），或配置里设环境变量 AGENT_BOARD_AGENT。
看板定位：优先 AGENT_BOARD_ROOT 环境变量，其次从工作目录向上找
        .agent-board / git 根；找不到时读操作会提示传 root 参数。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
from argparse import Namespace

SERVER_NAME = "agent-board"
SERVER_VERSION = "0.1.0"

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("agent_board_core", os.path.join(HERE, "board.py"))
boardmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(boardmod)

IDENTITY_DESC = "agent 身份名，须在本次对话内保持稳定（如 main / refactor-conv / worker-1）；并行 worker 各用不同名字"
ROOT_DESC = "可选：项目根目录；缺省从服务器工作目录向上查找 .agent-board 或 git 根"


def tool(name, desc, props, required=None, mutating=False, defaults=None):
    return {
        "name": name,
        "description": desc,
        "inputSchema": {"type": "object", "properties": props,
                        "required": required or [], "additionalProperties": False},
        "_mutating": mutating,
        "_defaults": defaults or {},
    }


TOOLS = [
    tool("board_list", "查看任务看板（JSON）。读操作，不改动任何状态。",
         {"root": {"type": "string", "description": ROOT_DESC},
          "all": {"type": "boolean", "description": "包含完成/已取消任务", "default": False},
          "status": {"type": "string", "enum": ["todo", "in_progress", "review", "done", "cancelled"]},
          "mine": {"type": "boolean", "description": "只看某身份认领的任务（配合 agent）", "default": False},
          "agent": {"type": "string", "description": "配合 mine 过滤"}},
         defaults={"all": False, "mine": False, "status": None}),
    tool("board_feed", "查看所有 agent 的最近操作流水（谁接了什么、交了什么），跨对话可见。",
         {"root": {"type": "string", "description": ROOT_DESC},
          "limit": {"type": "integer", "default": 20}},
         defaults={"limit": 20}),
    tool("board_show", "查看单个任务详情：说明、认领者、持锁状态、留言、结果。",
         {"task_id": {"type": "string"}, "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": "你的身份名"}},
         required=["task_id"]),
    tool("board_add", "创建任务（派单）。priority 0 最高 / 2 默认 / 3 低；parent 可挂父任务用于拆分。",
         {"title": {"type": "string"}, "desc": {"type": "string"},
          "priority": {"type": "integer", "default": 2},
          "parent": {"type": "string", "description": "父任务 ID，如 T-0001"},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["title", "agent"], mutating=True,
         defaults={"desc": "", "priority": 2, "parent": None}),
    tool("board_claim", "接任务：不传 task_id 自动认领优先级最高的待办（原子锁，不会被别人抢到同一个）；传 task_id 认领指定任务。",
         {"task_id": {"type": "string", "description": "缺省自动选单"},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["agent"], mutating=True, defaults={"task_id": None}),
    tool("board_done", "提交/完成任务并写结果摘要（其他对话可见）。只有认领者本人可提交。",
         {"task_id": {"type": "string"}, "result": {"type": "string", "description": "结果摘要：改了什么、如何验证"},
          "force": {"type": "boolean", "default": False, "description": "协调者强制收尾"},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["task_id", "agent"], mutating=True,
         defaults={"result": "", "force": False}),
    tool("board_review", "提交审核：任务进入待审核态，其他 agent 可验收(done)或打回(claim)。",
         {"task_id": {"type": "string"}, "force": {"type": "boolean", "default": False},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["task_id", "agent"], mutating=True, defaults={"force": False}),
    tool("board_release", "放弃任务并放回待办池（供别人接手）。",
         {"task_id": {"type": "string"}, "reason": {"type": "string"},
          "force": {"type": "boolean", "default": False},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["task_id", "agent"], mutating=True, defaults={"reason": "", "force": False}),
    tool("board_cancel", "取消任务（重复/作废时用）。",
         {"task_id": {"type": "string"}, "reason": {"type": "string"},
          "force": {"type": "boolean", "default": False},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["task_id", "agent"], mutating=True, defaults={"reason": "", "force": False}),
    tool("board_comment", "在任务上留言：交接进度、给其他 agent 提要求；同时续约认领锁。",
         {"task_id": {"type": "string"}, "text": {"type": "string"},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["task_id", "text", "agent"], mutating=True),
    tool("board_touch", "续约认领锁：长任务定期调用，防止 30 分钟无活动被别人接管。",
         {"task_id": {"type": "string"}, "force": {"type": "boolean", "default": False},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["task_id", "agent"], mutating=True, defaults={"force": False}),
    tool("board_steal", "接管已超时（默认 30 分钟无活动）的认领；接管前先 board_show 读留言。",
         {"task_id": {"type": "string"},
          "root": {"type": "string", "description": ROOT_DESC},
          "agent": {"type": "string", "description": IDENTITY_DESC}},
         required=["task_id", "agent"], mutating=True),
]

TOOL_MAP = {t["name"]: t for t in TOOLS}


class ToolError(Exception):
    pass


def board_root_available():
    d = os.path.realpath(os.getcwd())
    while True:
        if os.path.isdir(os.path.join(d, boardmod.BOARD_DIR)) or os.path.isdir(os.path.join(d, ".git")):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def dispatch(name, args):
    t = TOOL_MAP.get(name)
    if t is None:
        raise ToolError("未知工具：%s" % name)
    params = dict(args or {})
    root = params.pop("root", None)
    if root:
        root = os.path.expanduser(str(root))
        if not os.path.isdir(root):
            raise ToolError("root 目录不存在：%s" % root)
        os.chdir(root)
    agent = str(params.pop("agent", "") or os.environ.get("AGENT_BOARD_AGENT") or "")
    if params.pop("agent_required", False) and not agent:
        raise ToolError("缺少 agent 参数")
    if t["_mutating"] and not agent:
        raise ToolError("缺少 agent 参数：请传一个本次对话内稳定的身份名（如 main / worker-1）")
    if not t["_mutating"] and not board_root_available():
        return "未找到看板：当前工作目录 %s 上方没有 .agent-board，也不在任何 git 仓库内。" \
               "请传 root 参数指定项目根，或先由任一 agent 创建任务。" % os.getcwd()
    merged = dict(t["_defaults"])
    merged.update(params)
    merged["agent"] = agent or "unknown"
    merged["json"] = True  # list/show/feed/add/claim 输出 JSON，便于结构化解析
    ns = Namespace(**merged)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            b = boardmod.Board()
            getattr(b, t["name"].replace("board_", "cmd_"))(ns)
    except SystemExit:
        raise ToolError(err.getvalue().strip() or out.getvalue().strip() or "操作失败")
    text = out.getvalue().strip()
    return text if text else "(操作成功，无输出)"


def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(mid, result):
    send({"jsonrpc": "2.0", "id": mid, "result": result})


def reply_error(mid, code, message):
    send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def strip_internal(tools):
    clean = []
    for t in tools:
        t = dict(t)
        t.pop("_mutating", None)
        t.pop("_defaults", None)
        clean.append(t)
    return clean


def main():
    if os.environ.get("AGENT_BOARD_ROOT"):
        os.chdir(os.path.expanduser(os.environ["AGENT_BOARD_ROOT"]))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        mid = msg.get("id")
        if method.startswith("notifications/"):
            continue  # 通知不回复
        try:
            if method == "initialize":
                client_ver = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
                reply(mid, {
                    "protocolVersion": client_ver,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                })
            elif method == "tools/list":
                reply(mid, {"tools": strip_internal(TOOLS)})
            elif method == "resources/list":
                reply(mid, {"resources": []})
            elif method == "prompts/list":
                reply(mid, {"prompts": []})
            elif method == "ping":
                reply(mid, {})
            elif method == "tools/call":
                p = msg.get("params") or {}
                try:
                    text = dispatch(p.get("name", ""), p.get("arguments") or {})
                    reply(mid, {"content": [{"type": "text", "text": text}]})
                except ToolError as e:
                    reply(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
                except Exception as e:  # noqa: BLE001
                    reply(mid, {"content": [{"type": "text", "text": "服务器内部错误：%r" % e}], "isError": True})
            else:
                reply_error(mid, -32601, "未知方法：%s" % method)
        except Exception as e:  # noqa: BLE001  兜底：绝不让 server 崩掉
            if mid is not None:
                reply_error(mid, -32603, "内部错误：%r" % e)


if __name__ == "__main__":
    main()
