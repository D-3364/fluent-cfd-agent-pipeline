<!-- 由 scripts/sync_agents.py 从 .claude/ 生成，不要手工改。改请改源文件。 -->
# 求解驱动 API —— Fluent 26.1 版本锁定事实

给 **cfd-executor** 用。讲「怎么驱动一次求解」，与讲「怎么跟 MCP server 说话」的
`mcp-tool-truths.md` 互补，两者都要读。

内容全部在 **Fluent 26.1** 上实测，换版本必须重新验证。标 ✅ 的是后来单独复核
确认过的，其余来自一次真实运行的 executor 逐条试出来的结果。

那次运行里 executor 花了 26.5 分钟 / 250 次 tool call，绝大部分不在算流体，而在
现场考古下面这些字段名和枚举值。它们是可固化的事实，不该每次运行都重新发现。

> ### 这张表没覆盖到的，去查官方文档
>
> **本机安装目录里没有 Fluent 文档**（实测：`fluent/` 下无 `.chm`/`.pdf`，
> 全安装只有通用帮助壳 `commonfiles/help/`）。ANSYS 已将 Fluent 文档改为在线。
>
> 在线入口（`curl` 可通，WebFetch 在本环境被拦截）：
>
> ```
> https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/flu_ug/flu_ug.html   ← User's Guide
> https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/flu_th/flu_th.html   ← Theory Guide
> ```
>
> 更细的章节链接与引用规范见 `review-criteria.md` 的 §0「引用来源与可信度分级」。
>
> **遇到本表没写的路径/枚举，宁可查一次文档，也不要照记忆写。** 上面那 250 次
> 考古就是这么做出来的。

---

## 1. ★★ 时间推进：MCP 帮助文本本身是错的

**这条是静默错误，所以排在第一位。**

MCP 的 `get_help("solution.run_calculation.iterate")` 返回：

```
iter_count : int
    Incremental number of time steps.        ← ★ 错的
```

**照这句话写 `iterate(iter_count=800)` 期待推进 800 个时间步，实际只做了 800 次内迭代。**

### 实测证据

设 `time = transient`、`Δt = 5e-4 s` 后：

**`iterate(iter_count=2)`** —— 输出末列（剩余步数）`1 → 0`，
即 2 次迭代**只消耗掉 1 个时间步的内迭代**，flow time 不动。

**`dual_time_iterate(time_step_count=2, max_iter_per_step=20)`** —— 输出：

```
Flow time = 0.0005s, time step = 1
   1 more time step
Flow time = 0.001s, time step = 2
```

推进了 **2 个时间步**，每步跑满 20 次内迭代。✅ 这才是时间推进。

### 正确用法

```python
# ✅ 推进 N 个时间步，每步最多 M 次内迭代
solver.settings.solution.run_calculation.dual_time_iterate(
    time_step_count=N, max_iter_per_step=M
)

# ❌ 这是内迭代，不推进 flow time
solver.settings.solution.run_calculation.iterate(iter_count=M)
```

### 怎么确认自己没搞错

控制台会打印 `Flow time = Xs, time step = N`。**跑完必须核对它**，
别只看残差好看。规范里应当有一条 `flow time == N×Δt` 的验收判据（见 `spec-schema.md`）。

> ⚠️ **为什么这条是 P0 而非一般文档问题**：若照帮助文本闷跑 800 次内迭代，
> flow time 会是 `0.016 s` 而不是 `0.4 s`，而**残差、云图、通量报表看起来全都正常**。
> 没有 `flow time` 判据就发现不了。

---

## 2. ★ `clip_to_range = True` 的语义是破坏性的

`range_options.clip_to_range = True` **不是**"把超范围的值饱和到端点色"，
而是**把超范围区域从显示中删除**。

实测后果：用 t=0.4 s 的色标画 t=0.2 s 的压力图时，**热臂上游整块几何消失**；
速度图同样丢失峰值区。**看起来"挺正常"，实际缺了一块几何。**

> 那次运行因此重画了全部 10 张云图/矢量图。

**默认应当关掉**：

```python
obj.range_options.clip_to_range = False
```

并且每张图导出后**目视确认流体域几何完整**（三臂可见、无缺失区域）。
这条已作为规定动作写进 `spec-schema.md` 的 exports 要求。

---

## 3. ★ 云图色标：规范规定的机制在本版本不可实现

想"取某个时刻的自动范围并固定色标"时，直觉写法是读 `range_options.minimum/maximum`。
**在 26.1 上，`auto_range` 开启时这两个值读回恒为 0**，取不到范围。

**可行的机制**：用 `report_definitions` 读该时刻的 `volume-min` / `volume-max` 作锚点，
再手动写进各图的 `range_options`。

```python
# 读锚点（示例路径，用 describe_path 核对后再写）
mn = solver.settings.solution.report_definitions.volume[...].report().get_state()
```

**制定规范时必须指名这个机制**，否则下一轮的执行方还会卡在同一处。

**副作用要登记**：固定色标意味着**较早时刻若超出锚点范围就会出现饱和区**。
那是规则的必然结果，**不是渲染缺陷** —— 但要在报告里说明，免得被当成问题。

---

## 4. 字段与枚举改名（26.1 实测）

| 你以为的 | 26.1 实际是 |
|---|---|
| `static-temperature` | `temperature` |
| `volume-weighted-average` | `volume-average` |
| `surface-area-weighted-average` | `surface-areaavg` |
| `surface-vertex-maximum` | `surface-vertexmax` |
| `least-squares-cell-based` | `least-square-cell-based` |
| `first-order-implicit` | `unsteady-1st-order` |
| `average_pressure_spec` | `avg_pressure_spec` |
| `transient_controls.time_step_size` | **`parameters.time_step_size`** ✅ 已复核 |
| velocity-inlet 的 `thermal.total_temperature` | **不存在**（该入口不暴露总温） |

> `parameters.time_step_size` 这条是**由 Fluent 自己的弃用警告**给出的：
> `A newer syntax is available ... solver.settings.solution.run_calculation.parameters.time_step_size`。
> **弃用警告是免费的改名线索，注意看 stdout。**

### 一条通用建议

**不要凭记忆写字段名。** 写之前用 `describe_path(paths=[...])` 或
`find_api(query="...")` 查一次，代价远小于跑一轮失败。

---

## 5. 报告定义与导出

### 报告定义用 `surface_names=`，不是 `surfaces=`

```python
solver.settings.solution.report_definitions.flux["mf-in"] = {
    "report_type": "flux-massflow",
    "boundaries": ["hot-inlet", "cold-inlet"],   # 注意键名是 boundaries
    # 注意：flux 报表【没有 field 键】
}
```

> `surfaces` 是**图形对象**的字段。用错会触发 intent guard 的
> `reportdef.surface_field` 拦截（见 `mcp-tool-truths.md`）。

### ASCII 导出：关键字写错是**静默忽略**

```python
solver.settings.file.export.ascii(
    file_name=r"...\out.csv",
    surface_name_list=["outlet"],     # ← 不是 surfaces=
    quantities=["temperature"],       # ← 不是 fields=
)
```

**写错不报错，但产物是错的**（会把整个网格 dump 出来）。

### ★ 求和质量流量时必须排除 interior zone

对 `flux` 报表求和**必须只取真实边界面**。若把 `interior-water-pipe` 之类的内部面算进去，
正负相消会让 **Net 恒为 0，判据彻底失去鉴别力**。

那次运行的规范里 C3 专门写了这一点，是对的——**这条应当成为默认做法**。

### `solution.monitor.imbalance` 不存在

那是一次真实运行的失败尝试。质量不平衡要**自己算**：从 flux 报表取各边界的质量流量再作差。

### ★ 近壁形心距：面报表这条路走不通

想拿「壁面相邻单元形心距」（y+ 估算的输入）时，会自然地想到对壁面做一个
`wall-distance` 的报表。**两条路都已实测走不通**：

| 尝试 | 结果 |
|---|---|
| TUI `surface_integrals` | 该路径在 26.1 **不存在**（`'surface_integrals' object has no attribute ...`） |
| `report_definitions` + `field="wall-distance"` | **`Value is not allowed`** |

第二条的报错给出了原因：`(wall-distance is_not_in (pressure pressure-coefficient
dynamic-pressure absolute-pressure ...))` —— **面报表只接受面量，而 `wall-distance`
是体场**。这不是调用写法问题，是接口限制。

**可行的替代**：

1. **从 `.msh` 文件自行解析** —— 成本高但确定可行（一次真实运行的 spec 作者就是这么做的）
2. **等求解完成后读 y+ 报表** —— 那时 `y-plus` 是合法的面量：
   `report_type=surface-areaavg, field=y-plus, surface_names=[...]`

> 注意 2 是**事后**的，不能用来决定湍流模型——而那正是近壁形心距的用途。
> 所以定规范阶段需要它时，只能用 1。
>
> `scripts/probe_case_standalone.py` 的 `near_wall_geometry` 会尝试上面的路并
> **如实记录失败原因**（实测 `ok=false`），不会假装取到值。

---

## 6. `stop_criterion` 是**相对量**，不是绝对量

`convergence_conditions` 的 `stop_criterion` 含义是**相邻两次迭代的相对变化**，
不是"残差低于某个绝对值"。

**把它当绝对阈值写进规范会导致判据语义完全走样。** 规范的残差目标应当用
`solution.monitor.residual` 的 `absolute_criteria`，或者干脆在验收判据里
直接比对残差历史。

---

## 7. 能量账：Fluent 的 `Total Heat Transfer Rate` 是相对 298.15 K 的焓通量

这是一个**极容易踩的物理语义坑**。

Fluent 报的 `Total Heat Transfer Rate` **不是**相对于某个自然零点的能量率，而是

```
ṁ · cp · (T − 298.15 K)
```

即以 **298.15 K 为参考温度**的焓通量。

### 后果

对四个绝热边界面求和，**在稳态下确实为 0**（因为净质量流量为 0 且温度自洽），
但在**瞬态**下**不等于 0** —— 它等于**流体的储能率 dE/dt**：

```
Σ_i Φ_i  =  ρ · cp · V · dT_volavg/dt
```

**稳态只是 storage → 0 的特例。**

### 因此守恒判据必须分稳态/瞬态

```
稳态：  |Σ_i Φ_i| / Ḣ_in  <  0.5%
瞬态：  |Σ_i Φ_i − ρ·cp·V·dT_volavg/dt| / Ḣ_in  <  0.5%
```

**漏掉储能项会让瞬态算例的判据物理上不可能达成** —— 那次运行实测 11.65%，
直接触发了一次完整的 `route=spec` 回退。

> 详见 `review-criteria.md#3-收敛判据`，那里给了通式。

---

## 8. 监测文件的一个数据完整性坑

`monitors.csv` 的**第 0 行**可能把 `time_step` / `flow_time` 标为 `0` / `0.0`，
但同一行的物理量（如 `domain-tmax`）**已经是初值后的状态**（实测 319.79 K）。

**按"t=0"去解释第 0 行会得到错误结论。** 读监测历史时先确认第 0 行的语义。

---

## 9. 出错时的排查顺序

```
1. 看 error_code（见 mcp-tool-truths.md 的错误码表）
2. forbidden_name      → 代码有未绑定的名字（沙箱每次全新命名空间）
3. forbidden_call      → 用了 open/os/subprocess/setattr
4. tui_not_allowed     → 改成设置 API
5. unknown_settings_path → 可能是 27.1 校验表 vs 26.1 运行时的误报，
                           用 find_api 复核，查得到就照跑
6. execution_error     → 看 stdout 里的 Fluent 原文报错
   ├─ "File ... not found!" 而文件确实存在 → ★ 路径含非 ASCII，见 mcp-tool-truths.md#9
   └─ 字段/枚举不存在 → 看有没有弃用警告给出的新名字（第 4 节）
7. solver_disconnected → 进程死了，重新 connect + 重新读文件
```
