"""离线探测 Fluent 网格文件的元信息 —— 不启动 Fluent。

能在几毫秒内给出维度、单元数、节点数、面数，从而：

  * 在拉起 Fluent **之前**就知道该用 2D 还是 3D 求解器
    （维度不匹配会让 read_mesh 直接失败，浪费一次完整的启动周期）
  * 在定规范阶段就判断是否超出 ANSYS Student 的 100 万单元上限
    （超限是硬中止，早知道早止损）

**读不出边界条件名称** —— 那些存在二进制段里。边界名必须在勘察阶段连上 Fluent
用 `list_named_objects` 发现。这是勘察步骤不可省略的根本原因。

跑法：
    .venv/Scripts/python.exe scripts/probe_mesh.py <网格文件> [...]
    .venv/Scripts/python.exe scripts/probe_mesh.py "E:/CFD/Case files/data/03/couette_flow.msh"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ANSYS Student 2026 R1：流体物理上限 100 万单元/节点
STUDENT_CELL_LIMIT = 1_000_000

# Windows 控制台默认是 GBK/CP936，打印 ✓ ✗ ⚠ 会抛 UnicodeEncodeError
# （实测：网格明明读对了，却崩在最后一行输出上）。强制 UTF-8 并对无法编码的
# 字符降级替换，让探测结果本身能正常打出来。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # 老 Python / 被重定向的流
        pass


def _section_count(head: bytes, section: int) -> int | None:
    """从段头 `(N (0 1 <十六进制计数> 0))` 里取出计数。

    段头格式见 Fluent 网格文件里自带的说明，例如：
        nodes:  (10 (id start end type) (x y z ...))
        cells:  (12 (id start end type elemtype))
    实际数据行是 `(10 (0 1 ea6 0))`，第三个字段是十六进制的条目数。
    """
    m = re.search(rb"\(" + str(section).encode() + rb"\s+\(0\s+1\s+([0-9a-fA-F]+)\s+0\)", head)
    return int(m.group(1), 16) if m else None


def probe(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"file": str(p), "error": "文件不存在"}

    # 段头都在文件靠前的部分
    with p.open("rb") as f:
        head = f.read(65536)

    # 维度段：(2 <dim>)，dim 取 2 或 3
    m = re.search(rb"\(2\s+([23])\)", head)
    dimension = int(m.group(1)) if m else None

    result = {
        "file": str(p),
        "size_bytes": p.stat().st_size,
        "dimension": dimension,
        "nodes": _section_count(head, 10),
        "cells": _section_count(head, 12),
        "faces": _section_count(head, 13),
        "boundary_names_available_offline": False,
    }

    cells = result["cells"]
    if cells is not None:
        result["within_student_limit"] = cells < STUDENT_CELL_LIMIT
        result["student_limit"] = STUDENT_CELL_LIMIT
    if dimension is None:
        result["warning"] = "未能识别维度段，可能不是标准 Fluent .msh 文件"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="离线探测 Fluent 网格元信息")
    ap.add_argument("files", nargs="+", help="网格文件路径")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（供程序消费）")
    args = ap.parse_args()

    results = [probe(f) for f in args.files]

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0],
                         ensure_ascii=False, indent=2))
        return 0

    for r in results:
        print(f"\n{r['file']}")
        if "error" in r:
            print(f"  ✗ {r['error']}")
            continue
        dim = r["dimension"]
        print(f"  维度      : {dim}D" if dim else "  维度      : 未知")
        print(f"  单元数    : {r['cells']:,}" if r["cells"] else "  单元数    : 未知")
        print(f"  节点数    : {r['nodes']:,}" if r["nodes"] else "  节点数    : 未知")
        print(f"  面数      : {r['faces']:,}" if r["faces"] else "  面数      : 未知")
        if "within_student_limit" in r:
            mark = "✓" if r["within_student_limit"] else "✗ 超出 ANSYS Student 限制"
            print(f"  Student   : {mark}（上限 {r['student_limit']:,} 单元）")
        print("  边界名    : 无法离线读取 —— 需连 Fluent 用 list_named_objects 发现")
        if "warning" in r:
            print(f"  ⚠ {r['warning']}")

    if not args.json:
        dims = {r.get("dimension") for r in results if "error" not in r}
        if len(dims) == 1 and None not in dims:
            d = dims.pop()
            print(f"\n提示：拉起 Fluent 时用 dimension={d}（启动参数会是 {'2ddp' if d == 2 else '3ddp'}）")
            print("      维度不匹配会让 read_mesh 直接失败，白费一次启动周期。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
