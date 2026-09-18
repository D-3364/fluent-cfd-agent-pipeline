# fluent-cfd-agent-pipeline

用三个 agent 把「网格文件 + 自然语言需求」变成经过独立审查的 CFD 结果。

```
定规范 ──► 执行 ──► 审查 ──► 出报告
   ▲                  │
   └──────────────────┘  审查不通过则按归因回退
```

做这个是因为 CFD 里一个常见问题：残差收敛不等于结果正确。湍流模型选错、
y+ 不在适用范围、边界条件订错，都会产出看起来正常但其实是错的数。
所以这里的重点不是「能跑起来」，而是「跑出来的东西有人独立验过」。

## 环境要求

- **ANSYS Fluent 及有效许可证**（含免费的 Student 版）。本仓库不包含 Fluent。
- **Windows**。脚本使用 `.venv/Scripts/`、`tasklist` 等约定，其他平台需自行适配。

本项目非 ANSYS 官方产物，与 ANSYS, Inc. 无隶属或背书关系。它构建在官方的
[ansys-fluent-mcp](https://github.com/ansys/pyfluent-mcp) 之上，
「Ansys」「Fluent」是 ANSYS, Inc. 的商标，此处仅作描述性使用。

> **没有 Fluent 许可证？** `references/` 下有一批实测记录不需要 Fluent 也能读
> —— Fluent 26.1 的求解 API 陷阱、带官方出处的收敛与网格阈值、
> 怎么写可判定的验收判据。见 [无关 Fluent 也能读的部分](#无关-fluent-也能读的部分)。

## 安装

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install ansys-fluent-mcp
.venv/Scripts/python.exe scripts/setup.py
```

`setup.py` 会检测本机 Fluent 的安装位置并生成 `.mcp.json`（该文件依赖机器路径，
不入版本控制）。跑完需要重载 Claude Code 会话，因为 `.mcp.json` 只在会话启动时加载。

换机器或换目录后重跑一次 `setup.py` 即可。详见 [INSTALL.md](INSTALL.md)。

装完可以分层验证环境：

```bash
.venv/Scripts/python.exe scripts/probe_mesh.py "<网格>"        # 离线读网格元信息
.venv/Scripts/python.exe scripts/check_mcp.py                  # MCP 协议层
.venv/Scripts/python.exe scripts/smoke_test.py --case "<网格>"  # 真拉起 Fluent
.venv/Scripts/python.exe scripts/e2e_mcp.py --case "<网格>"     # MCP 全链路
```


## 使用

两个入口，共用同一套 `references/`：

```
/fluent-run   "<网格文件>" "<需求>"     # 完整流水线：勘察 → 定规范 → 求解 → 独立审查
/fluent-quick "<网格文件>" "<需求>"     # 轻量模式：单 agent 直接跑，无独立审查
```

例如：

```
/fluent-run "D:/cases/couette.msh" "两平板间库埃特流，上板以 1 m/s 移动，下板静止，介质为水，层流"
```

**怎么选**：

| | `/fluent-run` | `/fluent-quick` |
|---|---|---|
| agent | 3 个 + 调度 | 1 个 |
| 独立审查 | 有 | 无 |
| 中途停一次让你确认物理设定 | 有 | 无 |
| 耗时 | 长（实测一次 78 分钟 / 1.1M token） | 短 |
| 适合 | 结果要对外交付、要对物理设定负责 | 探索试算、改参数看效果、先跑通再说 |

`/fluent-run` 会在定完规范后停一次，把物理设定（湍流模型、边界条件、材料、
收敛判据）摆出来让你确认，确认后才开始求解 —— 求解可能几十分钟，
这一步是为了避免模型选错白跑。

产物在 `runs/<算例名>-<时间戳>/`：

| 文件 | 内容 |
|---|---|
| `report.md` | 最终报告，先看这个 |
| `00_env.json` | 环境快照：Fluent 版本、网格规模、MCP 接线状态 |
| `01_probe.json` | 勘察到的网格事实（边界名、规模、质量） |
| `02_spec.json` | 物理设定，每条带理由 |
| `03_journal.py` | 实际执行过的全部代码 |
| `04_results/` | 证据：case/data 文件、残差历史、剖面 CSV、云图 |
| `review_N.json` | 审查裁决 |

`report.md` 里有一节「存疑与无法验证」，列出审查者明确说它验不了的项。


## 无关 Fluent 也能读的部分

`references/` 下有一批实测记录，用 PyFluent 的人可能用得上，不需要装 Fluent：

| 内容 | 文件 |
|---|---|
| ansys-fluent-mcp 的工具签名哪些与官方文档不符 | [mcp-tool-truths.md](.claude/skills/fluent-cfd-pipeline/references/mcp-tool-truths.md) |
| Fluent 26.1 求解驱动 API 的坑：时间推进语义、字段改名、导出关键字 | [solver-api-26.1.md](.claude/skills/fluent-cfd-pipeline/references/solver-api-26.1.md) |
| 收敛判据、网格质量、y+ 阈值，带 ANSYS 官方出处 | [review-criteria.md](.claude/skills/fluent-cfd-pipeline/references/review-criteria.md) |
| 怎么写可判定的验收判据 | [spec-schema.md](.claude/skills/fluent-cfd-pipeline/references/spec-schema.md) |
| 判据阈值之外的工程事实：Student 许可限制 | [student-limits.md](.claude/skills/fluent-cfd-pipeline/references/student-limits.md) |

其中两条与 Fluent 无关，任何自动化数值仿真的架构都用得上：

- **审查者不连 MCP。** 审查 agent 拿不到求解器句柄，只能读落盘的证据，
  物理上无法篡改它正在审查的对象。审查独立性由架构保证，而不是靠提示词约束。
- **判据良构性检查表。** 一次真实运行里 13 条验收判据有 4 条是坏的，
  且全是措辞缺陷而非执行问题（基线不存在、没有容差、作用域不明、零事件时退化），
  代价是一整轮回退。

## 工作原理

三个 agent 各自独立派发，中间只通过 run 目录里的文件通信。

| agent | 职责 | 关键约束 |
|---|---|---|
| `cfd-spec-author` | 只读勘察网格，再把需求翻译成物理规范 | 没有 `run_code`，只能读不能改 |
| `cfd-executor` | 照规范在 Fluent 里落实设定、求解、导出证据 | 有 `run_code`，但不做决策 |
| `cfd-reviewer` | 独立判断结果是否可信，输出裁决与归因 | 不接 MCP，只能读证据 |

审查分两遍：合规性（结果达成规范写的判据了吗）和合理性（规范本身的物理设定
站得住吗）。两者失败归因不同，前者回执行 agent，后者回定规范 agent。

迭代上限是执行回退 3 轮、规范回退 2 轮，超限转人工。

细节见 [SKILL.md](.claude/skills/fluent-cfd-pipeline/SKILL.md)。

## 目录结构

```
.claude/
  agents/         三个 agent 的定义
  commands/       /fluent-run 入口
  skills/fluent-cfd-pipeline/
    SKILL.md      调度状态机
    references/   支撑文档
scripts/           接线、验证与巡视脚本
examples/          验证方法与故障注入配方
runs/              每次运行的产物（gitignore）
```

[examples/](examples/) 里是验证方法与故障注入配方。

故障注入是检验审查环节是否真的有效的唯一办法。跑通一次说明不了什么：
一个永远返回 accept 的审查者，和一个真审查者，在顺利的流程里表现完全一样。
只有故意注入错误、看它归因对不对，才检验得出来。

## 已知的坑

- **路径含非 ASCII 会让 Fluent 崩，报错却指向网格。** 项目根目录含中文时 Fluent
  报 `utf-8 can't decode byte 0xb8`，看起来像网格文件损坏；网格路径含中文时报
  `File not found`，而文件明明存在。两种症状都指向错误的方向。检测已内置在
  `preflight.py` 与 `setup.py --check` 中。
- **`connect` 拉起的 Fluent 是空会话。** MCP 不暴露 `read_mesh`，执行 agent
  必须自己读网格。定规范 agent 没有 `run_code`，拿不到边界名，只能另跑独立脚本勘察。
- **`connect` 要传 `cwd`。** 不传的话 Fluent 会把 `.trn` 临时文件写进项目根目录。
- **MCP server 是单会话的。** 第二次 `connect` 会静默拆掉前一个会话，所以三个
  agent 必须串行，每次收尾要 `disconnect`。
- **`run_code` 沙箱封了 `open` / `os` / `subprocess`。** 求解器产物走
  `file.write_*`，结构化数据用 `__return__` 返给 agent 再落盘。

更多见 [mcp-tool-truths.md](.claude/skills/fluent-cfd-pipeline/references/mcp-tool-truths.md)。

## 许可

[Apache-2.0](LICENSE)。依赖与商标声明见 [NOTICE](NOTICE)。
