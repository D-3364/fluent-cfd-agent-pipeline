"""全链路测试：通过 MCP server 驱动一次真实的只读勘察。

验证的是 MCP **层**能不能干实事——不只是协议握手，而是真的拉起 Fluent、
读网格、列出边界、查网格质量。这是三个 agent 干活时走的那条路。

比 check_mcp.py 重（要启动 Fluent，约 30-60 秒），但覆盖了真实调用路径。

跑法：
    .venv/Scripts/python.exe scripts/e2e_mcp.py
    .venv/Scripts/python.exe scripts/e2e_mcp.py --case "<其他网格>"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_env import mcp_env, require_ready, server_exe, utf8_streams  # noqa: E402

utf8_streams()

ROOT = Path(__file__).resolve().parent.parent

# Student 许可允许 4 核（见 references/student-limits.md）。默认用满 4 核 ——
# 单核会让求解慢 4 倍，而这是**白送的**。非 Student 许可可用环境变量调大。
CPU_COUNT = int(os.environ.get("CFD_PROCESSOR_COUNT", "4"))
SERVER = server_exe()
# 本项目【不附带】算例文件 —— 网格由使用者自己提供。
# 用 --case 指定，或设环境变量 CFD_DEFAULT_CASE。
DEFAULT_CASE = os.environ.get("CFD_DEFAULT_CASE")

_next_id = [0]


class MCP:
    def __init__(self) -> None:
        if not require_ready():
            raise SystemExit(1)
        # 环境以 .mcp.json 为准（setup.py 按本机生成的），真实环境变量可覆盖
        env = mcp_env()
        self.proc = subprocess.Popen(
            [str(SERVER), "--transport", "stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, encoding="utf-8", bufsize=1,
        )
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "e2e", "version": "1.0"},
        })
        self._notify("notifications/initialized", {})

    def _send(self, obj: dict) -> None:
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _rpc(self, method: str, params: dict):
        _next_id[0] += 1
        rid = _next_id[0]
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"{method}: 服务器关闭了连接")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result")

    def call(self, name: str, **args):
        res = self._rpc("tools/call", {"name": name, "arguments": args})
        out = []
        for item in res.get("content", []):
            if item.get("type") == "text":
                out.append(item["text"])
        text = "\n".join(out)
        if res.get("isError"):
            raise RuntimeError(f"{name} 报错：{text[:400]}")
        return text

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def show(label: str, text: str, limit: int = 700) -> None:
    body = text if len(text) <= limit else text[:limit] + f"\n   …（共 {len(text)} 字符）"
    print(f"  {label}\n    " + body.replace("\n", "\n    "))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=DEFAULT_CASE,
                    help="网格文件（或用环境变量 CFD_DEFAULT_CASE）")
    ap.add_argument("--keep", action="store_true", help="测完不断开 Fluent")
    args = ap.parse_args()

    if not args.case:
        print("✗ 必须给 --case <网格文件>（或设环境变量 CFD_DEFAULT_CASE）")
        print()
        print("  本项目不附带算例文件 —— 网格由你自己提供。")
        print("  任何 Fluent 能读的网格都行（.msh / .cas.h5）。")
        return 1

    sys.path.insert(0, str(ROOT / "scripts"))
    from probe_mesh import probe

    info = probe(args.case)
    dim = info.get("dimension") or 2
    print(f"目标：{args.case}")
    print(f"      离线探测 → {dim}D, {info.get('cells'):,} 单元\n")

    mcp = MCP()
    try:
        print("[1] session_status（连接前）")
        show("→", mcp.call("session_status"), 300)

        print(f"\n[2] connect（{dim}D，无头）")
        show("→", mcp.call("connect", connect_kwargs={
            "product_version": "26.1",
            "dimension": dim,
            "ui_mode": "no_gui",
            "precision": "double",
            "processor_count": CPU_COUNT,
        }), 400)

        print("\n[3] 读网格（通过 run_code）")
        show("→", mcp.call("run_code",
                           code=f"solver.file.read_mesh(file_name=r'{args.case}')"), 400)

        print("\n[4] list_named_objects —— 无 path 参数，一次返回全部集合")
        show("→", mcp.call("list_named_objects"), 1200)

        print("\n[5] mesh_quality")
        show("→", mcp.call("mesh_quality", include_check=True), 1200)

        print("\n[6] summarize_setup")
        show("→", mcp.call("summarize_setup"), 900)

        print("\n[7] run_code 的返回值机制（__return__）")
        show("→", mcp.call("run_code", code=(
            "__return__ = {'viscous': solver.setup.models.viscous.model.get_state(),"
            " 'energy': solver.setup.models.energy.enabled.get_state()}"
        )), 300)

        if not args.keep:
            print("\n[8] disconnect")
            show("→", mcp.call("disconnect"), 200)

        print("\n" + "=" * 60)
        print("全链路测试通过 —— MCP 层可以真实驱动 Fluent")
        print("=" * 60)
        return 0

    except Exception as e:  # noqa: BLE001
        print(f"\n✗ 失败：{type(e).__name__}: {e}")
        try:
            err = mcp.proc.stderr.read()
            if err:
                print("\nserver stderr:", err[-1500:])
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        mcp.close()


if __name__ == "__main__":
    sys.exit(main())
