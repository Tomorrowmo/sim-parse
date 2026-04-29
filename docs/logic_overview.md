# sim-parse 的逻辑（自下而上讲）

> **定位**：本文档自上而下解释 sim-parse 是怎么"思考"的。比 [design.md](design.md) 更口语、更走流程；比 [extraction_reference.md](extraction_reference.md) 更宏观。
> **适合读者**：第一次接触 sim-parse 的人；以及想知道"我喂一个 case 进去，它内部到底走了哪些步骤"的人。

---

## 目录

1. [一句话本质](#1-一句话本质)
2. [核心抽象：Tier × Path 二维矩阵](#2-核心抽象tier--path-二维矩阵)
3. [Dispatcher：把 Tier × Path 组合起来](#3-dispatcher把-tier--path-组合起来)
4. [核心数据流：从 case_root 到 7 层输出](#4-核心数据流从-case_root-到-7-层输出)
5. [跨求解器抽象：solver registry + alias 解析](#5-跨求解器抽象solver-registry--alias-解析)
6. [知识库分层：代码 vs YAML vs 真值](#6-知识库分层代码-vs-yaml-vs-真值)
7. [一个真实案例的完整 trace（fluent_nobl1）](#7-一个真实案例的完整-tracefluent_nobl1)
8. [设计原则总结](#8-设计原则总结)
9. [扩展：加新求解器要做什么](#9-扩展加新求解器要做什么)
10. [一图总结](#10-一图总结)

---

## 1. 一句话本质

**给一个 CAE 仿真算例的目录或文件，输出 7 层结构化数据**。每层比上一层更深、更慢、更接近物理本身。调用方按需选深度。

```
case_root → parse_case() → 7 个 Tier dict
```

输入是文件系统路径（OpenFOAM 是目录，Fluent 是 `.cas` 单文件或含 `.cas` 的目录）。输出是字典：

```python
{
  "case_root": "...",
  "tier_1_identify":   {format: "fluent", solver: "fluent", ...},
  "tier_2_inventory":  {report_files: [...], journal_files: [...], ...},
  "tier_3_metadata":   {mesh_cells: 11108120, variables: [...], ...},
  "tier_4_field_stats":{variable_ranges: {...}, bounding_box: {...}, ...},
  "tier_5_qoi":        [{variable: "lift_coefficient_last", value: -1.57e-7, ...}, ...],
  "tier_6_full_data":  {vtu_path: "...", ...},
  "tier_7_semantic":   {physics_class: "supersonic+...", nl_summary: "...", ...},
  "errors": [], "warnings": [],
}
```

---

## 2. 核心抽象：**Tier × Path 二维矩阵**

这是 sim-parse 的核心设计 —— **两个独立维度**：

### Tier 维度（信息深度）

| Tier | 名字 | 干什么 | 成本 |
|---|---|---|---|
| 1 | Identify | 这是什么类型？（OpenFOAM？Fluent？哪个 solver？） | ms |
| 2 | Inventory | 里面有什么文件？ | ms |
| 3 | Metadata | 求解器配置怎么样？（湍流模型、化学机理、网格量） | 几十 ms |
| 4 | FieldStats | 场数据统计是什么？（T 最大值、Qdot 峰值） | **秒级（要解码二进制）** |
| 5 | QOI | 工程指标统一后是什么？（lift_coefficient、is_extinguished） | ms（已有 T4） |
| 6 | FullData | 把整个网格+场导出成 VTU？ | 秒~分钟 |
| 7 | Semantic | 这是什么物理类型？（推断式判断） | 秒 |

**Tier 之间有依赖**：T3 用 T2 输出，T4 用 T3 输出，T5 用 T3+T4，T7 用所有前面层。

**用户调 `target_tier=N`** 决定跑到哪一层为止。

### Path 维度（实现策略）

| Path | 怎么做 | 何时触发 | 例子 |
|---|---|---|---|
| **A** | 静态求解器 parser | 已注册的求解器（OpenFOAM/Fluent/...） | `solvers/openfoam/tier3_metadata.py` 读 controlDict |
| **B** | 通用格式启发式 | 格式认得（HDF5/CGNS）但求解器不认得 | `generic/hdf5_dump.py` 用 h5py 扫树 |
| **C** | LLM 探索 | 完全陌生格式，前两条都失败 | （Phase 3 stub，未实现） |
| **D** | 用户插件 | 长尾求解器，用户自己实现 | （框架预留，未投产） |

**正交性**：每个 (Tier, Path) 组合都是一个独立可插拔的实现槽。OpenFOAM Tier 1 和 Fluent Tier 1 都是 Path A，但代码完全不同。

---

## 3. Dispatcher：把 Tier × Path 组合起来

[`cascade.py::parse_case()`](../src/sim_parse/core/cascade.py) 是个简单的状态机，**~50 行 if/else**。它干这件事：

```python
def parse_case(case_root, target_tier=4, ...):
    out = {"case_root": ..., "errors": [], "warnings": []}

    # Step 1: Tier 1 — 谁认这个 case？
    identity = _run_tier1_identify(case_root)
    out["tier_1_identify"] = identity

    if identity["format"] is None:
        # 没人认 → 报告 unknown
        return out

    # Step 2: 选默认 path
    fmt = identity["format"]
    bundle = get_solver(fmt)         # 注册表查找
    default_path = "A" if bundle else "B"

    # Step 3: 逐 Tier 跑
    if target_tier >= 2:
        out["tier_2_inventory"] = _run_tier2_inventory(case_root, identity, default_path)
    if target_tier >= 3:
        out["tier_3_metadata"] = _run_tier3_metadata(...)
    if target_tier >= 4:
        out["tier_4_field_stats"] = _run_tier4_field_stats(...)
    if target_tier >= 5:
        out["tier_5_qoi"] = _run_tier5_qoi(..., parse_result=out)  # 把已跑的 Tier 全传进去
    if target_tier >= 6:
        out["tier_6_full_data"] = _run_tier6_export(...)
    if target_tier >= 7:
        out["tier_7_semantic"] = _run_tier7_semantic(out)

    return out
```

每个 `_run_tierN_*` 内部：

```python
def _run_tier3_metadata(case_root, identity, inventory, default_path):
    fmt = identity["format"]                  # "fluent"
    bundle = get_solver(fmt)                  # {identify, inventory, metadata, ...}
    if bundle and "metadata" in bundle:
        # Path A：调求解器特定实现
        return bundle["metadata"](case_root, identity, inventory)
    return {}                                  # Path B/空
```

### Dispatcher 的重要约束

- 永远不抛异常（`@never_raise` 装饰器包着每个内部函数）
- Path A 失败时降级到 Path B（generic）；Path B 也失败时降级到空 dict
- 最差情况返回 `{format: None, errors: [...]}`，调用方永远拿到 dict

---

## 4. 核心数据流：从 case_root 到 7 层输出

用 `fluent_nobl1` 真实案例走一遍：

```
case_root = "D:/.../fluent_nobl1"
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Dispatcher: 遍历 solver registry（按优先级）                      │
│                                                                  │
│  openfoam.identify(case_root)  → None（没有 system/controlDict）  │
│  fluent.identify(case_root)    → {"format": "fluent",            │
│                                    "sub_format": "legacy",       │
│                                    "cas_path": "...steadyhb2.cas",│
│                                    "dat_path": "...steadyhb2.dat"}│
│                                                                  │
│  → bundle = registry["fluent"]                                   │
│  → default_path = "A"                                            │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 2: fluent.inventory(case_root, identity)                   │
│                                                                  │
│  → 扫目录：*.cas / *.dat / *-rfile.out / *.trn / *.jou / libudf/ │
│  → 输出: report_files / transcript_files / journal_files / ...   │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 3: fluent.metadata(case_root, identity, inventory)          │
│                                                                  │
│  → vtkFLUENTReader.SetFileName(cas_path)                        │
│  → reader.UpdateInformation()  （只读 header，不读 data）         │
│  → 拿到：variables[14], cell_zones[7], mesh_cells=11108120       │
│  → 解析 .trn 头部 → fluent_version, last_iteration                │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 4: fluent.field_stats(case_root, identity, metadata)        │
│                                                                  │
│  → reader.SetCellArrayStatus(每个变量, 1)                         │
│  → reader.Update()  （这里真读 280MB .dat，耗 20s）                │
│  → 遍历 vtkMultiBlockDataSet 各 zone：                            │
│       wrapped.CellData["TEMPERATURE"] → numpy → min/max/mean     │
│  → 加读 *-rfile.out → report_series + 归一化的 report_qoi        │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 5: qoi_engine.evaluate_qoi_rules(rules, full_result)        │
│                                                                  │
│  rules ← 合并加载：                                                │
│     1. sim-knowledge/physics/<domain>/criteria.yaml（高优先）     │
│     2. sim-parse 自带 qoi_rules.yaml（兜底）                       │
│                                                                  │
│  对每条规则（如 max_velocity_magnitude）：                         │
│     - path: "tier_4.variable_ranges.U.magnitude_max"              │
│     - 直接查 → 没有（Fluent 用 X_VELOCITY/Y_VELOCITY/Z_VELOCITY）  │
│     - 走 alias 解析: U → fluent → [X_VELOCITY, Y_VELOCITY, Z_V]   │
│     - 检测到向量分量 → 合成 magnitude_max = max(|component_max|)  │
│     - 输出: {variable: max_velocity_magnitude, value: 2605, ...} │
│                                                                  │
│  对 voting 规则（如 is_extinguished）：                            │
│     - 多 criterion 投票（含 expression）                          │
│     - 输出 boolean + evidence + confidence                       │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 6: fluent.export_vtu(...)                                   │
│                                                                  │
│  → 同样 reader 读全场                                              │
│  → vtkXMLMultiBlockDataWriter 写 .vtm + 子文件                    │
│  → 缓存到 <case>/.simparse_cache/                                 │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tier 7: discovery.semantic.interpret(out)                        │
│                                                                  │
│  纯启发式（不调 LLM）：                                            │
│   - physics_class: 看 Tier 2 + Tier 3 的 variables → 跨求解器     │
│                    匹配（TEMPERATURE→has_T，X_VELOCITY→has_U...） │
│                    + Tier 4 的 |U| 推断 sub/transonic/supersonic  │
│   - run_health:    Tier 5 的 is_diverged + Tier 4 是否有数据      │
│   - complexity:    mesh_cells × physics_score                    │
│   - nl_summary:    模板 + Tier 3/5/7 字段拼接的中文摘要            │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
        返回 out (dict)
```

整个过程**纯确定性**（除 Tier 7 的 LLM 选项），不抛异常，每层失败时降级。

---

## 5. 跨求解器抽象：solver registry + alias 解析

### Solver registry

每个求解器在 `solvers/<name>/__init__.py` 注册：

```python
# solvers/fluent/__init__.py
register_solver(
    name="fluent",
    identify=identify,        # 函数对象
    inventory=inventory,
    metadata=metadata,
    field_stats=field_stats,
    export_vtu=export_vtu,
    priority=70,              # OpenFOAM 是 80（更高）
)
```

Dispatcher 按 priority 降序遍历，第一个 `identify()` 返回非 None 的赢。

**这是个简单的策略模式**。每加一种求解器，写 5 个函数 + 一行注册，dispatcher 自动适配。

### Alias 解析（跨求解器统一变量名）

**问题**：QOI 规则写的是"OpenFOAM 风格"路径：

```yaml
- name: max_temperature
  source: {tier: 4, path: "variable_ranges.T.max"}
```

但 Fluent 用 `TEMPERATURE`，不是 `T`。直接查 → None。

`qoi_engine._get_path()` 的 alias fallback：

```
1. 直接查 path → 失败
2. 看是否在 variable_ranges.* 下
3. 取 canonical_var = "T"
4. 查 alias_map[("T", "fluent")] → ["temperature", "static-temperature"]
5. 大小写不敏感 + dash↔underscore 容错
6. 第一个能匹配的 alias 用其 stats.max
7. 如果是向量（U → X/Y/Z_VELOCITY），合成 magnitude_max
```

`alias_map` 来自 `sim-knowledge/physics/<domain>/variables.yaml`，**写一次跨所有求解器复用**。

---

## 6. 知识库分层：代码 vs YAML vs 真值

sim-parse 把"什么是确定的、什么是知识、什么是真值"严格分开：

| 层 | 形态 | 例子 | 谁维护 |
|---|---|---|---|
| **代码** | Python | `tier3_metadata.py` 读 controlDict | 程序员 |
| **规则** | YAML | `qoi_rules.yaml`、`criteria.yaml` —— 阈值、判据公式 | 物理工程师 |
| **变量映射** | YAML | `variables.yaml` —— OpenFOAM 的 T = Fluent 的 TEMPERATURE | 数据工程师 |
| **真值** | YAML | `labeled_cases/<intent>/<case>/ground_truth.yaml` | 标注员 |

**核心好处**：

- 改 extinction 阈值 = 改一行 YAML，**不改代码、不重新打包**
- 加一个新求解器的变量映射 = 改 variables.yaml
- 校准判据准确率 = 加几个 labeled cases

代码层只 ~3000 行 Python；逻辑越上层越靠 YAML 驱动。

---

## 7. 一个真实案例的完整 trace（fluent_nobl1）

```
输入: D:/.../fluent_nobl1/   (有 .cas/.dat/.trn/8 个 .out)

[Tier 1, ~0.1s]
  openfoam.identify() → None       (没 system/controlDict)
  fluent.identify() → {format=fluent, sub_format=legacy, cas+dat 配对 ✓}

[Tier 2, ~0.1s]
  fluent.inventory() → 扫目录
  → report_files=[liftc, dragc, fx, fy, fz, mx, my, mz]
  → transcript=[fluent-...trn]

[Tier 3, ~10s]
  vtkFLUENTReader.SetFileName(.cas).UpdateInformation()
  → variables=[14 个]
  → mesh_cells=11108120, mesh_points=2791299, cell_zones=7
  → 解析 .trn → fluent_version=2020 R2, last_iteration=15

[Tier 4, ~21s]
  reader.Update() （读 280MB）
  → variable_ranges (PRESSURE/TEMPERATURE/X_V/Y_V/Z_V/DENSITY)
  → bounding_box
  → 加读 8 个 *.out 文件 → report_series + report_qoi

[Tier 5, ~0.01s]
  load_qoi_rules() → 31 条规则（sim-knowledge + builtin）
  对每条:
    max_temperature       ← alias T → TEMPERATURE.max = 1157
    max_velocity_magnitude ← alias U → 向量合成 = 2605
    lift_coefficient_last ← report_qoi.lift_coefficient.last_value = -2.85e-6
    drag_coefficient_last ← report_qoi.drag_coefficient.last_value = 5.53e-3
    is_extinguished       ← required_paths 没满足（不是燃烧 case）→ 跳过
    is_diverged           ← 各 criterion 都不触发 → false [HIGH]
    ...
  → 25 条 QOI 长表

[Tier 6, ~5s]
  reader 再次执行 → vtkXMLMultiBlockDataWriter 写 .vtm

[Tier 7, ~0.01s]
  semantic.interpret(整个 out):
    physics_class: 看 vars → has_T+has_U+has_rho+has_Ma →
                            heat_transfer+flow+compressible
                   看 |U|=2605 → 加 hypersonic
    run_health: 看 is_diverged=False + Tier4 有数据 → healthy [MED]
    complexity: 11M cells + score 4 → large_complex
    nl_summary: 模板拼接 → "fluent (?); 网格 11M 单元; hypersonic; ..."

返回 out (dict, ~70 个字段)
```

---

## 8. 设计原则总结

> **5 个核心原则**，所有代码决策从这 5 条派生：

| 原则 | 落地 |
|---|---|
| **正交分层** | Tier × Path 是二维矩阵，每格独立可插拔；改一格不动其他 |
| **确定性优先** | Tier 1-6 全是确定性 Python；LLM 只在 Tier 7 / Path C 出现，且可关闭 |
| **零异常契约** | `@never_raise` 包所有 public API；失败字段 None + 写 errors/warnings |
| **知识与代码分离** | 阈值/判据/变量映射全在 YAML；改判据不改代码 |
| **下游单向依赖** | sim-parse 不知道 simgraph2 / sim-cli 存在；调用方依赖 sim-parse 而非反之 |

---

## 9. 扩展：加新求解器要做什么

要加 LS-DYNA：

1. 写 `solvers/lsdyna/{tier1_identify, tier2_inventory, tier3_metadata, tier4_field_stats}.py`
2. `solvers/lsdyna/__init__.py` 调 `register_solver()`
3. 在 `__init__.py` 顶层加 `from sim_parse.solvers import lsdyna`
4. （可选）在 `sim-knowledge/physics/structural/variables.yaml` 加 LS-DYNA 的变量别名

代码量 ~500 行（OpenFOAM/Fluent 的尺度）。**不需要碰 dispatcher 或 qoi_engine**。

---

## 10. 一图总结

```
                        case_root
                            │
                            ▼
              ┌─────────────────────────┐
              │   Dispatcher (cascade)   │
              │   ~50 行 if/else         │
              └────────┬────────────────┘
                       │
        ┌──────────────┼──────────────────┐
        ▼              ▼                  ▼
   solvers/...    generic/...       discovery/...
   (Path A)        (Path B)         (Path C, stub)
                       │
                       ▼
              ┌─────────────────────────┐
              │   每 Tier 跑一次          │
              │   T1→T2→T3→T4→T5→T6→T7  │
              └────────┬────────────────┘
                       │
                       ▼ (调用)
              ┌─────────────────────────┐
              │   qoi_engine            │
              │   - load YAML rules     │
              │   - alias 解析           │
              │   - safe expression eval│
              │   - 输出 long-format    │
              └────────┬────────────────┘
                       │
                       ▼
              ┌─────────────────────────┐
              │   semantic.interpret    │
              │   启发式 → physics_class │
              │            run_health   │
              │            nl_summary   │
              └────────┬────────────────┘
                       │
                       ▼
                   out dict
                   (7 层 + 元字段)


              ┌────────────────────┐
              │ sim-knowledge/     │  ← 不在 sim-parse 内
              │  physics/<domain>/ │     但被 qoi_engine 自动加载
              │  formats/<format>/ │     合并到规则 / alias map
              │  labeled_cases/    │
              └────────────────────┘
```

**这就是 sim-parse 的全部逻辑**。代码 ~3000 行，规则 YAML ~30 个文件，跑得通 4 种格式（OpenFOAM、Fluent legacy/CFF/gz、HDF5 generic、CGNS 部分）；测试 104 个全过；真实工程算例（DLR_A_cavity 燃烧、fluent_nobl1 高超音速）跑通。

---

## 配套阅读

- [design.md](design.md) —— 完整架构设计文档
- [extraction_reference.md](extraction_reference.md) —— 各求解器格式细节
- [criteria_reference.md](criteria_reference.md) —— 物理判据参考
- [../../sim-knowledge/README.md](../../sim-knowledge/README.md) —— 知识库结构

## 变更历史

- v0.1 — 初版，基于 sim-parse v0.9 写的逻辑总览
