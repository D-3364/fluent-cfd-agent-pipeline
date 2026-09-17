"""验证 MCP server 能通过 stdio 正常握手并列出工具。

在让 Claude Code 连接之前先跑这个，可以把"MCP 配置问题"和"agent 逻辑问题"
分开——否则连不上时很难判断是哪一层的锅。

不启动 Fluent，只做协议层握手，几秒出结果。

跑法：
    .venv/Scripts/python.exe scripts/check_mcp.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_env import mcp_env, require_ready, server_exe, utf8_streams  # noqa: E402

utf8_streams()

ROOT = Path(__file__).resolve().parent.parent
SERVER = server_exe()

PROTOCOL_VERSION = "2024-11-05"


def send(proc: subprocess.Popen, msg: dict) -> None:
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()


def read_msg(proc: subprocess.Popen) -> dict | None:
    line = proc.stdout.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"_raw": line.decode(errors="replace")}


def main() -> int:
    if not require_ready():
        return 1

    # 环境以 .mcp.json 为准（setup.py 按本机生成的），真实环境变量可覆盖
    env = mcp_env()

    print(f"启动 {SERVER.name} …")
    proc = subprocess.Popen(
        [str(SERVER), "--transport", "stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )

    try:
        # 1. initialize
        send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "check_mcp", "version": "1.0"},
            },
        })
        resp = read_msg(proc)
        if not resp or "result" not in resp:
            print(f"✗ initialize 失败：{resp}")
            err = proc.stderr.read(2000).decode(errors="replace")
            if err:
                print("stderr:", err[:800])
            return 1

        info = resp["result"].get("serverInfo", {})
        print(f"✓ 握手成功：{info.get('name')} {info.get('version')}")

        # 2. initialized 通知
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # 3. 列出工具
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = read_msg(proc)
        if not resp or "result" not in resp:
            print(f"✗ tools/list 失败：{resp}")
            return 1

        tools = resp["result"].get("tools", [])
        names = sorted(t["name"] for t in tools)
        print(f"✓ 暴露 {len(names)} 个工具\n")

        # 分组展示，便于核对是否齐全
        groups = {
            "会话": {"connect", "disconnect", "session_status", "solver_status"},
            "发现": {"find_api", "get_help", "get_state", "get_targeted_context",
                     "describe_path", "probe_path", "get_allowed_values",
                     "get_active_status", "describe_named_object_template"},
            "命名对象": {"list_named_objects", "find_named_object", "select_named_objects"},
            "执行": {"run_code", "validate_code"},
            "报告": {"summarize_setup", "simulation_report", "screenshot"},
            "网格/场": {"mesh_quality", "list_fields", "compare_files"},
        }
        seen = set()
        for gname, members in groups.items():
            hit = sorted(members & set(names))
            seen |= set(hit)
            if hit:
                print(f"  {gname:8s}: {', '.join(hit)}")
        rest = sorted(set(names) - seen)
        if rest:
            print(f"  {'其他':8s}: {', '.join(rest)}")

        # 4. 抽查几个关键工具是否可调用（只做 schema 检查，不真调）
        print()
        for probe in ("connect", "run_code", "mesh_quality", "list_named_objects"):
            t = next((x for x in tools if x["name"] == probe), None)
            if t:
                props = list((t.get("inputSchema") or {}).get("properties", {}))
                print(f"  ✓ {probe:22s} 参数: {props or '（无）'}")
            else:
                print(f"  ✗ {probe:22s} 不存在！")

        print("\nMCP 链路正常。")
        print("注意：Claude Code 需要重载会话才能加载新的 .mcp.json。")
        return 0

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
