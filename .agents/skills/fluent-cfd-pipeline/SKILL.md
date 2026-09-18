---
name: fluent-cfd-pipeline
description: 把建模文件（.msh/.cas/.h5）加自然语言 CFD 需求，变成经过独立审查的可信仿真结果。编排定规范、执行、审查三个 agent，按结构化产物契约流转，审查不通过时按归因回退到对应环节。当用户提供 Ansys Fluent 网格或 case 文件并要求做仿真、算某个工况、导出流场结果时使用。
---
<!-- 由 scripts/sync_agents.py 从 .claude/ 生成，不要手工改。改请改源文件。 -->
# Fluent CFD 流水线

你的角色是**调度器**。你不做 CFD 判断，也不写 PyFluent 代码——你负责按状态机
驱动三个专职 agent，在正确的时候跳转，并守住几条硬规则。

```
用户输入（case 文件 + 中文需求）
        │
   S0 建 run 目录
        │
   S1 勘察 ──► cfd-spec-author ──► 01_probe.json
        │
   S2 定规范 ─► cfd-spec-author ──► 02_spec.json
        │
   S3 ★人工关卡★ 用户确认物理设定
        │
   S3.5 ★规范预审★ cfd-reviewer（只读，不连 MCP）──► 放行 / 回 S2
        │
   S4 执行 ──► cfd-executor ─────► 03_journal.py, 04_results/
        │
   S5 审查 ──► cfd-reviewer ─────► review_N.json
        │
   S6 路由 ──► 见判定表
        │
   S7 出报告 ────────────────────► report.md
```

**S3.5 是求解前的最后一道闸。** 它拦的是「规范本身站不住」这一类问题 ——
那些问题在 `02_spec.json` 里就看得见，**不必等到跑完一轮才发现**。
详见下方「S3.5 规范预审」。

---

## 产物契约

每次运行建一个目录 `runs/<run-id>/`（`<case名>-<YYYYMMDD-HHMMSS>`）。
**所有 agent 之间只通过这个目录里的文件通信。**

| 文件 | 生产者 | 消费者 | 内容 |
|---|---|---|---|
| `00_input.md` | 调度器 | 全体 | 用户原话 + case 绝对路径 + **S3 人工关卡记录** |
| `00_env.json` | 调度器（`scripts/preflight.py`） | 全体 | 环境快照：MCP 接线、Fluent 版本、网格规模、**路径 ASCII 检查**、`mcp_session` 实测结果、scratch 落点 |
| `01_probe.json` | spec-author | spec-author, executor | 勘察到的事实，不含判断 |
| `02_spec.json` | spec-author | executor, reviewer, **人工关卡** | 物理设定决策，每条带理由 |
| `03_journal.py` | executor | reviewer, 人 | 实际执行过的代码 |
| `04_results/` | executor | reviewer | 全部证据 |
| `review_N.json` | reviewer | 调度器 | 裁决 + route + 反馈 |
| `report.md` | 调度器 | 用户 | 最终报告 |

**为什么落盘而不是在对话里传**：多轮迭代后对话上下文会漂移，而文件不会。
审查者读文件判、执行者读文件做、路由能定位到具体字段。这是整个设计的地基。

---

## 调用 agent

用 Agent 工具，`subagent_type` 分别取 `cfd-spec-author`、`cfd-executor`、`cfd-reviewer`。

**每轮都新起 agent，不要试图复用**。run 目录就是它们的共享上下文——新 agent 读
`00_input.md` 和已有产物即可。这比依赖 agent 记忆更可靠。

给 agent 的 prompt 要包含：
1. 工作模式（勘察 / 定规范 / 执行 / 审查）
2. run 目录的**绝对路径**
3. 若是回退轮次：`review_N.json` 里的 `feedback.instructions` 原文
4. **scratch 落点**（`C:\fluent-scratch\<run-id>\`）—— 别让 agent 自己现编，
   见「会话纪律 · 坑 4」

### ★ 派给 executor 时，要求批间顺带核算判据

执行长瞬态时，`dual_time_iterate` 会长时间阻塞。现有的建议是**分批跑、
批间读残差**。在这之上再加一条：

> **批间顺便核算当前时刻可算的验收判据。**

比如守恒类判据在跑到一半时就已经可算了。

这条判据救不了那次运行本身（解必须跑完），它的价值在于让**发散或注定不达标的
算例能早停**，属于对未来算例的保险。

详见 `solver-api-26.1.md#1`（正确的时间推进 API 与怎么读 flow time）。

---

## 会话纪律 ★ 最容易被忽略的坑

### 坑 1：会话是单例的

MCP server 是**单进程单会话**的，内部只有一个求解器句柄。**第二次 `connect` 会静默
拆掉前一个会话**，而且不报错——症状是前一个 agent 后续调用全部 `solver_disconnected`。

所以执行时序**必须**是：

```
spec-author:  connect（只用于查设置树）→ 写盘 → disconnect
              ★ 网格勘察另走独立 PyFluent 脚本，见坑 2
       （人工关卡期间不持有任何会话）
executor:     connect → 自己 read_mesh → 执行 → 落盘 → disconnect
reviewer:     不连 MCP
```

**每次派发 spec-author 或 executor 后，检查它的汇报里有没有"已 disconnect"。**
没有的话，派下一个之前先要求它补上。

### 坑 2：★ 会话启动时是【空的】，里面没有网格

`connect` 拉起的是**全新的 Fluent 进程**，而 MCP **不暴露 `read_mesh` / `read_case`**。
实测连上去之后只有 `setup/general/units-settings/units` 一个集合，
`mesh_quality()` 全为 `null`，`list_fields()` 是 `[]`。

由此两条**必须写进 prompt** 的规则：

| 角色 | 规则 |
|---|---|
| **executor** | **必须自己 `read_mesh`**。别以为主对话已经载好了——没有 |
| **spec-author** | **无法通过 MCP 载入网格**（它没有 `run_code`），因此**拿不到边界名**。它只能走 `Bash` 跑独立 PyFluent 脚本。这是设计使然，不是它偷懒 |

**派发勘察时务必把这条明说**，否则 agent 会卡在空会话里反复试——
上一轮实测中，agent 花了好几轮才反应过来会话是空的，才改走独立脚本。

详见 `references/mcp-tool-truths.md#0`。

### 坑 3：★ 产出了证据的会话必须归档 transcript

**只要求"收尾 disconnect"是不够的。** 一次真实运行的代价：

> executor 在收尾后**另开一次只读会话**补测 y+，而**那次会话的 transcript 没有落盘**。
> 直接后果：审查者制造了一条 `cannot_verify`（"y+ 的原始证据不可核"），
> 以及一处**无法独立复核的数值分歧**（vertex average 16.487 vs 16.484）。
> 而 y+ 正是那个算例选择湍流模型的**全部依据**。

**规则**：

> 任何**产出了证据**的 MCP 会话，其 transcript 必须归档到 run 目录。
> 只读、不产证据的会话可豁免。

第二轮 executor 自己做了（`04_results/mcp-round2-session.trn`），
审查者第 4 条专门核了它 —— **证明这条规则有效**。

**派发时要把这条写进 prompt**，尤其是那些"补测一个量"的收尾会话——
正是那种会话最容易漏掉归档。

### 坑 4：scratch 目录要统一，别每个 agent 现编

路径含非 ASCII 时需要一个纯 ASCII 的落点（见 `references/mcp-tool-truths.md#9`）。
问题是那次运行里，**不同 agent 各自现编了一套**：

```
C:\fluent-scratch\           ← 一个 agent 编的
C:\fluent-scratch\wp-run\    ← 另一个 agent 编的
```

**换个 run 就是另一套路径，不可复现。**

**规则**：scratch 落点由 `SKILL.md` 统一定义，并记进 `00_env.json`：

```
<scratch_root> = C:\fluent-scratch\<run-id>\
```

派发 agent 时**直接给它这个路径**，不要让它自己想。这样每个 run 的
临时产物位置是确定的、可回溯的。

**这个路径要作为 `connect_kwargs` 的 `cwd` 传下去**（不只是"绕开中文路径"，
是常规做法 —— 不传的话 Fluent 会把 `.trn` 写进项目根）。给 agent 的 prompt 里
把 `cwd` 一起写明。详见 `references/mcp-tool-truths.md#3`。

---

## S3.5 规范预审 ★ 求解之前，别等跑完才发现规范有问题

**这是一次真实运行暴露的架构性浪费**：审查只在 S5（求解之后）做。于是任何
**规范层面**的问题 —— 模型选错、边界条件类型错、判据不可达 —— 都要**先在 S4
烧掉几十分钟求解**，才在 S5 被指出来。

那次运行正是如此：规范里 `残差目标 1e-6 + max_iterations=3000 + 冻死数值格式`
三者自相矛盾，**在 `02_spec.json` 里一眼可见**，但没人看。跑到第 3000 步、
45 分钟后才由审查指出。

**规范预审几分钟就能挑出它。**

### 怎么做

S3 人工关卡通过后、S4 派执行 agent **之前**，派一次 `cfd-reviewer`，
**只给它 `02_spec.json` 与 `01_probe.json`**，明说：

> 这是**求解前**的规范预审，不是结果审查。**不要连 MCP、不要跑 Fluent。**
> 只读这两个 JSON，判断规范本身站不站得住。

### 检查什么

| 检查 | 判据 |
|---|---|
| **判据可达吗** | 目标值 vs `01_probe.json` 里的量级估算、vs 同类问题的常见平台。目标比可预期的平台低 2 个数量级以上 → 不可达 |
| **目标与上限相称吗** | 定 `1e-6` 却给 `3000` 步，且没有停机机制 → 必然跑满 |
| **数值格式与目标相容吗** | 例如冻死 SIMPLE + 默认松弛，却要求 `1e-6` —— 分离式求解器到不了那个量级 |
| **模型与 Re 匹配吗** | 湍流模型 vs 算出的 Re；壁面处理 vs 网格能给到的 y+ |
| **边界条件类型对吗** | 出口用 velocity-inlet（过约束）、对称面用成 wall、该动的壁面没动 |
| **单位/尺度有依据吗** | 网格无单位元数据时，缩放倍率是谁定的、依据是什么 |
| **判据良构性** | 见 `spec-schema.md` 的检查表（可求值 / 有容差 / 有作用域 / 有退化分支 / 有成本意识） |

### 结论与路由

- **放行** → 进 S4
- **发现问题** → **回 S2**，带上 `feedback.instructions`（同 `route=spec`）
  → 改完**重走 S3 人工关卡**再预审

> ⚠️ **预审不是可选项。** 它省下的是"跑完一轮才发现规范错了"。
> 那次运行的 45 分钟里，约 40 分钟本可被这一步拦下。
>
> 成本对比：预审是**一次只读 agent 调用（几分钟）**，而漏掉它的代价是
> **一次完整求解（几十分钟到几小时）**。

---

## S3 人工关卡 ★ 必做

`02_spec.json` 写完后**必须停下来**，把关键决策摆给用户确认，再进入执行。

```
规范已生成，请确认以下物理设定：

【需求理解】<normalized>
【湍流模型】laminar —— Re=1200 < 2300 转捩阈值
【边界条件】wall-top: 动壁面 1.0 m/s；wall-bot: 静止无滑移
【材料】water-liquid，25°C 常物性
【收敛判据】残差 < 1e-6，进出口质量不平衡 < 0.5%
【导出】速度剖面 CSV、残差历史、云图

【替你做的假设】<assumptions>
【存疑】<open_questions>

确认后开始求解。要改哪里直接说。
```

**这一步不能省。** CFD 跑一轮可能几十分钟，模型选错就是纯浪费。用户明确要求保留
这道关卡。

用户有异议 → 让 spec-author 改 `02_spec.json` → 再次确认。

### ★ 修订轮的关卡可以降级 —— 但只在"纯记账"时

**默认规则是：`spec` 回退后重走 S3 关卡**，因为物理设定可能变了。

但**无条件重走会让用户为记账问题做决策**。一次真实运行中，v1→v2 的
**写入求解器的字段逐字段完全相同**，变的只有 `physics.viscous.achievable_y_plus`
（估算值 → 实测值）这种描述性内容，却仍走了完整关卡，
把"y+ 描述字段是否保留实测值"这种问题摆给了用户。

**做法**：要求 spec-author 在修订时产出 **`physics_diff`**（v1 vs v2 逐字段），
然后据此分流：

| `physics_diff` 显示 | 关卡形态 |
|---|---|
| **写入求解器的字段变了**（`solver`/`materials`/`boundary_conditions`/`initial_condition`/`transient`/`numerics`） | **完整关卡**（默认规则） |
| **只有描述性字段变了**（`rationale`/`achievable_*`/`measured_*`/`open_questions`） | **降级为通知 + 只就新增的存疑项提问** |

> **降级 ≠ 取消用户的否决权。** 用户仍可推翻；只是不必为记账问题做决策。
> **真正新增的 `open_questions` 必须单独提问。**
>
> 也不能因为"这轮只是记账"就跳过 —— 那次运行里用户**确实**面临一个真决策
> （是否延长模拟时间），所以关卡不是纯浪费。**要砍的是记账类提问，不是关卡本身。**

详见 `references/spec-schema.md#修订轮physics_diff`。

### ★ 关卡决定必须【落盘】

**用户拍板之后，立刻把决定写进 `runs/<run-id>/00_input.md`**（追加一节
`# S3 人工关卡记录`），逐条记：**议题 / 用户裁决 / 时间**。

```markdown
# S3 人工关卡记录

## 第 N 次关卡（规范 v<N>，<日期>）

| 议题 | 用户裁决 |
|---|---|
| <规范提出的存疑项> | <用户的选择> |
```

**为什么必须落盘**：审查 agent **不连 MCP、只能读盘**。关卡决定只存在于对话里的话，
它无从核实——上一轮实测中，审查者因此把「人工关卡是否真的放行过周期拓扑改动」
写进了 `cannot_verify`，理由是 `00_input.md` 里没有任何记录。**对话会漂移，文件不会**，
这跟整条流水线"靠落盘产物通信"的地基是同一条道理。

若 `spec` 回退后重走关卡（见 S6），**新增一节**，不要覆盖上一次的记录。

---

## S6 路由判定

读 `review_N.json` 的 `route` 字段，**查表跳转，不要自行判断**：

| `route` | 动作 | 计数器 |
|---|---|---|
| `accept` | → S7 出报告 | — |
| `execution` | → S4，带上 `feedback.instructions` | `exec_retry += 1` |
| `spec` | → S2，带上 `feedback.instructions`，**然后重走 S3 关卡** | `spec_retry += 1` |
| `escalate` | → S7 出报告（诊断版），明确告诉用户为什么交给人 | — |

### 循环上限（由你强制，不由审查者决定）

- `execution` 回退 **≤ 3 轮**
- `spec` 回退 **≤ 2 轮**

超限时**强制改写为 `escalate`**，在报告里说明：
- 卡在哪一环
- 每轮的实际差异
- 你的判断：是归因错了，还是问题确实超出 agent 能力

**注意**：`spec` 回退后必须**重走人工关卡**，因为物理设定变了。

### 需要你介入的异常信号

- 连续两轮的 `feedback.instructions` **几乎一样** → 说明回退没生效，很可能是
  归因错了（该走 `spec` 却走了 `execution`，或反之）。停下来重新判断，别硬循环
- 执行 agent 汇报 `solver_disconnected` → 会话丢了，让它重新 `connect` 后重来
- 执行 agent 反复被沙箱拒绝 → 多半在尝试 `open()` 写文件，把
  `pyfluent-recipes.md` 的落盘两路原则再跟它强调一遍

---

## S7 出报告

写 `runs/<run-id>/report.md`。**只写文件和数字，不要粉饰。**

```markdown
# <仿真任务名>

## 结论
<一句话：结果可信 / 不可信，原因>

## 需求
<用户原话>

## 最终设定
<关键物理设定表>

## 结果
<关键数值>

## 验证
<审查做的物理核验，如与解析解的偏差>
<审查置信度，以及它明确说无法验证的项>

## 迭代历史
<每轮的 route 和原因；没有迭代就写"一次通过">

## 证据清单
<04_results/ 下各文件说明>
```

**`cannot_verify` 里列的东西必须出现在报告中。** 审查者说"无法确认 y+ 是否达标"，
用户就必须看到这句话——藏着等于欺骗。

若最终是 `escalate`，报告要写清**为什么人必须介入**，以及已经排除了哪些可能。

---

## 硬规则速查

| 规则 | 理由 |
|---|---|
| 不替 agent 做 CFD 判断 | 你不是领域专家，让专职 agent 干 |
| 每轮新起 agent，用 run 目录传上下文 | 比依赖 agent 记忆可靠 |
| 派发前确认上一轮已 `disconnect` | 单会话，静默互踩 |
| S3 关卡不可跳过 | 用户明确要求 |
| `spec` 回退后重走 S3 | 物理设定变了要重新确认 |
| 路由查表，不自由发挥 | 保证可复现 |
| 报告如实，`cannot_verify` 必须露出 | 诚实比好看重要 |

---

## 参考文档（子 agent 会自己读，你按需查）

`.agents/skills/fluent-cfd-pipeline/references/`

| 文档 | 内容 |
|---|---|
| `spec-schema.md` | `02_spec.json` 字段契约 + **判据良构性检查表** |
| `review-schema.md` | `review_N.json` 契约 + route 判定表 |
| `review-criteria.md` | 审查阈值，带官方出处 |
| `student-limits.md` | Student 版硬限制 |
| `mcp-tool-truths.md` | 怎么跟 MCP server 说话：工具签名、沙箱边界、非 ASCII 路径 |
| **`solver-api-26.1.md`** | **怎么驱动求解：时间推进 API、字段改名、导出关键字、能量账参考温度。executor 必读** |
| `pyfluent-recipes.md` | PyFluent 代码配方 |

---

## 开始之前

**这一步不能省。** 环境问题要在开工前暴露，不要跑到一半才炸。

### 第 0 步：本会话到底能不能用 MCP？★ 最容易搞错的一步

在会话内**调一次 `session_status`**，然后按结果分三种情况处理。
**这三种情况症状相似但修法相反，混了会浪费大量时间。**

| 现象 | 含义 | 处置 |
|---|---|---|
| **工具不存在**（调用直接报"没有这个工具"） | 会话启动时**没加载 `.mcp.json`** | ★ **必须重载 Codex 会话**。跑任何脚本都**没用** —— 见下方警告 |
| 工具存在，**返回正常内容** | 一切就绪 | 继续 |
| 工具存在，但**返回错误** | server 起来了，但连不上 Fluent | 转到下面的①②③排查 |

> ### ⚠️ 别把"会话未加载"当成"接线坏了"
>
> 这两种情况的症状**一模一样**（工具都不见了），但：
>
> - `scripts/setup.py --check` 验证的是**磁盘上的配置文件**
> - 它**验证不了当前会话有没有加载**那个配置
>
> 所以会出现这种死循环：**`setup.py --check` 报"接线正常"，但本会话一个 MCP 工具都调不到。**
>
> **判据很简单**：如果 `session_status` 是**工具本身不存在**（而不是返回错误），
> 那就是会话旧了——**直接让用户重载会话，不要跑任何脚本**。
> `.mcp.json` 只在会话启动时加载，改完配置必须重载才生效。

### 第 1 步：环境快照落盘

```bash
.venv/Scripts/python.exe scripts/preflight.py --case "<网格>" --run-dir runs/<run-id>
```

写 `runs/<run-id>/00_env.json`：MCP 接线、Fluent 版本、包版本、网格维度与规模、
Student 限额对照、**路径 ASCII 检查**。**几毫秒完成，不启动 Fluent。**

同时它会报出接线断裂、case 不存在、网格超 Student 上限、**路径含中文**等问题。

> ⚠️ **路径含非 ASCII 字符会让 Fluent 崩，而报错指向错误方向**——它会报
> `utf-8 can't decode byte 0xb8`，**看起来像网格损坏，实际不是**。
> 项目根（Fluent 的启动目录）已实测确证；case 路径与输出目录是同源风险。
>
> **若执行阶段出现 `utf-8 can't decode` / `UnicodeDecodeError`，
> 先查路径有没有中文，再去怀疑网格。** 顺序反了会浪费大量时间。
> 详见 `references/mcp-tool-truths.md#9`。

然后**把上面第 0 步的 MCP 会话实测结果补进 `00_env.json` 的 `mcp_session` 字段**
（脚本查不到这一项，故意留成 `unknown` 并附了填法说明）。

### 第 2 步：建 run 目录

把用户原话写进 `00_input.md`。

### 若确属"server 起不来"（第 0 步的第三种情况）

按这个阶梯查，从快到慢：

```bash
.venv/Scripts/python.exe scripts/setup.py --check   # ① 接线断在哪一环（最快）
.venv/Scripts/python.exe scripts/check_mcp.py       # ② MCP 协议层
.venv/Scripts/python.exe scripts/smoke_test.py --case "<网格>"   # ③ 真拉 Fluent
```

①会直接指出是 venv 缺了、MCP 包没装、还是 `.mcp.json` 的路径失效（**换机器/换目录
后最常见**）。若是路径失效，跑一次 `scripts/setup.py` 重写，**然后重载会话**。

详见 [INSTALL.md](../../../INSTALL.md)。
