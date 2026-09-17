"""开工前环境快照 —— 写进 `runs/<id>/00_env.json`。

每轮流水线开始时跑一次，把**当时的环境**固定下来。价值有两个：

  1. **当场拦住环境问题**。接线没做、case 不存在、网格超 Student 上限，
     都会在开工前暴露，而不是跑到一半才炸。
  2. **可回溯**。"这次结果是在什么环境下跑出来的"——Fluent 版本、网格规模、
     MCP 接线，全部留档。几周后回头看报告时不用猜。

## ★ 这个脚本查不到"本会话有没有加载 MCP"

它只能看**磁盘上的配置**。而 `.mcp.json` 配好 ≠ 当前会话加载了它 ——
后者是会话启动时决定的，跑任何脚本都查不出来。

**两者症状相同、修法相反**，所以 `mcp_session` 字段**故意留成 `unknown`**，
由 agent 在会话内实测后填入。见 SKILL.md「开始之前」。

跑法：
    .venv/Scripts/python.exe scripts/preflight.py --case "<网格>" --run-dir runs/<id>
    .venv/Scripts/python.exe scripts/preflight.py --case "<网格>"          # 只打印
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _mcp_env import (  # noqa: E402
    MCP_JSON, ROOT, check_fluent_paths, mcp_env, non_ascii_chars,
    server_exe, utf8_streams, venv_bin,
)

utf8_streams()

# 与 references/student-limits.md 保持一致。改一处要改两处。
# 来源：https://www.ansys.com/academic/students/ansys-student
STUDENT_MAX_CELLS = 1_000_000
STUDENT_MAX_CORES = 4
STUDENT_LIMITS_SRC = "https://www.ansys.com/academic/students/ansys-student"


def _load_setup_module():
    """按路径加载 setup.py（避免 `setup` 这个通用模块名起冲突）。"""
    spec = importlib.util.spec_from_file_location(
        "_ansys_setup", Path(__file__).resolve().parent / "setup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def collect_wiring() -> dict:
    """MCP 接线（磁盘层）。"""
    out: dict = {
        "mcp_json_present": MCP_JSON.exists(),
        "mcp_json_path": str(MCP_JSON),
        "mcp_server_exe": None,
        "mcp_server_exe_exists": False,
        "pyfluent_fluent_root": None,
        "ok": False,
    }
    if not MCP_JSON.exists():
        return out

    try:
        cfg = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        entry = cfg["mcpServers"]["ansys-fluent-mcp"]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    out["mcp_server_exe"] = entry.get("command")
    out["mcp_server_exe_exists"] = bool(entry.get("command")) and Path(entry["command"]).exists()
    out["pyfluent_fluent_root"] = entry.get("env", {}).get("PYFLUENT_FLUENT_ROOT")
    out["ok"] = out["mcp_server_exe_exists"]
    return out


def collect_python() -> dict:
    out: dict = {"venv_python": str(venv_bin("python")), "venv_exists": venv_bin("python").exists()}
    try:
        import ansys.fluent.core as pf
        out["ansys_fluent_core"] = pf.__version__
    except Exception as exc:  # noqa: BLE001
        out["ansys_fluent_core"] = None
        out["core_error"] = f"{type(exc).__name__}: {exc}"
    try:
        import ansys.fluent.mcp as pm
        out["ansys_fluent_mcp"] = getattr(pm, "__version__", None)
    except Exception as exc:  # noqa: BLE001
        out["ansys_fluent_mcp"] = None
        out["mcp_error"] = f"{type(exc).__name__}: {exc}"
    return out


def collect_case(case: str | None) -> dict | None:
    if not case:
        return None
    from probe_mesh import probe

    info = probe(case)
    cells = info.get("cells")
    return {
        "path": info.get("file"),
        "exists": "error" not in info,
        "dimension": info.get("dimension"),
        "cells": cells,
        "nodes": info.get("nodes"),
        "faces": info.get("faces"),
        "within_student_limit": info.get("within_student_limit"),
        "error": info.get("error"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="开工前环境快照")
    ap.add_argument("--case", default=None, help="case/网格文件路径")
    ap.add_argument("--run-dir", default=None, help="写到该目录下的 00_env.json")
    args = ap.parse_args()

    warnings: list[str] = []

    wiring = collect_wiring()
    if not wiring["mcp_json_present"]:
        warnings.append("没有 .mcp.json —— 全新 clone 的正常状态，跑 scripts/setup.py 生成")
    elif not wiring["ok"]:
        warnings.append("MCP server 路径失效 —— 多半换了机器/目录，跑 scripts/setup.py 重写")

    py = collect_python()
    if not py.get("ansys_fluent_core"):
        warnings.append("ansys-fluent-core 不可导入")

    try:
        setup_mod = _load_setup_module()
        fluent_root, fluent_ver, fluent_src = setup_mod.detect_fluent_root(None)
    except Exception as exc:  # noqa: BLE001
        fluent_root, fluent_ver, fluent_src = None, None, f"检测失败: {exc}"
        warnings.append(f"Fluent 检测异常：{exc}")

    fluent = {
        "root": fluent_root,
        "version": fluent_ver,
        "source": fluent_src,
        "exe": str(Path(fluent_root) / "ntbin" / "win64" / "fluent.exe") if fluent_root else None,
    }
    if not fluent_root:
        warnings.append("没检测到 Fluent 安装 —— 用 setup.py --fluent-root 显式指定")

    case_info = collect_case(args.case)
    if case_info:
        if not case_info["exists"]:
            warnings.append(f"case 文件不可用：{case_info.get('error')}")
        elif case_info["dimension"] is None:
            warnings.append("未能识别网格维度")
        elif case_info["within_student_limit"] is False:
            warnings.append(
                f"网格 {case_info['cells']:,} 单元，超出 ANSYS Student 上限 "
                f"{STUDENT_MAX_CELLS:,} —— 本机跑不了，应走 escalate"
            )

    # ── 非 ASCII 路径：Fluent 会崩，且报错指向错误方向 ──
    # 必须在启动 Fluent 之前拦下来，否则会得到一条像"网格损坏"的报错。
    run_dir = Path(args.run_dir) if args.run_dir else None
    path_warnings = check_fluent_paths(
        repo_root=ROOT,
        case_file=args.case,
        out_dir=run_dir,
    )
    warnings.extend(path_warnings)

    env_snapshot = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "project_root": str(ROOT),
        "wiring": wiring,
        "fluent": fluent,
        "python": py,
        "case": case_info,
        "student_limits": {
            "max_cells": STUDENT_MAX_CELLS,
            "max_cores": STUDENT_MAX_CORES,
            "source": STUDENT_LIMITS_SRC,
            "note": "与 references/student-limits.md 保持一致，改一处要改两处",
        },
        "paths_ascii": {
            "ok": not path_warnings,
            "repo_root": {
                "path": str(ROOT),
                "non_ascii": non_ascii_chars(ROOT),
                "crash_confirmed": True,
            },
            "case_file": {
                "path": str(args.case) if args.case else None,
                "non_ascii": non_ascii_chars(args.case) if args.case else "",
                "crash_confirmed": False,
            },
            "out_dir": {
                "path": str(run_dir) if run_dir else None,
                "non_ascii": non_ascii_chars(run_dir) if run_dir else "",
                "crash_confirmed": False,
            },
            "_why": (
                "Fluent 在非 ASCII 路径下会崩，报错是 "
                "`utf-8 can't decode byte 0xb8` —— 看起来像网格损坏，实际不是。"
                "项目根这一条已实测确证；case 与输出目录是同源风险，未单独确证。"
            ),
        },
        "mcp_session": {
            "status": "unknown",
            "_why_unknown": (
                "本脚本只能看磁盘配置，查不到当前会话有没有加载 MCP。"
                "两者症状相同但修法相反："
                "【工具不存在】= 会话未加载 .mcp.json → 必须重载会话，跑脚本无用；"
                "【工具存在但报错】= server 起来了但连不上 Fluent → 才该跑 setup.py --check。"
            ),
            "_agent_todo": (
                "在会话内调一次 session_status："
                "工具不存在 → status='not_loaded'，提示用户重载会话并中止；"
                "返回内容 → status='ok'；"
                "返回错误 → status='server_error'，转 scripts/setup.py --check 排查。"
            ),
        },
        "warnings": warnings,
    }

    payload = json.dumps(env_snapshot, ensure_ascii=False, indent=2) + "\n"

    if args.run_dir:
        run_dir = Path(args.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / "00_env.json"
        target.write_text(payload, encoding="utf-8")
        print(f"✓ 环境快照已写入 {target}")
    else:
        print(payload, end="")

    if warnings:
        print("\n⚠ 注意：", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
