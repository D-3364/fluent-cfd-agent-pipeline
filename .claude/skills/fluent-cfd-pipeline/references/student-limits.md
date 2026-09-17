# ANSYS Student 限制

本机是 **ANSYS Student 2026 R1 (v261)**。这是**硬件之外的第一道硬约束**，
定规范阶段就要对照，不要等跑到一半才发现。

来源：`https://www.ansys.com/academic/students/ansys-student`（"Problem Size Limits" 段）

---

## 限制表

| 项 | 限制 |
|---|---|
| **流体物理（Fluent / CFD）** | **100 万单元/节点** |
| **HPC** | **最多 4 个 CPU 核**；GPU 上限 **40 SM** |
| 结构物理 | 128K 节点/单元 |
| Ansys Motion | 每个柔性体 100K 节点 |
| Ansys Rocky — DEM | 最多 32K 颗粒；SPH 最多 128K 单元；无 GPU |
| Ansys SPEOS | 无 Live Preview、无 GPU 计算 |
| Tolerancing | 最多 4 个 CPU 核 |

> ⚠️ **不要硬编码 512,000。** 网上流传的 512K 是**旧版** Student 的数字。
> 当前官方页面写的是流体物理 **100 万**。

---

## 关键：超限是硬中止，不是静默截断

超限时的实际报错：

> *"Your product license has numerical problem size limits, you have exceeded these
> problem size limits and the solver cannot proceed."*

网格划分路径下更直接：*"Exiting due to licensing issue."*

### 这对审查意味着什么

**① 结果文件存在 ⟹ 网格当时在上限内。**
既然超限会中止，就不存在"结果是用缩水网格悄悄算出来的"这种情况。审查者不必怀疑这个。

**② 但真正的风险恰恰由此而来。**

正因为超限会中止，用户就**有动机把网格改粗以塞进限制**。而网格一粗，
**网格无关性就被破坏了**——这个后果**不会留下任何报错痕迹**，跑出来的数照样收敛、
照样"合理"，但可能是错的。

**所以审查的正确靶子是：有没有做过网格无关性验证，而不只是查单元数。**

---

## 判定规则

| 情况 | 判定 |
|---|---|
| 单元数 ≥ 1,000,000 | 本机跑不出来，`escalate` |
| 单元数接近上限（> 80 万） | 提示可能为了塞进限制而牺牲了网格质量 |
| 单元数远小于同类问题常规规模，**且无网格无关性说明** | `minor` finding 或 `cannot_verify` |
| 声称用了 > 4 核 | 来源可疑，本许可产不出来 |
| 声称用了大规模 GPU 并行 | 同上（40 SM 只是现代数据中心 GPU 的一小部分） |

> ⚠️ **不要断言"并行 UDF 被禁用"。**
> 官方许可页把限制表述为**核数上限**，并没有说并行 UDF 是硬开关。
> 没有 v261 专项来源就不要下这个结论——**编造限制和忽略限制一样有害**。

---

## 离线检查单元数

**不用启动 Fluent**，直接读网格文件头：

```bash
.venv/Scripts/python.exe scripts/probe_mesh.py "<网格文件>"
```

输出维度、单元数、节点数、面数，并自动对照本表判断是否超限。

在**勘察阶段之前**就可以跑，几毫秒出结果——超限的话当场止损，
不必浪费时间启动 Fluent。

> 附带好处：能读出维度（2D/3D）。维度不匹配会让 `read_mesh` 直接失败，
> 白费一次完整的启动周期（实测约 25 秒）。

---

## 与审查判据的关系

本表是 `review-criteria.md` 的**前置约束**——判据再漂亮，超了许可限制也跑不出来。
所以审查顺序上，本表**先于** `review-criteria.md` 的 §1（网格质量）：

```
1. 查许可限制（本表）  → 超了直接 escalate，不用往下看
2. 查网格质量          → review-criteria.md §2
3. 查收敛              → §3
4. 查物理合理性        → §4、§5
```

---

## 对定规范 agent 的要求

写 `02_spec.json` 时：

- `case.within_student_limit` 必须如实填写（用 `probe_mesh.py` 的结果）
- **若超限**：不要试图通过改粗网格来"解决"——那是把问题藏起来。
  应当照实记录，让审查者走 `escalate`，由人决定是换机器还是降规模
- 若确实因为需求而必须缩小规模，**在 `open_questions` 里显式写明**
  "为适配 Student 限制而降低了网格规模，结果可能受网格影响"
