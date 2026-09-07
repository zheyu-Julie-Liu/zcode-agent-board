# Agent Board — ZCode 多 Agent 任务看板插件

> **这是一个 ZCode 插件（a ZCode plugin）**，为 [ZCode](https://z.ai) CLI 的 AI 编程对话设计；也兼容支持 skills / 自定义命令 / MCP 的同类工具（如 Claude Code）。
>
> 让**同一项目的多个 AI 对话**（以及单个对话内的多个并行 worker）共享一块任务看板：接任务、提交任务、留言交接，全部通过**文件 + POSIX 原子锁**实现，零依赖、无需任何服务。

[![version](https://img.shields.io/badge/version-0.2.3-green)](CHANGELOG.md) [![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

## 它解决什么问题

你开了 3 个对话同时开发一个项目：A 在改代码、B 在跑调试、C 在写文档——它们互相看不见对方在做什么。Agent Board 在项目根放一块共享看板（`.agent-board/`），任何对话都能：

- **接任务**：`claim` 自动认领优先级最高的待办，原子锁保证两个 agent 绝不会抢到同一单
- **提交任务**：`done -r "结果摘要"`，其他对话立刻能看到结果
- **互通消息**：`comment` 留言交接、`feed` 看所有人最近做了什么
- **容错接管**：认领者掉线超时（默认 30 分钟）后其他 agent 可 `steal` 接管；长任务 `touch` 续约
- **单对话并行**：把大任务 `add --parent` 拆成子任务，并行 worker 各用自己的名字去 `claim`

## 安装

> **国内镜像（Gitee）**：https://gitee.com/zheyu-julie-liu/zcode-agent-board （与 GitHub 同步，国内直连免代理）

### 方式一：插件市场（推荐）

1. 把本仓库推到你的 GitHub（见文末「发布指南」），或直接使用本地目录
2. ZCode → **Settings → Plugin Management → Discover → ＋** → 粘贴
   `https://github.com/zheyu-Julie-Liu/zcode-agent-board`
3. 安装 `Agent Board`，重启/新开对话即生效

### 方式二：脚本快捷安装（不需要市场）

```bash
cd agent-board        # 本仓库内的插件目录
./install.sh          # 装到 ~/.zcode（skill + /board 命令 + MCP 注册），全项目可用
```

## 用法

安装后**无需记忆命令**：skill 会在「多对话协作 / 接任务 / 派任务 / 看板」等场景自动触发，引导 agent 使用；MCP server 提供 12 个 `board_*` 工具；人类随时可用 `/board` 查板。

```bash
# agent 视角（也可全部走 MCP 工具）
python3 <插件目录>/skills/agent-board/scripts/board.py list          # 看板
python3 …/board.py add "修复寻路抖动" -d "…" -p 1 --agent main       # 派任务
python3 …/board.py claim --agent conv-b                             # 接任务（自动挑单）
python3 …/board.py done T-0001 -r "改动+验证方式" --agent conv-b     # 提交
python3 …/board.py feed                                             # 谁在做什么
```

> 详细工作流（跨对话 / 单对话并行 / 交接接管）见 [agent-board/README.md](agent-board/README.md) 与 `skills/agent-board/SKILL.md`。

## 工作原理

- 看板数据在**项目根** `.agent-board/`：`tasks/*.json`（原子写入）、`locks/*.lock`（`O_CREAT|O_EXCL` 抢锁）、`events.jsonl`（流水）、`config.json`（可选，`stale_seconds`）
- 认领 = 抢锁，POSIX 语义保证同瞬间只有一个进程成功；任务 ID 分配同样用 `O_EXCL`，并发 `add` 不撞号
- 锁 mtime 超过 `stale_seconds`（默认 1800s）视为原认领者离开，可 `steal`
- 同机协作无需提交 `.agent-board/`；若要多机同步，把它提交进 git 即可升级为 GNAP 式协作

## 发布指南（维护者）

```bash
# 1. 把本目录初始化为仓库并推送（替换成你的用户名）
cd agent-board-market
git init && git add -A && git commit -m "agent-board 0.1.0"
git remote add origin https://github.com/zheyu-Julie-Liu/zcode-agent-board.git
git push -u origin main

> 可选：在 Gitee 导入同地址建立国内镜像，发版后点一次「强制同步」即可刷新

# 2. 其他人（或你自己）在 ZCode 里添加市场
#    Settings → Plugin Management → Discover → ＋ → 粘贴
#    https://github.com/zheyu-Julie-Liu/zcode-agent-board
```

市场清单在 `marketplace.json`（`.zcode-plugin/` 与 `.claude-plugin/` 内有兼容副本），插件清单在 `agent-board/.zcode-plugin/plugin.json`。改完插件后把 `version` 两处同步递增并更新 `CHANGELOG.md`。

## 目录结构

```
agent-board-market/            ← 整个目录就是一个 marketplace 仓库
├── marketplace.json           ← 市场清单（.zcode-plugin/.claude-plugin 内有兼容副本）
├── LICENSE / CHANGELOG.md / README.md
└── agent-board/               ← 插件本体
    ├── .zcode-plugin/plugin.json
    ├── skills/agent-board/    ← SKILL.md + scripts/board.py + scripts/board_mcp.py
    ├── commands/board.md      ← /board 命令
    ├── assets/icon.svg
    ├── install.sh
    └── README.md
```

## License

[MIT](LICENSE)
