# MCP 工具实况（ansys-fluent-mcp 0.4.0）

给 **cfd-executor** 与 **cfd-spec-author** 用。

内容以 0.4.0 **实际注册的代码**为准，不以包内自带的 `SKILL.md` 为准。实测发现那份
有多处签名与代码不符，有冲突时信这份。

下面的约束（`_FORBIDDEN_CALLS`、`_ALLOWED_IMPORTS` 等）在 AST 层强制，猜错一次就
浪费一轮求解。每条都从 `common/validation.py`、`common/base.py`、
`solve/backends/pyfluent.py` 里读出，不是推测。

---

## 0. ★ MCP 会话启动时是【空的】—— 没有网格

**这是最容易被想当然坑到的一条，实测确认过。**

`connect` 拉起的是一台**全新的 Fluent 进程**，里面**什么都没载入**。
MCP **不暴露 `read_mesh` / `read_case` 工具**，所以连上去之后：

```jsonc
list_named_objects()
// → {"setup/general/units-settings/units": [...]}   ← 只有这一个集合！
//   没有任何 boundary-condition / cell-zone / material / surface 集合

get_state(["setup.boundary_conditions", "setup.cell_zone_conditions", "results.surfaces"])
// → 三者全部 {"inactive": true}

mesh_quality()     // → cell_count / face_count / node_count / domain_extents 全为 null
list_fields()      // → []
summarize_setup()  // → InactiveObjectError
```

**由此推出两条硬规则：**

| 角色 | 后果 |
|---|---|
| **cfd-executor** | 有 `run_code`，**必须自己 `read_mesh`**（`solver.file.read_mesh(file_name=...)`）。别以为主对话已经载好了 |
| **cfd-spec-author** | **没有 `run_code`**，所以**无法通过 MCP 载入网格**，也就**拿不到边界名**这个勘察阶段最关键的交付项 |

### 定规范 agent 的勘察怎么办

`cfd-spec-author` 只有 `Bash` 这一条路：**跑一个独立的 PyFluent 脚本**连同一套 Fluent
读入网格（它 agent 定义里"用 Bash 跑一个短的 PyFluent 脚本"允许这么做）。

注意这条路的性质：

- 它起的是**独立的 Fluent 进程**，与 MCP 那个单例会话**互不干扰**——
  但前提是**先 `disconnect` 掉 MCP 会话**，否则两条会话并存会让人搞不清在看哪一个
- 脚本**必须自己收尾 `solver.exit()`**，别留残留 `fluent.exe`（用 `tasklist` 复核）
- 这条路的读操作结果**无法被 MCP 侧的只读保证约束**（脚本本身有全权限）。
  它仍然是"只读勘察"靠的是**纪律**，不是架构——审查者要留意这一点

> **为什么不做成"给 spec-author 加 `run_code`"**：那会破坏整条流水线的核心设计——
> 定规范的人不应该同时是执行的人。宁可绕道，也不放开这个口子。

### 那 MCP 还能用来干什么

勘察阶段仍然有用，但**只对那些不依赖网格已载入的路径**：

- `find_api` / `get_help` —— 查设置树结构（但注意 §6 的版本错配）
- `describe_path` / `get_allowed_values` —— 对**无网格也存在的**全局路径有效
  （如 `mesh.modify_zones.*`、`solution.methods.*`）；
  对 `setup.boundary_conditions.*` 这类**无网格即 inactive** 的路径会取不到值

**所以：想靠 MCP 单独完成勘察，是走不通的。** 边界名只能从独立脚本或执行 agent 的
会话里得到。

---

## 1. 签名校正表

| 包内 SKILL.md 声称 | **实际注册的签名** |
|---|---|
| `connect(ip?, port?, ...)` | `connect(backend_kind=None, connect_kwargs=None)` |
| `find_api(query, limit?, kind?)` | `find_api(query, top_k=10, kinds=None, under=None, compact=False)` |
| `summarize_setup(scope?)` | `summarize_setup()` — **不接受任何参数** |
| `screenshot(filename?, width?, height?)` | `screenshot(view=None)` |
| `get_state(path, projection?)` | `get_state(paths=None, key=None)` |
| `list_named_objects(path=...)` | **`list_named_objects(limit=None, offset=0)` — 根本没有 `path` 参数** |
| 号称 "22 个工具" | 实际注册 **25** 个（多一个 `manage_fluent`） |

### `list_named_objects` 的正确用法（容易踩）

包内文档写 `list_named_objects(path="setup.boundary_conditions.velocity_inlet")`，
**在 0.4.0 上不成立**。真实签名只有 `limit` / `offset`：

```python
async def list_named_objects(limit=None, offset=0):
    mapping = await self.backend.list_named_objects()   # 无 path
```

它**一次返回全部**命名对象集合的映射，由调用方自己筛。实测返回（截取）：

```jsonc
{
  "setup/boundary-conditions/wall": ["wall-light", "wall-right", "wall-up", "wall-down"],
  "setup/cell-zone-conditions/fluid": ["fluid"],
  "setup/materials/fluid": ["air"],
  "solution/monitor/residual/equations": ["continuity", "x-velocity", "y-velocity", "k", "omega"],
  "results/surfaces/zone-surface": ["wall-down", "wall-up", "wall-right", "wall-light", "interior-fluid"]
}
```

`limit`/`offset` 是**对每个集合分别切片**的，并在结果里附一个 `_pagination`
信封报告原始总数。**不加参数就返回全集，勘察阶段直接用这个。**

### ★ 两套路径写法，混了就废

这是最容易出错的地方：

| 场景 | 写法 | 例 |
|---|---|---|
| **MCP 工具**返回/接受的路径 | **斜杠 `-` 连字符** | `setup/boundary-conditions/wall` |
| **`run_code` 里的 PyFluent API** | **点号 + 下划线** | `solver.setup.boundary_conditions.wall` |

**`list_named_objects` 给你的路径不能直接喂给 PyFluent。** 拿到的名字要这样用：

```python
# 从 list_named_objects 得到 "setup/boundary-conditions/wall" → ["wall-up", ...]
# 在 run_code 里要翻译成点号形式：
solver.setup.boundary_conditions.wall["wall-up"]
```

而 `find_api` / `get_help` / `describe_path` 这些**发现类工具用的是点号**形式
（`setup.boundary_conditions.velocity_inlet`）。所以：

- **发现路径** → 点号（`find_api`、`describe_path`）
- **`list_named_objects` 的返回** → 斜杠（只用于阅读，用前要翻译）

---

## `mesh_quality` 是超值工具

实测它**一次返回结构化数据和全部规模信息**，比手写 PyFluent 强得多：

```jsonc
{
  "connected": true,
  "cell_count": 3750,          // ← 原始 PyFluent API 拿不到这个
  "face_count": 7625,
  "node_count": 3876,
  "quality": {
    "min_orthogonal_quality": 0.998156,
    "max_ortho_skew": null,     // ← Fluent 2024+ 常态，null ≠ 合格
    "max_aspect_ratio": 1.46674
  },
  "check": {
    "domain_extents": {"x": [0.0, 1.5], "y": [0.0, 1.0], "z": null},
    "volume_min": 0.0003906084,   // > 0 即无负体积
    "volume_total": 1.5,
    "errors": [], "warnings": [], "raw": "..."
  }
}
```

**三个用途**：

1. `cell_count` 直接可判 Student 上限，**不必再手写探测**
2. `volume_min > 0` 即通过负体积检查
3. `domain_extents` 给出**域尺寸** —— 这是物理核验的关键输入
   （库埃特流的板间距 h 就是这么来的）

> ⚠️ `max_ortho_skew` 实测**返回 `null`**（Fluent 26.1）。这不是读失败——
> 正交质量 ≈ 1 − 正交偏斜，两者冗余，Fluent 只打印其中一个。
> **但 `null` 绝不等同于合格**，审查时要按"未知"处理，以
> `min_orthogonal_quality` 为主判据。

**最容易踩的坑**：`connect` 的启动参数不是平铺的，全部塞进 `connect_kwargs` 字典：

```python
# ✅ 正确
connect(connect_kwargs={"product_version": "26.1", "ui_mode": "no_gui"})

# ❌ 错误 —— 这样传参数会被当成 backend_kind
connect(product_version="26.1")
```

## 2. 未被文档列出但很有用的工具

`probe_path` · `get_active_status` · `get_allowed_values` · `describe_path`
· `describe_named_object_template` · 以及 `mesh_quality` / `list_fields` / `compare_files`

其中 **`describe_path` 性价比最高**：

```python
describe_path(paths=["setup.boundary_conditions"],
              include_template=True, include_command_arguments=True)
```

一次调用同时返回 active 状态、当前值、允许值、对象模板。替代 `probe_path` +
`get_allowed_values` + `describe_named_object_template` 四次往返。**勘察阶段优先用它。**

它的轻量兄弟 `get_targeted_context` 签名是：

```python
get_targeted_context(paths_to_check=[...],      # 必填，空列表会报错
                     named_object_types=None,
                     instance_state_fetch=None)
```

---

## 3. `connect_kwargs` 全部可用参数

以下为 `PyFluentBackend.connect` 的 keyword-only 参数（**不是** MCP 层参数）：

```
ip, port, password, server_info_file,          # 附着到已有会话
precision="double", processor_count=1, ui_mode="gui", product_version=None,
dimension=None, mode=None, gpu=None,
journal_file_names, case_file_name, case_data_file_name,
cwd, fluent_path, env,
graphics_driver, scheduler_options, start_timeout, cleanup_on_exit, additional_arguments
```

### 新建 vs 附着的判定（`_do_connect` 的真实逻辑）

```python
if server_info_file or (ip and port):
    → 附着
else:
    → 无条件 launch_fluent
```

**没有"探测本机已有会话并复用"这回事。** 只要不给 `ip`+`port` 或不给
`server_info_file`，就一定新起一个进程。这就是为什么三个 agent 必须串行、
且每次收尾要 `disconnect`。

### ★ `processor_count` 默认 1 是坑 —— Student 许可允许 4 核

上面的参数表里 `processor_count=1` 是 **PyFluent 的默认值**，不是推荐值。

**ANSYS Student 允许 4 核**（见 `student-limits.md`）。用 1 核 =
**白扔 4 倍速度**。

一次真实运行实测：5 万单元、单核、3000 步 = **45 分钟**。
同样的算例四核会快得多。

```python
connect(connect_kwargs={
    "product_version": "26.1",
    "ui_mode": "no_gui",
    "precision": "double",
    "processor_count": 4,        # ★ 用满 Student 允许的 4 核
    "cwd": r"C:\fluent-scratch\<run-id>",
})
```

**本项目脚本默认已改为 4**，可用环境变量覆盖：

```bash
set CFD_PROCESSOR_COUNT=8      # 非 Student 许可可以调大
```

> 注意：核数上限是**许可**约束。超过许可是硬失败，不是"慢一点"。
> 见 `student-limits.md`。

### 取值约束（传错会 `invalid_launch_arguments`）

- `dimension` — 接受 `2 / 3 / "2d" / "3d" / "2" / "3"`；**传布尔值会被拒**
- `mode` — 合法值 `solver` / `meshing` / `solver_aero` / `solver_icing` / `pre_post`
  （别名 `prepost` `post` → `pre_post`，`aero` → `solver_aero`）
- `ui_mode` — 合法值 `gui` / `hidden_gui` / `no_gui` / `no_graphics` / `no_gui_or_graphics`
  **无头运行用 `no_gui`**（默认是 `gui`，会弹窗）
- `gpu` — `gpu` 与 `dimension=2` 组合会被拒（Fluent 不允许 2D 用 `-gpu`）；
  `gpu` 与 `mode="meshing"` 组合也会被拒
- `precision` — `"double"`（默认）或 `"single"`

### 两个不存在的参数

- **`start_transcript` 不是用户参数** —— 在启动候选里被硬编码为 `True`，
  因为 `mesh_quality` / `mesh_check` 的 stdout 解析、transcript 尾部读取、
  发散诊断都依赖它。不要试图关掉。
- **`lightweight_setup` 不是启动参数** —— 它只在 `read_case` 时生效：
  `session.settings.file.read_case(file_name=..., lightweight_setup=True)`。
  包内注释明确写了"lightweight 在 read_case 时应用，不在启动时"。

### ★ `cwd` 应当【每次都传】—— 别只把它当 ASCII 绕行手段

Fluent 会往**进程的当前工作目录**写 `.trn` 临时文件。而 MCP server 的 cwd 就是
**项目根**，所以**不传 `cwd` 的话，每次 `connect` 都会在仓库根目录拉下一坨
`fluent-<时间戳>-<pid>.trn`**。

它们在 `.gitignore` 里（所以 `git status` 是干净的，不易察觉），
但会持续堆积、且没有任何归处。

```python
connect(connect_kwargs={
    "product_version": "26.1",
    "ui_mode": "no_gui",
    "cwd": r"C:\fluent-scratch\<run-id>",   # ★ 每次都传
})
```

**`cwd` 容易漏传**，因为本文件别处（§9）只把它当作**非 ASCII 路径的绕行手段**
介绍，读起来像是"路径没问题就不用管它"。实际它是常规做法：Fluent 往**进程的当前
工作目录**写 `.trn`，而 MCP server 的 cwd 就是项目根，不传就会在仓库根不断堆积。
那些文件被 gitignore，`git status` 干净，不易察觉。

---

## 4. Fluent 安装路径的发现

**MCP 包本身不做任何路径发现。** 全包 grep 不到 `AWP_ROOT` 的读取、也没有对
`Program Files/ANSYS Inc/*/v*/fluent` 的扫描。唯一出现 `AWP_ROOT` 的地方是
`common/session_logging.py`，作用仅仅是把这些前缀**记进调试日志**。

发现逻辑 100% 委托给 `ansys-fluent-core`，其优先级为：

1. `fluent_path=` 显式指定 ← **最可靠，本机就用这个**
2. `product_version=` → 按版本号推导
3. 环境变量 `PYFLUENT_FLUENT_ROOT`
4. 扫描 `AWP_ROOT<版本号>` 环境变量，取最新

> ⚠️ **`fluent_path` 不是"Fluent 根目录"** —— 这是踩过的坑。
> `process_launch_string.py:162-166` 明确写着该参数**原样返回**：
>
> ```python
> fluent_path = launch_argvals.get("fluent_path")
> if fluent_path:
>     # Return the fluent_path string verbatim.
>     return fluent_path
> ```
>
> 传目录（`...\v261\fluent`）会让 PyFluent 把目录当可执行文件调用，报
> `FileNotFoundError: [WinError 2]`。要传就传**完整 exe 路径**。

**本机情况**：`AWP_ROOT261` 由安装程序设好。实测三种方式都能正确解析到可执行文件：

| 方式 | 解析结果 | 结论 |
|---|---|---|
| **什么都不传**，靠 `.mcp.json` 里的 `PYFLUENT_FLUENT_ROOT` | `...\v261\fluent\ntbin\win64\fluent.exe` | ✅ **推荐 —— 唯一不绑版本的写法** |
| `product_version="26.1"` | 同上 | ⚠️ 能用，但**把版本号写死了**，换机器就错 |
| `fluent_path=...\Fluent.exe` | 原样返回 | ⚠️ 可以，但要写全路径 |

### ★ 标准写法：不传版本参数

`.mcp.json` 的 `env` 段里已经有 `PYFLUENT_FLUENT_ROOT` —— 那是 `scripts/setup.py`
**按本机实际情况检测出来的**。所以直接省掉版本参数即可：

```python
connect(connect_kwargs={
    "ui_mode": "no_gui",       # 无头运行，默认是 gui 会弹窗
    "precision": "double",
    "dimension": 2,            # 必须与网格维度一致，否则 read_mesh 直接失败
})
```

> **为什么不写 `product_version`**：它是**版本号硬编码**，换一台装了别的版本的机器
> 就会解析失败（报 `invalid_launch_arguments`）。而 `PYFLUENT_FLUENT_ROOT` 由
> `setup.py` 生成，天然跟着机器走。
>
> 只有自动解析失败时，才用环境变量 `CFD_PRODUCT_VERSION` 显式指定。
>
> ⚠️ 别处出现的「26.1」大多是**实测记录**（"这个枚举在 26.1 上不存在"之类），
> 那是历史事实、不是配置项，**不要批量替换**。

> 优先级：`fluent_path`（原样）> `product_version` > `PYFLUENT_FLUENT_ROOT` > `AWP_ROOT*` 取最新。
>
> 注意变量名是 `PYFLUENT_FLUENT_ROOT`，**`PYFLUENT_PRODUCT_ROOT` 不存在**。
> 该变量期望的是 `fluent` **目录**（PyFluent 会自己补 `ntbin/win64/fluent.exe`）——
> 和 `fluent_path` 的语义**正好相反**，别搞混。
> 要在 MCP server 进程启动前设环境变量：PyFluent 在 import 时固化环境，所以必须在
> `.mcp.json` 的 `env` 段里给，而不是在 `run_code` 里改。

---

## 5. `run_code` 沙箱边界 ⚠️ 最重要的一节

### 5.1 注入的命名空间

```python
{"solver": solver, "session": solver, "__builtins__": safe_builtins}
```

**`solver` 和 `session` 是同一个对象**，两个名字随便用。

最后一个表达式会被自动 `repr`（Jupyter 风格），也可以用 `__return__` 显式覆盖。
**要把数据取回给 agent，就用 `__return__`。**

### 5.2 禁止调用的名字（AST 层拦截）

```
os.system, subprocess.Popen, subprocess.call, subprocess.run,
subprocess.check_call, subprocess.check_output,
shutil.rmtree, shutil.move, shutil.copy, shutil.copytree,
eval, exec, compile, __import__, globals, locals, vars,
open, input, exit, quit, breakpoint, help
```

注意 **`open` 在里面**。所以"在沙箱里写个 JSON 文件"这条路是堵死的。

### 5.3 允许导入的模块（白名单，其余一律 ImportError）

```
math, json, itertools, functools, collections, dataclasses,
typing, ansys.fluent.core, ansys, pyfluent
```

**`os` 不在里面。** 相对导入也会在运行时被拒。

### 5.4 允许的内置函数

```
int float str bool list tuple dict set frozenset bytes bytearray complex
range len enumerate zip map filter sorted reversed min max sum any all
abs round isinstance issubclass hasattr getattr setattr type repr print dir
Exception BaseException ValueError TypeError KeyError IndexError AttributeError
RuntimeError LookupError ArithmeticError ZeroDivisionError StopIteration
NotImplementedError AssertionError True False None
```

**没有 `open`。** 即使绕过 AST 校验也到不了 `os`/`open`。

### 5.5 另外三条硬规则

| 规则 | 说明 |
|---|---|
| **TUI 绝对禁止** | 任何 `solver.tui.*`、`execute_tui`、`getattr(..., "tui")` 一律报 `tui_not_allowed`，且该判定**优先于** `forbidden_call` |
| **反射写入禁止** | `setattr(...)` / `delattr(...)` / `__setitem__` / `__setattr__` 等一律拒。必须用直接属性赋值 `solver.x.y = value` 或 `.set_state(...)` |
| **严格名字检查** | 任何处于 Load 上下文的 `ast.Name` 若未绑定，报 `forbidden_name`。绑定来源包括赋值、推导式、`for` 目标、`with as`、lambda 参数、`except as`、函数/类定义 |

**推论**：沙箱代码必须是自包含的。不能在两次 `run_code` 之间靠"上次定义的变量还在"
——每次调用都是全新命名空间。跨调用的状态只能存在**求解器会话本身**上。

### 5.6 那执行 agent 到底能干什么？—— 能干的很多

| 能力 | 判定 |
|---|---|
| `solver.solution.run_calculation.iterate(iter_count=N)` | ✅ 可以 |
| 设置边界条件 `solver.setup.boundary_conditions.velocity_inlet["in"].momentum.velocity = ...` | ✅ 可以 |
| 创建 report definition | ✅ 可以 |
| 导出数据 / 写 case 和 data 文件 `solver.file.write_case(...)` / `write_data(...)` | ✅ 可以 |
| `print` / `dir` / `getattr` / 推导式 | ✅ 可以 |
| `open()` / `os` / `subprocess` / `eval` / `exec` | ❌ AST + 运行时双重拦截 |
| `.tui.*` | ❌ 硬禁 |

### 5.7 因此：落盘必须分两路

```
求解器产物（.cas/.dat/.trn） → solver.file.write_case() / write_data()   ← 沙箱内可做
结构化数据（残差、监测量、汇总） → __return__ 返回 → agent 用 Write 工具写盘  ← 沙箱外做
```

**这条必须写进执行 agent 的提示词。** 否则它会反复生成 `open()` 写 JSON 的代码，
每次都被拒，空转好几轮。

### 5.8 错误码速查

`syntax_error` · `forbidden_import` · `forbidden_name` · `forbidden_call` ·
`tui_not_allowed` · `risk_blocked` · `sequence_error` · `solver_disconnected` ·
`execution_error` · `invalid_arguments`

其中 **`solver_disconnected` 需要特别警惕**——它意味着 Fluent 进程已经死了，
后续所有调用都会失败，必须重新 `connect`。

---

## 6. `validate_code` 的已知版本错配 ⚠️

`validate_code` 会同时做两件事：

1. `validate_python_source(code, strict=True)` —— 纯 AST 检查
2. 拿**随包附带的设置表**做语义校验，命中不了就报 `unknown_settings_path`

问题在于：**随包附带的是 Fluent 27.1 的设置表**（`solve/data/settings_271.json.gz`），
而本机运行的是 **26.1**。

**处置原则**：`validate_code` 报 `unknown_settings_path`，但 `find_api` 或
`describe_path` 能查到该路径时，**以实际运行环境为准，不要盲信校验器**。
在 26.1 上做 27.1 的校验，误报是预期内的。

（可用 `FLUIDS_MCP_STRICT_VALIDATION=1` 把近似匹配从 warning 升级为 error，
本机**不要**开，会误伤。）

---

## 7. 会话是单例 —— 多 agent 的头号陷阱

MCP server 是**单进程、单 `_active_kind`、单 `_solver`**，所有求解器调用被一把
`asyncio.Lock` 串行化。`connect` 只在 `status == "ok"` 时才写入 `self._active_kind`。

**后果**：第二个 agent 调 `connect` 会拆掉并替换前一个会话，**且不报错**。
第一个 agent 后续的调用会以 `solver_disconnected` 告终，排查起来很费劲。

**纪律**（SKILL.md 里是硬规则）：

```
spec-author:  connect → 只读勘察 → 写 01_probe.json → disconnect
              ← 人工关卡 →
executor:     connect → 执行 → 落盘 → disconnect
reviewer:     不连 MCP，直接读盘
```

每个 agent 的收尾必须显式 `disconnect`，不能靠进程退出兜底。

---

## 8. 随包附带了什么工程知识 —— 几乎没有

这一点对设计很关键，避免重复造轮子或错误假设。

| 内容 | 有无 |
|---|---|
| 设置树 schema + 帮助文本（1167 个节点） | ✅ 有（27.1 版） |
| BM25 检索，自然语言 → 设置路径（`find_api` 靠它） | ✅ 有 |
| 单位换算表（`solve/lib/units.py`，含 `quantity_hints`） | ✅ 有 |
| 网格质量报告解析（`mesh_report_parsers.py`） | ✅ 有 |
| **CFD 工程指征**：y+ 该取多少、URF 该设多少、什么算收敛 | ❌ **完全没有** |
| 材料属性库、经验关联式（`compute_htc` 之类） | ❌ 明确说明"不在本层" |

包里 `domain_tools.py` 有一句原话：工程关联式工具"完全属于可选的上层 agent 层，
公开的 MCP leaf 不暴露它们。**不要往这里加。**"

**所以 `review-criteria.md` 的阈值必须我们自己写，而且要标官方出处。**

### 一个解析器的坑（`mesh_report_parsers.py`）

Fluent 2024 以后，`mesh.quality` 经常**只**打印 `Minimum Orthogonal Quality` 和
`Maximum Aspect Ratio`，**`Maximum Ortho Skew` 常常不出现**——因为正交质量 ≈ 1 −
正交偏斜，两者冗余。

解析器是**故意宽松**的：取不到的字段返回 `None`，而 **`None` 绝不等于"合格"**。
审查时遇到 `None` 必须当作"未知"，并以 `min_orthogonal_quality` 作为主判据。

### 那么"怎么驱动一次求解"的知识在哪

**不在包里，在 `solver-api-26.1.md`。**

上面的内容讲的全是「怎么跟 MCP server 说话」。而「怎么驱动一次瞬态求解」——
`dual_time_iterate` 签名、字段改名、导出关键字、能量账的参考温度 —— 在那里。

它是一份版本锁定的实测事实表（Fluent 26.1）。**executor 开工前必读**，
否则会重演一次真实运行里那 250 次 tool call 的现场考古。

---

## 9. ★ 非 ASCII 路径会让 Fluent 崩，而报错指向错误方向

**这个坑的症状与真实原因最脱节。**

路径里只要有中文字符（实测触发者是 `副本`），Fluent 就起不来，报：

```
utf-8 can't decode byte 0xb8 ...
```

**这条报错看起来像网格文件损坏** —— 于是排查时间会全部花在检查网格上，
而真正的问题在目录名。实测确认：两者的联系极弱，极易误判。

### ★ 不同路径角色的症状**不一样** —— 不要合并成一句话

三项隔离对照实测（C 盘 ASCII ✓ / D 盘 ASCII ✓ / CJK ✗，排除盘符与会话因素）：

| 路径角色 | 症状 |
|---|---|
| **项目根 / cwd** | Fluent **直接崩**：`utf-8 can't decode byte 0xb8` —— **看起来像网格文件损坏** |
| **case 文件路径** | **不崩**：`Error: File "..." not found!` —— **文件明明存在**，看着像路径敲错了 |
| 输出目录 | 推断同上（未单独确证） |

**两种症状都指向错误的方向，但错法不同**：前者让你去查网格，后者让你去查路径有没有写错。
把两者混为一谈，告警本身也会变成误导 —— 所以 `_mcp_env.py` 里按角色分别措辞。

> ⚠️ **纯 Python 的文件读写不受影响** —— `probe_mesh.py` 读网格头、`preflight.py`
> 读配置，在中文路径下都正常。**只有把路径交给 Fluent 才出问题。**
> 所以不要把"中文路径"当成全局禁令，要针对性地看这三个。

### 已经做了的防护

`preflight.py` 和 `setup.py` 都会在启动 Fluent **之前**查这三个路径，命中就
报警并给出正确诊断。`preflight.py` 的结果落进 `00_env.json` 的 `paths_ascii` 段。

`setup.py --check` 里这条**计入不通过**（`ok = False`）——因为接线全绿也不代表
能跑，路径含中文时照样崩。

### 绕行办法

把工作目录或文件挪到纯 ASCII 路径。实测可用的落点：

```
C:\fluent-scratch\
```

> ⚠️ **不要用 `%TEMP%` 代替。** 它在多数机器上看着是 ASCII，但**用户名含非 ASCII 时
> 它本身就带 CJK** —— 例如 `C:\Users\张三\AppData\Local\Temp`。
> 那正好会踩中这个坑，而且因为"看起来是标准临时目录"更容易被忽略。
>
> 判据是**最终展开后的完整路径**是否为纯 ASCII，不是"它是不是系统标准目录"。
> `preflight.py` 查的就是展开后的完整路径。

> 注意：**改目录名不总是可行**（比如仓库已经在某个中文目录下）。此时至少保证
> **Fluent 的启动目录**是 ASCII —— 即 `connect_kwargs` 里传 `cwd`，
> 指向一个纯 ASCII 的临时目录。

### 给 agent 的提醒

执行 agent 若遇到 `utf-8 can't decode` / `UnicodeDecodeError` 类报错，
**先查路径有没有非 ASCII，再去怀疑网格**。这个顺序反了会浪费大量时间。
