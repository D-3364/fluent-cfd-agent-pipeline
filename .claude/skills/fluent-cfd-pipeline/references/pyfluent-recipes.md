# PyFluent 代码配方

给 **cfd-executor** 用的预置片段。优先照抄，改之前先用 `find_api` / `describe_path` 确认。

> 全部片段都满足 `run_code` 沙箱约束：不用 `open` / `os` / `subprocess` / `.tui.*` / `setattr`。
> 每个片段都是**自包含**的——沙箱每次调用都是全新命名空间，跨调用不能靠变量残留。

---

## 路径根（只有五个）

```
setup.  |  solution.  |  results.  |  file.  |  mesh.
```

**`setup.solution.*` 不存在。** 这是最常见的臆造路径。

---

## 读：勘察常用

### 列边界条件（**定规范前必做**）

⚠️ **实测坑**：`solver.setup.boundary_conditions.get_object_names()` **不存在**，
会报 `AttributeError`。正确做法是**先取类型列表，再按类型取对象名**：

```python
__return__ = {
    bc_type: list(getattr(solver.setup.boundary_conditions, bc_type).get_object_names())
    for bc_type in solver.setup.boundary_conditions()
    if hasattr(solver.setup.boundary_conditions, bc_type)
}
```

`list(solver.setup.boundary_conditions())` 返回的是**类型名**列表，例如：

```
['wall', 'non_reflecting_bc', 'perforated_wall', 'settings']
```

而 `solver.setup.boundary_conditions.wall.get_object_names()` 返回**具体边界名**。
本机 `couette_flow.msh` 实测得到：

```
['wall-light', 'wall-right', 'wall-up', 'wall-down']
```

> ⚠️ **实测：`get_object_names()` 只在部分类型上有。**
> `wall` ✓，但 `non_reflecting_bc`、`perforated_wall`、`settings` **都会报
> `AttributeError`**。
>
> 注意 `hasattr(...)` **挡不住**——那些对象本身存在，只是没有这个方法。
> **必须用 try/except 逐个类型兜住**，上面的写法遇到没有该方法的类型会中断。
> 稳妥版本：
>
> ```python
> out = {}
> for bc_type in solver.setup.boundary_conditions():
>     try:
>         names = list(getattr(solver.setup.boundary_conditions, bc_type).get_object_names())
>         if names:
>             out[bc_type] = names
>     except Exception:
>         pass
> __return__ = out
> ```
>
> **更省事的做法**：直接用 MCP 的 `list_named_objects()`（**不带 path 参数**——
> 0.4.0 上它不存在，见 `mcp-tool-truths.md`）。它一次返回全部集合的映射，
> 不用先猜路径。

> 本机的网格是二进制 `.msh`，边界名**离线读不出来**（存在二进制段里）。
> 任何硬编码边界名的代码都会失败。名字还常常反直觉：库埃特流的上下板叫
> `wall-up` / `wall-down`（连字符），不是 `top` / `bottom`。

### 看某个边界当前的类型和设置

```python
solver.setup.boundary_conditions["wall-top"].get_state()
```

### 读单个值

```python
__return__ = solver.setup.models.viscous.model.get_state()
```

> ⚠️ `get_state` 在 **MCP 工具层**签名是 `get_state(paths=None, key=None)`（要列表或字符串）；
> 在 **`run_code` 沙箱里**是 PyFluent 对象方法 `.get_state()`（不要传 path 参数）。两者别混。

### 网格规模

⚠️ **实测：`solver.mesh.get_cell_count()` 不存在**（新旧 API 都没有），
`solver.mesh()` 只返回设置项名列表，**拿不到单元数**。

**正确做法：用离线探测，不要连 Fluent。**

```bash
.venv/Scripts/python.exe scripts/probe_mesh.py "<网格文件>" --json
```

毫秒级返回维度、单元数、节点数、面数，且能顺带判断是否超 Student 上限。
本机 `couette_flow.msh` → 2D，3,750 单元。

### 网格质量 ★ 会打印，不返回值

⚠️ **实测：`solver.mesh.quality` 是 command/query 对象，必须调用；
而且调用后返回 `None` —— 结果是【打印】到 stdout 的。**

```python
solver.mesh.quality()      # → None，但控制台会打出：
                           #   Minimum Orthogonal Quality =  9.98156e-01 ...
                           #   Maximum Aspect Ratio =  1.46674e+00 ...
```

在 `run_code` 里，这段打印会出现在返回的 stdout 里，**要从文本解析**。
这正是 MCP 专门有个 `mesh_report_parsers.py` 的原因——跨版本输出格式不一致。

**所以：优先用 MCP 的 `mesh_quality` 工具**，它已经把这些差异归一化好了，
还带 `mesh.check()` 开关。实测它返回的结构里**同时包含 `cell_count`**——
这正是原始 API 拿不到的：

```jsonc
{"connected": true, "cell_count": 3750, "face_count": 7625, "node_count": 3876,
 "quality": {"min_orthogonal_quality": 0.998156,
             "max_ortho_skew": null,        // ← 常态，null ≠ 合格
             "max_aspect_ratio": 1.46674},
 "check": {"domain_extents": {"x": [0.0, 1.5], "y": [0.0, 1.0], "z": null},
           "volume_min": 0.0003906084, "errors": [], "warnings": []}}
```

**一次调用拿到规模 + 质量 + 域尺寸**，比手写三段 PyFluent 划算得多。
`domain_extents` 尤其有用——它是物理核验的输入（板间距、特征长度都从这来）。

> ⚠️ 本机实测（Fluent 26.1）`mesh.quality()` **只打印两项**：
> `Minimum Orthogonal Quality` 与 `Maximum Aspect Ratio`，
> **没有 `Maximum Ortho Skew`**。这不是 bug——正交质量 ≈ 1 − 正交偏斜，两者冗余。
> **不要因为缺项就判"读失败"，但也不能把缺失当作合格。**
> 详见 `review-criteria.md#2-网格质量`。

### 网格检查 `mesh.check()` —— 同样只打印

```python
solver.mesh.check()        # → None，但会打出域范围和体积统计
```

本机 `couette_flow.msh` 实测输出（**这段有真实用途**）：

```
Domain Extents:
  x-coordinate: min (m) = 0.000000e+00, max (m) = 1.500000e+00
  y-coordinate: min (m) = 0.000000e+00, max (m) = 1.000000e+00
Volume statistics:
  minimum volume (m3): 3.906084e-04
  maximum volume (m3): 4.116006e-04
    total volume (m3): 1.500000e+00
```

**两个用途**：

1. **负体积检查** → `minimum volume` 为正即通过。负值必须消除（见 `review-criteria.md`）
2. **拿到域尺寸** → 这是**物理核验的关键输入**。上例给出板间距 h = 1.0 m，
   正是库埃特流解析解 `τ = μU/h` 需要的量

（`mesh.check()` 与 `mesh.quality()` 是**两件事**，都要做——前者只报负体积和拓扑，
不含偏斜度/正交质量。）

### 有没有能量方程、湍流模型是什么

```python
__return__ = {
    "energy": solver.setup.models.energy.enabled.get_state(),
    "viscous": solver.setup.models.viscous.model.get_state(),
}
```

---

## 写：求解设置

### 读网格 / case

```python
solver.file.read_case(file_name=r"C:\path\to\case.cas.h5")
solver.file.read_data(file_name=r"C:\path\to\case.dat.h5")
solver.file.read_mesh(file_name=r"C:\path\to\mesh.msh")
```

> `lightweight_setup=True` 只在 `read_case` 时可用，不是启动参数。

### 湍流模型

```python
solver.setup.models.viscous.model = "k-omega"
solver.setup.models.viscous.k_omega_model = "sst"
```

```python
solver.setup.models.viscous.model = "laminar"
```

> 值必须用**字符串**，且要跟 `describe_path` 查到的允许值逐字一致。

### 能量方程

```python
solver.setup.models.energy.enabled = True
```

### 边界条件：速度入口

```python
inlet = solver.setup.boundary_conditions.velocity_inlet["inlet"]
inlet.momentum.velocity = 1.0                       # 标量，或
inlet.momentum.velocity = [1.0, 0.0, 0.0]           # 分量
inlet.turbulence.turbulent_intensity = 0.05
inlet.turbulence.hydraulic_diameter = 0.1
```

### 边界条件：压力出口

```python
outlet = solver.setup.boundary_conditions.pressure_outlet["outlet"]
outlet.momentum.gauge_pressure = 0.0
```

### 边界条件：动壁面（库埃特流用这个）

```python
wall = solver.setup.boundary_conditions.wall["wall-top"]
wall.momentum.wall_motion = "moving-wall"
wall.momentum.shear_condition = "no-slip"
wall.momentum.speed = 1.0
```

> **不要**改边界名，尤其不要改成带空格的名字——intent guard 会用 `risk_blocked` 拦下。
> 要改也只能用下划线。

### 材料

```python
solver.setup.materials.fluid["water-liquid"].density = 998.2
solver.setup.materials.fluid["water-liquid"].viscosity = 0.001003
```

### 数值格式

```python
solver.solution.methods.pressure_velocity_coupling.type = "coupled"
solver.solution.methods.discretization_scheme["momentum"] = "second-order-upwind"
```

### 松弛因子

```python
solver.solution.controls.under_relaxation["pressure"] = 0.3
solver.solution.controls.under_relaxation["momentum"] = 0.7
```

---

## 算：求解

### 迭代 N 步

```python
solver.solution.run_calculation.iterate(iter_count=200)
```

> **这一步会阻塞到算完**，没有流式进度。超长求解要考虑分批：
> 多次 `run_code` 各算一段，中间读残差判断要不要继续。

#### ★★ `iterate(iter_count=N)` 会自己提前停 —— 别把 N 当"必然跑满"

**实测确认**（Fluent 26.1，2D 库埃特流）：

```
设 absolute_criteria = 1e-2，调用 iterate(iter_count=500)
→ 控制台打印 "!   21 solution is converged"
→ 第 21 步就停了，没有跑满 500
```

**所以 `iter_count` 是「上限」不是「固定步数」** —— Fluent 每步都检查
`check_convergence`，达到 `absolute_criteria` 就停。

**默认设置本来就是开着的**：

```
solution.monitor.residual.options.criterion_type = "absolute"
solution.monitor.residual.equations["<eq>"].check_convergence = True
solution.monitor.residual.equations["<eq>"].absolute_criteria = 0.001   ← 手册默认
```

```python
# 按规范设判据 —— 判据【可达】，Fluent 就会自己停
r = solver.settings.solution.monitor.residual
for eq in list(r.equations.get_state()):
    r.equations[eq].absolute_criteria = 1e-3      # 别设成够不到的值
solver.settings.solution.run_calculation.iterate(iter_count=3000)   # 这是上限，不是任务量
```

> ⚠️ **反过来说**：判据若不可达，提前停**永远不会触发**，`iter_count` 就真的变成
> 必跑满的步数。一次真实运行正是如此 —— 规范要求连续方程残差 `< 1e-6`，
> 而实测平台在 **4.4e-2**（差 4 个数量级），于是 3000 步一步不少地跑完，
> 耗时 45 分钟，最终残差还是没达标。
>
> **「设一个够得到的判据让它自己停」比「设一个够不到的目标然后封顶」好得多。**

### 中断（发现发散时）

```python
solver.solution.run_calculation.interrupt()
```

> ⚠️ **迭代进行中不能改 `setup.*`**——intent guard 会以 `runtime.write_during_iter` 拦截。
> 必须先 `interrupt()`，再改设置。

### 初始化

```python
solver.solution.initialization.hybrid_initialize()
# 或
solver.solution.initialization.standard_initialize()
```

---

## 取：结果与证据

### 残差（**审查者的主要证据**）

```python
__return__ = solver.solution.monitor.residual.get_state()
```

### 报告定义 + 取值

报告定义**必须**用 `surface_names=[...]`（不是 `surfaces`——那是图形对象的字段）：

```python
solver.solution.report_definitions.flux["massflow-in"] = {
    "report_type": "flux",
    "surface_names": ["inlet"],
    "field": "mass-flow-rate",
}
__return__ = solver.solution.report_definitions.flux["massflow-in"].report().get_state()
```

> ⚠️ 用 `surfaces` 会触发 intent guard 的 `reportdef.surface_field` 拦截。

### 导出速度剖面到 CSV

`run_code` 里**不能**写文件（`open` 被禁）。两条可行路线：

**路线 A** —— 用 Fluent 自己的导出接口（沙箱内可做）：

```python
# ⚠️ 实测更正：本会话可用的容器是 line_surface（不是 plane_surface），
#    字段是 {name, p0, p1}（不是 {type, p1, p2}）；二维算例端点带 z = 0.0
solver.results.surfaces.line_surface["centerline"] = {
    "name": "centerline",
    "p0": [0.75, 0.0, 0.0],
    "p1": [0.75, 1.0, 0.0],
}
# ⚠️ 实测更正：关键字是 surface_name_list / quantities。
#    写成 surfaces= / fields= 会被【静默忽略】（只发一个 PyFluentUserWarning），
#    结果是整个网格被 dump 出来 —— 不报错，但产物是错的。
solver.file.export.ascii(
    file_name=r"C:\...\04_results\velocity-profile.csv",
    surface_name_list=["centerline"],
    quantities=["x-velocity"],
)
```

> ⚠️ **写错了不会报错。** `surfaces=` / `fields=` 是"静默忽略"而非"拒绝"，
> 实测第一次导出把整个网格 dump 了出来。**导出后务必自己检查文件内容**
> （行数、列头、数值范围），别只看文件存在与否。

**路线 B** —— 把值 `__return__` 回 agent，由 agent 用自己的 Write 工具落盘：

```python
__return__ = {"values": [...], "note": "agent 负责写 CSV"}
```

### 截图

用 MCP 的 **`screenshot(view=...)`** 工具，不要写 `run_code`。

### 写 case / data

```python
solver.file.write_case(file_name=r"C:\...\04_results\case.cas.h5")
solver.file.write_data(file_name=r"C:\...\04_results\case.dat.h5")
```

> 这两条是**未被禁**的，是产出持久化证据的正规途径。

---

## 命名表达式：必须带单位

```python
solver.setup.named_expressions["grav"] = {"definition": "9.81 [m s^-2]"}
```

> 裸数字（`"9.81"`）会触发 `named_expr.missing_units` 警告，且在下游被消费时静默出错。
> 有量纲的量一律写单位。

---

## ★ 实测校正（Fluent 26.1，2026-09-16 库埃特流算例）

这一节的每一条都是**跑出来的**，不是推测。规范阶段如果按字面写错了，执行阶段会卡住。

### 伪瞬态：规范里想写的那个枚举在 26.1 上不存在

```python
# ❌ 规范常这么写，但 'pseudo-transient' 不是合法枚举
solver.solution.methods.pseudo_time_method.formulation.segregated_solver = "pseudo-transient"

# ✅ 实测允许值只有：
#    formulation.segregated_solver  -> ["off", "local-time-step"]
#    formulation.coupled_solver     -> ["off", "global-time-step"]
```

**官方依据**（UG v261 §37.14）：Local Time Step 只对**压力基分离式**求解器可用；
Global Time Step 只对**压力基耦合式**或**密度基隐式**可用。**两者互斥。**

**而且只有耦合分支能指定物理时间步**——分离式的 Local Time Step 由 Courant 数控制，
没有用户指定步长的入口。所以"用户指定伪时间步 + 分离式求解器"这个组合**在 26.1 上不成立**。

实测对比（同一算例，粘性刚性主导）：

| 路径 | 每步推进的伪时间 | 后果 |
|---|---|---|
| `local-time-step`（分离式）| 约 0.0095 s | 比 SIMPLE 还慢，推算需 ~4e7 步 |
| `coupled_solver="global-time-step"` + 指定步长 | 用户给定（本例 1.0e4 s）| 5197 步收敛 |

```python
# ✅ 能用的写法
solver.solution.methods.pseudo_time_method.formulation.coupled_solver = "global-time-step"
solver.solution.run_calculation.pseudo_time_settings.time_step_method.time_step_method = "user-specified"
solver.solution.run_calculation.pseudo_time_settings.time_step_method.pseudo_time_step_size = 1.0e4
```

> **路径比直觉多一层**：是 `pseudo_time_settings.time_step_method.time_step_method`，
> 不是 `pseudo_time_settings.time_step_method`。

> ⚠️ **别把 `time_step_method` 留在 `automatic`。** Fluent 按网格尺度估的自动步长
> （本例约 0.02 s）可能比 SIMPLE 的等效步长还小三四个数量级，把收敛拖成 1e7 步。
> 用完**读回实际值**落盘，别假定写进去的就是生效的。

### `convergence_conditions` 的 Stop Criterion 是【相对量】

这是最容易搞错的一条，会让"自动停止"变成"永不停止"或"立刻停止"。

```python
solver.solution.monitor.convergence_conditions["tau-up"] = {
    "stop_criterion": 2e-4,              # ← 这是【相对变化】，不是绝对量！
    "previous_values_to_consider": 100,
}
```

官方定义（UG §37.18.1）：`Res-m(Np) = |m(n) − m(n−Np)| / m(n)`。

实测后果：按**绝对量**写会出事——
- 给质量流量写 `0.5`（本意是 0.5 kg/s）→ 实为 **50%**，每一步都满足，
  transcript 里 "has converged" 打印了几千次，**等于没设**
- 给壁面剪应力写 `1e-6`（本意是 1e-6 Pa）→ 实为 1e-6 相对，严了约 1000 倍，**永远达不到**

**照抄前先想清单位**：要表达"0.1% 的相对变化"，就写 `1e-3`。

### `solution.monitor.imbalance` 在 26.1 上不存在

```python
solver.solution.monitor.imbalance          # → probe_path: exists=false
```

26.1 的 `solution.monitor` 子对象只有 `residual` / `report_files` / `report_plots` /
`convergence_conditions`。**质量平衡要另找路子**：用 `results.report.fluxes` 的
逐 zone 通量报表，或读体场 `mass-imbalance`。

> ⚠️ **逐 zone 通量求和的坑**：Fluent 的 `Net` 行在边界列表含 `interior-fluid` 时
> **恒为 0**（内部面成对抵消），对未收敛态也照样报 0，**没有鉴别力**。
> 要判质量平衡，得明确约定用哪些面、并说明影子周期面的报表约定
> （实测：周期对的通量**全部记在主面**，影子面返回 0）。

### 周期转换：二维下 `auto_translation=True` 会崩

```python
# ❌ 2D 下报 RuntimeError: can't set 0 at position 2（默认平移矢量不足 3 分量）
solver.mesh.modify_zones.make_periodic(
    zone_name="wall-light", shadow_zone_name="wall-right",
    rotate_periodic=False, create=True, auto_translation=True)

# ✅ 显式给平移矢量
solver.mesh.modify_zones.make_periodic(
    zone_name="wall-light", shadow_zone_name="wall-right",
    rotate_periodic=False, create=True,
    auto_translation=False, direction=[1.5, 0.0, 0.0])
# 控制台回: "translation deltas: 1.500000 0.000000 / created periodic zones."
```

注意 `mesh.modify_zones.*` 是**设置树 Command**，不是 `.tui.*`，沙箱允许。
`make_periodic` 与 `zone_type` 在 26.1 上 `exists=true, is_active=true`。

> **转换后必须读回**：`wall-light`/`wall-right` 应从 wall 集合消失、进入新的 periodic 集合，
> 且面数不变、`mesh.check()` 干净。

### 场名与枚举的实测值

| 想写的 | 实际是 |
|---|---|
| `wall-shear-stress` | **`wall-shear`** |
| `least-squares-cell-based` | **`least-square-cell-based`**（单数）|
| `wall.momentum.velocity = [1,0,0]` | **不存在**，用 `speed` + `direction`，绝对系写 `relative = "Absolute"` |
| `report_type="surface-area-weighted-average"` | 短名 **`surface-areaavg`**；`surface-massflowrate` |
| flux 报表用线面 | **被拒**（`Values contain disallowed entries`），只能用 zone 面 |

---

## 铁律

| 铁律 | 说明 |
|---|---|
| **绝不发 `.tui.*`** | 一律用设置 API。TUI 被硬禁，且优先于其他错误判定 |
| `.list()` / `.list_properties()` 是 void | 只打印不返回。要程序化发现请用 MCP 的 `list_named_objects` |
| `run_code` **无撤销** | 立即改活求解器。没有 journal、没有 rollback |
| `find_api(query=...)` 传字符串 | 不是列表 |
| 代码必须先 `validate_code` 再 `run_code` | 模型切换、BC 写入、`file.write_*` 尤其不能跳 |

---

## intent guard 错误码 → 正确应对

`run_code` 执行前会跑一个离线的 intent guard，拦住若干**已知崩溃签名**。
命中时报错，**不要重试同一段代码**，改写后再来。

| `error_code` | 含义 | 怎么改 |
|---|---|---|
| `risk_blocked` | 命中已知崩溃签名（见下表） | 按 `stderr` 里的建议改写 |
| `sequence_error` | 同一段内"先用后建" | 调整语句顺序，或拆成两次 `run_code` |
| `solver_disconnected` | Fluent 的 gRPC 通道死了 | **重新 `connect`**，重新读 case/data，再重发。会话已被标记断开 |

### 六种被拦的签名

1. **边界重命名带空格** → 用下划线（`oil_inlet`，不是 `oil inlet`）
2. **直接给 `multiphase.number_of_phases` 赋整数** → 用
   `multiphase.number_of_phases.number_of_eulerian_phases = N`
3. **相改名发生在材料赋值之后** → 先改名，再用新键赋材料
4. **迭代进行中改 `setup.*`** → 先 `interrupt()`
5. **命名表达式先用后建** → 先创建再引用，或拆成两次调用
6. **报告定义用了 `surfaces` 字段** → 改用 `surface_names=[...]`

### 非阻断警告（不停止执行，但要看）

- `named_expr.missing_units` —— 命名表达式用了裸数字，补单位
- `tui.usage` —— 用了 `.tui.*`，改用设置树

---

## 出错时的排查顺序

```
1. 看 error_code
2. forbidden_name      → 代码里有未绑定的名字（沙箱每次全新命名空间）
3. forbidden_call      → 用了 open/os/subprocess/setattr 之类
4. tui_not_allowed     → 改成设置 API
5. unknown_settings_path → 可能是 27.1 校验表 vs 26.1 运行时的误报，
                           用 find_api 复核；查得到就照跑
6. solver_disconnected → 进程死了，重新 connect + 重新读文件
```
