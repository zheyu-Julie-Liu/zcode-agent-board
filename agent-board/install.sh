#!/usr/bin/env bash
# agent-board 安装脚本
#
# 用法：
#   ./install.sh              # 装到用户级：所有项目、所有对话可用（skill + /board + MCP）
#   ./install.sh --project    # 额外把协作规则注入当前项目的 AGENTS.md（幂等，可重复执行）
#
# 卸载：见 README「卸载」一节。
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$HOME/.zcode/skills"
COMMANDS_DIR="$HOME/.zcode/commands"

mkdir -p "$SKILLS_DIR" "$COMMANDS_DIR"

# 1) skill + 命令
rm -rf "$SKILLS_DIR/agent-board"
cp -R "$SRC_DIR/skills/agent-board" "$SKILLS_DIR/agent-board"
chmod +x "$SKILLS_DIR/agent-board/scripts/board.py" \
         "$SKILLS_DIR/agent-board/scripts/board_mcp.py" 2>/dev/null || true
cp "$SRC_DIR/commands/board.md" "$COMMANDS_DIR/board.md"
echo "✔ skill 已安装到  $SKILLS_DIR/agent-board"
echo "✔ /board 命令已装到 $COMMANDS_DIR/board.md"

# 2) MCP server 注册到用户级配置（保留已有内容）
python3 - "$SKILLS_DIR/agent-board/scripts/board_mcp.py" <<'PYEOF'
import json, os, sys
mcp_path = sys.argv[1]
cfg_path = os.path.expanduser("~/.zcode/cli/config.json")
cfg = {}
if os.path.exists(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
cfg.setdefault("mcp", {}).setdefault("servers", {})["agent-board"] = {
    "command": "python3",
    "args": [mcp_path],
}
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print("✔ MCP server 已注册到", cfg_path)
PYEOF

# 3) 可选：向当前项目注入协作规则（--project）
if [ "${1:-}" = "--project" ] || [ "${2:-}" = "--project" ]; then
  python3 - "$SRC_DIR/templates/AGENTS-snippet.md" <<'PYEOF'
import os, sys
tpl_path, = sys.argv[1:]
target = os.path.abspath("AGENTS.md")
with open(tpl_path, encoding="utf-8") as f:
    snippet = f.read()
existing = ""
if os.path.exists(target):
    with open(target, encoding="utf-8") as f:
        existing = f.read()
if "agent-board 协作规则" in existing:
    print("ℹ 当前项目 AGENTS.md 已包含 agent-board 规则，跳过注入")
else:
    with open(target, "a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("\n" + snippet)
    print("✔ 协作规则已注入", target)
PYEOF
fi

echo ""
echo "完成！重启 / 新开对话后生效。验证：新对话里输入 /board 或说「看看任务看板」。"
