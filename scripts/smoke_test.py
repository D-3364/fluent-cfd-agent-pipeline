"""冒烟测试：验证本机能否用 PyFluent 拉起 Fluent 并读到真实数据。

这一步不通，整个流水线就不成立 —— 所以它排在所有其他工作之前。

用与 MCP server 完全相同的启动参数（见 references/mcp-tool-truths.md 第 4 节），
因此这里过了，MCP 那边大概率也能过。

跑法：
    .venv/Scripts/python.exe scripts/smoke_test.py --case "<你的网格文件>"

    本项目【不附带】算例文件 —— 网格由使用者自己提供。
    任何 Fluent 能读的网格都行（.msh / .cas.h5）。也可用环境变量 CFD_DEFAULT_CASE。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

# Windows 控制台默认 GBK，中文输出会乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

# ── 与 mcp-tool-truths.md 记录的一致 ──
#
# ⚠️ 不要传 fluent_path —— 实测 PyFluent 把该参数【原样】当作可执行文件路径
#    （见 ansys/fluent/core/launcher/process_launch_string.py:162-166），
#    传目录会直接 FileNotFoundError。
#
# 解析优先级：fluent_path > product_version > PYFLUENT_FLUENT_ROOT > AWP_ROOT*
# 换机器时**优先什么都不传**（下面用 None 表示），让 PyFluent 走 .mcp.json 里
# setup.py 检测出的 PYFLUENT_FLUENT_ROOT 或 AWP_ROOT* 自动解析 —— 这样不绑版本。
# 只有自动解析失败时才用 CFD_PRODUCT_VERSION 显式指定。
PRODUCT_VERSION = os.environ.get("CFD_PRODUCT_VERSION") or None
DEFAULT_CASE = os.environ.get("CFD_DEFAULT_CASE")

# Student 许可允许 4 核（见 references/student-limits.md）。默认用满 4 核 ——
# 单核会让求解慢 4 倍，而这是**白送的**。非 Student 许可可用环境变量调大。
CPU_COUNT = int(os.environ.get("CFD_PROCESSOR_COUNT", "4"))


def step(n: int, total: int, msg: str) -> None:
    print(f"[{n}/{total}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=DEFAULT_CASE,
                    help="要读入的 .msh / .cas 文件（或用环境变量 CFD_DEFAULT_CASE）")
    ap.add_argument("--dimension", type=int, choices=(2, 3), default=None,
                    help="求解器维度。不指定则用 probe_mesh 离线自动识别")
    ap.add_argument("--ui-mode", default="no_gui",
                    help="no_gui（默认，无头）或 gui（想看着界面就改成这个）")
    ap.add_argument("--keep", action="store_true", help="测完不退出 Fluent，便于手工检查")
    ap.add_argument("--timeout", type=int, default=300, help="Fluent 启动超时秒数（默认 300）")
    args = ap.parse_args()

    if not args.case:
        print("✗ 必须给 --case <网格文件>（或设环境变量 CFD_DEFAULT_CASE）")
        print()
        print("  本项目不附带算例文件 —— 网格由你自己提供。")
        print("  任何 Fluent 能读的网格都行（.msh / .cas.h5）。")
        return 1

    total = 7
    solver = None
    t0 = time.time()

    try:
        step(1, total, "导入 PyFluent")
        import ansys.fluent.core as pyfluent

        print(f"       ansys-fluent-core {pyfluent.__version__}")

        # 维度不匹配会让 read_mesh 直接失败，白费一次完整的启动周期。
        # probe_mesh 能离线读出来，所以先问它。
        dimension = args.dimension
        if dimension is None:
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from probe_mesh import probe  # noqa: PLC0415

                info = probe(args.case)
                dimension = info.get("dimension")
                if dimension:
                    print(f"       离线探测到 {dimension}D 网格，"
                          f"{info.get('cells', 0):,} 单元")
                else:
                    dimension = 3
                    print("       ⚠ 未能识别维度，按 3D 处理")
            except Exception as e:  # noqa: BLE001
                dimension = 3
                print(f"       ⚠ 维度探测失败（{e}），按 3D 处理")

        step(2, total, f"拉起 Fluent（版本 {PRODUCT_VERSION or '自动解析'}, {dimension}D, "
                       f"ui_mode={args.ui_mode}）")
        print("       首次启动通常要 20-60 秒，请耐心等……")
        solver = pyfluent.launch_fluent(
            product_version=PRODUCT_VERSION,
            dimension=dimension,
            ui_mode=args.ui_mode,
            precision="double",
            processor_count=CPU_COUNT,
            start_transcript=True,
            start_timeout=args.timeout,
        )
        print(f"       ✓ 启动成功，耗时 {time.time() - t0:.1f}s")

        step(3, total, f"读网格 {args.case}")
        solver.file.read_mesh(file_name=args.case)
        print("       ✓ 网格已载入")

        step(4, total, "读网格规模")
        info = {
            "cells": solver.mesh.get_cell_count() if hasattr(solver.mesh, "get_cell_count") else None,
        }
        # 不同版本方法名有差异，逐个试
        for attr in ("get_cell_count", "get_node_count", "get_face_count"):
            if hasattr(solver.mesh, attr):
                try:
                    info[attr.replace("get_", "").replace("_count", "")] = getattr(solver.mesh, attr)()
                except Exception as e:  # noqa: BLE001
                    info[attr] = f"<失败: {e}>"
        for k, v in info.items():
            print(f"       {k}: {v}")

        step(5, total, "读边界条件名（定规范阶段的关键输入）")
        try:
            bc_names = solver.setup.boundary_conditions.get_object_names()
            print(f"       ✓ 发现 {len(bc_names)} 个边界: {bc_names}")
        except Exception as e:  # noqa: BLE001
            print(f"       ⚠ 列举边界失败: {e}")
            print("         这不一定是致命错误，但说明勘察阶段要换法子发现边界名")

        step(6, total, "读网格质量")
        try:
            quality = solver.mesh.quality
            for probe in ("minimum_orthogonal_quality", "maximum_aspect_ratio"):
                if hasattr(quality, probe):
                    try:
                        print(f"       {probe}: {getattr(quality, probe)()}")
                    except Exception as e:  # noqa: BLE001
                        print(f"       {probe}: <失败: {e}>")
        except Exception as e:  # noqa: BLE001
            print(f"       ⚠ 读网格质量失败: {e}")

        step(7, total, "断开连接")
        if args.keep:
            print("       --keep 已指定，Fluent 保持运行")
        else:
            solver.exit()
            solver = None
            print("       ✓ 已断开")

        print(f"\n{'=' * 60}")
        print(f"冒烟测试通过，总耗时 {time.time() - t0:.1f}s")
        print(f"{'=' * 60}")
        return 0

    except Exception:  # noqa: BLE001
        print(f"\n{'=' * 60}")
        print("冒烟测试失败")
        print(f"{'=' * 60}")
        traceback.print_exc()
        print("\n排查建议：")
        print("  0. 先跑 `scripts/setup.py --check` —— 它会直接告诉你接线断在哪一环")
        print("  1. Fluent 装在哪 → 跑 `scripts/setup.py` 看检测结果；"
              "检测不到就用 --fluent-root 显式指定")
        print("  2. 许可证是否可用 → 手工启动一次 Fluent 看能否进界面")
        print("  3. 是否被安全软件拦截 → 看是否有防火墙弹窗")
        print("  4. 启动超时 → 可加大 start_timeout，或先用 ui_mode=gui 手工确认能起来")
        return 1

    finally:
        if solver is not None and not args.keep:
            try:
                solver.exit()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
