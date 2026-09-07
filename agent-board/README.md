# Agent Board — 让你的多个 AI 对话互相看见、协作干活

> 一个零依赖的任务看板插件。同一项目里开的多个 AI 对话（以及一个对话里派出的多个并行 worker）通过它**接任务、交任务、留言交接**；你作为老板随时发任务、指正问题。无需启动任何服务，复制即用。

适用于 ZCode（也兼容 Claude Code 等支持 skills/commands/MCP 的 CLI agent 工具）。

---

## 它解决什么问题？

你同时开着 3 个对话开发一个项目：A 在改代码、B 在跑调试、C 在写文档。它们**互相看不见对方**——重复改同一个文件、没人接你的活、一个对话关了它手头的进度就丢了。

Agent Board 在你的项目根目录放一块共享看板（`.agent-board/` 文件夹），所有对话读写同一块板：

```
对话A ──┐
对话B ──┼──▶  .agent-board/ 看板  ◀── 你（boss，最高优先级）
对话C ──┘
```

- **接任务**：agent 一条命令认领待办。原子锁保证两个 agent **绝不会抢到同一个任务**
- **交任务**：完成后提交结果摘要，其他对话立刻可见
- **不丢进度**：对话关了？任务、留言、操作流水全在磁盘上，新对话读一下「交接单」就能接着干
- **老板直通车**：你说话，agent 替你落板（记录为 `boss` 身份），全体 agent 把它当最高优先级指示

---

## 安装（30 秒）

**方式一：拿到压缩包（发给朋友就用这种）**

```bash
unzip agent-board-0.2.0.zip && cd agent-board
./install.sh              # 装到用户级，所有项目可用
```

**方式二：从插件市场**

ZCode → Settings → Plugin Management → Discover → **＋** → 粘贴本仓库地址（或选择本地文件夹）→ 安装 Agent Board。

**方式三（推荐追加）：把协作规则注入当前项目**

```bash
cd 你的项目
~/.zcode/skills/agent-board/scripts/board.py whoami   # 确认装好了
# 在本项目执行一次：
bash ~/.zcode/skills/agent-board/install.sh --project
# 或者手动把 templates/AGENTS-snippet.md 的内容追加进项目根的 AGENTS.md
```

`--project` 会把「空闲必看板 / boss 身份 / 新对话入职」等协作铁律写进该项目的 `AGENTS.md`，**这个项目里的每个新对话都会自动遵守**——不加这条，agent 只在碰巧聊到协作时才会用看板。

> 装完后**重启或新开对话**生效。

---

## 装完后你有三样东西

| 东西 | 是什么 | 怎么生效 |
|---|---|---|
| **skill** | 一份给 AI 看的使用说明书 | 对话里出现「接任务/派任务/看板/协作」等场景时 agent 自动使用 |
| **`/board` 命令** | 你的看板代办员 | 输入 `/board`：汇报进度；**用自然语言说**「在板上发个任务：……」，它替你以 boss 身份落板 |
| **MCP server** | 12 个 `board_*` 工具 | agent 直接调用，毫秒级读写看板（`board_list/add/claim/done/feed/...`） |

---

## 三个典型用法

**① 你（老板）发任务 / 指正 —— 不用学任何命令**

在任意对话里直接说：

> 「在看板上发个任务：把登录页的按钮对齐，优先级高」
> 「T-0003 那个你们理解错了，应该是先修滚动再修弹窗，我在板上评论了」

对话里的 agent 会替你落板（记录为 `boss` 创建/留言）并告诉你落到了哪个任务。其他对话下次查板就会看到并回应——**boss 的内容全体 agent 必须优先处理**。随时输 `/board` 查看全局进度。

**② 多个对话分工（跨对话协作）**

```
对话A（协调者）: board add "修复登录bug" -p 1 --agent main
对话B: board claim --agent conv-b     ← 自动认领优先级最高的待办
对话B: ...干活... board done T-0001 -r "改了xx，已验证" --agent conv-b
对话C: board feed                     ← 看到B交了什么
```

**③ 一个对话内并行 + 长任务交接**

大任务拆子任务（`add --parent T-0001 "子任务"`），对话内并行派出多个 worker，各自用 `--agent worker-1/2/3` 认领。对话快关了？让 agent 把进度写成【交接单】留言（`comment`）；新对话按 `AGENTS.md` 的入职规则读交接单接续；没人认领的陈旧锁（默认 30 分钟无活动）可被 `steal` 接管。

---

## 命令速查（agent 也可全部走 MCP 工具）

| 命令 | 作用 | 对应 MCP 工具 |
|---|---|---|
| `list [--all/--mine/--status]` | 查看看板 | `board_list` |
| `add "标题" -d 说明 -p 0..3 --parent ID` | 发任务（0 最高优先级） | `board_add` |
| `claim [ID]` | 接任务（原子锁，自动挑最高优先级） | `board_claim` |
| `done ID -r "结果摘要"` | 交任务 | `board_done` |
| `review ID` / `release ID` / `cancel ID` | 提审 / 放回待办池 / 取消 | `board_review/release/cancel` |
| `comment ID "留言"` | 交接/汇报（同时续约锁） | `board_comment` |
| `feed -n 20` | 所有人最近干了什么 | `board_feed` |
| `show ID` | 任务详情（留言/持锁人） | `board_show` |
| `touch ID` / `steal ID` | 续约锁 / 接管超时认领 | `board_touch/steal` |

每次都要带 `--agent <身份名>`（同一对话内保持一致；并行 worker 各用不同名字；`boss` 保留给人类）。

---

## 工作原理（为什么它可靠）

看板就是项目根的 `.agent-board/` 文件夹：

```
.agent-board/
├── tasks/T-0001.json    任务文件（临时文件+rename 原子写入）
├── locks/T-0001.lock    认领锁（O_CREAT|O_EXCL 创建，操作系统保证唯一）
├── events.jsonl         全部操作流水（feed 的数据源）
└── config.json          可选：{"stale_seconds": 1800} 超时接管阈值
```

- **认领 = 抢锁**。POSIX `O_EXCL` 保证同一瞬间只有一个进程能创建锁文件——这就是「绝不吃重」的底气，无守护进程、无数据库、无网络依赖
- 锁文件超过 `stale_seconds`（默认 1800 秒）没活动即视为原认领者离开，可 `steal`
- 任务 ID 分配同样用 `O_EXCL`，并发发任务不撞号

`.agent-board/` 建议加进 `.gitignore`（同机协作不需要提交）；想跨机器协作就把它提交进 git，靠 git 合并自然同步。

---

## 常见问题

**Q：两个对话同时抢一个任务怎么办？**
只会成功一个，另一个收到明确报错提示换任务或稍后重试。

**Q：一个对话中途关了，它的任务怎么办？**
锁 30 分钟无活动后其他人可 `steal`；急的话接管方也可走 `show` 读留言后协调强制收尾。

**Q：agent 不主动用看板？**
对当前项目执行一次 `install.sh --project`（或把 `templates/AGENTS-snippet.md` 内容加进项目根 `AGENTS.md`）——规则里写明了「空闲必看板」是铁律，新对话自动加载。

**Q：我想看板但不想打命令？**
输入 `/board`，或直接用自然语言让任意对话里的 agent「看看任务看板」。

**Q：支持哪些工具？**
任何支持 skills / 自定义命令 / MCP 的 CLI agent 工具（ZCode、Claude Code 等）。即使三者都不可用，`board.py` 本身是零依赖 Python 脚本，agent 用 Bash 也能操作。

---

## 卸载

```bash
rm -rf ~/.zcode/skills/agent-board ~/.zcode/commands/board.md
# 再从 ~/.zcode/cli/config.json 的 mcp.servers 里删掉 "agent-board" 条目
# 项目内如注入过规则，删除 AGENTS.md 中 agent-board 标注段落即可
```

## 版本

见 [CHANGELOG](../CHANGELOG.md)。本插件 MIT 许可证发布（[LICENSE](../LICENSE)）。
