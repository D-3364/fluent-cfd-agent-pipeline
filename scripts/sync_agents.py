"""从 `.claude/` 生成 Codex / agents.md 约定的副本。

## 为什么用脚本，而不是手工拷贝

手工副本**必然漂移**，而且已经漂过一次 —— 有人在 `.agents/` 里做了一份拷贝，
一天之内就出现三种不一致：

  1. `solver-api-26.1.md` 少了 `.claude/` 这边后来新增的一节
  2. 查找替换只做了一半：两个 reference 原样照抄、压根没替换
  3. 替换生成了一条**死路径** `.Codex/skills/` —— Windows 大小写不敏感能糊过去，
     GitHub（Linux）上不存在

**副本由脚本生成就不会漂**：源只有一份（`.claude/`），其余是产物。

## 生成什么

| 产物 | 来源 |
|---|---|
| `.agents/skills/fluent-cfd-pipeline/**` | `.claude/skills/fluent-cfd-pipeline/**` |
| `.codex/agents/*.toml` | `.claude/agents/*.md` |

**不生成** `.codex/config.toml` —— 那里有本机绝对路径，和 `.mcp.json` 同性质，
由 `setup.py` 按本机生成（见 .gitignore）。

## 跑法

    .venv/Scripts/python.exe scripts/sync_agents.py           # 生成
    .venv/Scripts/python.exe scripts/sync_agents.py --check   # 只查是否同步（退出码 1 = 不同步）
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_SKILL = ROOT / ".claude" / "skills" / "fluent-cfd-pipeline"
AGENTS_SKILL = ROOT / ".agents" / "skills" / "fluent-cfd-pipeline"
CLAUDE_AGENTS = ROOT / ".claude" / "agents"
CODEX_AGENTS = ROOT / ".codex" / "agents"

# ── 替换表。只做实测枚举过的两处，不凭猜扩大 ──
# 注意 `.claude/skills/` → `.agents/skills/`：这是**修正**，不是照抄原副本的
# `.Codex/skills/`（那条在大小写敏感的文件系统上是死路径）。
SUBS: list[tuple[str, str]] = [
    ("Claude Code", "Codex"),
    (".claude/skills/", ".agents/skills/"),
]

GENERATED_MD = "<!-- 由 scripts/sync_agents.py 从 .claude/ 生成，不要手工改。改请改源文件。 -->\n"
GENERATED_TOML = "# 由 scripts/sync_agents.py 从 .claude/agents/ 生成，不要手工改。改请改源文件。\n"


def subst(text: str) -> str:
    for a, b in SUBS:
        text = text.replace(a, b)
    return text


def render_skill() -> dict[Path, str]:
    """skill 目录：逐文件替换。"""
    out: dict[Path, str] = {}
    for src in sorted(CLAUDE_SKILL.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(CLAUDE_SKILL)
        body = subst(src.read_text(encoding="utf-8"))
        if rel.suffix == ".md":
            # 首行 frontmatter 之前插生成标记
            if body.startswith("---"):
                parts = body.split("---", 2)
                if len(parts) == 3:
                    body = f"---{parts[1]}---\n{GENERATED_MD}{parts[2].lstrip()}"
            else:
                body = GENERATED_MD + body
        out[rel] = body
    return out


def _toml_str(s: str) -> str:
    """TOML 基本字符串：转义反斜杠与引号。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_agents() -> dict[Path, str]:
    """agent 定义：frontmatter → TOML 字段，正文 → developer_instructions。

    `tools` 字段**被丢掉** —— Codex 那份副本里没有这个字段（实测）。
    """
    out: dict[Path, str] = {}
    for src in sorted(CLAUDE_AGENTS.glob("*.md")):
        text = src.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        if not m:
            continue
        front, body = m.group(1), m.group(2)

        def field(key: str) -> str:
            mm = re.search(rf"^{key}:\s*(.+)$", front, re.M)
            return mm.group(1).strip() if mm else ""

        name = field("name") or src.stem
        desc = field("description")
        body = subst(body)

        # ★ 用【字面量】多行字符串 '''，不是基本字符串 """
        # 正文含 Windows 路径与正则里的裸反斜杠（如 `C:\...`、`\d`），
        # 而 TOML 基本字符串会处理转义 —— 用 """ 会直接报
        # "Unescaped '\' in a string"。
        if "'''" in body:
            raise SystemExit(
                f"✗ {src.name} 的正文含 `'''`，会破坏 TOML 字面量字符串。\n"
                f"  需要改成转义写法，或换一种引号方案。"
            )
        toml = (
            GENERATED_TOML
            + f"name = {_toml_str(name)}\n"
            + f"description = {_toml_str(desc)}\n"
            + f"developer_instructions = '''\n{body}'''\n"
        )
        out[Path(f"{name}.toml")] = toml
    return out


def plan() -> list[tuple[Path, str]]:
    """返回 [(目标路径, 期望内容)]。"""
    items: list[tuple[Path, str]] = []
    for rel, body in render_skill().items():
        items.append((AGENTS_SKILL / rel, body))
    for rel, body in render_agents().items():
        items.append((CODEX_AGENTS / rel, body))
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="从 .claude/ 生成 Codex 副本")
    ap.add_argument("--check", action="store_true",
                    help="只查是否同步，不写文件（退出码 1 = 不同步）")
    args = ap.parse_args()

    items = plan()
    stale: list[Path] = []
    for dst, want in items:
        have = dst.read_text(encoding="utf-8") if dst.exists() else None
        if have != want:
            stale.append(dst)

    if args.check:
        if stale:
            print(f"✗ {len(stale)} 个文件与 .claude/ 不同步：")
            for p in stale:
                print(f"    {p.relative_to(ROOT)}")
            print("\n  跑一次 scripts/sync_agents.py 修正。")
            return 1
        print(f"✓ {len(items)} 个生成文件全部同步")
        return 0

    # 生成：先清掉目标目录里多余的旧文件
    for base in (AGENTS_SKILL, CODEX_AGENTS):
        if base.exists():
            shutil.rmtree(base)
    written = 0
    for dst, want in items:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(want, encoding="utf-8")
        written += 1

    print(f"✓ 生成 {written} 个文件")
    print(f"    .agents/skills/fluent-cfd-pipeline/  （{len([1 for d, _ in items if AGENTS_SKILL in d.parents])} 个）")
    print(f"    .codex/agents/                        （{len([1 for d, _ in items if CODEX_AGENTS in d.parents])} 个）")
    print("\n  用 --check 可以验证是否同步。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
