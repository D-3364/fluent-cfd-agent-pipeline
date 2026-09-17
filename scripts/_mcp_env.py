"""验证脚本的公共装配：**以 `.mcp.json` 为唯一事实源**。

`check_mcp.py` / `e2e_mcp.py` / `smoke_test.py` 都要起一个 MCP server 子进程，
需要两样东西：server 可执行文件在哪、环境变量给什么。

**不要在这几个脚本里各写一份硬编码路径** —— 那样换机器时得改好几处，
而且容易只改对一半。改成统一从 `.mcp.json` 读：那个文件由 `scripts/setup.py`
按本机实际情况生成，是唯一的接线事实源。

`.mcp.json` 缺失或路径失效时，给出**明确的修复指令**，而不是让脚本
用某个猜的路径悄悄跑下去——那正是"工具全不见了但不知道哪一环坏了"的成因。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv"
MCP_JSON = ROOT / ".mcp.json"
MCP_TEMPLATE = ROOT / ".mcp.json.template"


def venv_bin(name: str) -> Path:
    """venv 里的可执行文件。Windows 是 Scripts/，POSIX 是 bin/。"""
    sub = "Scripts" if os.name == "nt" else "bin"
    return VENV_DIR / sub / (name + (".exe" if os.name == "nt" else ""))


def server_exe() -> Path:
    """MCP server 可执行文件。

    优先用 `.mcp.json` 里写的那个（setup.py 生成的、本机真实路径），
    找不到就退回 venv 的常规位置。
    """
    try:
        cfg = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        cmd = cfg["mcpServers"]["ansys-fluent-mcp"]["command"]
        p = Path(cmd)
        if p.exists():
            return p
        return p  # 存在但不有效，交给调用方报错（提示更具体）
    except Exception:  # noqa: BLE001
        return venv_bin("ansys-fluent-mcp")


def mcp_env() -> dict:
    """起 MCP server 子进程用的环境。

    以 `.mcp.json` 的 `env` 段打底（setup.py 按本机检测写进去的），
    再叠上真实环境变量——真实环境优先，方便临时覆盖调试。
    """
    env = dict(os.environ)
    try:
        cfg = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        for k, v in cfg["mcpServers"]["ansys-fluent-mcp"].get("env", {}).items():
            env.setdefault(k, v)
    except Exception:  # noqa: BLE001
        pass
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def require_ready() -> bool:
    """检查接线是否就绪；不就绪就打印修复指令并返回 False。

    两种常见的"没接线"状态，分别给不同的提示 —— 因为修法不同：

      * `.mcp.json` 不存在：全新 clone，或刚被清理过
      * `.mcp.json` 在但路径失效：换了机器/目录
    """
    setup_cmd = f"{venv_bin('python')} scripts/setup.py"

    if not MCP_JSON.exists():
        print(f"✗ 没有 {MCP_JSON.name} —— 接线还没做。")
        print()
        print("  这是正常的全新状态：真正的 .mcp.json 由 setup.py 按本机生成，")
        print(f"  不入版本控制（模板见 {MCP_TEMPLATE.name}）。")
        print()
        print("  跑一次：")
        print(f"      {setup_cmd}" + (" --create-venv" if not VENV_DIR.is_dir() else ""))
        print("  然后【重载 Claude Code 会话】。")
        return False

    exe = server_exe()
    if not exe.exists():
        print(f"✗ .mcp.json 里写的 MCP server 不存在：{exe}")
        print()
        print("  多半是换了机器或目录。跑一次重写接线：")
        print(f"      {setup_cmd}" + (" --create-venv" if not VENV_DIR.is_dir() else ""))
        return False

    return True


def utf8_streams() -> None:
    """Windows 控制台默认 GBK，打印 ✓/✗ 会崩。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# ─────────────────────────────────────────────────────────────────────
# 非 ASCII 路径检测
#
# Fluent 在中文路径下会直接崩，而且【报错信息指向完全错误的方向】：
#
#     utf-8 can't decode byte 0xb8 ...
#
# 看起来像网格文件损坏，实际是路径编码问题。实测这条报错与真实原因的
# 联系极弱，极易误判 —— 排查时会把时间全花在检查网格上。
#
# 所以这里【提前】把路径查一遍，让失败带上正确的诊断。纯 Python 的文件读写
# （如 probe_mesh.py 读网格头）不受影响 —— 只有把路径交给 Fluent 才出问题。
# ─────────────────────────────────────────────────────────────────────

def non_ascii_chars(path: str | Path) -> str:
    """返回路径里出现的非 ASCII 字符（去重保序）。全 ASCII 则返回空串。"""
    out: list[str] = []
    for ch in str(path):
        if ord(ch) > 127 and ch not in out:
            out.append(ch)
    return "".join(out)


def ascii_risk(path: str | Path, label: str, *, symptom: str, confirmed: bool) -> str | None:
    """检查一个将交给 Fluent 的路径。有问题返回告警文本，否则 None。

    Parameters
    ----------
    path : str | Path
        要检查的路径。
    label : str
        这个路径的角色（"项目根" / "case 文件" / "输出目录"）。
    symptom : str
        该角色被实测到的**具体失败症状**。
        ★ 不同角色的症状【不一样】，必须分别指名 —— 实测：
          项目根   → Fluent 崩溃，报 utf-8 解码错误
          case 文件 → 不崩溃，报 "File ... not found!"（文件明明存在）
        把两者混为一谈会让告警本身也变成误导。
    confirmed : bool
        该症状是否已实测确证。False 时措辞降级为"同源风险"。
    """
    bad = non_ascii_chars(path)
    if not bad:
        return None

    verdict = "实测确证" if confirmed else "未确证（同源风险，建议一并避开）"
    return (
        f"{label}含非 ASCII 字符 {bad!r}：{path}\n"
        f"      判定：{verdict}\n"
        f"      该角色的实测症状：{symptom}\n"
        f"      ⚠ 这条症状【指向的方向是错的】—— 别照着它去查别的东西\n"
        f"      处置：把工作目录/文件挪到纯 ASCII 路径（如 C:\\fluent-scratch\\）"
    )


# 各路径角色的实测症状。★ 三者不同，不要合并成一句话。
_SYMPTOM_ROOT = (
    "Fluent 直接崩，报 `utf-8 can't decode byte 0xb8` —— "
    "看起来像网格文件损坏，实际是目录名"
)
_SYMPTOM_CASE = (
    "不崩溃。Fluent 报 `Error: File \"...\" not found!` —— "
    "**文件明明存在**，看着像路径敲错了，实际是编码被破坏"
)


def check_fluent_paths(
    repo_root: str | Path | None = None,
    case_file: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> list[str]:
    """把将交给 Fluent 的路径统一查一遍，返回告警列表（空 = 没问题）。

    三个路径角色的失败症状【各不相同】，所以措辞也各不相同：

      * 项目根（Fluent 的启动目录）—— 崩溃，utf-8 解码错误。【已实测确证】
      * case 文件（read_mesh 入参）—— "File not found"。【已实测确证】
      * 输出目录（write_case / export.ascii 落点）—— 同上推断，未单独确证。

    前两条由三项隔离对照实测得出（C 盘 ASCII ✓ / D 盘 ASCII ✓ / CJK ✗），
    排除了盘符与会话因素。
    """
    warn: list[str] = []
    if repo_root is not None:
        w = ascii_risk(repo_root, "项目根", symptom=_SYMPTOM_ROOT, confirmed=True)
        if w:
            warn.append(w)
    if case_file is not None:
        w = ascii_risk(case_file, "case 文件", symptom=_SYMPTOM_CASE, confirmed=True)
        if w:
            warn.append(w)
    if out_dir is not None:
        w = ascii_risk(out_dir, "输出目录", symptom=_SYMPTOM_CASE, confirmed=False)
        if w:
            warn.append(w)
    return warn
