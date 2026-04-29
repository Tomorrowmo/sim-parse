# sim-parse 设计方案

> **状态**：方案设计 v0.3，未实现
> **作者**：基于本会话讨论收口
> **依赖关系**：sim-parse 下游会被 simgraph2 backend、sim-cli runtime 调用，但**不反向依赖**任何下游

---

## 目录

- [新读者：基础概念速览（FAQ）](#新读者基础概念速览faq) ← **第一次读建议先看**

---

1. [项目概述](#1-项目概述)
2. [三个动作 × 三个知识库（核心架构）](#2-三个动作--三个知识库核心架构)
3. [背景与动机](#3-背景与动机)
4. [目标与非目标](#4-目标与非目标)
5. [用户场景](#5-用户场景)
6. [设计原则](#6-设计原则)
7. [核心概念：信息分层 (Tier)](#7-核心概念信息分层-tier)
8. [信息族目录（提取什么）](#8-信息族目录提取什么)
9. [实现策略 (Path A/B/C/D)](#9-实现策略-path-abcd)
10. [Dispatcher（路径调度器）](#10-dispatcher路径调度器)
11. [Agent 临场推断的支撑机制](#11-agent-临场推断的支撑机制)
12. [QOI 演化闭环](#12-qoi-演化闭环)
13. [输出 Schema](#13-输出-schema)
14. [API 设计](#14-api-设计)
15. [项目结构](#15-项目结构)
16. [配套生态：sim-knowledge / Query Agent / Discovery Agent](#16-配套生态sim-knowledge--query-agent--discovery-agent)
17. [求解器覆盖矩阵 & 阶段交付](#17-求解器覆盖矩阵--阶段交付)
18. [依赖与环境](#18-依赖与环境)
19. [关键设计决策](#19-关键设计决策)
20. [待决策项](#20-待决策项)
21. [风险与开放问题](#21-风险与开放问题)
22. [不在本方案范围](#22-不在本方案范围)
23. [变更历史](#23-变更历史)

---

## 新读者：基础概念速览（FAQ）

> 第一次读这份文档？以下 5 个常见问题先看 —— 然后再进 §1。每条都给了到深度章节的指针。

### Q1. "Tier" 是什么意思？

**Tier = "解析深度的分层"**。每深一层，知道得更多，但耗时也更大：

| Tier | 我了解到的 | 例子 | 成本 |
|---|---|---|---|
| 1 | 这是什么类型的算例 | "这是 OpenFOAM 案例" | 毫秒 |
| 2 | 里面有哪些文件 | "时间步 0 / 5000，变量 T/U/p/CH4/CO2..." | 毫秒 |
| 3 | 求解器怎么配置的 | "reactingFoam + kEpsilon + EDC + GRI-Mech 3.0" | 几十毫秒 |
| 4 | 场数据的统计量 | "T 最大 1998K，Qdot 最大 7.7e8" | **秒级**（要解码二进制） |
| 5 | 工程指标（统一命名） | "lift_coefficient=0.382" | 毫秒 |
| 6 | 完整网格 + 场数据 | 一个 VTU 文件 | 秒~分钟 |
| 7 | 物理意义（推断） | "这是燃烧 case，没熄火" | LLM 几秒 |

**调用方按需选**：simgraph2 入图谱要 Tier 3；可视化要 Tier 6；Query Agent 用 Tier 4+5。详见 [§7](#7-核心概念信息分层-tier)。

### Q2. "Dispatcher" 是不是"级联"？

不完全等同。**Dispatcher 是个组件，"级联"是它的工作方式之一**：

- **Dispatcher（调度器）** —— 实现在 [`core/cascade.py`](../src/sim_parse/core/cascade.py)，干两件事：① 决定走哪条 Path，② 管理 Tier 之间的依赖
- **级联** —— 是这个组件**重试模式**的名字："Path A 失败 → 试 Path B → 试 Path C"

文件名 `cascade.py` 同时表达了"它是 dispatcher 的代码"和"工作方式是级联"。详见 [§10](#10-dispatcher路径调度器)。

### Q3. core / solvers / generic / discovery / adapters / cli 是平级的吗？

**目录上平级，代码上各有职责，且依赖单向**：

```
sim_parse/
├── core/        ← 框架内核（协议 + 调度）── 所有人都依赖它
├── solvers/     ← Path A：每个求解器一个子目录
├── generic/     ← Path B：格式通用兜底
├── discovery/   ← Path C：LLM 兜底（stub）
├── adapters/    ← 共享工具（VTK helper）── solvers 和 generic 共用
└── cli/         ← 命令行入口
```

依赖方向：

```
            cli ──┐
                  ▼
              core ◄────  所有模块都依赖 core
              ╱  │  ╲
             ▼   ▼   ▼
        solvers generic discovery   ← 三条 path 互不调用
             │   │
             ▼   ▼
          adapters    ← solvers 和 generic 共享 VTK 工具
```

关键点：**solvers 和 generic 不互相调用**（它们是不同 path 的实现）。详见 [§15.1 项目结构](#151-sim-parse-自身结构)。

### Q4. sim-knowledge 是什么？

sim-knowledge 是 **sim-parse 的兄弟仓库**（同 workspace 下的另一个目录），**不是代码、是 YAML 知识库**：

| | sim-parse | sim-knowledge |
|---|---|---|
| 内容 | Python 代码 | YAML + 标注真值 |
| 维护者 | 程序员 | 物理工程师 |
| 形态 | 确定性代码 | 结构化数据 |
| 给谁用 | 直接 `import` 调用 | LLM Agent 检索 |
| 跟物理学 | 不懂 | 物理判据存这里 |

**举例**：判断"是否熄火"，sim-parse 写**机制**（投票算法），sim-knowledge 存**参数**（阈值=1e6 W/m³、引用 Williams 1985）。改阈值不动 sim-parse 代码，改 YAML 即可。

详见 [§2 三个动作 × 三个知识库](#2-三个动作--三个知识库核心架构) 和 [§16 配套生态](#16-配套生态sim-knowledge--query-agent--discovery-agent)。

### Q5. 为什么 labeled_case 针对具体 DLR_A_cavity_normal？

因为 labeled_cases 必须是**真实的、具体的、能跑的算例**，作为**"真值锚点"**：

```
sim-parse 提取  →  T_max=1998K, Qdot_max=7.7e8, OH_max=3.1e-3
                               │
                               │ "这案例算熄火吗？" 人看 ParaView 后判
                               ▼
              ground_truth.yaml: { is_extinguished: false }
                               │
                               │ 这就是个真值锚点
                               ▼
        新写的 extinction 判据，跑这个 case，结果应该 == false
        如果不等于 → 判据有 bug
        如果等于 → 这条判据通过了一个真值检验
```

**为什么必须是具体 case？** 因为物理判据有无数边界情况（燃料、压力、尺度都影响阈值），光靠"理论"无法验证，必须真实算例对照。

每个判据需要 **5-10 个 labeled cases**（含 positive + negative 真值）。我们刚建的 `DLR_A_cavity_normal` 是第一个 —— 未来还会加 `CH4_jet_blowout`（真熄火）、`H2_jet_normal`、`kerosene_high_pressure` 等。详见 [§11 Agent 临场推断的支撑机制](#11-agent-临场推断的支撑机制) 和 [criteria_reference.md §11 labeled_cases 联动](criteria_reference.md#11-labeled_cases-联动)。

---

## 1. 项目概述

**sim-parse** 是一个**通用的 CAE 仿真结果解析框架**，对外接口是"喂一个结果文件夹、吐出多层次的结构化数据信息"。它是更大的 sim-* 生态中的"**解析层**"，独立于具体下游消费方（simgraph2 / sim-cli / 自定义脚本），可被任何需要"读取已完成的仿真算例"的工具调用。

sim-parse 自己**只做 per-case 静态提取**，不做：
- 用户问题理解（属于 Query Agent）
- 跨案例聚合查询（属于 simgraph2 / 数据湖）
- 物理判据演绎推断（属于 sim-knowledge + Agent）
- 求解器运行控制（属于 sim-cli driver）

整个生态见 §2。

---

## 2. 三个动作 × 三个知识库（核心架构）

理解一个 CAE 算例的全过程**正交地**分解为三个认知动作，每个动作对应一个独立的**知识库（仓库）**。这是整个 sim-* 生态的脊柱，也是 sim-parse 设计的根。本节是全文档**最重要的一节**，后续所有边界讨论都引用这里。

### 2.1 三动作三仓总表

| 动作 | 一句话描述 | 知识库（仓库） | 内容形态 | 组织轴 | 服务的 Agent | 消费时机 |
|---|---|---|---|---|---|---|
| **跑** (run) | 怎么把求解器跑起来 | **sim-skills/** | markdown 叙事 | per-solver | sim-cli driver Agent | 运行时（live solver session） |
| **读** (extract) | 陌生格式怎么读懂 | **sim-knowledge/formats/** | YAML 结构化 | per-format | Discovery Agent（在 sim-parse Path C 内） | 事后（陌生算例首次解析） |
| **判** (judge) | 这个案例物理上是什么状态 | **sim-knowledge/physics/** | YAML 结构化 | per-physics-domain | Query Agent | 事后（用户查询时） |

**注意**：sim-parse 自身**不在三仓里**，它是一个独立的"代码框架"，连接这三个动作（见 §2.3）。

### 2.2 三仓关系图（layered view）

```
┌────────────────────────────────────────────────────────────────────────────┐
│                       理解一个 CAE 算例需要回答的三类问题                    │
└────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│   "怎么跑？"             │  │   "怎么读？"             │  │   "怎么判？"             │
│   (run)                 │  │   (extract)             │  │   (judge)               │
│                         │  │                         │  │                         │
│  Allrun 失败怎么修？     │  │  陌生 .h5 里有什么？     │  │  哪些 case 熄火？        │
│  decomposeParDict 改哪？ │  │  陌生 solver 输出怎么抓？│  │  这个 case 收敛吗？      │
│  FAIL_PRECHECK 怎么处理？│  │  格式约定是什么？         │  │  Courant 飙升了吗？     │
└──────────┬──────────────┘  └──────────┬──────────────┘  └──────────┬──────────────┘
           │ 知识载体                     │ 知识载体                    │ 知识载体
           ▼                              ▼                             ▼
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│  sim-skills/            │  │  sim-knowledge/formats/ │  │  sim-knowledge/physics/ │
│  ─────────────          │  │  ───────────────────    │  │  ───────────────────    │
│  • markdown 叙事         │  │  • YAML 结构化           │  │  • YAML 结构化           │
│  • per-solver 组织       │  │  • per-format 组织       │  │  • per-physics 组织      │
│  • SKILL.md frontmatter │  │  • + playbooks/         │  │  • + labeled_cases/     │
│  • 已有                  │  │  • + examples/          │  │  • + few_shot/          │
│                         │  │  • 拟建                  │  │  • 拟建                  │
└──────────┬──────────────┘  └──────────┬──────────────┘  └──────────┬──────────────┘
           │ 消费方                       │ 消费方                     │ 消费方
           ▼                              ▼                             ▼
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│  sim-cli driver Agent   │  │  Discovery Agent        │  │  Query Agent            │
│  (LLM)                  │  │  (LLM, in sim-parse     │  │  (LLM, 独立组件)         │
│                         │  │   Path C)               │  │                         │
│  生成代码操作 solver     │  │  探索陌生格式 → 抓字段   │  │  生成 Cypher/SQL → 查询 │
└──────────┬──────────────┘  └──────────┬──────────────┘  └──────────┬──────────────┘
           │                             │                            │
           ▼                             ▼                            ▼
       运行时                       陌生算例首次解析                  用户查询时
   (live solver session)         (onboarding new format)           (query time)


┌────────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│            sim-parse  ←──── 本方案                                          │
│                                                                            │
│      "代码框架"，不是知识库；横跨三个动作之间的连接者：                        │
│        • Path A/B：纯静态代码，不消费任何外部知识库                           │
│        • Path C：消费 sim-knowledge/formats（"读"动作）                      │
│        • 输出 (Tier 1-6)：喂给 Query Agent 做"判"动作的核心数据来源           │
│        • 自带 core/canonical/qoi_rules/（最小 Tier 5 规则，外挂可加载）       │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 sim-parse 在三轴中的位置

sim-parse 是**"代码框架"而非"知识库"**：

| 维度 | sim-parse 是 / 不是 |
|---|---|
| 知识库？ | ❌ 它是代码 + 自带最小 qoi_rules，但不存"跑/读/判"知识 |
| Agent？ | ❌ 确定性代码，无 LLM（Path C 例外，但 LLM 实现在 Discovery Agent 模块） |
| 仅一个 Path 的实现？ | ❌ 4 条 Path（A/B/C/D）并存，Path A 是主力 |
| 三仓的"中转站"？ | ✅ per-case 提取，把原始数据变成可被 Agent 消费的结构化输出 |

具体地：

- sim-parse 的 **Path A**（已知求解器静态 parser）—— **不消费**任何外部知识库
- sim-parse 的 **Path B**（通用格式启发式）—— **不消费**任何外部知识库
- sim-parse 的 **Path C**（LLM Discovery）—— **消费 sim-knowledge/formats**（"读"动作的载体）
- sim-parse 的 **输出**（Tier 1-6）—— 是 Query Agent 完成"判"动作的核心输入
- sim-parse 的 **自带 qoi_rules/**—— 是它自己的内嵌规则，外挂可覆盖；不是知识库

### 2.4 为什么是三个正交动作（不是两个，不是四个）

每个动作回答**完全不同类**的问题。三者**正交**（独立维度，互不替代）：

- 同一物理可以由不同求解器跑（**跑动作不同**，判动作相同）：CH4 燃烧可以用 OpenFOAM 也可以用 Fluent
- 同一格式可以装不同物理（**读动作相同**，判动作不同）：HDF5 可以装 CFD 也可以装结构动力学
- 同一求解器可以输出不同格式（跑动作相同，**读动作可能不同**）：OpenFOAM 可以输出 binary 也可以输出 ASCII，可压缩可不压缩

所以这三个**不能合并**：把"读"和"判"合成一个仓也不行，它们的内容、维护者画像、检索方式都不同。也**不会扩展为更多动作**：所有"理解算例"的认知任务都能落到这三个之一。

### 2.5 三仓详细对照

| 维度 | sim-skills（跑） | sim-knowledge/formats（读） | sim-knowledge/physics（判） |
|---|---|---|---|
| **回答** | "怎么操作求解器" | "怎么读陌生格式" | "怎么用物理判据分类案例" |
| **典型条目** | "OpenFOAM Allrun 失败模式表" | "HDF5 文件 magic + 结构识别" | "extinction 判据：Qdot < 1e6 ∧ T_excess < 100K" |
| **内容形态** | markdown 叙事文 | YAML 结构化（schema） | YAML 结构化（rule + threshold + reference） |
| **组织目录** | per-solver（openfoam/ fluent/ ...） | per-format（hdf5/ cgns/ ...）+ playbooks/ + examples/ | per-physics-domain（combustion/ heat_transfer/ ...） |
| **维护者画像** | DevOps + 仿真工程师 | 数据工程师 + 文件格式专家 | 物理工程师 + 学者 |
| **测试 / 真值** | tests/fixtures（运行回归） | examples/（成功识别历史） | **labeled_cases/（标注真值）✓ 关键** |
| **跨求解器复用** | 不太能 | 部分（HDF5 不分谁写的） | **能**（物理是普世的） |
| **被谁消费** | sim-cli driver Agent | Discovery Agent (sim-parse Path C) | Query Agent |
| **消费时机** | 运行时 | 事后（首次遇到陌生格式） | 事后（用户查询） |
| **演化驱动** | 求解器版本迭代 | 新格式被发现 | 用户查询 → QOI 演化闭环（§12） |
| **当前状态** | 已有 | 拟建 | 拟建 |

### 2.6 三个知识库的关键边界（避免后续错位）

- **sim-parse 不内嵌 LLM**：Tier 1-6 全是确定性代码。LLM 只出现在 sim-parse **外部**的两个 Agent 中（Discovery Agent / Query Agent）
- **sim-parse 不"维护知识"**：自带的 `core/canonical/qoi_rules/` 是规则文件不是知识库；真正的知识库都在 sim-skills 和 sim-knowledge
- **三个仓库的 PR / 维护节奏独立**：sim-skills 跟着求解器版本走，sim-knowledge 跟着用户问题走（QOI 闭环），sim-parse 跟着自身代码节奏走
- **跑 / 读 / 判 不要混进同一个 yaml**：sim-knowledge/physics 不放求解器命令，sim-skills 不放物理判据 —— 即使内容相关，载体应该分开
- **本设计文档（design.md）只详细描述 sim-parse**：sim-skills 已有自己的 CLAUDE.md / SKILL.md；sim-knowledge 待建独立设计文档（参见 §16）

### 2.7 三动作的完整生命周期举例

以"用户问一个新算例熄火没"为例，看三动作如何串起来：

```
[Day 1] 工程师跑一个新的 reactingFoam 燃烧算例
              │
              ▼
        sim-cli driver Agent ◄──── consumes ──── sim-skills/openfoam/
              │
              │ "跑"动作
              │ (Allrun 失败 → 查 failure_patterns.md → 修)
              ▼
        [跑通了，留下结果文件夹]


[Day 2] simgraph2 自动入库该算例
              │
              ▼
        sim-parse.parse_case(case_root, target_tier=4)
              │
              │ Path A 命中（已知是 OpenFOAM）
              ▼
        提取 Tier 4 信号：Qdot/T/OH/...      ◄──── 此处不需要任何知识库
              │
              ▼
        [输出 JSON 入 Neo4j]


[Day 5] 另一个用户问 "哪些案例熄火？"
              │
              ▼
        Query Agent ◄──── consumes ──── sim-knowledge/physics/combustion/
              │                              criteria.yaml::extinction
              │ "判"动作                      labeled_cases/combustion_extinction/
              │ (套用判据 → 校准 → 翻译 Cypher → 跑库)
              ▼
        [返回 5 个熄火案例 + reasoning trace]


[Day 30] simgraph2 收到一个完全陌生的 .h5 文件（in-house solver）
              │
              ▼
        sim-parse.parse_case(case_root)
              │
              │ Path A 失败（不认识）→ Path B 部分识别 → Path C 启动
              ▼
        Discovery Agent ◄──── consumes ──── sim-knowledge/formats/hdf5/
        (在 sim-parse Path C 内)            structure.yaml
              │                              playbooks/unknown_hdf5_inspection.yaml
              │ "读"动作                      examples/successful_discoveries.json
              │ (LLM 探索 → 推断结构 → 调 h5py 抓字段)
              ▼
        [输出 inventory + 提议把这个 solver 加入 Path A 或 sim-knowledge/formats/]
```

三个动作各司其职、不互相替代。**任何"理解 CAE 算例"的工程任务都会落到这三个动作之一**。

---

## 3. 背景与动机

### 3.1 来源需求

1. **simgraph2 §3.3 模块三**：需要从 OpenFOAM/Fluent/CGNS/HDF5 等结果文件夹提取确定性元描述 JSON 给 LLM 模块和图谱使用（要求"绝不抛异常、未提取字段填 null"）
2. **跨求解器 QOI 对比**（场景 2/3）：CL/CD、Nu、最大温度、模态频率等工程量需要进入数据湖，做参数扫掠和跨求解器聚合
3. **可视化并排**（场景 4）：需要把任意求解器的结果导出 VTU 给 ParaView
4. **未来扩展**（场景 1，求解器验证）：需要完整 mesh+field 数据 —— 当前不做但架构要预留

### 3.2 现有空缺

- sim-cli 的 `DriverProtocol` 只覆盖**运行时**（`run_file` / `lint` / `parse_output(stdout)`），不覆盖**结果文件解析**
- simgraph2 backend 计划自建 `parsers/` 模块，但工作量大且和 sim-cli 体系割裂
- 行业内没有现成的"统一多求解器结果解析层"开源工具

---

## 4. 目标与非目标

### 4.1 目标（按优先级）

| 优先级 | 目标 | 说明 |
|---|---|---|
| **P0** | 给定一个结果文件夹，输出**分层级**结构化信息（Tier 1-6） | 7 层分层见 §7 |
| **P0** | 第一批支持 **OpenFOAM**（含主流 solver 家族） | 用 [DLR_A_cavity](D:/Git/SimGraph2/test_data/DLR_A_combustion/DLR_A_cavity) 验收 |
| **P0** | 调度器（dispatcher）自动级联实现策略（Path A/B/C/D） | 见 §10 |
| **P0** | 不抛未捕获异常；缺失字段填 `None` + 标记来源 | simgraph2 硬性要求 |
| **P1** | 第二批支持 **CGNS / HDF5 / Fluent / LS-DYNA** | 都有 VTK reader |
| **P1** | 提供 Python API + CLI 双形态 | `import sim_parse` 和 `simparse <case>` |
| **P1** | 用户可注册自定义 parser 插件 | 扩展长尾求解器（Path D） |
| **P2** | Tier 7 LLM 语义推断 | 接 simgraph2 模块四的 Ollama 或外部 LLM |
| **P2** | Path C LLM Discovery（陌生格式兜底） | 涉密环境可关闭；依赖 sim-knowledge/formats |

### 4.2 非目标（明确不做）

| 不做什么 | 原因 |
|---|---|
| **运行求解器** | sim-cli driver 干这个 |
| **图谱写入 / 数据库交互** | simgraph2 backend 的事，sim-parse 只输出 JSON / Parquet |
| **回答用户自然语言问题** | **Query Agent** 的事（不在 sim-parse 内） |
| **跨案例聚合查询**（"哪些案例熄火"） | simgraph2 / DuckDB 的事 |
| **物理判据知识库** | **sim-knowledge/physics** 的事（独立仓库） |
| **格式探索知识库** | **sim-knowledge/formats** 的事（独立仓库） |
| **跨网格场插值（mesh-to-mesh mapping）** | 场景 1 不在当前范围 |
| **可视化渲染** | sim-parse 只导 VTU，渲染交给 ParaView |
| **Abaqus .odb 第一阶段不支持** | VTK 没有 Abaqus reader；只支持 .inp 文本（Tier 1-3） |
| **ANSYS Mechanical / MAPDL 第一阶段不支持** | 需要 PyDPF，单独大依赖，留后续 |
| **STAR-CCM+ (.sim) 第一阶段不支持** | 完全封闭格式，需要 macro 预导出 |

---

## 5. 用户场景

### 场景 A：simgraph2 入图谱（核心）
```python
from sim_parse import parse_case
result = parse_case("/data/case_run023", target_tier=3, discovery="off")
# → simgraph2 backend 把 result 写入 Neo4j
```

### 场景 B：数据湖 QOI 提取
```python
from sim_parse import parse_case
result = parse_case("/data/case_run023", target_tier=5)
# → result["tier_5_qoi"] 写 Parquet 长表
```

### 场景 C：可视化导出
```python
from sim_parse import to_vtu
vtu_path = to_vtu("/data/case_run023", time=5000.0)
# → ParaView 直接打开
```

### 场景 D：CLI 快速勘察
```bash
simparse /data/case_run023 --tier 3 --json
```

### 场景 E：Query Agent 调用（外部组件，不在 sim-parse 内）
```
用户问 "哪些案例熄火了？"
  → Query Agent 检索 sim-knowledge/physics/combustion/criteria.yaml
  → 调 sim_parse.parse_case() 拿 Tier 4 信号（如果数据湖里没有）
  → 应用判据，跨案例查询
  → 返回答案
```

---

## 6. 设计原则

1. **分层 (Tier) 与策略 (Path) 正交**：Tier 描述"信息深度"，Path 描述"实现方式"，两者独立组合
2. **确定性优先**：Tier 1-6 永远确定性，LLM 只在 Tier 7 或 Path C 出现，且可关闭
3. **零异常契约**：所有 public API 永不抛未捕获异常，失败字段 `None` + 标 source
4. **依赖最小化**：核心依赖仅 `vtk + numpy + h5py + pyyaml + Python stdlib`
5. **下游单向依赖**：sim-parse 不知道 simgraph2 / sim-cli / sim-knowledge 的存在
6. **插件式扩展**：用户可注册自定义 parser 不改框架（Path D）
7. **成本透明**：每层标注典型耗时，调用方按需选择深度
8. **Agent 之外无 LLM**：sim-parse 自身代码绝不调用 LLM；LLM 是 Agent 的事

---

## 7. 核心概念：信息分层 (Tier)

每个 Tier 有**判定规则**（遇到新字段判断归属哪一层）。

"Tier" 字面意思是"层级"。在 sim-parse 里它指**"我对这个算例了解到了多深"**。每深一层，知道得更多，但耗时也更大：

Tier	我了解到的	例子	成本
1	这是什么类型	"这是 OpenFOAM 案例"	毫秒
2	里面有什么	"有 0/ 5000/ 时间目录、T/U/p 等变量、Allrun 脚本"	毫秒
3	求解器配置	"用 reactingFoam + kEpsilon 湍流 + EDC 燃烧 + GRI-Mech 3.0"	几十毫秒
4	场数据统计	"T 最大 1998K、Qdot 最大 7.7e8 W/m³"	秒级（要解码二进制数组）
5	工程指标（统一命名）	"lift_coefficient=0.382, max_temperature=1998K"	毫秒
6	完整网格+场数据	一个完整的 VTU 文件	秒到分钟
7	物理意义	"这是燃烧 case，没熄火"	LLM 几秒
调用方按需选：simgraph2 入图谱只要 Tier 3 就够；ParaView 可视化要 Tier 6；Query Agent 回答问题用 Tier 4+5。




| Tier | 名称 | 判定规则 | 输入 | 输出 | 重工具？ | 确定性 | 典型耗时 |
|---|---|---|---|---|---|---|---|
| **1** | Identify | 仅看文件签名/扩展名/魔数/目录结构能判断？ | 目录路径 | `{format, solver, version, layout}` | ❌ | ✅ | < 10 ms |
| **2** | Inventory | 仅靠 `os.listdir` + 后缀扫描能列出？ | 目录路径 | 文件/变量/时间步清单 | ❌ | ✅ | < 50 ms |
| **3** | Metadata | 仅读文本字典/文件头/keyword block 能拿到？ | 目录路径 | 扁平 KV dict | ❌ | ✅ | < 200 ms |
| **4** | FieldStats | 需解码数组成 numpy 但不保留完整数组？ | 目录 + 时间/字段 | min/max/mean/积分/时序 | ✅ VTK | ✅ | 秒级 |
| **5** | QOI | 需查物理映射表 + 单位字典做命名归一？ | Tier 3+4 输出 | QOI 长表 records | 部分 | ✅ | 毫秒（已有 T4） |
| **6** | FullData | 需输出整个 mesh + 完整场？ | 目录 + 时间/字段 | VTU 文件 / numpy | ✅ VTK | ✅ | 秒~分钟 |
| **7** | Semantic | 需推断（不是提取）？ | 上面任意层输出 | 字段 + confidence 标注 | LLM | ❌ | 秒级 |

### 两条铁律

- **Tier 1-3 永远不调用 VTK / 任何重工具**（无 VTK 环境也能用，对涉密/容器环境友好）
- **Tier 7 永远不混进 Tier 1-6**（语义推断独立、可关闭、可降级）

### 模糊字段归属表（避免争议）

| 字段 | 归属 | 理由 |
|---|---|---|
| `mesh_cells` (从 owner header note) | T3 | 文本，不解码数组 |
| `bounding_box` (从 points) | T4 | 必须解码 binary 数组 |
| `boundary patches list` (名字+类型) | T3 | 文本字典 |
| `variable_ranges` (per-field min/max) | T4 | 解码 internalField |
| `convergence_orders` (从 log 算残差下降) | T4 | 文本 + 数值计算 |
| `lift_coefficient` (forceCoeffs.dat) | T4 输出 → T5 命名 | 读 CSV=T4，命名=T5 |
| `solver_type = "reactingFoam"` | T1 | 直接读 controlDict::application |
| `physics_class = "combustion+spray"` | T7 | 推断不是直读 |
| `is_extinguished` | T5（YAML 规则）/ T7（Agent 临场） | 见 §11 |

---

## 8. 信息族目录（提取什么）

按"信息族"组织，每族下面分到对应 Tier，每条标了来源。

### 8.1 OpenFOAM 信息族

#### 族 A — 网格 (Mesh)

| 信息 | Tier | 来源 |
|---|---|---|
| 网格量 (`nCells`) | 3 | `constant/polyMesh/owner` 头部 `note "nCells: NNN nFaces: NNN ..."` 字段 |
| 点数 (`nPoints`) | 3 | `constant/polyMesh/points` 头部 / 第一行计数 |
| 内部面数 (`nInternalFaces`) | 3 | `constant/polyMesh/owner` 头部 |
| 总面数 (`nFaces`) | 3 | `constant/polyMesh/faces` 头部 |
| 包围盒 | 4 | 全量读 `points` 算 min/max（VTK） |
| 网格类型（hex/tet/poly/混合） | 3-4 | `polyMesh/cellZones` 推断 + `faces` 文件每个 face 的点数分布 |
| 边界 patch 列表（名字、类型、面数、起始面） | 3 | `constant/polyMesh/boundary` 文本字典 |
| cellZones / faceZones / pointSets | 2-3 | `constant/polyMesh/{cellZones,faceZones,sets/}*` 存在性 + 内容 |
| 多 region 结构 | 1 | `constant/<region>/polyMesh/` 子目录是否存在 |
| 网格分解（并行） | 3 | `system/decomposeParDict` + `processor*` 目录 |

#### 族 B — 物理场 (Fields)

| 信息 | Tier | 来源 |
|---|---|---|
| 变量名清单 | 2 | 时间目录里的文件名 |
| 变量类型（volScalarField/volVectorField/volTensorField/surfaceScalarField/...） | 3 | 每个场文件 header 里的 `class` 字段 |
| 量纲（dimensions [kg m s K mol A cd]） | 3 | 每个场文件 header 里的 `dimensions` 行 |
| BC 类型/值（per patch per field） | 3 | 每个场文件的 `boundaryField{...}` 块 |
| 内场 min/max/mean | 4 | VTK 读 `internalField` |
| 内场分布直方图 | 4 | VTK |
| 时间演化（每时间步统计） | 4 | 遍历所有时间目录 |
| 全场数据（point/cell-centered arrays） | 6 | VTK → VTU |

#### 族 C — 求解器/物理设置 (Setup)

| 信息 | Tier | 来源 |
|---|---|---|
| 求解器名（`application`） | 1-3 | `system/controlDict::application` |
| 时间控制（startTime/endTime/deltaT/writeInterval/adjustTimeStep） | 3 | `system/controlDict` |
| 离散格式（divergence/gradient/laplacian） | 3 | `system/fvSchemes` |
| 线性求解器配置（solver/preconditioner/tolerance/relaxation） | 3 | `system/fvSolution` |
| 湍流模型（RAS/LES + model name + coefficients） | 3 | `constant/turbulenceProperties` + `RASProperties`/`LESProperties` |
| 热物性模型（thermo type/mixture/transport/equation of state） | 3 | `constant/thermophysicalProperties` |
| 化学反应启用？机理名 | 3 | `constant/chemistryProperties`、`constant/reactions`、`chemkin/*.inp` |
| 燃烧模型（PaSR / EDC / laminar） | 3 | `constant/combustionProperties` |
| 喷雾设置（injection model / particle props / 蒸发模型） | 3 | `constant/sprayCloudProperties` |
| 重力 | 3 | `constant/g` |
| MRF / fvOptions（动量源、点火源、多孔介质） | 3 | `constant/MRFProperties`、`system/fvOptions` |
| 边界条件（per patch + per field） | 3 | 时间目录场文件的 `boundaryField` |

#### 族 D — 拉格朗日 / 离散相 (Lagrangian)

| 信息 | Tier | 来源 |
|---|---|---|
| 是否有拉格朗日相 | 1 | `<time>/lagrangian/` 是否存在 |
| Cloud 名列表 | 2 | `<time>/lagrangian/<cloud>/` 子目录 |
| 每 cloud 的粒子变量 | 2 | cloud 子目录里的文件名 |
| 粒子数（每 cloud） | 4 | VTK 读 `nParticle` 或 `positions` 长度 |
| 粒子统计（直径分布、温度分布、速度分布） | 4 | VTK |

#### 族 E — 运行状态 (Run state)

| 信息 | Tier | 来源 |
|---|---|---|
| 已写时间步列表 | 2 | 顶层目录浮点目录扫描 |
| 最新时间 | 2 | 时间步排序取最大 |
| 时间步数 | 2 | 计数 |
| 是否完成（vs endTime） | 3 | 最新时间 vs `controlDict::endTime` |
| 残差历史 | 4 | log 文件解析 |
| 收敛判断 | 4 | 残差最后值 vs convergence tolerance |

#### 族 F — 后处理 (Post-processing)

| 信息 | Tier | 来源 |
|---|---|---|
| 函数对象列表 | 2-3 | `system/controlDict::functions` 或独立 dict 文件 |
| forces / forceCoeffs（CL/CD/CM 时序） | 4 | `postProcessing/forces*/<time>/forceCoeffs.dat` |
| 探针 / probe 数据 | 4 | `postProcessing/probes/<time>/<var>` |
| 切片 / 采样平面 | 2 | `system/cuttingPlane`、`postProcessing/cuttingPlane/` |
| monitor（质量流、平均量等） | 4 | `postProcessing/<name>/<time>/*.dat` |
| 残差文件（functions 写出的） | 4 | `postProcessing/residuals/<time>/residuals.dat` |

#### 族 G — 周边文件 (Auxiliary)

| 信息 | Tier | 来源 |
|---|---|---|
| log 文件路径列表 | 2 | 顶层 `log.*` |
| Allrun/Allclean/Allmesh 脚本 | 2 | 顶层脚本文件 |
| README / 注释 | 2 | `README*`, `*.txt` |
| OpenFOAM 版本（版本号） | 1-3 | 任意一个 OF 字典文件的 header `version` 字段或 log 里的 banner |
| 外部资源（chemkin、CAD 几何文件） | 2 | `chemkin/`、`constant/triSurface/` |

#### 族 H — 派生 QOI（Tier 5，跨求解器统一）

通过物理映射表 + 单位字典翻译：

- `lift_coefficient` ← `forces/forceCoeffs.dat:Cl[-1]`
- `drag_coefficient` ← `forces/forceCoeffs.dat:Cd[-1]`
- `convergence_orders_U` ← log 残差下降阶数
- `max_temperature` ← `T.internalField.max()`
- `ignition_delay` ← `Qdot` 时序中超阈值的时刻
- `mean_outlet_velocity` ← outlet patch 上 `U` 的面积加权平均（需要场积分）

完整映射表见 `core/canonical/variable_dict.yaml`（Phase 2 实化）。

#### 族 I — 语义解释（Tier 7，LLM）

- 物理类型分类（"燃烧 + 喷雾 + 反应流"）
- 自然语言摘要
- 推断车辆类型 / 工况 / 项目（结合 dirname、log 头、scripts）

### 8.2 关于"Tier 4 信号面应该铺多宽"

Tier 4 输出**不是只为当前已知 QOI 服务**，而是为**未来用户可能问的所有问题**铺路。原则：

> **凡是燃烧/传热/流场/数值类常见问题需要的信号，都应该在 Tier 4 抓出来**，哪怕当前没有对应 Tier 5 QOI 规则。

这是因为 Query Agent (§16) 在用户问陌生问题时，需要从已提取信号合成判据。**信号没抓 → Agent 也无能为力**。具体推荐覆盖见 §11。

### 8.3 其他求解器

CGNS、Fluent、LS-DYNA 等的信息族表，待 Phase 2+ 各自的 `docs/solvers/<name>.md` 文档化。

---

## 9. 实现策略 (Path A/B/C/D)

每个 Tier 内部可以有四条**实现路径**，dispatcher 自动级联：

| Path | 名称 | 知道求解器？ | 知道格式？ | LLM？ | 确定性 | 何时用 | 知识库依赖 |
|---|---|---|---|---|---|---|---|
| **A** | 静态求解器 parser | ✅ | ✅ | ❌ | ✅ | 已注册的求解器，最快路径 | 自带 (`solvers/`) |
| **B** | 通用格式启发式 | ❌ | ✅ | ❌ | ✅ | 格式认得（HDF5/CGNS）但求解器不认得；A 部分缺失时填补 | 自带 (`generic/`) |
| **C** | LLM Discovery | ❌ | ❌ | ✅ | ❌ | 完全陌生格式；A+B 都失败 | **外部 sim-knowledge/formats** |
| **D** | 用户插件 | ✅（用户写） | ✅ | ❌ | ✅ | 长尾求解器；不改框架代码 | 用户自带 |

### Path A: 静态求解器 parser（主力）

每个支持的求解器在 `solvers/<name>/` 下实现 Tier 1-6（按需）。详见 §15 项目结构。

### Path B: 通用格式启发式（兜底）

不绑求解器，只利用格式标准。在 `generic/` 下实现：

```
generic/
  ├── hdf5_dump.py     # 任何 HDF5 → 树+shape+attrs
  ├── vtk_probe.py     # 暴力尝试每个 VTK reader
  ├── text_log.py      # 通用残差/收敛正则
  ├── csv_inventory.py # 通用 CSV 元数据
  └── magic.py         # 文件魔数库
```

被两类情况触发：
1. **Path A 失败但格式认得** —— 例如未知求解器写出的 HDF5
2. **Path A 部分字段缺失** —— Path B 针对 missing 字段补填

### Path C: LLM Discovery（最后兜底）

跟 sim-cli driver paradigm 同款：LLM 写代码、框架给安全工具集 + 沙盒执行。

```
discovery/
  ├── agent.py         # LLM 调度逻辑
  ├── tools.py         # list_dir / read_text / try_vtk_reader / py_exec
  └── sandbox.py       # 沙盒执行 LLM 生成的代码（只读 FS、无网络）
```

**Path C 实现需要消费 `sim-knowledge/formats/`**（外部仓库，见 §16），具体包括：
- `formats/hdf5/structure.yaml` —— HDF5 结构识别规则
- `formats/conventions/*.yaml` —— 跨求解器命名约定
- `formats/playbooks/*.yaml` —— 探索策略
- `formats/examples/*.json` —— 历史成功案例（few-shot）

**Phase 1 不实现 Path C 真实逻辑**，留 stub：返回 `{"unsupported": True, "reason": "discovery not implemented"}`。

### Path D: 用户插件

`plugins/` 目录注册第三方 parser，使用 entry_points 机制：

```python
# 用户自己的项目里
from sim_parse import register_parser

@register_parser(name="my_solver", priority=80)
class MyParser:
    def identify(self, path) -> dict | None: ...
    def metadata(self, path) -> dict: ...
```

---

## 10. Dispatcher（路径调度器）

**确定性的 ~30 行 if/else 状态机**，不调 LLM。

```python
def parse_case(case_root, target_tier=4, discovery="auto"):
    out = {}

    # Step 1: Tier 1 识别
    identity = path_a_or_b_identify(case_root)
    out.update(identity)

    if identity["format"] is None:
        if discovery in ("auto", "on"):
            return path_c_discover(case_root, target_tier)
        return {**out, "status": "unknown_format"}

    # Step 2: 决定默认 path
    default_path = "A" if identity["format"] in REGISTERED_SOLVERS else "B"

    # Step 3: 逐 Tier 跑
    for tier in range(2, target_tier + 1):
        result = run_tier(tier, case_root, default_path)
        out.update(result)

        # Step 4: 缺失字段兜底
        missing = [k for k, v in result.items() if v is None]
        if missing and default_path == "A":
            patch = run_tier(tier, case_root, "B", only=missing)
            out.update(patch)
        if any_still_missing(out) and discovery in ("auto", "on"):
            patch = path_c_fill_missing(case_root, missing_fields(out), context=out)
            out.update(patch)

    return out
```

### 用户控制

| flag | 含义 |
|---|---|
| 默认 | A→B→C 自动级联 |
| `--strict` | 仅 A，缺失字段保留 None，不补 |
| `--discovery=off` | 允许 A+B，禁 C（涉密环境） |
| `--force-path=B` | 跳过 A 直接从 B 开始（调试用） |
| `--force-solver=openfoam` | 强行当作 OpenFOAM 解（识别错误时纠正） |

---

## 11. Agent 临场推断的支撑机制

**注意**：本节描述的 Agent 推断不在 sim-parse 内部，但 sim-parse 的设计需要为它**预留接口**。具体 Agent 实现属于 Query Agent / Discovery Agent（见 §16）。

### 11.1 Agent 准确性的五个机制

让 Agent 临场推断（如"这个案例熄火没？"）足够可靠，需要五个机制叠加（按 ROI 排序）：

| # | 机制 | ROI | 一句话本质 |
|---|---|---|---|
| 1 | **结构化知识库**（YAML，非 markdown） | ⭐⭐⭐⭐⭐ | LLM 不"想"，只"查" |
| 2 | **校准检验**（在 labeled_cases 上测试判据） | ⭐⭐⭐⭐⭐ | 实测胜过理论 |
| 3 | **工具编排** 取代自由生成 | ⭐⭐⭐⭐ | 自由度受限 = 出错空间小 |
| 4 | **强制结构化输出** | ⭐⭐⭐ | 强制 self-assessment |
| 5 | **Few-shot 示例** | ⭐⭐ | 模仿好示范 |

机制 #1 + #2 占总收益 70%+。如果只能做两件事，做这两件。**这两件事都在 `sim-knowledge/` 仓库内**，不在 sim-parse 内。

### 11.2 sim-parse 对 Agent 准确性的承诺

为了让上述机制能跑起来，sim-parse 承诺三件事：

1. **Tier 4 信号面足够宽**：把 Agent 可能需要的判据基础信号都抓出来。具体推荐覆盖（OpenFOAM 燃烧场景）：

   | 信号 | 能回答什么问题 |
   |---|---|
   | `Qdot_max_over_time` | 是否熄火、是否点火延迟 |
   | `T_max_over_time`, `T_min`, `T_inlet` | 是否熄火、最高温达不达标 |
   | `OH_max`, `CH_max`, `H_max` | 火焰是否存在、火焰锋位置 |
   | `species_max` per 各组分 | 排放、未燃烧燃料残余 |
   | `velocity_max`, `velocity_at_outlet` | 是否回流、出口流速 |
   | `pressure_drop_inlet_outlet` | 压损 |
   | `wall_heat_flux_per_patch` | 壁面热负荷 |
   | `residual_drop_orders_per_var` | 收敛性 |
   | `mass_flow_in`, `mass_flow_out` | 质量守恒检查 |
   | `courant_max` | 数值稳定性 |
   | `turbulence_intensity` | 湍流强度 |

2. **Tier 5 规则 YAML 是外挂可加载**：用户可以新增 `extinction.yaml` 不改 sim-parse 代码，规则文件遵守约定 schema

3. **输出包含 provenance**：每个字段标记来自哪个 Tier 哪条 Path，方便 Agent 信任度评估

### 11.3 知识库三档来源

sim-knowledge 内容来源（**不在 sim-parse 内，仅供参考**）：

| 档 | 谁写 | 占比 | 准确率 |
|---|---|---|---|
| **Gold** | 人写 + 人审 | ~20% | 最高 |
| **Silver** | LLM 起草 + 人审 | ~50% | 高 |
| **Bronze** | 用户查询自动提议 + 人审 | ~30% | 中 |

冷启动需要的人工投入：~4-6 周覆盖单个物理域（如燃烧）。具体见 sim-knowledge 自己的设计文档。

---

## 12. QOI 演化闭环

**注意**：本节描述的闭环跨越 sim-parse / sim-knowledge / Query Agent / 人工审核四个组件。sim-parse 在闭环中扮演的角色：**消费固化后的规则 YAML，重跑全库填充新字段**。

### 12.1 闭环角色与流程

```
[Day 1] 用户首次问 "哪些案例熄火了？"
   │
   ▼
① Query Agent 临场推断 → 答案 + reasoning trace
   │
   ▼
② Query Log 记录：{ question, criterion_used, result_count, trace }
   │
[Day 1~30] 100 个用户问同一问题（重复劳动 + 不一致风险）
   │
   ▼
[每周] Promotion Pipeline 跑：
③ 读 Query Log → 语义聚类 → 识别热点 intent
   ↓
④ 取最常用判据 → 在 labeled_cases 上校准 → 通过则提议固化
   ↓
⑤ 自动开 PR 到 sim-knowledge/physics/<domain>/criteria.yaml
   │
   ▼
⑥ Curator (人) 审核 PR → merge
   │
   ▼
⑦ Re-ingest Worker：sim-parse 用新 YAML 重跑全库
   - Tier 5 多了 is_extinguished 字段
   - Neo4j / Parquet 加列
   │
   ▼
[Day 30+] 再问同问题
⑧ Query Agent 看到 schema 已有 is_extinguished →
   不再临场推断，直接 WHERE is_extinguished = TRUE
```

### 12.2 sim-parse 在闭环中的角色

| 步骤 | sim-parse 负责吗 |
|---|---|
| ①②③④ Agent 临场推断 + 日志 + 提议 | ❌ |
| ⑤ 自动开 PR | ❌（sim-knowledge 仓库的 CI） |
| ⑥ 人审 | ❌（Curator） |
| ⑦ **Re-ingest（用新规则重跑）** | ✅ —— sim-parse 在 Phase 1 就要支持这个 |
| ⑧ 直接查询 | ❌（数据库 / 数据湖） |

**承诺**：sim-parse 的 `parse_case()` 必须支持 `qoi_rules_path=...` 参数，让 Re-ingest Worker 用新版 YAML 重跑全库。

---

## 13. 输出 Schema

### 13.1 主输出（dict / JSON）

```json
{
  "case_root": "/data/case_run023",
  "case_hash": "sha256:...",
  "ingested_at": "2026-04-27T10:30:00Z",

  "tier_1_identify": {
    "format": "openfoam",
    "solver": "reactingFoam",
    "version": "v2212",
    "layout": "single-region",
    "decomposed": false,
    "lagrangian": false,
    "_path": "A"
  },

  "tier_2_inventory": {
    "time_steps": [0, 500, 1000, 5000],
    "variables": ["U", "p", "T", "CO", "CO2", "..."],
    "scripts": ["Allrun", "Allclean"],
    "log_files": ["log.blockMesh", "log.reactingFoam"],
    "_path": "A"
  },

  "tier_3_metadata": {
    "application": "reactingFoam",
    "mesh_cells": 3466,
    "turbulence_model": "kEpsilon",
    "combustion_model": "EDC",
    "boundaries": [{"name": "inletfuel", "type": "patch"}],
    "_path": "A"
  },

  "tier_4_field_stats": {
    "variable_ranges": {"T": {"min": 292, "max": 2350}},
    "bounding_box": {"x": [0, 0.19], "y": [-0.008, 0.008], "z": [-0.02, 1.0]},
    "_path": "A"
  },

  "tier_5_qoi": [
    {"variable": "max_temperature", "value": 2350, "unit": "K", "source": "tier_4.T.max"},
    {"variable": "is_extinguished", "value": false, "unit": "boolean",
     "evidence": [{"name": "low_heat_release", "value": 9.8e8}],
     "rule_version": "extinction.yaml@v3"}
  ],

  "field_provenance": {
    "chemistry_mechanism": {"value": "GRI-Mech 3.0", "path": "B", "confidence": "MED"},
    "physics_class":       {"value": "combustion",   "path": "C", "confidence": "LOW"}
  },

  "errors": [],
  "warnings": ["log.reactingFoam not found at top level, looked in processor*/log.reactingFoam"]
}
```

### 13.2 QOI 长表（Parquet schema）

| 列 | 类型 | 说明 |
|---|---|---|
| `case_hash` | str | 算例唯一 ID |
| `solver` | str | "openfoam" / "fluent" / ... |
| `variable` | str | 受控词表名 (`lift_coefficient`) |
| `value` | float / bool |  |
| `unit` | str | UDUNITS-2 字符串 |
| `region` | str? | 选填 |
| `time` | float? | 选填（稳态 null） |
| `source` | str | 来源描述 |
| `path` | str | 'A'/'B'/'C' |
| `rule_version` | str? | YAML 规则版本号（用于审计） |
| `evidence` | json? | 投票判据的证据（针对布尔 QOI） |

### 13.3 物理映射表 schema

`core/canonical/variable_dict.yaml`：

```yaml
lift_coefficient:
  description: "升力系数"
  unit: "1"
  sources:
    openfoam:
      - {file: "postProcessing/forces/forceCoeffs.dat", column: "Cl", aggregator: "last"}
    fluent:
      - {file: "report-cl.out", column: "lift", aggregator: "last"}
    cgns:
      - {flow_solution_attr: "LiftCoefficient"}

max_temperature:
  description: "最大温度"
  unit: "K"
  sources:
    openfoam:
      - {field: "T", aggregator: "max"}
    fluent:
      - {field: "temperature", aggregator: "max"}
```

---

## 14. API 设计

### 14.1 Python API

```python
from sim_parse import parse_case, to_vtu, register_parser

# 主入口：返回完整 dict
result = parse_case(
    case_root,                       # str | Path
    target_tier = 4,                 # 1-7
    discovery = "auto",              # "auto" | "on" | "off"
    force_solver = None,             # str | None
    force_path = None,               # "A" | "B" | None
    qoi_rules_path = None,           # 外挂 YAML 路径，为 Re-ingest 闭环服务
)

# 单 Tier 调用（精细控制）
from sim_parse.tiers import tier1, tier2, tier3, tier4
identity = tier1.identify(case_root)
inventory = tier2.inventory(case_root, identity)

# Tier 6 单独入口
vtu_path = to_vtu(
    case_root,
    time = None,                     # None = 最后时间步
    fields = None,                   # None = 全部
    output = None,                   # None = 临时文件
)

# 插件注册
@register_parser(name="my_solver", priority=80)
class MyParser:
    def identify(self, path): pass
    def metadata(self, path): pass
```

### 14.2 CLI

```bash
# 默认输出 Tier 1-3 简要
simparse <case>

# 指定深度 + JSON 输出
simparse <case> --tier 4 --json > out.json

# 仅 Tier 5 QOI 长表，输出 Parquet
simparse <case> --tier 5 --as parquet -o qoi.parquet

# Tier 6 导出 VTU
simparse <case> --tier 6 --time 5000 --vars U,T,p -o case.vtu

# 涉密环境模式
simparse <case> --discovery=off

# 调试：强制走某条 path
simparse <case> --force-path=B

# Re-ingest 闭环：用外挂 YAML 重跑
simparse <case> --tier 5 --qoi-rules /path/to/new_rules.yaml
```

---

## 15. 项目结构

### 15.1 sim-parse 自身结构

```
sim-parse/                                  # d:\Git\GitBubProj\simcli\sim-parse\
├── pyproject.toml                          # 依赖：vtk, numpy, h5py, pyyaml
├── README.md
├── CHANGELOG.md
├── src/sim_parse/
│   ├── __init__.py                         # 暴露 parse_case / to_vtu / register_parser
│   ├── core/
│   │   ├── tiers.py                        # Tier 1-7 dataclass 协议
│   │   ├── cascade.py                      # Dispatcher
│   │   ├── registry.py                     # 求解器+插件注册表
│   │   ├── provenance.py                   # 字段来源追踪
│   │   ├── errors.py                       # ParseError, never raise contract
│   │   └── canonical/
│   │       ├── variable_dict.yaml          # 物理映射表
│   │       ├── unit_map.yaml               # UDUNITS-2
│   │       └── qoi_rules/                  # Tier 5 规则
│   │           ├── extinction.yaml         # 熄火判据
│   │           ├── convergence.yaml        # 收敛判据
│   │           └── ...
│   ├── solvers/                            # Path A
│   │   ├── openfoam/
│   │   │   ├── tier1_identify.py
│   │   │   ├── tier2_inventory.py
│   │   │   ├── tier3_metadata.py
│   │   │   ├── tier4_field_stats.py
│   │   │   ├── tier5_qoi.py                # Phase 2
│   │   │   ├── tier6_export.py             # Phase 2
│   │   │   ├── _foam_dict.py               # 字典文本 parser 工具
│   │   │   ├── _polymesh.py                # owner header / boundary 解析
│   │   │   └── families/
│   │   │       ├── _registry.yaml
│   │   │       ├── reactingFoam.py
│   │   │       └── simpleFoam.py
│   │   ├── cgns/                           # Phase 2
│   │   ├── fluent/                         # Phase 2
│   │   └── lsdyna/                         # Phase 2
│   ├── generic/                            # Path B
│   │   ├── hdf5_dump.py
│   │   ├── vtk_probe.py
│   │   ├── text_log.py
│   │   └── magic.py
│   ├── discovery/                          # Path C (Phase 3, stub for Phase 1)
│   │   ├── agent.py                        # 真实实现需要消费 sim-knowledge/formats
│   │   ├── tools.py
│   │   └── sandbox.py
│   ├── cli/
│   │   └── __main__.py                     # `python -m sim_parse` 入口
│   └── adapters/
│       └── vtk_io.py                       # VTK→numpy 共享工具
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_tiers_protocol.py
│   │   ├── test_cascade.py
│   │   └── test_openfoam_dict_parser.py
│   ├── integration/
│   │   └── test_dlr_a_cavity.py            # Phase 1 验收
│   └── fixtures/
│       └── minimal_openfoam_case/          # 小 KB 级 fixture for CI
└── docs/
    ├── design.md                           # 本文档
    ├── tier_reference.md
    ├── adding_a_solver.md
    └── solvers/
        ├── openfoam.md
        ├── cgns.md                         # Phase 2
        ├── fluent.md                       # Phase 2
        └── lsdyna.md                       # Phase 2
```

### 15.2 在 simcli/ workspace 中的位置

```
d:\Git\GitBubProj\simcli\                   # 现有 workspace
├── sim-cli/                                # 现有：runtime
├── sim-skills/                             # 现有：求解器操作知识（markdown）
├── sim-mcp/                                # 现有
├── sim-parse/                              # 本方案
└── sim-knowledge/                          # 拟建：物理+格式知识（YAML）
    ├── physics/
    │   ├── combustion/
    │   ├── heat_transfer/
    │   ├── turbulence/
    │   ├── multiphase/
    │   └── numerics/
    ├── formats/
    │   ├── hdf5/
    │   ├── cgns/
    │   ├── conventions/
    │   └── playbooks/
    ├── labeled_cases/
    │   ├── combustion_extinction/
    │   ├── convergence_failure/
    │   └── ...
    └── few_shot/
```

依赖方向：

```
simgraph2 backend                ──►  sim-parse
sim-cli runtime (可选)            ──►  sim-parse
Query Agent (待建)                ──►  sim-parse + sim-knowledge
Discovery Agent (在 sim-parse 内) ──►  sim-knowledge/formats
sim-cli driver Agent              ──►  sim-skills
```

**sim-parse 不反向依赖任何上层**。

---

## 16. 配套生态：sim-knowledge / Query Agent / Discovery Agent

**这些是 sim-parse 之外的组件**，本文档仅做指针式说明，详细设计留给各自的设计文档。

### 16.1 sim-knowledge（拟建仓库）

YAML 结构化知识库，分两子域：

#### `sim-knowledge/physics/<domain>/`

物理判据知识库，被 Query Agent 消费。冷启动覆盖：

- `combustion/` —— 燃烧（extinction / ignition / heat release）
- `heat_transfer/` —— 传热（wall flux / Nusselt / temperature gradient）
- `turbulence/` —— 湍流（intensity / dissipation rate / Re_t）
- `multiphase/` —— 多相
- `numerics/` —— 收敛 / 发散 / Courant / 守恒

每个 domain 含：

```
<domain>/
├── criteria.yaml          # 判据库（intent → signals → rule → reference）
├── variables.yaml         # 物理量 ↔ 求解器变量名 映射
├── thresholds.yaml        # 不同燃料/压力的阈值表
└── few_shot.json          # 推理示范
```

#### `sim-knowledge/formats/<format>/`

格式探索知识库，被 Discovery Agent (sim-parse Path C) 消费。冷启动覆盖：

- `hdf5/` —— HDF5 通用结构识别
- `cgns/` —— CGNS 标准
- `conventions/` —— 跨求解器 log/CSV/目录命名约定
- `playbooks/` —— "未知文件怎么探索"策略
- `examples/` —— 历史成功识别案例（few-shot）

#### `sim-knowledge/labeled_cases/`

人工标注的真值案例，是 §11 机制 #2（校准检验）的基础。每个 intent 至少 5-10 个：

```
labeled_cases/
├── combustion_extinction/
│   ├── case_id_001/
│   │   └── ground_truth.yaml: {is_extinguished: true, fuel: CH4}
│   └── ...
└── ...
```

### 16.2 Query Agent（拟建组件，候选名 sim-query）

接收用户自然语言问题（"哪些案例熄火了？"），输出查询结果 + reasoning trace。**不在 sim-parse 内**。

**消费**：sim-knowledge/physics + sim-parse 的 Tier 4/5 输出（通过数据湖或直接调用）

**输出**：Cypher / SQL / Python 查询 + 答案 + trace

**关键约束**：见 §11 五个准确性机制

### 16.3 Discovery Agent（在 sim-parse Path C 内）

当 Path A/B 都失败时，LLM 驱动的探索 Agent。**实现在 sim-parse 内，但消费外部 sim-knowledge/formats**。

**消费**：sim-knowledge/formats + sandbox 工具集

**输出**：临时 inventory 字典 + 可能的 parser 升级建议（提议加进 sim-parse/solvers/ 或 sim-knowledge/formats/）

---

## 17. 求解器覆盖矩阵 & 阶段交付

### 17.1 长期目标覆盖

| 求解器 | T1 | T2 | T3 | T4 | T5 | T6 | 阶段 | 备注 |
|---|---|---|---|---|---|---|---|---|
| **OpenFOAM** | ✅A | ✅A | ✅A | ✅A | ✅A | ✅A | **Phase 1** | 用 DLR_A_cavity 验收 |
| **HDF5 通用** | ✅B | ✅B | ✅B | ⚠️B | — | — | **Phase 1** | h5py dump |
| **CGNS** | ✅A | ✅A | ✅A | ✅A | ⚠️ | ✅A | Phase 2 | vtkCGNSReader |
| **Fluent .cas/.dat** | ✅A | ✅A | ✅A | ✅A | ✅A | ✅A | Phase 2 | vtkFLUENTReader |
| **Fluent .cas.h5** | ✅A | ✅A | ✅A | ✅A | ✅A | ✅A | Phase 2 | vtkFLUENTCFFReader |
| **LS-DYNA** | ✅A | ✅A | ✅A | ✅A | ✅A | ✅A | Phase 2 | vtkLSDynaReader |
| **Tecplot PLT** | ✅A | ✅A | ✅A | ✅A | ⚠️ | ✅A | Phase 3 | vtkTecplotReader |
| **EnSight Gold** | ✅A | ✅A | ✅A | ✅A | ⚠️ | ✅A | Phase 3 | vtkEnSightReader |
| **Plot3D** | ✅A | ✅A | ✅A | ✅A | ⚠️ | ✅A | Phase 3 | vtkMultiBlockPLOT3DReader |
| **Abaqus (.inp)** | ✅A | ✅A | ✅A | ❌ | ❌ | ❌ | Phase 3 | 仅文本输入卡 |
| **Abaqus (.odb)** | — | — | — | ⚠️ | ⚠️ | ⚠️ | Phase 4 | 需 odb2vtk 预转 |
| **MAPDL/Mechanical** | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | Phase 4 | 需 PyDPF |
| **STAR-CCM+** | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ | Phase 4 | 需 macro 预导出 |
| **任意未知格式** | ✅C | ✅C | ⚠️C | ⚠️C | ❌ | ❌ | Phase 3 | LLM Discovery |

### 17.2 Phase 1 + 加 Tier 5/6/7 交付 —— ✅ **已完成**

**做（已交付）**：
- ✅ 项目骨架（pyproject.toml、目录结构、基础协议）
- ✅ Tier 协议（`core/tiers.py` 数据类 + 字段计数器）
- ✅ Dispatcher（`core/cascade.py` —— A→B→C 级联调度，**支持 Tier 1-7 全部**）
- ✅ **OpenFOAM 完整 Path A 实现**：
  - Tier 1 Identify / Tier 2 Inventory / Tier 3 Metadata / Tier 4 FieldStats（VTK）
  - **Tier 5 QOI**：通用规则引擎 (`core/canonical/qoi_engine.py`) + 14 条内置规则 (`qoi_rules.yaml`) + `qoi_rules_path` 外挂
  - **Tier 6 VTU 导出**：`vtkXMLUnstructuredGridWriter`，缓存到 `.simparse_cache/`
  - **Tier 7 Semantic**：启发式引擎（`discovery/semantic.py`），输出 physics_class / time_semantics / run_health / complexity_class / nl_summary（中文）
- ✅ HDF5 通用 Path B（Tier 1+2，含 magic-byte 识别 + h5py dump + CGNS/Fluent CFF 子识别）
- ✅ Path C **stub**（接口定义、`{"unsupported": True}` 返回 —— 真实 LLM 实现 Phase 3+）
- ✅ CLI（`python -m sim_parse <case> --tier N --json` + `simparse` entry point + `to_vtu()` API）
- ✅ 用 DLR_A_cavity 跑 31 个 OpenFOAM 测试 + 7 个 Path B 测试 + 3 个 labeled_cases + 12 个 Tier 5/6/7 测试 = **63 测试**

**不做（按计划，Phase 3+ 才做）**：
- 真实的 Path C LLM Discovery（sim-knowledge/formats 还没建）
- Tier 7 LLM 上行（heuristic 引擎已可用，LLM 是可选升级）
- sim-knowledge 仓库内容完整化（仅 labeled_cases 雏形）

**验收标准（实测）**：

| # | 标准 | 状态 |
|---|---|---|
| 1 | `simparse DLR_A_cavity --tier 4 --json` 正确输出 ≥ 30 个字段 | ✅ 实测 ~50 字段 |
| 2 | 字段值与手工分析一致（mesh_cells=3466、reactingFoam、kEpsilon、EDC、GRI-Mech 等） | ✅ |
| 3 | 喂入空目录返回 `{"format": null}` 而不抛异常 | ✅ |
| 4 | 喂入 `.h5` 文件触发 Path B 返回 dataset 列表 | ✅ |
| 5 | `--qoi-rules` 加载外挂 YAML 实化解析 | ✅ Tier 5 引擎已支持 |
| 6 | `pytest -q` 全绿 | ✅ **63/63 passed** |
| 7 | Tier 5 QOI 输出长表 | ✅ 14 条记录（mesh_cells、max_temperature、is_extinguished 等） |
| 8 | Tier 6 VTU 导出可被 ParaView 打开 | ✅ 149 KB，n_cells=3466 |
| 9 | Tier 7 输出 5 个语义字段 + 中文摘要 | ✅ |

**实测关键指标**（DLR_A_cavity）：

| 指标 | 实测值 |
|---|---|
| Tier 1+2+3 解析耗时 | ~80 ms |
| Tier 4 (VTK) 解析耗时 | ~1.5 s |
| Tier 5 QOI 引擎耗时 | ~1 ms |
| Tier 6 VTU 导出耗时 | ~1 s（写 149 KB 文件） |
| Tier 7 启发式 | < 1 ms |
| 提取字段总数 | ~70（含 Tier 5 14 条 QOI + Tier 7 5 个语义字段） |
| 物理量识别准确性 | 全部命中：T_max=1998K, Qdot_max=7.7e8, U_max=43.6 m/s, OH_max=3.1e-3 |
| labeled_cases 校准检验 | ✅ extinction 判据投票结果与 ground truth 一致（is_extinguished=false） |
| Tier 7 关键判定 | physics_class=combustion+heat+flow / time_semantics=pseudo_time_LTS / run_health=healthy |

### 17.3 Phase 2（4-6 周）

- Tier 5 QOI 长表完整化（OpenFOAM 优先）
- Tier 6 VTU 导出（OpenFOAM）
- 加 CGNS / Fluent / LS-DYNA 三个求解器的 Path A
- 物理映射表 YAML 实化（OpenFOAM + Fluent 共同的 ~20 个 QOI）
- 插件注册机制（Path D）
- Parquet 输出格式

### 17.4 Phase 3+（弹性）

- Tecplot / EnSight / Plot3D
- Abaqus .inp 解析
- Path C 真实实现（LLM Discovery） —— 需要 sim-knowledge/formats 先建
- Tier 7 Semantic（接外部 LLM）

---

## 18. 依赖与环境

### 18.1 运行时依赖

| 包 | 版本 | 必需？ | 用途 |
|---|---|---|---|
| `python` | ≥ 3.10 | ✅ | type hints |
| `numpy` | ≥ 1.24 | ✅ | 数组 |
| `pyyaml` | ≥ 6.0 | ✅ | 物理映射表 / qoi_rules |
| `vtk` | ≥ 9.3 | Tier 4-6 需要 | OpenFOAM/Fluent/CGNS/LS-DYNA reader |
| `h5py` | ≥ 3.0 | Path B HDF5 需要 | 通用 HDF5 |
| `httpx` | ≥ 0.25 | Path C 需要 | LLM client（可选） |

**Tier 1-3 仅依赖 numpy + pyyaml + stdlib**，无 VTK 也能跑。

### 18.2 开发依赖

`pytest`, `pytest-cov`, `ruff`, `mypy`

### 18.3 测试环境

用户已有 `D:\TOOL\Conda\conda\envs\PostProcessTool`：
- VTK 9.4.1 ✅
- numpy 2.4.4 ✅
- h5py 3.16.0 ✅
- 缺 `pyyaml` —— 安装一次即可

`pyproject.toml` 中 `[project.optional-dependencies]`：
```toml
[project.optional-dependencies]
fields = ["vtk>=9.3", "h5py>=3.0"]
discovery = ["httpx>=0.25"]
all = ["sim-parse[fields,discovery]"]
```

---

## 19. 关键设计决策

| 决策 | 选择 | 替代方案 | 选择理由 |
|---|---|---|---|
| 项目位置 | sim-cli/ workspace 兄弟目录 | sim-cli 内部 / 独立 git repo | 解耦、依赖最小、未来易拆 |
| 包名 | `sim_parse`（Python）/ `sim-parse`（仓库） | `simparse`/`solver-parse`/`cae-parse` | 与 sim-cli family 一致 |
| VTK 用法 | raw `vtk` (vtkmodules.*) | `pyvista` 高层封装 | 当前环境无 pyvista，避免新增 50 个间接依赖 |
| LLM 在 paradigm 中的位置 | Path C + Tier 7（可选、可关闭，且实现需消费外部 sim-knowledge） | 全 LLM-driven / 全静态 | 平衡覆盖率和确定性 |
| Tier 边界 | 看是否解码数组 / 是否查映射表 / 是否推断 | 按数据类型分 / 按求解器分 | 边界规则简单可记 |
| 错误契约 | 永不抛 + 字段填 None + provenance 标记 | 抛异常让上层捕获 | simgraph2 硬性要求 |
| 测试数据 | 引用绝对路径 + 小 fixture for CI | 全部拷贝进仓库 | 大 case 不进仓库，小 fixture 保证 CI |
| 知识库放哪 | 独立 sim-knowledge 仓库（physics + formats 两子域） | 嵌入 sim-parse / 嵌入 sim-skills | 维护者画像不同；跨求解器复用 |
| Agent 不在 sim-parse 内 | Query Agent / Discovery Agent 是独立组件 | 嵌入 sim-parse | sim-parse 保持纯净的 per-case 静态提取 |
| QOI 演化采用闭环 | 用户查询驱动 QOI 固化（Bronze 档来源） | 一次性预定义所有 QOI | 不可能猜全用户问题 |
| 物理映射表外挂可加载 | `qoi_rules_path` 参数 + 外挂 YAML | 写死在 sim-parse 代码里 | 闭环 Re-ingest 需要 |

---

## 20. 待决策项

启动前需要确认（**已确认**项标 ✅）：

1. ✅ **三轴架构**：sim-skills / sim-parse / sim-knowledge 三仓并立
2. ✅ **sim-knowledge 子域**：physics + formats 两子域并放（不再讨论是否合并）
3. ✅ **包名**：`sim_parse` (Python) / `sim-parse` (仓库)
4. ✅ **VTK 用法**：raw vtk，不依赖 pyvista
5. ✅ **Tier 边界**：本文档 §7 模糊字段表
6. **CLI 命令名**：`simparse`（短）/ `sim-parse`（与包名一致） —— 待确认
7. **测试 case 引用方式**：`tests/integration/` 用绝对路径 `D:\Git\SimGraph2\test_data\...`（CI 跳过）；`tests/fixtures/` 放小 KB fixture —— 待确认
8. **Phase 1 求解器范围**：建议只 OpenFOAM —— 待确认
9. **Phase 1 完成定义**：本文档 §17.2 验收标准 6 条 —— 待确认
10. **是否要 git init sim-parse**：独立 git 历史 vs 跟 simcli workspace 共享 —— 待确认
11. **sim-knowledge 是否同步建** —— 还是先 Phase 1 用 sim-parse 自带最小规则 —— 待确认（建议后者）

---

## 21. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| OpenFOAM 字典文件方言多（v2206 vs v2406 略有差异） | 在 `_foam_dict.py` 做容错；先按 v2212+ 做 |
| `vtkOpenFOAMReader` 需要 `.foam` marker 文件 | 自动创建 `<case_name>.foam` |
| 字段命名争议（用 OpenFOAM 原生名 `U` 还是受控名 `velocity`） | Tier 4 输出原生名；Tier 5 才查表换受控名 |
| Path C 沙盒安全性（LLM 生成代码注入风险） | `multiprocessing` + 文件系统 read-only mount + 网络断开；Phase 3 再细化 |
| 性能：100 个算例批量回填 | Tier 1-3 纯 Python ≤ 50 ms/case → 100 个 ~5 秒；Tier 4 加上 VTK ~2-10 秒/case |
| 物理映射表准确性 | Phase 2 实化时 cross-check 多个真实算例；闭环逐步精化 |
| Agent 临场推断不一致 | 不在 sim-parse 责任内；由 sim-knowledge 提供结构化知识 + labeled_cases 校准 |
| Re-ingest 性能（重跑全库） | 增量重跑：只跑触发 qoi_rules 变化的 case |

---

## 22. 不在本方案范围

为防止后续误解，这些是**未来可以做但当前明确不做**的事：

**结构性不做**（属于其他组件）：
- 用户问题理解（Query Agent 的事）
- 跨案例聚合查询（数据库的事）
- 物理判据知识维护（sim-knowledge 的事）
- 格式探索知识维护（sim-knowledge 的事）
- 求解器运行控制（sim-cli 的事）

**功能性暂不做**：
- 监控目录 / inotify 触发（simgraph2 的事）
- 增量解析（同 case 改动后重解析）
- 解析结果缓存层（Redis / 本地 KV）
- 多线程 / 多进程批处理（用户自己用 `joblib` 包一层）
- 异常队列管理（simgraph2 的事）
- Web UI / 可视化前端
- REST 服务化

---

## 23. 变更历史

- **v0.1** — 初版方案（架构 + Tier + Path + Dispatcher + Schema + Phase）
- **v0.2** — 加 §7 信息族目录（OpenFOAM 九族明细），明确每族提取哪些字段、来源、Tier 归属
- **v0.3** — 三轴架构整合：
  - 新增 §2 三轴架构（操作 / 解析 / 解释）作为大图
  - 新增 §11 Agent 临场推断的支撑机制（5 机制）
  - 新增 §12 QOI 演化闭环（8 步骤）
  - 新增 §16 配套生态（sim-knowledge / Query Agent / Discovery Agent 指针式说明）
  - §4 非目标 增加"Agent / 知识库 / 跨案例查询不在 sim-parse 范围"
  - §9 Path C 明确依赖 sim-knowledge/formats
  - §14 API 增加 `qoi_rules_path` 参数（为 Re-ingest 闭环留接口）
  - §15 项目结构 显示 sim-knowledge 兄弟目录
  - §19 决策 增加"知识库独立"、"Agent 不在 sim-parse 内"、"QOI 演化闭环" 三条
  - §20 待决策 部分项标记已确认
- **v0.4** — 三仓显式化（核心架构）：
  - **§2 重写**：从"3 个生命周期阶段"改为"**3 个动作 × 3 个知识库**"为主轴
    - §2.1 三动作三仓总表（跑/读/判 → sim-skills / sim-knowledge/formats / sim-knowledge/physics）
    - §2.2 三仓关系图（layered view：动作 → 知识库 → Agent → 时机）
    - §2.3 sim-parse 在三轴中的位置（"代码框架"而非"知识库"）
    - §2.4 三动作正交性论证
    - §2.5 三仓详细对照（11 维度）
    - §2.6 关键边界（5 条）
    - §2.7 完整生命周期举例（4 个 Day 串联三动作）
  - 此节升级为**全文档最重要的一节**，后续所有边界讨论都引用这里
- **v0.9** — 第二个求解器 Fluent 接入（架构验证）：
  - 加 `solvers/fluent/` —— Tier 1/2/3/4/6 完整实现
  - 三种 Fluent 子格式全覆盖：
    - `legacy`（.cas + .dat 二进制）
    - `legacy_gz`（.cas.gz + .dat.gz，gzip 压缩，VTK 透明解压）
    - `cff`（.cas.h5 + .dat.h5，HDF5 后端，V19+）
  - dispatcher 同时支持目录式（OpenFOAM）和单文件式（Fluent）case_root
  - solver 优先级：openfoam=80, fluent=70（同目录有两者标记时 OpenFOAM 赢）
  - 17 个 Fluent Tier 1 单测（合成 .cas fixtures 覆盖三种子格式 + .dat 配对 + 拒绝假阳性）
  - sim-knowledge/formats/fluent quirks 加 gzip transparent reading 条目
  - **测试 87 → 104 全过**（多 17 个 Fluent 测试）
- **v0.8** — Phase 2 表达式判据 + sim-knowledge v0.2 内容化：
  - 加 `core/canonical/safe_eval.py` —— 受限 ast eval（数值运算 + 白名单函数 + 沙盒）
  - qoi_engine 加 `expression` 规则类型 + voting 判据中的 per-criterion 表达式支持
  - **sim-knowledge 集成翻转**：sim-knowledge 物理判据现在**覆盖** sim-parse 自带规则（之前是相反）
  - Tier 3 OpenFOAM 提取器加 BC 入口值（`bc.T_inlet`、`bc.U_inlet`）从 0/<var> boundaryField
  - 真物理 `is_extinguished` 判据：`T_max - T_inlet < 100` 替代之前的 `T.max < 400` 妥协
  - 新增表达式 QOI：`temperature_excess_above_inlet`、`heat_release_collapse_ratio`
  - sim-knowledge 内容化：5 个物理域 25 个 intent + 8 种格式（含 Tecplot/Plot3D/Abaqus.inp）
  - PyYAML 1.1 quirk 修复：自动 coerce `1.0e6` → 1e6（YAML 1.1 把 `1.0e6` 当字符串）
  - 测试 63 → **87 全过**（24 个新 safe_eval 单测 + 4 个表达式集成）
- **v0.7** — Tier 5/6/7 全部实现：
  - **Tier 5 QOI**：通用规则引擎 (`core/canonical/qoi_engine.py`) + 14 条规则 (`qoi_rules.yaml`) 覆盖 mesh / 场统计 / 投票判据
  - **Tier 6 VTU 导出**：`vtkXMLUnstructuredGridWriter`，`to_vtu()` 公开 API
  - **Tier 7 Semantic**：启发式引擎，输出 physics_class / time_semantics / run_health / complexity_class / nl_summary
  - 所有 7 个 Tier 全部可被 `--tier N` 参数触发，CLI 不再有"Tier 5/6/7 静默跳过"
  - 测试从 51 → **63 全过**
  - DLR_A_cavity 完整测试：QOI 14 条 / VTU 149KB / 语义识别 LTS pseudo-time
- **v0.6** — 加"新读者基础概念速览（FAQ）"章节（在 TOC 后、§1 前）：5 个问题用大白话解释 Tier / Dispatcher / 模块结构 / sim-knowledge / labeled_cases，每条带深度章节指针
- **v0.5** — Phase 1 实施完成回填：
  - **§17.2 Phase 1** 标记 ✅ 已完成；加入实测验收表（51/51 测试通过）
  - 加入实测关键指标（耗时 / 字段数 / 物理量准确性 / labeled_cases 校准结果）
  - 与 `extraction_reference.md` v0.3 同步：补 OpenFOAM quirk 11-14（VTK stderr 警告、boundary patch 数差异、endTime 类型、LTS pseudo-time 歧义）
  - 配套建立 `sim-knowledge/labeled_cases/combustion_extinction/DLR_A_cavity_normal/ground_truth.yaml` 作为 sim-knowledge 仓库的第一个种子
