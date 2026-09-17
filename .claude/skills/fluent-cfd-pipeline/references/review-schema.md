# `review_N.json` 契约

由 **cfd-reviewer** 产出。主对话读其中的 `route` 字段决定跳转。

## 设计意图

审查输出必须**结构化且可判定**，不能让主对话去"理解"一段散文再决定往哪跳。
`route` 是枚举，跳转规则是查表，不是判断。

同时，每一条不通过的判定都必须带 **`basis`（依据）** 和 **`source`（出处）**。
审查者不许说"我觉得这个不对"——要么引用 `review-criteria.md` 里的阈值条目，
要么给出 ANSYS 官方文档 URL，要么承认自己**无法判定**（写进 `cannot_verify`）。

---

## 两遍审查

审查者要分开做两件事，因为它们的失败归因不同：

| 遍次 | 问题 | 失败归因 |
|---|---|---|
| **A 合规性** | 执行结果达成了 `02_spec.json` 里 `convergence.acceptance` 写的判据吗？ | 归 **执行 agent** → `route: "execution"` |
| **B 合理性** | `02_spec.json` 本身的物理设定站得住吗？ | 归 **定规范 agent** → `route: "spec"` |

**必须先做 A 再做 B**，且 A 不过时 B 的结论不可信——一个没收敛的解，
讨论它的物理合理性没有意义。

> B 遍次的关键：审查者要拿 `requirement.raw`（用户原话）对照 `rationale`，判断
> "这个选择在物理上是否成立、是否真的回应了用户的需求"。这一步不需要跑 Fluent，
> 需要的是 CFD 知识 + 官方依据——这正是 `curl` 查官网的用武之地。

---

## 结构

```jsonc
{
  "schema_version": "1.0",
  "run_id": "couette-flow-20260916-143022",
  "review_iteration": 1,
  "reviewed_artifacts": [
    "02_spec.json", "03_journal.py",
    "04_results/residuals.json", "04_results/velocity-profile.csv"
  ],
  "artifacts_missing": [],          // 缺失的证据文件，会直接压低可信度

  // ── A 合规性 ──
  "compliance": {
    "verdict": "pass",              // pass | fail
    "criteria": [
      {
        "criterion": "残差全部低于目标值",       // 抄自 spec.convergence.acceptance
        "expected": "continuity < 1e-6",
        "observed": "continuity 终值 3.2e-7",
        "met": true,
        "evidence": "04_results/residuals.json:continuity[-1]"
      }
    ]
  },

  // ── B 合理性 ──
  "validity": {
    "verdict": "pass",              // pass | fail
    "findings": [
      {
        "field": "physics.viscous.model",        // 定位到 spec 的具体字段
        "issue": "选了 k-epsilon，但模型适用范围与算得的 Re 数不符（须给出你的独立计算）",
        "severity": "blocking",     // blocking | minor
        "basis": "review-criteria.md#湍流模型选择",
        "source": "https://...（查证到的官方链接，或 null）"
      }
    ]
  },

  // ── 物理合理性核验（有解析解/守恒律可对照时必做）──
  "physical_checks": [
    {
      "check": "壁面剪应力 vs 解析解 τ=μU/h",
      "expected": "1.003 Pa",
      "observed": "0.998 Pa",
      "relative_error": "0.5%",
      "verdict": "pass"
    }
  ],

  // ── 路由 ──
  "route": "accept",                // accept | execution | spec | escalate
  "route_reason": "一句话说明为什么是这个 route",

  // ── 给下一轮 agent 的具体指令 ──
  "feedback": {
    "to": null,                     // "cfd-executor" | "cfd-spec-author" | null
    "instructions": []              // route=accept 时为空
  },

  "confidence": "high",             // high | medium | low
  "cannot_verify": [                // 诚实的"我判不了"，不要伪装成通过
    "无法确认 y+ 是否达标：执行未导出 y+ 场"
  ]
}
```

---

## `route` 判定表（**必须严格照此判定**）

按顺序判定，**第一条命中即返回**：

| # | 条件 | `route` | 去向 |
|---|---|---|---|
| 1 | 存在 `severity: "blocking"` 的合理性 finding，或 `validity.verdict == "fail"` | `spec` | 回 S2，`feedback.to = "cfd-spec-author"` |
| 2 | `compliance.verdict == "fail"` | `execution` | 回 S4，`feedback.to = "cfd-executor"` |
| 3 | 根因不在 agent 可控范围（见下） | `escalate` | 终止 |
| 4 | A、B 均 pass，且 `cannot_verify` 不涉及关键判据 | `accept` | 出报告 |

**第 1 条优先于第 2 条**：如果规范本身是错的，让执行 agent 再跑一遍只会浪费一轮求解。

### 第 3 条"根因不可控"的判定

只有以下情况才算，**不许滥用**（它是逃生舱，不是偷懒的出口）：

- 网格质量不达标（最小正交质量、最大长宽比超限），且需求不涉及重新划分网格
- 网格规模超出 ANSYS Student 限制
- 用户需求本身自相矛盾，无法通过任何设定解决
- 关键证据文件缺失且无法重新生成

### 循环上限（由主对话强制，不由审查者决定）

- `execution` 回退 ≤ 3 轮
- `spec` 回退 ≤ 2 轮
- 超限时主对话**强制改写为 `escalate`** 并输出诊断报告。

审查者只管判，不看计数器——但如果它发现连续两轮的失败原因完全一样，
应当在 `route_reason` 里显式指出（说明回退没起作用，可能是归因错了）。

---

## 给审查者的硬约束

1. **不许空口判断。** 每条 `fail` 的 finding 必须有 `basis`。引不出阈值条目、
   也查不到官方链接的，写进 `cannot_verify`，不要伪装成 finding。
2. **不许改东西。** 审查者没有 MCP 权限、不能连 Fluent、不能修改 `04_results/` 下的
   任何文件。它只能读、只能写 `review_N.json`。这是架构保证，不是提示词约束。
3. **不许只说"不行"。** `feedback.instructions` 必须是下一轮 agent **可以直接执行**的
   具体动作：
   ```
   ✅ "把 continuity 目标从 1e-4 收紧到 1e-6，并增加进出口质量不平衡监测"
   ✅ "physics.viscous.model 改为 laminar，理由：Re=1200 < 2300 转捩阈值"
   ❌ "结果不够好，再算一次"
   ❌ "收敛性需要改进"
   ```
4. **证据不足时降低 `confidence` 并说明**，不要用高置信度掩盖没验证的东西。
   一个诚实标了 `low` confidence 和三条 `cannot_verify` 的审查，比一个虚假的
   `accept` 有用得多。
