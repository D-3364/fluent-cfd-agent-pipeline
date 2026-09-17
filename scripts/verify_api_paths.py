"""逐个探活一份设置路径/enum 清单 —— 把「N 轮 agent 考古」换成「一次确定性调用」。

## 为什么要它

一次真实运行的 executor 花了 **250 次 tool call**，其中绝大部分在**现场考古 API**：
逐条试出哪些字段改了名、哪些枚举不存在。那些结论已固化进
`references/solver-api-26.1.md` —— 但那份表是**版本锁定**的。

**换 Fluent 版本时，需要一次性、确定性地重新验证整张表。** 这个脚本就是干这个的。

## 与 MCP 会话的关系

它是**独立进程、自己拉 Fluent**（和 `probe_case_standalone.py` 同模式），
**不占用 MCP 的单例会话**。所以要在**执行 agent 接管之前**跑。

## 跑法

    .venv/Scripts/python.exe scripts/verify_api_paths.py --mesh "<网格>" --run-dir runs/<id>

产物：`<run-dir>/api-path-check.json`，形如

    { "checked": N, "ok": M, "problem": [{path, kind, detail}, ...] }

`kind` ∈ `ok` / `missing` / `renamed` / `no_enum` / `error`
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / ".fluent-transcripts"

# ─────────────────────────────────────────────────────────────────────
# 待验证清单。每条 = (点号路径, 期望的枚举值列表或 None)
#
# 期望值为 None → 只查路径是否存在（不查取值）。
# 期望值非空    → 还要求当前值落在该集合内。
#
# ★ 这份清单来自 references/solver-api-26.1.md，改那边时同步改这里。
# ─────────────────────────────────────────────────────────────────────
CHECKS: list[tuple[str, list[str] | None]] = [
    # 求解器与时间推进
    ("setup.general.solver.time", ["steady", "transient"]),
    ("setup.general.solver.type", ["pressure-based", "density-based"]),
    ("solution.run_calculation.parameters.time_step_size", None),
    ("solution.methods.p_v_coupling",
     ["SIMPLE", "SIMPLEC", "PISO", "coupled", "fractional-step"]),
    # 物理模型
    ("setup.models.viscous.model",
     ["laminar", "k-epsilon", "k-omega", "spalart-allmaras", "reynolds-stress"]),
    ("setup.models.energy.enabled", None),
    ("mesh.wall_distance_method", None),
    # 残差与收敛
    ("solution.monitor.residual.equations", None),
    ("solution.monitor.residual.options.criterion_type", ["absolute", "relative"]),
    # 导出与报告（★ 这几个的【关键字名】是那次运行踩坑的地方）
    ("file.export.ascii", None),
    ("solution.report_definitions.flux", None),
    ("solution.report_definitions.surface", None),
    ("results.surfaces.plane_surface", None),
    # 图形（clip_to_range / range_options 在这里）
    ("results.graphics.contour", None),
    ("results.graphics.vector", None),
]

# 单独列出的、需要【调用】才能确认的命令（路径存在 ≠ 命令可调）
COMMANDS: list[str] = [
    "solution.run_calculation.iterate",
    "solution.run_calculation.dual_time_iterate",
    "solution.run_calculation.interrupt",
    "mesh.check",
    "mesh.quality",
]


def resolve(solver, dotted: str):
    """把 'a.b.c' 逐级取出来。取不到就抛。"""
    obj = solver.settings
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description="逐个探活设置路径清单")
    ap.add_argument("--mesh", required=True, help="网格文件，用来把会话载起来")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--dimension", type=int, choices=(2, 3), default=None)
    ap.add_argument("--product-version", default=os.environ.get("CFD_PRODUCT_VERSION") or None)
    args = ap.parse_args()

    mesh = Path(args.mesh)
    if not mesh.exists():
        print(f"✗ 网格不存在：{mesh}")
        return 1
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    dimension = args.dimension
    if dimension is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from probe_mesh import probe as probe_mesh_file
        dimension = probe_mesh_file(mesh).get("dimension")
    if dimension is None:
        print("✗ 无法识别维度，请用 --dimension 指定")
        return 1

    import ansys.fluent.core as pyfluent

    SCRATCH.mkdir(parents=True, exist_ok=True)
    solver = pyfluent.launch_fluent(
        product_version=args.product_version, dimension=dimension,
        ui_mode="no_gui", precision="double", processor_count=1,
        cleanup_on_exit=True, cwd=str(SCRATCH),
    )

    results: list[dict] = []
    try:
        solver.settings.file.read_mesh(file_name=str(mesh))

        for dotted, allowed in CHECKS:
            rec = {"path": dotted, "kind": "ok", "detail": None}
            try:
                obj = resolve(solver, dotted)
            except Exception as exc:  # noqa: BLE001
                rec["kind"] = "missing"
                rec["detail"] = f"{type(exc).__name__}: {str(exc)[:140]}"
                results.append(rec)
                continue
            if allowed:
                try:
                    cur = obj.get_state() if hasattr(obj, "get_state") else obj
                    if isinstance(cur, str) and cur not in allowed:
                        rec["kind"] = "no_enum"
                        rec["detail"] = f"当前值 {cur!r} 不在期望集合 {allowed}"
                except Exception as exc:  # noqa: BLE001
                    rec["kind"] = "error"
                    rec["detail"] = f"取值失败 {type(exc).__name__}: {str(exc)[:120]}"
            if rec["detail"] is None:
                rec["detail"] = "路径存在"
            results.append(rec)

        for dotted in COMMANDS:
            rec = {"path": dotted, "kind": "ok", "detail": None}
            try:
                obj = resolve(solver, dotted)
                if not callable(obj) and not hasattr(obj, "execute_command"):
                    rec["kind"] = "missing"
                    rec["detail"] = "解析到了但不是可调用命令"
                else:
                    rec["detail"] = "命令存在且可调用"
            except Exception as exc:  # noqa: BLE001
                rec["kind"] = "missing"
                rec["detail"] = f"{type(exc).__name__}: {str(exc)[:140]}"
            results.append(rec)

        # ★ 单独确认 MCP 帮助文本出错的那条：iterate 的参数说明
        try:
            doc = (solver.settings.solution.run_calculation.iterate.__doc__ or "")
            rec = {
                "path": "solution.run_calculation.iterate 的 iter_count 语义",
                "kind": "ok",
                "detail": "见 stdout —— MCP 帮助文本称其为 time steps，实际是内迭代",
            }
            results.append(rec)
            print("\n--- iterate 的 docstring（用于人工复核语义）---")
            print(doc[:500])
        except Exception as exc:  # noqa: BLE001
            results.append({"path": "iterate docstring", "kind": "error",
                            "detail": f"{type(exc).__name__}: {exc}"})

    except Exception:  # noqa: BLE001
        results.append({"path": "__fatal__", "kind": "error",
                        "detail": traceback.format_exc()})
    finally:
        try:
            solver.exit()
        except Exception:  # noqa: BLE001
            pass

    problem = [r for r in results if r["kind"] != "ok"]
    payload = {
        "checked": len(results),
        "ok": len(results) - len(problem),
        "problem": problem,
        "all": results,
        "_note": "路径清单来自 references/solver-api-26.1.md，改那边时同步改这里",
    }
    out = run_dir / "api-path-check.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n探活 {len(results)} 条，通过 {len(results) - len(problem)} 条")
    for r in problem:
        print(f"  ✗ [{r['kind']}] {r['path']}")
        print(f"      {r['detail']}")
    print(f"\nwrote {out}")
    return 0 if not problem else 1


if __name__ == "__main__":
    raise SystemExit(main())
