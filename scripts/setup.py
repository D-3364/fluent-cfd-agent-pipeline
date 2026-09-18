"""把本项目接到【本机】上 —— 换机器/换目录后跑这个。

它做四件事，全部幂等，可以随时重跑：

  1. 定位项目根（按本文件的位置推，不写死）
  2. 检测本机的 ANSYS Fluent 安装（版本 + 根目录）
  3. **重写 `.mcp.json`**，把 venv 可执行文件与 Fluent 根目录换成这台机器的真实路径
  4. 校验：venv 在不在、`ansys-fluent-mcp` 装没装、Fluent 能不能解析到可执行文件

跑法：

    .venv/Scripts/python.exe scripts/setup.py            # 检测 + 写 .mcp.json + 校验
    .venv/Scripts/python.exe scripts/setup.py --check     # 只看不改（CI/排查用）
    .venv/Scripts/python.exe scripts/setup.py --fluent-root "D:\\...\\v261\\fluent"
    .venv/Scripts/python.exe scripts/setup.py --create-venv   # 连 venv 一起建（慢）

为什么需要它：`.mcp.json` 里的 `command` 和 `PYFLUENT_FLUENT_ROOT` 都是**绝对路径**，
换一台机器就失效——而 MCP server 起不来时，症状是"工具全都不见了"，
不一定报错，很难排查。跑一次这个脚本就能定位到具体是哪一环。

**`.mcp.json` 不入版本控制**（见 `.gitignore`）—— 它是机器专属的接线，
提交进仓库既没意义又会泄漏本机目录结构。仓库里提交的是 `.mcp.json.template`，
本脚本负责从**检测结果**生成真正的 `.mcp.json`。

所以全新 clone 后的正常状态是"**没有 `.mcp.json`**"，
跑一次本脚本就有了。若拿到的是别人拷来的整目录（含旧 `.mcp.json`），
本脚本会检测到路径失效并重写。

注意：本脚本**只改 `.mcp.json`**，不动任何文档。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_env import check_fluent_paths, non_ascii_chars  # noqa: E402

# Windows 控制台默认 GBK，打印 ✓/✗ 会抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".venv"
MCP_JSON = REPO_ROOT / ".mcp.json"
MCP_TEMPLATE = REPO_ROOT / ".mcp.json.template"
CODEX_CONFIG = REPO_ROOT / ".codex" / "config.toml"

OK = "✓"
NO = "✗"
WARN = "⚠"


def venv_bin(name: str) -> Path:
    """venv 里的可执行文件路径。Windows 是 Scripts/，POSIX 是 bin/。"""
    sub = "Scripts" if os.name == "nt" else "bin"
    return VENV_DIR / sub / (name + (".exe" if os.name == "nt" else ""))


def detect_fluent_root(explicit: str | None = None) -> tuple[str | None, str | None, str]:
    """找 Fluent 的 `fluent` 目录。返回 (root, version, 来源说明)。

    优先级与 PyFluent 一致：显式指定 > PYFLUENT_FLUENT_ROOT > AWP_ROOT<ver> 取最高
    > 扫常见安装位置。

    注意 `PYFLUENT_FLUENT_ROOT` 期望的是 **fluent 目录**（PyFluent 自己补
    ntbin/win64/fluent.exe），不是 AWP_ROOT，也不是 exe 路径。
    """
    def version_of(root: Path) -> str | None:
        """从 ...\v261\fluent 反推 "26.1"。"""
        for part in root.parts:
            if len(part) >= 2 and part[0] in "vV" and part[1:].isdigit():
                d = part[1:]
                return f"{d[:-1]}.{d[-1]}" if len(d) >= 2 else d
        return None

    def accept(p: str | Path, src: str):
        path = Path(p)
        exe = path / "ntbin" / "win64" / "fluent.exe"
        if exe.exists() or (path / "bin" / "fluent").exists():
            return str(path), version_of(path), src
        return None

    if explicit:
        r = accept(explicit, "--fluent-root 显式指定")
        return r if r else (str(explicit), version_of(Path(explicit)), "显式指定（未验证到可执行文件）")

    env_root = os.environ.get("PYFLUENT_FLUENT_ROOT")
    if env_root:
        r = accept(env_root, "环境变量 PYFLUENT_FLUENT_ROOT")
        if r:
            return r

    # AWP_ROOT<ver>，取版本号最高的那个
    awps = []
    for key, val in os.environ.items():
        if key.upper().startswith("AWP_ROOT") and key[8:].isdigit():
            awps.append((int(key[8:]), val))
    for ver, val in sorted(awps, reverse=True):
        r = accept(Path(val) / "fluent", f"环境变量 AWP_ROOT{ver}")
        if r:
            return r

    # 扫常见安装位置
    candidates = [
        Path(r"C:\Program Files\ANSYS Inc"),
        Path(r"D:\Program Files\ANSYS Inc"),
        Path(r"C:\Program Files\ANSYS Inc\ANSYS Student"),
        Path(r"D:\Program Files\ANSYS Inc\ANSYS Student"),
        Path("/usr/ansys_inc"),
        Path("/opt/ansys_inc"),
    ]
    found = []
    for base in candidates:
        if not base.is_dir():
            continue
        # 形如 <base>/*/v261/fluent 或 <base>/v261/fluent
        for pattern in ("*/v*/fluent", "v*/fluent"):
            for p in base.glob(pattern):
                r = accept(p, f"扫描 {base}")
                if r:
                    found.append((version_of(p) or "", r))
    if found:
        # 取版本最高的
        found.sort(key=lambda t: [int(x) for x in t[0].split(".") if x.isdigit()], reverse=True)
        return found[0][1]

    return None, None, "未找到"


def build_mcp_json(fluent_root: str | None) -> dict:
    """生成 .mcp.json 的内容。fluent_root 为 None 时不写该变量（让 PyFluent 自己找）。"""
    env = {
        "PYTHONIOENCODING": "utf-8",
        "FLUIDS_MCP_LOG_LEVEL": "INFO",
    }
    if fluent_root:
        env["PYFLUENT_FLUENT_ROOT"] = fluent_root
    return {
        "mcpServers": {
            "ansys-fluent-mcp": {
                "type": "stdio",
                "command": str(venv_bin("ansys-fluent-mcp")),
                "env": env,
            }
        }
    }


def build_codex_config(fluent_root: str | None) -> str:
    """生成 .codex/config.toml —— 与 .mcp.json 等价的内容，换成 TOML 格式。

    同样是**机器专属**的（里面有 venv 与本机 Fluent 的绝对路径），
    所以和 .mcp.json 一样不入版本控制，由本脚本按本机生成。

    路径用 TOML **字面量**字符串（单引号）—— Windows 路径里的反斜杠
    在基本字符串（双引号）里需要转义，字面量里则原样保留。
    """
    def q(s: str) -> str:
        if "'" in s:
            raise SystemExit(f"✗ 路径含单引号，无法写进 TOML 字面量字符串：{s}")
        return f"'{s}'"

    lines = [
        "# 由 scripts/setup.py 按本机生成，不要手工改，也不入版本控制。",
        "# 与根目录的 .mcp.json 等价（那份是 JSON，这份是 TOML）。",
        "",
        "[mcp_servers.ansys-fluent-mcp]",
        f"command = {q(str(venv_bin('ansys-fluent-mcp')))}",
        "",
        "[mcp_servers.ansys-fluent-mcp.env]",
        'PYTHONIOENCODING = "utf-8"',
        'FLUIDS_MCP_LOG_LEVEL = "INFO"',
    ]
    if fluent_root:
        lines.append(f"PYFLUENT_FLUENT_ROOT = {q(fluent_root)}")
    return "\n".join(lines) + "\n"


def check() -> tuple[bool, list[str]]:
    """校验当前接线是否可用。返回 (是否全通过, 消息列表)。"""
    msgs: list[str] = []
    ok = True

    # ── 非 ASCII 路径：先查，因为它的报错会指向错误方向 ──
    # 项目根是 Fluent 的启动目录，此处已实测确证会崩。
    # 接线全绿也不代表能跑 —— 路径含中文时 Fluent 会报一条像"网格损坏"的错。
    for w in check_fluent_paths(repo_root=REPO_ROOT):
        ok = False
        msgs.append(f"{NO} {w}")

    if not VENV_DIR.is_dir():
        ok = False
        msgs.append(f"{NO} 没有 .venv —— 跑 `python -m venv .venv` 或 `setup.py --create-venv`")
    else:
        msgs.append(f"{OK} .venv 存在：{VENV_DIR}")

    mcp_exe = venv_bin("ansys-fluent-mcp")
    if not mcp_exe.exists():
        ok = False
        msgs.append(f"{NO} 缺 {mcp_exe.name} —— 跑 `.venv/Scripts/python.exe -m pip install ansys-fluent-mcp`")
    else:
        msgs.append(f"{OK} {mcp_exe.name} 在：{mcp_exe}")

    if not MCP_JSON.exists():
        ok = False
        msgs.append(f"{NO} 没有 .mcp.json —— 接线还没做（全新 clone 的正常状态）")
        msgs.append(f"    → 跑一次 `setup.py`（不带 --check）生成；模板见 {MCP_TEMPLATE.name}")
        return ok, msgs

    try:
        cfg = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        entry = cfg["mcpServers"]["ansys-fluent-mcp"]
    except Exception as exc:  # noqa: BLE001
        return False, msgs + [f"{NO} .mcp.json 解析失败：{exc}"]

    cmd = entry.get("command", "")
    if Path(cmd).exists():
        msgs.append(f"{OK} .mcp.json 的 command 指向存在的文件")
    else:
        ok = False
        msgs.append(f"{NO} .mcp.json 的 command 指向【不存在】的路径：{cmd}")
        msgs.append(f"    → 多半是换了机器/目录。跑一次 `setup.py` 重写")

    root = entry.get("env", {}).get("PYFLUENT_FLUENT_ROOT")
    if root:
        exe = Path(root) / "ntbin" / "win64" / "fluent.exe"
        if exe.exists():
            msgs.append(f"{OK} Fluent 可执行文件在：{exe}")
        else:
            ok = False
            msgs.append(f"{NO} PYFLUENT_FLUENT_ROOT 指向的位置没有 fluent.exe：{root}")
    else:
        msgs.append(f"{WARN} .mcp.json 没设 PYFLUENT_FLUENT_ROOT，靠 pyfluent 自己发现")

    # pyfluent 装没装
    try:
        import ansys.fluent.core  # noqa: F401
        msgs.append(f"{OK} ansys-fluent-core 可导入")
    except ImportError:
        try:
            r = subprocess.run(
                [str(venv_bin("python")), "-c", "import ansys.fluent.core"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                msgs.append(f"{OK} ansys-fluent-core 在 venv 里可导入")
            else:
                ok = False
                msgs.append(f"{NO} venv 里 import ansys.fluent.core 失败")
        except Exception as exc:  # noqa: BLE001
            msgs.append(f"{WARN} 无法确认 ansys-fluent-core：{exc}")

    return ok, msgs


def create_venv() -> bool:
    print(f"\n[建 venv] {VENV_DIR}")
    r = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if r.returncode != 0:
        print(f"  {NO} venv 创建失败")
        return False
    py = venv_bin("python")
    print(f"[装包] ansys-fluent-mcp（可能要几分钟）")
    r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "ansys-fluent-mcp"])
    print(f"  {OK} 完成" if r.returncode == 0 else f"  {NO} 安装失败")
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把本项目接到本机：检测 Fluent、重写 .mcp.json、校验",
    )
    ap.add_argument("--check", action="store_true", help="只校验，不写任何文件")
    ap.add_argument("--create-venv", action="store_true", help="缺 venv 时顺便建（慢）")
    ap.add_argument("--fluent-root", default=None,
                    help=r"手动指定 Fluent 根，如 D:\...\v261\fluent")
    args = ap.parse_args()

    print("=" * 68)
    print("fluent-cfd-agent-pipeline —— 本机接线")
    print("=" * 68)
    _bad = non_ascii_chars(REPO_ROOT)
    print(f"项目根：{REPO_ROOT}")
    if _bad:
        print(f"  {NO} 路径含非 ASCII 字符 {_bad!r} —— Fluent 在此会崩，见下方校验")
    else:
        print(f"  {OK} 路径为纯 ASCII（Fluent 对此敏感）")

    if args.check:
        ok, msgs = check()
        print("\n[校验]")
        for m in msgs:
            print("  " + m)
        print("\n" + ("接线正常。" if ok else "★ 有问题，见上。"))
        return 0 if ok else 1

    if args.create_venv and not VENV_DIR.is_dir():
        if not create_venv():
            return 1

    print("\n[检测 Fluent]")
    root, version, src = detect_fluent_root(args.fluent_root)
    if root:
        print(f"  {OK} 根目录：{root}")
        print(f"  {OK} 版本  ：{version or '未知'}")
        print(f"     来源  ：{src}")
    else:
        print(f"  {WARN} 没找到 Fluent 安装（{src}）")
        print("     不写 PYFLUENT_FLUENT_ROOT，交给 pyfluent 自己发现。")
        print("     若之后 connect 失败，用 --fluent-root 显式指定。")

    print("\n[写 .mcp.json]")
    cfg = build_mcp_json(root)
    if MCP_JSON.exists():
        old = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        old_cmd = old.get("mcpServers", {}).get("ansys-fluent-mcp", {}).get("command", "")
        if old_cmd and old_cmd != cfg["mcpServers"]["ansys-fluent-mcp"]["command"]:
            print(f"  旧 command：{old_cmd}")
    MCP_JSON.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  {OK} 已写入 {MCP_JSON}")

    # Codex 那份配置，内容等价、格式换成 TOML。同样是机器专属。
    print("\n[写 .codex/config.toml]")
    CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG.write_text(build_codex_config(root), encoding="utf-8")
    print(f"  {OK} 已写入 {CODEX_CONFIG}")

    ok, msgs = check()
    print("\n[校验]")
    for m in msgs:
        print("  " + m)

    print("\n" + "=" * 68)
    if ok:
        print(f"{OK} 接线就绪。")
        print()
        print("下一步：**重载 Claude Code 会话**（.mcp.json 只在启动时加载），")
        print("然后用 /mcp 确认 ansys-fluent-mcp 已连接。")
    else:
        print(f"{NO} 还有问题，见上面的 ✗。")
    print("=" * 68)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
