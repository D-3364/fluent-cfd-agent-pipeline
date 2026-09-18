---
description: 启动 CFD 流水线：给建模文件和需求，自动勘察、定规范、求解、审查、出报告
argument-hint: <case文件路径> <需求描述>
---

用 `fluent-cfd-pipeline` skill 处理下面这个 CFD 任务。

**case 文件**：$0

**需求**：$1

> ### ⚠️ 位置参数是 **0-based** —— 不要"修"成 `$1`/`$2`
>
> `$0` 是**第一个**参数，`$1` 是**第二个**。官方文档原文：
>
> > `$ARGUMENTS[N]` — Access a specific argument by **0-based** index, such as
> > `$ARGUMENTS[0]` for the first argument.
> > `$N` — Shorthand for `$ARGUMENTS[N]`, such as `$0` for the first argument
> > or `$1` for the second.
>
> **这个文件原本写的是 `$1`/`$2`，于是**：case 路径的位置拿到了**需求文本**，
> 而 `$1` 拿到需求、`$2` 因为不存在**原样留在正文里**。
> 整轮跑在错位的输入上，**而且不报任何错**。
>
> **开工前做参数自检**，出现下列任一情况就**停下来问用户**，不要硬跑：
>
> - `$0` 看起来不是路径（比如塞进来的是需求文本）
> - `$1` 仍显示字面的 `$1`
> - case 文件不存在或读不了

---

按 skill 里的状态机执行，注意：

0. **开工前先做环境检查**（见下），不要跳过
1. 建 `runs/<run-id>/`，把用户原话写进 `00_input.md`
2. S1 派 `cfd-spec-author` 做只读勘察（完成后确认它已 `disconnect`）
3. S2 派 `cfd-spec-author` 定规范，产出 `02_spec.json`
4. **S3 停下来**，把关键物理设定摆给用户确认——这一步不能跳过
5. **S3.5 派 `cfd-reviewer` 做规范预审**——只给 `02_spec.json` 和 `01_probe.json`，
   明说"**求解前的预审，不要连 MCP、不要跑 Fluent**"。挑出判据不可达、
   目标与上限不相称、模型与 Re 不匹配、BC 类型错、单位没依据等问题。
   发现问题 **回 S2**（改完重走 S3 关卡）。
   **这一步是求解前的最后一道闸，不能省** —— 它省下的是一次完整的白跑求解。
6. S4 派 `cfd-executor` 求解并导出证据（完成后确认它已 `disconnect`）
7. S5 派 `cfd-reviewer` 独立审查（模式 B：结果审查），产出 `review_N.json`
8. 按 `route` 查表跳转，守住迭代上限
9. S7 写 `report.md`

若用户没给 case 路径，先问清楚再开始。

---

## 第 0 步：环境检查 ★

### (a) 本会话能不能用 MCP？

**先调一次 `session_status`**，按结果分三种情况：

| 现象 | 含义 | 处置 |
|---|---|---|
| **工具不存在**（调用直接报"没有这个工具"） | 会话启动时没加载 `.mcp.json` | ★ **停下来让用户重载会话并中止**。**不要跑任何脚本**——它们查的是磁盘配置，会报"接线正常"，然后你就在死循环里 |
| 返回正常内容 | 就绪 | 继续 |
| 返回错误 | server 起来了但连不上 Fluent | 转 (c) 排查 |

> ⚠️ **最容易搞错的地方**：`setup.py --check` 全绿 ≠ 本会话能用 MCP。
> 前者查磁盘配置，后者是会话启动时决定的。**判据是"工具本身在不在"**，
> 不是命令输出的内容。

### (b) 环境快照落盘

```bash
.venv/Scripts/python.exe scripts/preflight.py --case "$0" --run-dir runs/<run-id>
```

写 `00_env.json`：MCP 接线、Fluent 版本、包版本、网格维度与规模、Student 限额对照。
**几毫秒，不启动 Fluent。** 顺便能拦住：接线断裂、case 不存在、网格超 Student 上限。

**然后把你 (a) 的实测结果补进 `00_env.json` 的 `mcp_session` 字段**
（脚本查不到，故意留成 `unknown`）。

### (c) 确属 server 起不来时才排查

```bash
.venv/Scripts/python.exe scripts/setup.py --check   # ① 最快，指出断在哪一环
.venv/Scripts/python.exe scripts/check_mcp.py       # ② MCP 协议层
.venv/Scripts/python.exe scripts/smoke_test.py --case "<网格>"   # ③ 真拉 Fluent
```

①若报 `.mcp.json` 路径失效（换机器/换目录后最常见），跑一次
`scripts/setup.py` 重写，**然后重载会话**。
