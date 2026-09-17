# 安装与迁移

把项目接到一台机器上，或者从一台机器搬到另一台。

新机器上最快的一条路：

```bash
.venv/Scripts/python.exe scripts/setup.py --create-venv
```

它会建 venv、装依赖、检测本机 Fluent、生成 `.mcp.json`、并校验接线。跑完重载
Claude Code 会话。

## 前置条件

- **Windows**。脚本使用 `.venv/Scripts/`，其他平台需要适配（见文末「平台限制」）。
- **Python 3.12+**。
- **ANSYS Fluent，且许可证可用**。本项目在 ANSYS Student 2026 R1 上验证过。
  确认方式：手工启动一次 Fluent，能进界面即可。
- Claude Code。

Fluent 装在哪个版本不重要，只要 `setup.py` 能检测到。检测不到就用 `--fluent-root`
显式指定。

## 从零装

```bash
cd <项目目录>

# 建 venv 并装 MCP server（几百 MB，只需一次）
python -m venv .venv
.venv/Scripts/python.exe -m pip install ansys-fluent-mcp

# 检测 Fluent、写 .mcp.json、校验
.venv/Scripts/python.exe scripts/setup.py

# 然后重载 Claude Code 会话，用 /mcp 确认 ansys-fluent-mcp 已连接
```

前两步也可以合成一条 `setup.py --create-venv`。

重载会话这一步不能省。`.mcp.json` 只在会话启动时加载，不重载的话 MCP 工具不会出现。

## 换机器或换目录

把目录拷过去后重跑一次 `setup.py`，然后重载会话。

```bash
.venv/Scripts/python.exe scripts/setup.py
```

原因是 `.mcp.json` 里有两处绝对路径，换机器或换目录就会失效：

```jsonc
{
  "command": "C:\\Users\\<你>\\Desktop\\fluent-cfd-agent-pipeline\\.venv\\Scripts\\ansys-fluent-mcp.exe",
  //         ^^^ 换目录就失效
  "env": { "PYFLUENT_FLUENT_ROOT": "D:\\Program Files\\ANSYS Inc\\ANSYS Student\\v261\\fluent" }
  //                                ^^^ 换机器就失效
}
```

症状不好认：MCP server 起不来时，Claude Code 里看到的是「工具全都不见了」，
不一定报错。此时先跑 `setup.py --check`，它直接指出断在哪一环。

### 拷贝时哪些不用带

| 目录/文件 | 要不要拷 | 说明 |
|---|---|---|
| `.venv/` | 不用 | 420 MB，跨机器常有 ABI 问题。新机器用 `--create-venv` 重建 |
| `.mcp.json` | 不用 | 机器专属，且已 gitignore。新机器跑 `setup.py` 生成 |
| `runs/` | 不用 | 运行产物，已 gitignore |
| 网格文件 | 单独拷 | 本项目不包含算例 |
| 其余 | 要 | 含 `.claude/`、`scripts/`、`.mcp.json.template` |

### `.mcp.json` 为什么不入库

仓库里提交的是 `.mcp.json.template`，真正的 `.mcp.json` 由 `setup.py` 生成、被
`.gitignore` 排除。里面的路径是机器专属的，提交进仓库换台机器就失效，还会泄漏
本机目录结构。

所以全新 clone 之后没有 `.mcp.json` 是正常的，此时 MCP 工具不可见，跑一次
`setup.py` 就有了。若文件里还留着模板占位符（`<REPO_ROOT>`、`<FLUENT_ROOT>`），
MCP server 会起不来，`setup.py --check` 会报出来。

## 校验接线

```bash
.venv/Scripts/python.exe scripts/setup.py --check    # 不写文件，纯检查
```

逐项检查 venv、`ansys-fluent-mcp`、`.mcp.json` 的路径、Fluent 可执行文件。

再往下可以分层验证，从轻到重：

```bash
.venv/Scripts/python.exe scripts/preflight.py --case "<网格>"  # 毫秒级，环境快照
.venv/Scripts/python.exe scripts/probe_mesh.py "<网格>"        # 毫秒级，离线探网格
.venv/Scripts/python.exe scripts/check_mcp.py                  # 几秒，MCP 协议层
.venv/Scripts/python.exe scripts/smoke_test.py --case "<网格>"  # 约 30 秒，真拉 Fluent
.venv/Scripts/python.exe scripts/e2e_mcp.py --case "<网格>"     # 约 60 秒，MCP 全链路
```

分层是为了出问题时能立刻定位是哪一层。比如 `smoke_test.py` 过了但 `e2e_mcp.py`
没过，问题就在 MCP 配置而不在 Fluent。

### 这些脚本全过，仍有可能用不了

它们查的都是磁盘上的配置。而「当前会话有没有加载 `.mcp.json`」是会话启动时决定的，
任何脚本都查不出来。

所以会出现 `setup.py --check` 报接线正常、但会话里一个 MCP 工具都调不到的情况。

判据是在会话里调一次 `session_status`：

- 工具本身不存在：会话旧了，重载会话。跑脚本没用。
- 工具存在但返回错误：这才是上面那些脚本的用武之地。

`/fluent-run` 开工时会先做这个判断。

## 排查

| 症状 | 多半是 | 怎么办 |
|---|---|---|
| MCP 工具全不见（刚 clone） | 没有 `.mcp.json`，这是全新状态的正常现象 | 跑 `setup.py --create-venv`，然后重载会话 |
| MCP 工具全不见（拷了整目录） | `.mcp.json` 里是旧机器的路径 | 跑 `setup.py`，然后重载会话 |
| `connect` 报 `invalid_launch_arguments` | 版本号写死得不对 | 见下节 |
| `connect` 报 `FileNotFoundError WinError 2` | 传了 `fluent_path` 且传的是目录 | 不要传 `fluent_path`，见 [mcp-tool-truths.md](.claude/skills/fluent-cfd-pipeline/references/mcp-tool-truths.md) |
| `smoke_test.py` 起不来 Fluent | 许可证、安全软件、或 Fluent 位置 | 跑 `setup.py --check`，再手工启动一次 Fluent |
| 中文输出乱码 | Windows 控制台是 GBK | 脚本已内置 UTF-8 重配置；仍乱码就 `set PYTHONIOENCODING=utf-8` |
| Fluent 报 `utf-8 can't decode byte 0xb8` | 路径里有中文，不是网格损坏 | 把工作目录挪到纯 ASCII 路径（如 `C:\fluent-scratch\`）。`setup.py --check` 会提前报出来 |

### 版本号

`setup.py` 检测到 Fluent 后会打印版本。本项目在 26.1 上验证过。

跑流水线时不需要指定版本，让 PyFluent 走 `.mcp.json` 里的 `PYFLUENT_FLUENT_ROOT`
自动解析即可，这是唯一不绑版本的写法。只有在自动解析失败时才显式指定：

```bash
set CFD_PRODUCT_VERSION=26.1     # cmd
export CFD_PRODUCT_VERSION=26.1  # bash
```

文档里大量出现的「26.1」是实测记录（例如「这个枚举在 26.1 上不存在」），
不是配置项，不要批量替换。

## setup.py 的行为

它只改 `.mcp.json` 一个文件，不动任何文档、skill/agent 定义、或 `runs/` 下的产物。
重跑是幂等的。

```bash
setup.py                                      # 检测 + 写 .mcp.json + 校验
setup.py --check                              # 只校验
setup.py --create-venv                        # 缺 venv 时顺带建
setup.py --fluent-root "D:\...\v261\fluent"   # 手动指定 Fluent 根
```

## 平台限制

目前只在 Windows 上验证过。搬到 Linux/macOS 需要一轮适配：

- 脚本用 `.venv/Scripts/`（Windows 布局），Linux/macOS 是 `.venv/bin/`。
  `setup.py` 与 `_mcp_env.py` 已用 `os.name` 判断，其余脚本还没有。
- `.claude/settings.json` 的权限规则里写了 `.venv/Scripts/python.exe`，换平台后
  这些规则失效，每个 Bash 调用都会弹确认。
- `tasklist` 等命令是 Windows 专有。
