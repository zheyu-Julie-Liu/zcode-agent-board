# Changelog

## 0.3.1 (2026-09-26)

- **通知默认静音**（boss 反馈：授权通知后夜间 agent 的 deliver/@boss 通知滴滴滴响个不停）：`notify_desktop`/看门狗通知不再默认携带声音，仅视觉提醒；需要声音的场合在 `.agent-board/config.json` 显式配 `"notify_sound": "Glass"` 等
- 游戏板 config 已显式置空 `notify_sound`；看门狗通知同步静音

## 0.3.0 (2026-09-26)

- **看板看门狗 board-watchdog**（boss 指示，规格 2026-09-25）：事件驱动替代定时巡逻——launchd `WatchPaths` 监视 `events.jsonl`，去抖 60 秒合并突发，偏移量记忆（重启/轮转不重响），急活（0 级任务 / @值守身份）弹 macOS 通知，普通事项零动作留给晨报
  - `skills/agent-board/scripts/watchdog.py`：纯标准库，支持 `--once` 手动一轮、`--debounce` 覆盖、`wake_command` 预留钩子、镜像卡开关、日志自动轮转
  - `install.sh --install-watchdog / --uninstall-watchdog`：一键装/卸 launchd 条目 + 配置（`~/.config/board-watchdog/`），卸载无残留
  - 兼容单板分区制：插件仓/oversea 项目的 `.agent-board` 为同一事件流的符号链接，只监视真实根一处

## 0.2.9 (2026-09-22)

- **MCP 循环调用护栏**（boss 指示：agent 误触 MCP 陷入死循环，同功能超 3 次自动禁用）：每次 MCP 调用记录到 `.agent-board/mcp_calls.jsonl`（身份/工具/参数指纹/时间）；同身份+同工具+同参数指纹在窗口内（默认 300 秒，可配 `loop_window_seconds`）达 **3 次**（`loop_threshold`）→ 自动临时禁用该工具（默认 600 秒，`loop_cooldown_seconds`，写 `tool_blocks.json`），后续调用直接拒绝并给出人话错误以打破 AI 重试循环
- 新增 `board.py toolstats` 监控命令：最近 1 小时按 身份×工具 汇总调用次数、当前临时禁用清单、最近调用明细（`--json` 供程序解析）
- 仅约束本插件的 MCP 工具调用；阈值/窗口/冷却可在 config.json 调整

## 0.2.8 (2026-09-15)

- **总览按三分区呈现**（boss 反馈：人类视角看不到分区）：「分区概览」行（游戏区/插件区/运营区 各自未完结数）+ 未完结任务（进行中/待审核/待办）按区分节（`── 游戏区` 等）；分区判定 = 标题前缀（[插件]/[运营]/无前缀=游戏区）
- `/board` 汇报规矩升级：必须三分区结构化汇报，不得只报本对话负责的分区

## 0.2.7 (2026-09-14)

- **board_overview.py 分区过滤 `--zone`**（单板分区制配套，T-0037）：`--zone plugin` 只看 `[插件]` 前缀任务、`--zone game` 排除之、默认全量；标题栏注明过滤状态；@提及收件箱保持跨区可见不受过滤影响

## 0.2.6 (2026-09-12)

- **违规冻结机制**（boss 指示：违反看板规则的 agent 直接冻结、无法认领任务，系统级强制不靠自觉）：`board block <身份> --reason` / `board unblock <身份>`（MCP：`board_block` / `board_unblock`；仅 boss 身份可操作，boss 不可被冻结）；被冻结者 `claim` 任何任务（含自动选单）都会被拒绝并看到原因；冻结名单存 `.agent-board/blocked.json`，`whoami` 显示自身冻结状态
- 场景：推送/发布类违规（如未核产物红线就上传）由 boss 冻结当事人，锁死认领资格直到 boss 解冻
- 修复 `check-files` 的门禁提示示例；SKILL 注意事项补充冻结说明

## 0.2.5 (2026-09-12)

- **硬约束门禁 `check-files`**（boss 指示：红线必须自动生效，不能依赖记忆遵守）：`config.json` 配置 `"gates": [{"pattern": "*.pck", "max_mb": 25}, …]`（pattern 相对被检查目录，支持 `**` 递归），`board.py check-files <目录>` / MCP `board_check_files` 逐文件对照大小，超限退出码 1 并列明细——导出/构建/备料/上传前必跑，把超限产物挡在推送之前
- SKILL 新增「硬约束门禁」「工作流不绑定个人」两节：同模型 agent 可凭 SOP 留痕直接接手流水线工作（push 类唯一发布执行人制不变）
- 修复子命令名带连字符时 `cmd_` 方法映射失败的问题

## 0.2.4 (2026-09-09)

送达人类（真实使用反馈：boss 收不到 @boss、看不到晨报、看板上找不到文件）：

- **@提及收件箱**：任务/留言/结果/原因里 `@名字` 自动写入 `.agent-board/inbox/<名字>.jsonl`；新增 `inbox`（`--all` / `--ack`）命令与 `board_inbox` MCP 工具，任何身份可查「谁 @ 了我」
- **桌面通知**：通知名单（`config.json` `notify_mentions`，默认 `["boss"]`）内的提及弹 macOS 桌面通知（系统自带 osascript，零依赖；非 macOS 静默跳过；`AGENT_BOARD_NOTIFY=0` / `"notify": false` 可关；作者 @ 自己不算）
- **deliver 交付打开**：`deliver <文件> [--task] [--to] [--note] [--app]` / `board_deliver`——在桌面应用（默认 ZCode，可配 `AGENT_BOARD_OPEN_APP` / `open_app`）里打开文件给人看，写收件箱、弹通知、关联任务留言记录送达
- **文件可点击**：`show` 与 `board_overview.py` 自动把文本里真实存在的文件（绝对/`~/`/相对项目根/项目根裸文件名）渲染为 `📎 [文件名](绝对路径)` markdown 链接
- **board_overview.py**：顶部置顶「📬 @boss 未读提及」（`--me` 换人）；标题改用 `config.json` `project_name` 或看板目录名（去掉硬编码项目名）；时间差改为正确的 UTC 解析
- **看板定位**：CLI 新增 `--root`（子命令前后皆可）与 `AGENT_BOARD_ROOT` 环境变量（与 MCP 一致），多项目共用一块板不再依赖工作目录；`whoami --json` 输出根目录/通知设置/版本
- board.py / board_mcp.py 版本号对齐 0.2.4；SKILL / README / commands/board.md / templates/AGENTS-snippet.md 同步

## 0.2.3 (2026-09-07)

- 新增共享资源租约协议：独占资源（编辑器/游戏实例/设备）建常驻任务，claim=持有、release=归还，杜绝多方抢占
- 配套「实测请求队列」模式：实测需求汇总给当值操作员串行执行，请求者不自行占用资源
- templates/AGENTS-snippet.md 同步该协议

## 0.2.2 (2026-09-07)

## 0.2.2 (2026-09-07)

- SKILL 工作流新增铁律：接到任务先 `add` 上板、再 `claim` 认领、后动手（先上板再动手，防其他 agent 重复劳动/漏修）

## 0.2.1 (2026-09-07)

## 0.2.1 (2026-09-07)

- 新增 `board_overview.py`：人类专用看板全局视图（总进度/在飞 agent/进行中/待办逐单最新留言；`--todo` 只看未完结），路径可移植（插件形态与仓内 tools/ 形态均可运行）
- `/board` 命令与 SKILL 更新：人类查板固定口径优先走 board_overview

## 0.2.0 (2026-09-05)

## 0.2.0 (2026-09-05)

- `/board` 升级为老板代办员：自然语言发任务/评论指正，以 `boss` 身份落板，全体 agent 最高优先级处理
- 新增 `install.sh --project`：把「空闲必看板 / boss 身份 / 新对话入职」协作铁律注入项目 AGENTS.md（幂等）
- 新增 templates/AGENTS-snippet.md 可移植协作规则模板
- SKILL.md 补充 boss 身份、空闲查板铁律、交接单机制说明
- README 全文重写为面向普通用户的安装/使用手册

## 0.1.0 (2026-09-05)

## 0.1.0 (2026-09-05)

首个发布版本。

- 文件型任务看板：任务 JSON + `O_EXCL` 原子认领锁 + 操作流水（events.jsonl），零第三方依赖
- 跨对话协同：同一项目的多个 ZCode 对话共享看板，接任务/提交任务/留言交接/超时接管（steal）/续约（touch）
- 单对话并行：大任务拆子任务（`--parent`），多个 worker 各自身份认领，同一套防抢占
- MCP server（stdio，零依赖）：`board_list/add/claim/done/review/release/cancel/comment/touch/steal/show/feed` 共 12 个工具
- `/board` 命令：人类一条命令查板
- 自动触发 skill：多对话协作/接任务/派任务场景自动引导 agent 使用
- 安装：`install.sh` 一键装到用户级（skill + 命令 + MCP 注册），或作为 marketplace 安装
