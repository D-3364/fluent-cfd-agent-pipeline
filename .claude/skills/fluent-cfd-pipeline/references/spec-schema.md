# `02_spec.json` 契约

由 **cfd-spec-author** 产出，由 **cfd-executor** 消费，由 **cfd-reviewer** 作为审查基准之一。

## 设计意图

这份文件是三个 agent 之间**唯一的物理设定接口**。它要同时支撑两种性质完全不同的审查：

| 审查遍次 | 问的问题 | 失败时 `route` |
|---|---|---|
| **合规性** | 执行结果达成了 spec 里写的判据吗？ | `execution` |
| **合理性** | spec 本身的物理设定站得住吗？ | `spec` |

所以 spec 必须自带两样东西：
- **可判定的判据**（让合规性审查有据可依）
- **选择理由**（让合理性审查能追溯"为什么选这个模型"，而不是只看结果）

**每条设定都必须写 `rationale`。** 没有理由的设定，审查者无法判断它是深思熟虑还是
随手填的——这是 `spec` 路由最主要的触发来源。

---

## 结构

```jsonc
{
  "schema_version": "1.0",
  "run_id": "couette-flow-20260916-143022",
  "created_at": "2026-09-16T14:30:22+08:00",

  // ── 需求来源：合理性审查的对照基准 ──
  "requirement": {
    "raw": "用户的原话，一字不改",
    "normalized": "规范化后的工程描述",
    "assumptions": [
      "需求没明说、我替你定的，逐条列出"
    ],
    "ambiguities": [
      "需求里确实有歧义、我按 X 处理的地方"
    ]
  },

  // ── 勘察结果摘要（完整版在 01_probe.json）──
  "case": {
    "file": "绝对路径",
    "mesh_cells": 12000,
    "mesh_nodes": 12500,
    "dimension": 2,
    "within_student_limit": true,
    "mesh_quality": {
      "min_orthogonal_quality": 0.87,
      "max_aspect_ratio": 12.4,
      "max_skewness": null
    }
  },

  // ── 求解器设置 ──
  "solver": {
    "type": "pressure-based",
    "formulation": "implicit",
    "velocity_formulation": "absolute",
    "time": "steady",
    "dimension": 2,
    "precision": "double",
    "rationale": "不可压低速内流，压力基求解器是标准选择"
  },

  // ── 物理模型 ──
  "physics": {
    "energy": { "enabled": false, "rationale": "需求未涉及传热，且..." },
    "viscous": {
      "model": "laminar",           // laminar | k-epsilon | k-omega | sst | sa | ...
      // ★ rationale 要给出【推理链】，不是一个结论句。
      //   写结论、"算过的"、"显然"都会被审查者打回 —— 它要复算。
      //   若结论依赖 Re：给出 Re 的算式与取值、对照的阈值、以及阈值出处。
      //   若结论【不】依赖 Re（如本网格有 NS 严格解、或网格给不出所需 y+），
      //   就写那个理由 —— 别硬套 Re 阈值。
      "rationale": "算得 Re = ρUh/μ = <值>，对照 <阈值>（出处：<来源>）判定为 <层流/湍流>；…",
      "wall_treatment": null,        // 仅湍流模型需要
      "target_y_plus": null          // 仅湍流模型需要，如 [30, 300] 或 1
    },
    "multiphase": { "model": "none", "rationale": "单相流" },
    "radiation":  { "model": "none", "rationale": "..." }
  },

  // ── 材料 ──
  "materials": [
    {
      "name": "water-liquid",
      "source": "fluent-database",   // fluent-database | user-defined
      "properties": { "density": 998.2, "viscosity": 0.001003 },
      "units": { "density": "kg/m3", "viscosity": "kg/(m*s)" },
      "rationale": "25°C 水，取 Fluent 内置物性"
    }
  ],

  // ── 边界条件：名字必须来自 01_probe.json 的实测结果 ──
  "boundary_conditions": [
    {
      "zone": "wall-top",            // ⚠️ 必须与勘察发现的真实名字逐字一致
      "type": "wall",
      "settings": {
        "velocity": { "value": 1.0, "units": "m/s", "motion": "moving-wall" }
      },
      "rationale": "上板以 1 m/s 拖动，这是库埃特流的驱动条件"
    }
  ],

  // ── 数值格式与松弛 ──
  "numerics": {
    "schemes": { "pressure": "second-order", "momentum": "second-order-upwind" },
    "under_relaxation": { "pressure": 0.3, "momentum": 0.7 },
    "rationale": "..."
  },

  // ── 收敛判据：合规性审查直接照这个判 ──
  "convergence": {
    "max_iterations": 500,
    "residual_targets": { "continuity": 1e-6, "x-velocity": 1e-6, "y-velocity": 1e-6 },
    "monitors": [
      { "name": "wall-shear-top", "type": "wall-shear-stress", "zone": "wall-top" }
    ],
    "acceptance": [
      "残差全部低于目标值",
      "壁面剪应力监测值在 200 次迭代内变化小于 0.1%",
      "进出口质量不平衡小于 0.5%"
    ],
    "rationale": "稳态层流，1e-6 是可达且足够的；层流无需能量方程判据"
  },

  // ── 导出清单：审查者的证据来源 ──
  "exports": [
    { "kind": "case-data",  "path": "04_results/case.cas.h5" },
    { "kind": "residual-history", "path": "04_results/residuals.json" },
    { "kind": "monitor-history",  "path": "04_results/monitors.json" },
    { "kind": "profile", "path": "04_results/velocity-profile.csv",
      "spec": { "surface": "centerline", "field": "x-velocity" } },
    { "kind": "contour", "path": "04_results/velocity-contour.png" }
  ],

  // ── 交给人工关卡看的疑点 ──
  "open_questions": [
    "需求没说要算多久，我按稳态处理"
  ]
}
```

---

## 字段规则

### 硬规则

| 规则 | 理由 |
|---|---|
| `requirement.raw` 必须逐字保留用户原话 | 合理性审查要拿它对照，转述会丢信息 |
| `boundary_conditions[].zone` 必须来自 `01_probe.json` 实测 | 这些网格是二进制的，名字无法离线预读；猜必错 |
| 每条设定必须带 `rationale` | 没有理由 = 无法判断是深思熟虑还是随手填，是 `spec` 路由的主因 |
| `convergence.acceptance` 必须是**可判定**的陈述 | 见下 |
| `exports` 必须覆盖审查者需要的全部证据 | 审查者不连 MCP，拿不到没落盘的东西 |

### 关于 `convergence.acceptance`

这是合规性审查的判据表，必须写成**能对着数据判真假**的句子。

```
✅ "残差全部低于 1e-6"
✅ "进出口质量不平衡小于 0.5%"
✅ "壁面剪应力在最后 100 次迭代内相对变化小于 0.1%"

❌ "结果收敛良好"           —— 无法判定
❌ "流场合理"               —— 无法判定
❌ "达到工程精度"           —— 没有数值
```

### ★★ 判据良构性检查表 —— 每条判据都必须过这五关

**为什么单列这一节**：一次真实运行里 13 条判据中 **4 条是坏的，且全部是措辞缺陷
而非执行问题**。后果是一次完整的 `route=spec` 回退 + 重走人工关卡。
下面每一条都有那次运行的实例佐证。

写完每条判据，逐条过一遍：

#### 1. 可求值 —— 两个时刻都能取到值

**禁止出现可能不存在的基线。**

> ❌ 实例（C10）：要求 t=0.4 s 的"≥330 K 前缘位置"**大于 t=0.2 s 时的同一量**，
> 但 t=0.2 s 时该线上**根本不存在 ≥330 K 的点**（最高温 299.80 K），基线为空，
> 判据不可求值。
>
> **改法**：要么把基线显式定义为"前缘未进入该线"，要么去掉与 t=0.2 s 的比较、
> 只保留可判定的那半句。

**检查动作**：把判据里的每个量，在**每个**被引用的时刻上都问一遍"这个量一定存在吗"。

#### 2. 有容差 —— 不等式判据必须给容差，并写明依据

**物理量有数值误差，硬边界会把舍入误差判成物理错误。**

> ❌ 实例（C5）：写成 `T_min ≥ 295 K`，无容差。实测 t=0.2 s 全域最低温
> **294.9999924 K**，欠冲 **7.6e-6 K**（相对 2.6e-8）—— 双精度二阶迎风解在
> Dirichlet 边界附近的舍入量级，被判死。
>
> **改法**：`T_min ≥ 295 K − 1e-3 K`，并写明容差依据。

#### 3. 有作用域 —— 涉及残差/迭代的判据必须写清**步内**还是**跨界**

**瞬态求解每个时间步会重启内迭代，跨时间步的首末残差比天然很大。**

> ❌ 实例（C2）："全过程中没有任何一次内迭代的归一化残差比上一次高 2 个数量级"。
> 按字面跨时间步读，实测中位数 **209 倍**、p90 5.6e4、max 5.6e6；
> 按步内读则是 **11.75 倍**。**两种读法差 18 倍**，判据不可判。
>
> **改法**：限定为"同一时间步内相邻两次内迭代之间"，并显式说明跨界比值是
> 固有现象、不构成发散信号。

#### 4. 有退化分支 —— "什么都没发生"时必须定义算通过还是失败

> ❌ 实例（C8）："最后 20 个时间步的回流面积占比必须【严格小于】前 20 个时间步的占比"。
> 全程零回流时退化为 `0 < 0`，**假** —— 把干净结果判成不通过。
>
> **改法**：补一句"若全程未出现 Reversed flow，本判据判通过"。

#### 5. 区分稳态/瞬态 —— 守恒判据尤其

**见下方「关于守恒判据」。**

#### 6. ★ 有成本意识 —— 收敛目标与迭代上限必须相称

**判据再对，跑不出来也没用。**

> ❌ 实例：某算例把残差目标从手册默认 1e-3 收紧到 **1e-6**，`max_iterations=3000`。
> 实测跑满 3000 步，continuity 残差停在 **4.4e-02**（差 4 个数量级），
> **45 分钟**求解换来一个没达标的残差。而网格只有 5 万单元。
>
> 该规范的理由写得很认真（"要辨别正反向压降之差"），但它论证的是
> 「1e-6 **可达**」—— **可达 ≠ 快速可达**。**它没算代价。**

**检查动作**：

- 残差目标比手册默认（1e-3）更严时，`rationale` 里**必须给出代价预期**
- `max_iterations` 与残差目标**相称吗**？定 1e-6 却给 3000 步 = 数学上必然跑满
- 验收主要看**积分量**（质量守恒、流量、压降）时，残差用默认值就够 ——
  收紧它对那些判据没有帮助
- **优先用监测量停机**，而不是固定的步数上限

**并把预期耗时写进 `rationale`。** 定规范的人如果从没问过"这一步要跑多久"，
就没有人会为这个负责。

详见 `review-criteria.md#3-收敛判据`。

---

### ★ 瞬态算例必须含 flow time 判据

**这是把运气变成规则。**

> 一次真实运行中，执行方差点照 MCP 帮助文本用 `iterate(iter_count=800)` 推进时间，
> 那样 `flow_time` 会停在 **0.016 s** 而所有残差/云图/通量报表**看起来全都正常**。
> 之所以没中招，是因为定规范时**自发**写了一条 C12「flow time 必须 = 0.400 s」。
> **那是运气，不是设计。**

**对任何瞬态算例**，`acceptance` 里**必须**有一条：

```
C_n 【真的跑到 T】跑完后读回求解器报告的 flow time = <T> s（容差 <ε>），
     且确有各时间点的 data 文件写出。
```

理由与正确的时间推进 API 见 `solver-api-26.1.md#1`。

---

### ★ 关于守恒判据：必须声明是否含储能项

**稳态与瞬态的守恒形式不同，写错会让判据物理上不可能达成。**

Fluent 的 `Total Heat Transfer Rate` 是**相对 298.15 K 参考温度**的焓通量
`ṁ·cp·(T−298.15)`。对绝热边界求和：

```
稳态：  ΣΦ = 0
瞬态：  ΣΦ = 储能率 dE/dt = ρ·cp·V·dT_volavg/dt   ≠ 0
```

> ❌ 实例（C4）：写成 `|Σ(总热流量)| / Ḣ_in < 0.5%`，rationale 还写明
> "全域绝热、无内热源 → 四面求和应为 0"。这是**稳态**推理，对"冷态起步、
> 0.4 s 内仍在被填充"的瞬态算例**物理上不成立**——实测 **11.65%**，
> 直接触发一次 `route=spec` 回退。正确形式闭合到 **1.16e-5 %**。

**规则**：任何守恒判据**必须显式声明是否含储能项**；瞬态算例**必须含**。
详见 `review-criteria.md#3-收敛判据` 的通式。

---

### ★ 每张云图/矢量图必须附几何完整性自检

> ❌ 一次真实运行中，执行方发现 `range_options.clip_to_range = True` 会把
> **超范围区域从显示中删除**（不是饱和），导致热臂上游整块几何消失、
> 速度图丢失峰值区。**看起来"挺正常"，实际交付物缺了一块几何。**
> 10 张图全部重画。

**规则**：`exports` 里凡有云图/矢量图，必须同时要求
"导出后确认流体域几何完整（各支路可见、无缺失区域）"，并列为验收判据。

**这条要做成规定动作，不能靠执行方自愿。** 那次是执行方**自己**做了目视复核 ——
把它变成规则，下一次才还会有人做。详见 `solver-api-26.1.md#2`。

### 关于 `assumptions` 和 `ambiguities`

这两项是**人工关卡的输入**，也是合理性审查最关心的部分。用户的需求提示词通常
不会说清所有物理设定，agent 必须替用户做决定——但要把决定**显式记录下来**。

```
✅ assumptions: ["需求说'水'，取 25°C 常物性", "未指定出口压力，取 0 Pa 表压"]
❌ 直接填个密度 998.2 什么都不说
```

### 关于 `exports`

审查者只能看到落盘的东西。**先想清楚"要审查什么"，再倒推"该导出什么"。**

举例：要判"速度剖面是否线性"，就必须导出中心线上的速度剖面 CSV；只导一张云图
是判不了的。定规范时就要把这条链路想通。

#### ★ `reviewer_evidence_manifest` —— 逐条判据声明"用哪个产物证明"

上面那句话以前只是**隐含的原则**，于是漏项真的发生了：

> ❌ 一次真实运行中，`physics.viscous` 的**整段论证建立在 y+ 上**，
> 但 `exports` 清单里**没有任何 y+ 导出项**。执行方只能在收尾后**另开一次只读会话**
> 补测 y+，而**那次会话的 transcript 没有落盘** —— 直接制造了一条
> `cannot_verify`，以及一处审查者无法独立复核的数值分歧。

**规则**：`02_spec.json` 增加一个 `reviewer_evidence_manifest` 字段，
**把每条验收判据映射到证明它的产物**：

```jsonc
"reviewer_evidence_manifest": [
  { "criterion": "C3 质量不平衡",
    "evidence": ["04_results/flux-balance-t020.json", "04_results/flux-balance-t040.json"],
    "how": "对三个真实边界面的质量流量作差" },
  { "criterion": "C12 flow time",
    "evidence": ["04_results/monitors.csv", "04_results/fluent_console.log"],
    "how": "读回 Flow time 打印行" }
]
```

**检查动作**：manifest 里出现的每个产物路径，都必须同时出现在 `exports` 里。
两边对不上就是漏项 —— **写规范时当场就能发现，不必等审查者**。

顺带：`case` 段应当带**近壁几何量**（壁面相邻单元形心距中位数），
它由 `01_probe.json` 提供。y+ 估算依赖它，而它**是关于网格的事实、不是规范决策** ——
放在勘察阶段由脚本产出，spec 作者就不必自己写二进制解析器了。

---

### 修订轮：`physics_diff`（回退到 S2 时必填）

> 一次真实运行中，v1→v2 的物理设定**逐字段比对完全相同**
> （`solver`/`materials`/`boundary_conditions`/`initial_condition`/`transient`/`numerics`
> 全部 IDENTICAL；只有 `physics.viscous.achievable_y_plus` 从**估算值**改成了**实测值** ——
> 纯记账）。但规则要求重走 S3 人工关卡，于是把这种记账问题也摆给了用户。

**规则**：spec 作者在修订时**必须**产出一个**可机检的 `physics_diff`**，
逐字段比对 v1 与 v2，并区分两类：

| 类别 | 判据 | 人工关卡 |
|---|---|---|
| **写入求解器的字段** | `solver` / `materials` / `boundary_conditions` / `initial_condition` / `transient` / `numerics` | 变了 → **正常关卡** |
| **记账性字段** | `rationale` / `achievable_y_plus` / `measured_*` / `open_questions` 等描述性内容 | 只变这类 → **降级为通知** |

**降级不等于取消用户的否决权**，只是不必为记账问题做决策。
**真正新增的存疑项仍须单独提问。**

### 修订轮：`no_rerun_required` 也要管证据

> 一次真实运行中，S4 后处理轮重跑了一次 flux 报表，产出
> `flux-round2-t0{20,40}-heattransfer.txt`，而与第一轮的
> `flux-t0{20,40}-heattransfer.txt` **逐字节相同** —— 纯粹浪费。

**规则**：`no_rerun_required` 除了写"不要重跑求解"，还要补一句
"**不要重新生成已有的证据，只补缺的**"。

---

## 与 `01_probe.json` 的分工

| 文件 | 内容 | 谁写 | 谁读 |
|---|---|---|---|
| `01_probe.json` | 勘察**原始事实**：网格规模、质量、边界名、现有场、单位制。不做任何解读 | spec-author（勘察阶段） | spec-author（定规范时）、executor（参考） |
| `02_spec.json` | **决策**：基于勘察事实做出的物理设定选择，每条带理由 | spec-author（定规范阶段） | executor、reviewer、人工关卡 |

分开的理由：事实和判断混淆在一起时，审查者无法分辨"这是观测到的"还是"这是选定的"。
边界名写错属于事实错误，湍流模型选错属于判断错误——两者的 `route` 不同。
