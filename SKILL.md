---
name: agent-board
description: 跨对话/多 agent 任务看板的安装与更新技能（agent task board installer/updater）。用户下载本文件并交给 AI 后，AI 应自行安装本插件：从 Gitee/GitHub 仓库拉取安装包，完成安装、验证，并学会使用与持续更新。当用户提到安装看板、更新看板插件、agent-board、任务板、多对话协作、拉取新版本时也应触发本技能。适用于 Codex / ZCode / 其他支持 skills 或 MCP 的 AI 客户端。
---

# Agent Board 安装/更新技能（自包含 · 小红书分发版）

> **给 AI 的元指令**：用户把本文件交给你（通常下载自小红书）。你的任务是：①把本文件放到正确的 skills 目录（见第 0 步，这样本技能常驻、以后可随时触发更新）；②按第 1 步从仓库拉取并安装插件；③按第 2 步验证；④按第 3 步学会使用；⑤以后用户说「更新看板插件」时按第 4 步拉取最新版。全程无需人工操作；失败时按第 6 步换源重试，仍失败再向用户报告卡点。

## 第 0 步：把本文件放对位置（让技能常驻）

你（AI）刚收到本文件——把它复制为对应客户端的技能文件（`<下载位置>` 换成它现在的实际路径）：

```bash
# Codex：
mkdir -p ~/.codex/skills/agent-board && cp <下载位置>/SKILL.md ~/.codex/skills/agent-board/SKILL.md
# ZCode：
mkdir -p ~/.zcode/skills/agent-board && cp <下载位置>/SKILL.md ~/.zcode/skills/agent-board/SKILL.md
```

放好后，本技能即常驻：用户随时说「安装/更新看板插件」，你就按下面步骤执行。

## 第 1 步：安装插件（从仓库拉取安装包）

前置检查：`python3 --version` ≥ 3.9；有 `bash`；`git` 可选（没有走路线 D）。

```bash
# 路线 A（推荐，Gitee 国内直连免代理）：
bash <(curl -fsSL https://gitee.com/zheyu-julie-liu/zcode-agent-board/raw/main/agent-board/install.sh)

# 路线 B（GitHub，海外/有代理时）：
bash <(curl -fsSL https://raw.githubusercontent.com/zheyu-Julie-Liu/zcode-agent-board/main/agent-board/install.sh)

# 路线 C（想先审计代码）：
git clone https://gitee.com/zheyu-julie-liu/zcode-agent-board.git && bash zcode-agent-board/agent-board/install.sh

# 路线 D（无 git）：
curl -L -o /tmp/agent-board.zip https://gitee.com/zheyu-julie-liu/zcode-agent-board/repository/archive/main.zip
unzip -q /tmp/agent-board.zip -d /tmp/ab && bash /tmp/ab/*/agent-board/install.sh
```

**安装脚本做了什么**（透明可审计，可先 `curl … | less` 审一遍再装）：①`skills/agent-board/`（CLI 脚本+使用说明）→ `~/.zcode/skills/agent-board/`；②`commands/board.md` → `~/.zcode/commands/`（人类 `/board` 查板）；③注册 MCP server 到 `~/.zcode/cli/config.json`（stdio：`python3 ~/.zcode/skills/agent-board/scripts/board_mcp.py`）；④`--project` 参数可额外把协作规则注入当前项目 AGENTS.md（幂等）。纯 Python 标准库 3.9+，零第三方依赖，MIT。

**非 ZCode 客户端（Codex 等）**：把 `skills/agent-board/` 拷进你的 skills 目录；MCP 手动注册 stdio 命令 `python3 <skills路径>/scripts/board_mcp.py`；CLI 无需注册永远可用。

## 第 2 步：验证

```bash
python3 ~/.zcode/skills/agent-board/scripts/board.py --version        # 打印版本号即装成功
python3 ~/.zcode/skills/agent-board/scripts/board.py whoami --agent self-test   # 确认看板目录定位正常
```

重启客户端后应出现 MCP 工具：`board_list / board_add / board_claim / board_done / board_comment / board_inbox / board_deliver / board_check_files …`。CLI 不依赖重启。

## 第 3 步：使用速成

**身份**：每个对话定一个全程稳定的身份 `--agent <名字>`（如 `main`、`worker-1`）。

```bash
B=~/.zcode/skills/agent-board/scripts/board.py
python3 $B add "任务标题" -d "说明/验收标准" -p 1 --agent conv-a   # 派任务（0最高/2默认）
python3 $B claim --agent conv-b                # 接任务（原子锁，同瞬间只有一人成功）
python3 $B comment T-0001 "进度/发现" --agent conv-b                 # 交接留言（顺带续约锁）
python3 $B done T-0001 -r "改了什么+怎么验证" --agent conv-b          # 提交
python3 $B feed -n 20 --agent conv-b           # 流水：谁在做什么
python3 $B inbox --agent conv-b                # 谁 @ 了我（--ack 清零）
python3 $B deliver 报告.md --task T-0001 --agent conv-b              # 文件在桌面应用打开给人看并记录送达
```

**四条铁律**：①接到任务先 `add` 上板、再 `claim`、才动手；②空闲时 `feed`/`list`/`inbox` 巡查并回应；③交接写 `comment`、结论写 `done -r`；④给人看的文件必须 `deliver`（留言里 `@名字` 会进对方收件箱，默认名单 `boss` 还会弹桌面通知）。

**进阶**：多项目共用一块板=建符号链接或设 `AGENT_BOARD_ROOT`（不同项目用标题前缀分区，`board_overview.py --zone` 过滤）；产物红线配进 `.agent-board/config.json` 的 `gates` 后 `check-files` 强制拦截；管理员可 `block/unblock` 冻结/解冻违规 agent。详见安装后的 `~/.zcode/skills/agent-board/SKILL.md`。

## 第 4 步：更新到最新版（本技能的长期价值）

```bash
# 看线上最新版本：
curl -fsSL https://gitee.com/zheyu-julie-liu/zcode-agent-board/raw/main/marketplace.json | grep '"version"'
# 看本地已装版本：
python3 ~/.zcode/skills/agent-board/scripts/board.py --version
# 不一致 → 重跑第 1 步路线 A（幂等覆盖，即升级）；数据在项目 .agent-board/ 里不受影响
```

只要本技能文件还在你的 skills 目录，就永远可以这样拉到仓库里的新版本；仓库每次发新版，用户无需重新下载本文件。

## 第 5 步：卸载

删除 `~/.zcode/skills/agent-board/`、`~/.zcode/commands/board.md`、config.json 里 `mcp.servers["agent-board"]`（项目数据 `.agent-board/` 按需保留）。

## 第 6 步：排错

- Gitee/GitHub 超时 → 换另一个源；四个路线任选。
- `python3` < 3.9 → 先升级解释器。
- MCP 工具没出现 → 重启客户端（CLI 不受影响，可先用 CLI）。
- 桌面通知不弹 → 系统设置→通知→允许「脚本编辑器」（一次性）；或用 `inbox` 替代。
- 版本自查对不上 → 安装器装的是仓库 main 分支当前版本，以 `--version` 输出为准。

## 仓库

- Gitee（主，国内直连）：https://gitee.com/zheyu-julie-liu/zcode-agent-board
- GitHub（镜像）：https://github.com/zheyu-Julie-Liu/zcode-agent-board
- 文档：仓库内 `README.md` / `CHANGELOG.md`；License：MIT
