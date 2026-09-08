# Changelog

## 0.2.4 (2026-09-09)

- **@boss 桌面通知**：看板 add/comment/done 检测 @boss 自动弹 macOS 通知（其他平台静默降级）
- **boss 收件箱**：`.agent-board/inbox_boss.md` 追加式收件箱 + `board.py inbox [--unread|--ack]` 命令
- **总览置顶**：board_overview 顶部显示 @boss 未读数与条目
- **deliver 交付命令**：`board.py deliver <文件> [--task T-xxxx]` 一条命令打开交付物（晨报等）
- **--root 参数**：跨仓操作看板显式传根，防默认根漂移
- 文件链接可点击化等体验修正
- 由 plugin-dev 开发、main 版本化发布

## 0.2.3 (2026-09-07)

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
