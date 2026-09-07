---
name: agent-board
description: 跨对话/多 agent 任务看板（agent task board / kanban）。当需要在同一项目的多个对话（会话）之间分配任务、认领任务、提交任务、交接进度，或在单个对话内把大任务拆分给多个并行 worker 时使用；用户提到看板、任务板、接任务、派任务、多会话协作、multi-agent coordination、claim/submit tasks 时也应使用。File-based multi-agent task board that lets agents in different conversations of the same project claim and submit tasks, with atomic locking and an activity feed.
---

# Agent Board：跨对话任务看板

看板数据在项目根的 `.agent-board/` 目录，同一项目的所有对话（以及对话内并行的 worker）共享它。

**两种访问方式（若本会话已连接 agent-board 的 MCP server，优先用 MCP）：**

1. **MCP 工具**（推荐，结构化、免拼命令行）：工具名为 `board_list` / `board_add` / `board_claim` / `board_done` / `board_feed` 等（客户端里前缀通常是 `mcp__agent-board__`）。身份通过每个工具的 `agent` 参数传，同一对话内保持稳定。
2. **CLI 脚本**（永远可用，MCP 未连接时用这个）：

```bash
python3 scripts/board.py <命令> …
```

> `scripts/board.py` 相对本 SKILL.md 所在目录；零第三方依赖，直接用 python3 运行。

## 第一步：确定身份

每次对话开始做看板操作前，先确定一个**全程稳定的身份名**并在所有命令中传 `--agent`（或设置环境变量 `AGENT_BOARD_AGENT`）：

- 普通对话：`--agent <角色或主题>`，如 `--agent main-refactor`
- 并行 worker：`--agent worker-1`、`--agent worker-2` …

> **`boss` 身份保留给用户本人**：看板上 boss 创建的任务/留言 = 用户直接指示，最高优先级处理并回应；用户用自然语言或 `/board` 提出时，由当前对话的 agent 代为落板（agent 不得冒用 boss 身份）。

同一对话内身份必须一致，否则无法认领/完成自己的任务。可先运行 `python3 scripts/board.py whoami` 确认。

## 常用命令

```bash
python3 scripts/board_overview.py --todo           # 人类全局视图（总进度/在飞/逐单留言）
python3 scripts/board.py list                     # 查看看板（默认隐藏已完成）
python3 scripts/board.py list --all --json        # 全量 JSON（适合程序解析）
python3 scripts/board.py add "任务标题" -d "详细说明" -p 1   # 创建任务（优先级 0 最高 / 2 默认）
python3 scripts/board.py claim                    # 接任务：自动认领优先级最高的待办
python3 scripts/board.py claim T-0003             # 接指定任务（已被抢会明确报错）
python3 scripts/board.py done T-0003 -r "结果摘要"  # 提交任务（其他对话能看到 result）
python3 scripts/board.py comment T-0003 "留言"     # 交接/留言给其他 agent，同时续约锁
python3 scripts/board.py feed -n 20               # 看其他 agent 最近干了什么
python3 scripts/board.py show T-0003              # 任务详情（含留言、持锁人）
python3 scripts/board.py release T-0003           # 放弃任务放回待办池
python3 scripts/board.py touch T-0003             # 长任务续约锁（默认 30 分钟无活动可被接管）
python3 scripts/board.py steal T-0003             # 接管已超时的认领
python3 scripts/board.py cancel T-0003 --reason "重复"  # 取消任务
```

## 工作流

**接任务（一个对话的标准循环）**
0. **先上板再动手**：接到新任务/新问题（含用户口头反馈）→ 先 `add` 上板 → 再 `claim` 认领 → 才动手。顺序不可颠倒，否则其他 agent 看板时不知道问题存在、也不知道已有人在修
1. `list` 看板 → `claim`（自动挑优先级最高的待办，原子锁保证不会被别的对话抢走同一单）
2. 干活；任务耗时较长时偶尔 `touch` 续约，进度/发现写 `comment`（其他对话可见）
3. 完成后 `done T-xxxx -r "结果摘要"` —— 摘要要写清楚改了什么、怎么验证的，方便其他对话接手

**派任务（协调者视角）**
1. 把目标拆成若干独立任务 `add`，尽量让每个任务不与别人改同一批文件；有依赖的用 `--parent` 挂在总任务下
2. 其他对话会自动认领；你用 `list` / `feed` 跟踪，`comment` 补充要求，验收后让执行者 `done` 或你 `--force` 收尾

**共享资源租约（编辑器/设备/游戏实例等独占资源）**
为独占资源建一个常驻任务（如「🎮 编辑器独占权」）：用前 `claim` 即持有（原子锁保证同一时刻只有一个 agent 持有），用完 `release` 归还（永不 done，任务循环使用）；他人 claim 失败即知被占用，排队等待，禁止绕过租约直接使用。需要该资源做实测的请求，comment 到配套的「实测请求队列」常驻任务（写清场景/步骤/验收标准/@操作员），由当值操作员串行执行并回报——避免多方抢资源、反复重启。

**单个对话内并行**
把大任务拆成子任务放上板，然后并行派出多个 worker（子 agent），每个 worker 被告知：
- 用自己的名字（`--agent worker-N`）执行 `python3 scripts/board.py claim`，处理拿到的任务，`done -r` 提交
- 这样并行 worker 之间、以及与其他对话之间共用同一套防抢占机制

**交接与接管**
- 对话可能随时被关闭：把上下文写进 `comment`（做了什么/剩什么/坑在哪）
- 看到别人的任务 `touch`/活动超时（默认 1800 秒）才可 `steal`；接管前先 `show` 读留言了解进度

## 注意事项

- **空闲必看板（协同铁律）**：等待用户、等待外部结果、或任务空档时，先 `feed`/`list` 检查其他 agent 的新请求并逐条回应——看板是跨对话唯一通道，已读不回会让对端阻塞
- 认领是原子的：两个 agent 同时 `claim` 同一单，只有一个成功，失败方会收到明确报错，换一单或稍后重试即可
- 只有锁的持有者能 `done`/`release`；`review` 后任务进入待审核态，任何 agent 可 `done` 验收或 `claim` 返工
- 不要直接编辑 `.agent-board/` 里的文件，一律走命令，保证锁与状态一致
- 看板目录默认建议加入 `.gitignore`（同机协作不需要提交；若要多机同步再提交它，靠 git 合并解决冲突）
