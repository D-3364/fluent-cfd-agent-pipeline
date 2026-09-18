"""离线解析 .msh 的**逐边界几何**（只读，不启动 Fluent）。

为什么需要它
------------
`probe_mesh.py` 给的是**整体**包围盒、单元/节点/面数，**没有逐边界的几何量**。
而许多关键决策依赖进出口的**截面尺寸**：尺寸缩放倍率、水力直径 D_h、Re 数、
以及"这个通道算不算细长"这类判断。整体 bbox 判不了截面。

**实测教训**：一次真实运行的定规范 agent 因为拿不到这个，**自己现写了一个
8.7 KB 的 .msh 解析脚本**（`01_probe_geometry.py`），算了六分钟。它算得对，
但那是在重新发明工具 —— 这个脚本就是那次成果的通用化。

它给什么
--------
对每个 face zone：面数、**面积**、包围盒、**各方向跨度**（= 截面尺寸）、
以及通道间隙的统计（median / p90 / max）。

**只能处理 ASCII 格式的 .msh。** 二进制网格会被明确拒绝并给出替代方案 ——
以前它会返回一堆空集合，看起来像"这个网格没有边界"，那是假象。

本脚本只读：`open(..., 'rb')` 读文件做几何计算，不触碰任何 Fluent 会话。

跑法
----
    .venv/Scripts/python.exe scripts/probe_boundary_geometry.py "<网格>" --json

. msh 段落格式（Fluent 网格文件自带）
------------------------------------
  (2 3)                       维度
  (10 (0 1 e46a 1 3))         节点段索引：0xe46a = 58474 个节点
  (12 (0 1 c2a0 0 0))         单元段索引：0xc2a0 = 49824 个单元
  (13 (0 1 26850 0 0))        面段索引：  0x26850 = 157776 个面
  (39 (5 velocity-inlet Inflow))  zone-id / type / name
数据段：
  (10 (1 1 e46a 1 3)\n( x y z x y z ... ))
  (13 (5 1 90 4 0)\n( <n> <n 个十六进制节点号> <c0> <c1> \n ... ))
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _tri_area(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def _is_ascii_mesh(raw: bytes) -> tuple[bool, str]:
    """判断 .msh 是 ASCII 文本还是二进制。

    本脚本靠正则解析文本段，**只对 ASCII 格式有效**。二进制网格必须明确拒绝 ——
    否则会返回一堆空集合、看起来像"这个网格没有边界"，而那是假象。

    （此前该脚本把 `encoding` 写死为 "ascii"，在二进制网格上会给出误导性结果。）
    """
    sample = raw[:200_000]
    if not sample:
        return False, "文件为空"
    # 允许的字节：可打印 ASCII + 制表/换行/回车
    bad = sum(1 for b in sample if b > 126 or (b < 9) or (13 < b < 32))
    if bad / len(sample) > 0.02:
        return False, (
            f"二进制 .msh（抽样 {len(sample)} 字节中有 {bad} 个非文本字节）。"
            "本脚本基于正则解析文本段，**只能处理 ASCII 格式的 .msh**。"
            "二进制网格请改用：连上 Fluent 后读 `list_named_objects` 与 "
            "`mesh_quality` 的 domain_extents，或先把网格导成 ASCII。"
        )
    return True, "ascii"


def parse(path: Path) -> dict:
    raw = path.read_bytes()
    ok, enc = _is_ascii_mesh(raw)
    out: dict = {"file": str(path), "encoding": enc, "source": "offline .msh parse"}
    if not ok:
        out["error"] = enc
        return out

    data = raw.decode("latin-1")

    # ---- 段 39：zone id / type / name（用于交叉核对 01_probe.json 的边界名） ----
    zones = {}
    for m in re.finditer(r"\(39\s*\((\d+)\s+([^)\s]+)\s+([^)\s]+)\)", data):
        zones[int(m.group(1))] = {"type_in_file": m.group(2), "name": m.group(3)}
    out["zones_section39"] = zones

    # ---- 节点段 ----
    m = re.search(r"\(10 \(1 1 ([0-9a-f]+) 1 3\)\r?\n\(", data)
    if not m:
        out["error"] = "找不到节点数据段 (10 (1 1 <hex> 1 3)"
        return out
    n_nodes = int(m.group(1), 16)
    s = m.end()
    e = data.index("))", s)
    toks = data[s:e].split()
    if len(toks) != 3 * n_nodes:
        out["error"] = f"节点数不匹配: 段头 {n_nodes}, 实际 {len(toks)/3}"
        return out
    v = [float(t) for t in toks]
    coords = [None] + [(v[3 * i], v[3 * i + 1], v[3 * i + 2]) for i in range(n_nodes)]
    out["n_nodes"] = n_nodes

    # ---- 逐个面 zone ----
    results = {}
    for zid, zinfo in sorted(zones.items()):
        mm = re.search(r"\(13 \(%d [0-9a-f]+ [0-9a-f]+ ([0-9]+) 0\)\r?\n\(" % zid, data)
        if not mm:
            continue  # 单元 zone（4）不走这里
        s2 = mm.end()
        e2 = data.index("))", s2)
        faces, area, pts = [], 0.0, []
        node_counts: dict[str, int] = {}
        for line in data[s2:e2].split("\n"):
            line = line.strip()
            if not line:
                continue
            t = line.split()
            n = int(t[0])
            ns = [int(x, 16) for x in t[1:1 + n]]
            node_counts[str(n)] = node_counts.get(str(n), 0) + 1
            p = [coords[i] for i in ns]
            pts.extend(p)
            if n == 3:
                area += _tri_area(p[0], p[1], p[2])
            elif n == 4:
                area += _tri_area(p[0], p[1], p[2]) + _tri_area(p[0], p[2], p[3])
            else:
                raise ValueError(f"zone {zid}: 意外节点数 {n}")
            faces.append(ns)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        results[zinfo["name"]] = {
            "zone_id": zid,
            "type_in_file": zinfo["type_in_file"],
            "n_faces": len(faces),
            "nodes_per_face": node_counts,
            "area_mesh_units2": area,
            "bbox_mesh_units": {"x": [min(xs), max(xs)], "y": [min(ys), max(ys)], "z": [min(zs), max(zs)]},
            "span_mesh_units": {"x": max(xs) - min(xs), "y": max(ys) - min(ys), "z": max(zs) - min(zs)},
        }
    out["face_zones"] = results

    # ---- 局部通道间隙：壁面面心到「法向正前方最近的反平行壁面」的距离 ----
    wall = next((v for v in results.values() if v["type_in_file"] == "wall"), None)
    if wall:
        def centroid_normal(ns):
            p = [coords[i] for i in ns]
            c = [sum(q[k] for q in p) / len(p) for k in range(3)]
            if len(p) == 4:
                def nrm(a, b, cc):
                    u = [b[i] - a[i] for i in range(3)]
                    w = [cc[i] - a[i] for i in range(3)]
                    return [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]]
                n1, n2 = nrm(p[0], p[1], p[2]), nrm(p[0], p[2], p[3])
                n = [n1[i] + n2[i] for i in range(3)]
            else:
                u = [p[1][i] - p[0][i] for i in range(3)]
                w = [p[2][i] - p[0][i] for i in range(3)]
                n = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]]
            L = math.sqrt(sum(x * x for x in n)) or 1.0
            return c, [x / L for x in n]

        zid = wall["zone_id"]
        mm = re.search(r"\(13 \(%d [0-9a-f]+ [0-9a-f]+ [0-9]+ 0\)\r?\n\(" % zid, data)
        s3 = mm.end()
        e3 = data.index("))", s3)
        wall_faces = []
        for line in data[s3:e3].split("\n"):
            line = line.strip()
            if not line:
                continue
            t = line.split()
            n = int(t[0])
            wall_faces.append([int(x, 16) for x in t[1:1 + n]])
        fcen = [centroid_normal(ns) for ns in wall_faces]

        cell = 0.06
        grid: dict = {}
        for i, (c, n) in enumerate(fcen):
            grid.setdefault((int(c[0] // cell), int(c[1] // cell), int(c[2] // cell)), []).append(i)

        import random
        random.seed(0)
        step = max(1, len(fcen) // 250)
        gaps = []
        for i in range(0, len(fcen), step):
            ci, ni = fcen[i]
            k = (int(ci[0] // cell), int(ci[1] // cell), int(ci[2] // cell))
            best = None
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    for dz in range(-2, 3):
                        for j in grid.get((k[0] + dx, k[1] + dy, k[2] + dz), ()):
                            if j == i:
                                continue
                            cj, nj = fcen[j]
                            d = [cj[q] - ci[q] for q in range(3)]
                            dist = math.sqrt(sum(x * x for x in d))
                            if dist < 1e-9:
                                continue
                            if sum(d[q] * ni[q] for q in range(3)) / dist < 0.9:
                                continue
                            if sum(ni[q] * nj[q] for q in range(3)) > -0.8:
                                continue
                            if best is None or dist < best:
                                best = dist
            if best:
                gaps.append(best)
        gaps.sort()
        if gaps:
            out["channel_gap_mesh_units"] = {
                "method": "壁面面心沿其法向到最近的反平行壁面的距离（抽样）",
                "n_sampled": len(gaps),
                "min": gaps[0],
                "p10": gaps[len(gaps) // 10],
                "median": gaps[len(gaps) // 2],
                "p90": gaps[9 * len(gaps) // 10],
                "max": gaps[-1],
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="离线解析 .msh 逐边界几何（只读，不启动 Fluent）")
    ap.add_argument("mesh", help="ASCII 格式的 .msh（二进制会被明确拒绝）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = parse(Path(args.mesh))
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    # ★ 出错要返回非 0 —— 否则调用方会以为"跑成功了，只是结果空"
    return 1 if r.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
