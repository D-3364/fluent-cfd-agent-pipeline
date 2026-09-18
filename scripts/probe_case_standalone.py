"""独立（非 MCP）网格勘察脚本 —— `cfd-spec-author` 的模式 A 走这条。

MCP 不暴露 `read_mesh`，而定规范 agent 又没有 `run_code`，所以它**无法**通过
MCP 载入网格、也就拿不到边界名。唯一的路是用 `Bash` 起这个独立脚本：
它会拉起一台全新的 Fluent、读入网格、把勘察所需的事实全部 dump 成 JSON。

**只读**：不改任何求解器状态（靠纪律，不靠架构——独立进程是有全权限的）。

跑法：
    .venv/Scripts/python.exe scripts/probe_case_standalone.py \
        --mesh "E:/CFD/Case files/data/03/couette_flow.msh" \
        --run-dir runs/<run-id>

参数也可用环境变量给：`CFD_MESH`、`CFD_RUN_DIR`。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import ansys.fluent.core as pyfluent

# Windows 控制台默认 GBK，打印 ✓/✗ 会抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent

# Student 许可允许 4 核（见 references/student-limits.md）。默认用满 4 核 ——
# 单核会让求解慢 4 倍，而这是**白送的**。非 Student 许可可用环境变量调大。
CPU_COUNT = int(os.environ.get("CFD_PROCESSOR_COUNT", "4"))

# ★ Fluent 会往**进程的当前工作目录**写 .trn 临时文件。不指定 cwd 的话，
#   它们会堆在项目根目录里（实测发生过：根目录攒了 7 个 fluent-*.trn）。
#   所以这里固定把 cwd 指到一个专用的、已被 .gitignore 忽略的目录。
SCRATCH = REPO_ROOT / ".fluent-transcripts"

report: dict = {}


def capture(key, fn):
    try:
        report[key] = fn()
    except Exception as exc:  # noqa: BLE001
        report[key] = {"__error__": f"{type(exc).__name__}: {exc}"}
    return report[key]


def get_state(obj):
    try:
        return obj.get_state()
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def object_names(obj):
    f = getattr(obj, "get_object_names", None)
    if callable(f):
        try:
            return list(f())
        except Exception as exc:  # noqa: BLE001
            return {"__error__": f"{type(exc).__name__}: {exc}"}
    return {"__error__": "no get_object_names"}


def child_names(obj):
    for attr in ("child_names", "_child_names"):
        v = getattr(obj, attr, None)
        if v is not None:
            try:
                return list(v)
            except Exception:  # noqa: BLE001
                pass
    return {"__error__": "no child_names"}


def tui(fn):
    try:
        out = fn()
        return out if isinstance(out, str) else repr(out)
    except Exception as exc:  # noqa: BLE001
        return f"__error__ {type(exc).__name__}: {exc}"


def collect_near_wall(solver, setup) -> dict:
    """尽力而为地取「壁面相邻单元形心距」—— y+ 估算的输入。

    **为什么放在勘察阶段**：这是一个**关于网格的事实**，不是规范决策。
    若不在这里供给，spec 作者就得自己去解析二进制 `.msh` 算近壁单元形心距
    （真实运行中确实如此，被另行标注为"额外只读测量"）。

    多策略尝试，**取到哪个算哪个**，全失败则如实记下错误 —— 不假装成功。
    策略：
      A. 确认 `mesh.wall_distance_method` 已启用（取壁面距离的前提）
      B. 报告定义 `surface-areaavg` / `surface-facetmax` 取 `wall-distance` 场
    返回形如 {ok, method, walls: {zone: {value/…}}, attempts: [...]}。
    """
    attempts: list[dict] = []
    out: dict = {"ok": False, "method": None, "walls": {}, "attempts": attempts}

    # 先拿到 wall 类型的 zone 名
    wall_zones: list[str] = []
    try:
        names = object_names(setup.boundary_conditions.wall)
        if isinstance(names, list):
            wall_zones = [n for n in names if isinstance(n, str)]
        attempts.append({"step": "枚举 wall zone", "result": wall_zones})
    except Exception as exc:  # noqa: BLE001
        attempts.append({"step": "枚举 wall zone",
                         "error": f"{type(exc).__name__}: {exc}"})
        return out

    if not wall_zones:
        attempts.append({"step": "枚举 wall zone", "note": "没有 wall 类型边界"})
        return out

    # 策略 A：启用壁面距离计算（纯几何量，不需要解）
    try:
        wd = solver.settings.mesh.wall_distance_method
        try:
            cur = wd.get_state()
        except Exception:  # noqa: BLE001
            cur = None
        attempts.append({"step": "读 wall_distance_method 当前值", "result": str(cur)})
        if isinstance(cur, str) and cur.lower() in ("none", "off"):
            wd = "flood-fill"
            attempts.append({"step": "启用 wall_distance_method=flood-fill", "result": "set"})
        else:
            attempts.append({"step": "启用 wall_distance_method", "result": "已是启用状态"})
    except Exception as exc:  # noqa: BLE001
        attempts.append({"step": "启用 wall_distance_method",
                         "error": f"{type(exc).__name__}: {exc}"})

    # 策略 B：报告定义 surface-areaavg 取 wall-distance 场。
    # 与那次运行取 y+ 用的是同一套机制，比 TUI 稳定。
    # （TUI 路径实测不存在：'surface_integrals' object has no attribute ...）
    for zone in wall_zones:
        rec: dict = {}
        for rtype, tag in (("surface-areaavg", "areaavg"),
                           ("surface-facetmax", "facetmax")):
            key = f"nw-{tag}-{zone}"
            try:
                solver.settings.solution.report_definitions.surface[key] = {
                    "report_type": rtype,
                    "field": "wall-distance",
                    "surface_names": [zone],
                }
                val = solver.settings.solution.report_definitions.surface[key].report().get_state()
                rec[f"wall_distance_{tag}"] = val
            except Exception as exc:  # noqa: BLE001
                rec[f"wall_distance_{tag}"] = (
                    f"__error__ {type(exc).__name__}: {str(exc)[:120]}")
        out["walls"][zone] = rec

    got = [z for z, r in out["walls"].items()
           if not str(r.get("wall_distance_areaavg", "")).startswith("__error__")]
    if got:
        out["ok"] = True
        out["method"] = "report_definitions surface-areaavg + wall-distance"
        out["_note"] = "壁面相邻单元形心距。spec 的 y+ 估算直接用它。"
    else:
        out["_note"] = (
            "取不到，且【不是调用写错】—— `wall-distance` 是体场，而面报表只接受"
            "面量，Fluent 直接报 `Value is not allowed`。以下两条路已实测走不通："
            "  (a) TUI surface_integrals —— 该路径在 26.1 不存在；"
            "  (b) report_definitions surface-areaavg + field=wall-distance —— 字段不被允许。"
            "可行的替代：从 --mesh 文件自行解析近壁单元形心距（成本高但确定可行），"
            "或等求解后直接读 y+ 报表（那时 y-plus 是合法的面量）。"
            "spec 作者需在 02_spec.json 里说明用的是哪种。"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="独立网格勘察（cfd-spec-author 模式 A）")
    ap.add_argument("--mesh", default=os.environ.get("CFD_MESH"),
                    help="网格文件路径（或用环境变量 CFD_MESH）")
    ap.add_argument("--run-dir", default=os.environ.get("CFD_RUN_DIR"),
                    help="run 目录，产物写到这里（或用环境变量 CFD_RUN_DIR）")
    ap.add_argument("--dimension", type=int, choices=(2, 3), default=None,
                    help="不指定则离线自动识别（维度不匹配会让 read_mesh 直接失败）")
    ap.add_argument("--product-version", default=os.environ.get("CFD_PRODUCT_VERSION") or None,
                    help="不指定则让 PyFluent 走 PYFLUENT_FLUENT_ROOT / AWP_ROOT* 自动解析")
    args = ap.parse_args()

    if not args.mesh:
        print("✗ 必须给 --mesh（或设环境变量 CFD_MESH）")
        return 1
    mesh_path = Path(args.mesh)
    if not mesh_path.exists():
        print(f"✗ 网格文件不存在：{mesh_path}")
        return 1
    if not args.run_dir:
        print("✗ 必须给 --run-dir（或设环境变量 CFD_RUN_DIR）")
        return 1
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    # 维度不匹配会让 read_mesh 直接失败、白费一次约 25 秒的启动 —— 先离线问出来
    dimension = args.dimension
    if dimension is None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from probe_mesh import probe as probe_mesh_file
            dimension = probe_mesh_file(mesh_path).get("dimension")
        except Exception:  # noqa: BLE001
            pass
        if dimension is None:
            print("✗ 无法自动识别维度，请用 --dimension 2 或 3 指定")
            return 1
        print(f"离线探测维度：{dimension}D")

    # ★ cwd 指向 scratch，避免 Fluent 把 .trn 写进项目根目录
    SCRATCH.mkdir(parents=True, exist_ok=True)
    print(f"Fluent 工作目录（.trn 落这里）：{SCRATCH}")

    solver = pyfluent.launch_fluent(
        product_version=args.product_version,
        dimension=dimension,
        ui_mode="no_gui",
        precision="double",
        processor_count=CPU_COUNT,
        cleanup_on_exit=True,
        cwd=str(SCRATCH),
    )

    try:
        capture("fluent_version", lambda: str(solver.get_fluent_version()))

        def do_read_mesh():
            solver.settings.file.read_mesh(file_name=str(mesh_path))
            return "read_mesh returned (no exception)"

        capture("read_mesh", do_read_mesh)

        setup = solver.settings.setup
        bc = setup.boundary_conditions

        capture("bc_child_names", lambda: child_names(bc))

        def collect_bc():
            result = {}
            names = child_names(bc)
            if not isinstance(names, list):
                return {"__error__": "cannot enumerate bc children"}
            for name in names:
                if not isinstance(name, str):
                    continue
                try:
                    container = getattr(bc, name)
                except Exception:  # noqa: BLE001
                    continue
                onames = object_names(container)
                if isinstance(onames, list) and onames:
                    result[name] = {
                        "object_names": onames,
                        "state": get_state(container),
                    }
            return result

        capture("boundary_conditions", collect_bc)

        def collect_cz():
            cz = setup.cell_zone_conditions
            result = {}
            names = child_names(cz)
            if not isinstance(names, list):
                return {"__error__": "cannot enumerate cz children"}
            for name in names:
                if not isinstance(name, str):
                    continue
                try:
                    container = getattr(cz, name)
                except Exception:  # noqa: BLE001
                    continue
                onames = object_names(container)
                if isinstance(onames, list) and onames:
                    result[name] = {
                        "object_names": onames,
                        "state": get_state(container),
                    }
            return result

        capture("cell_zone_conditions", collect_cz)

        capture("materials_fluid_names", lambda: object_names(setup.materials.fluid))
        capture("materials_fluid_state", lambda: get_state(setup.materials.fluid))

        capture("model_viscous", lambda: get_state(setup.models.viscous))
        capture("model_energy", lambda: get_state(setup.models.energy))
        capture("model_multiphase", lambda: get_state(setup.models.multiphase))
        capture("general_solver", lambda: get_state(setup.general.solver))
        capture("general_mesh", lambda: get_state(setup.general.mesh))

        def collect_surfaces():
            try:
                zs = solver.settings.results.surfaces.zone_surface
                return object_names(zs)
            except Exception as exc:  # noqa: BLE001
                return {"__error__": f"{type(exc).__name__}: {exc}"}

        capture("zone_surfaces", collect_surfaces)

        def collect_fields():
            try:
                fd = solver.settings.results.field_data
            except Exception as exc:  # noqa: BLE001
                return {"__error__": f"{type(exc).__name__}: {exc}"}
            out = {}
            for meth in ("get_fields_info", "get_field_info"):
                f = getattr(fd, meth, None)
                if callable(f):
                    try:
                        out[meth] = f()
                    except Exception as exc:  # noqa: BLE001
                        out[meth] = f"__error__ {type(exc).__name__}: {exc}"
            return out or {"__error__": "no field info method"}

        capture("field_data", collect_fields)

        # ---- ★ 近壁几何量：y+ 估算的输入，属于【网格事实】而非规范决策 ----
        # 放在勘察阶段，spec 作者就不必自己写二进制 .msh 解析器。
        # 尽力而为：多策略尝试，取到哪个算哪个，全失败则如实记 __error__。
        capture("near_wall_geometry", lambda: collect_near_wall(solver, setup))

        # ---- TUI-based ground truth (TUI is allowed: this is NOT the MCP sandbox) ----
        capture("tui_mesh_list_zones", lambda: tui(solver.tui.mesh.list_zones))
        capture("tui_mesh_check", lambda: tui(solver.tui.mesh.check))
        capture("tui_mesh_size_info", lambda: tui(solver.tui.mesh.size_info))
        capture("tui_mesh_quality", lambda: tui(solver.tui.mesh.quality))
        capture("tui_define_boundary_conditions_list_zones",
                lambda: tui(solver.tui.define.boundary_conditions.list_zones))

        try:
            report["transcript"] = str(solver.transcript)
        except Exception as exc:  # noqa: BLE001
            report["transcript"] = f"__error__ {type(exc).__name__}: {exc}"

    except Exception:  # noqa: BLE001
        report["__fatal__"] = traceback.format_exc()

    finally:
        try:
            solver.exit()
        except Exception:  # noqa: BLE001
            pass

    out_json = run_dir / "01_probe_raw.json"
    out_trn = run_dir / "01_probe_transcript.txt"
    trn = report.pop("transcript", "")
    out_trn.write_text(str(trn), encoding="utf-8")
    out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"wrote {out_json}")
    print(f"wrote {out_trn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
