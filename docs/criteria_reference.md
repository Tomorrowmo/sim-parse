# 物理判据参考（How to Judge）

> **状态**：参考文档 v0.1
> **配套文档**：
> - [design.md](design.md) —— 高层架构、Tier/Path 定义
> - [extraction_reference.md](extraction_reference.md) —— 姊妹文档，"读"轴的实施依据
> **对应三轴**：design.md §2 中**"判"** 这一动作的实施依据
> **目的**：为 `sim-knowledge/physics/<domain>/criteria.yaml` 提供素材；给 Query Agent **临场推断**兜底用；给 Tier 5 QOI 规则提供需求规格

---

## 目录

1. [文档目的与边界](#1-文档目的与边界)
2. [通用约定](#2-通用约定)
3. [Fluid Dynamics（流体动力学）](#3-fluid-dynamics流体动力学)
4. [Heat Transfer（传热）](#4-heat-transfer传热)
5. [Combustion（燃烧）](#5-combustion燃烧)
6. [Numerics（数值收敛/守恒）](#6-numerics数值收敛守恒)
7. [Structural（结构动力学）](#7-structural结构动力学)
8. [Multiphase（多相）](#8-multiphase多相)
9. [跨域考虑](#9-跨域考虑)
10. [阈值校准哲学](#10-阈值校准哲学)
11. [labeled_cases 联动](#11-labeled_cases-联动)
12. [迁移到 sim-knowledge](#12-迁移到-sim-knowledge)
13. [变更历史](#13-变更历史)

---

## 1. 文档目的与边界

### 1.1 这份文档回答什么

每个物理域（流/热/燃/数值/结构）下**可被自动判定的物理状态**有哪些，每个状态用什么信号判、阈值是多少、理论依据是什么。

### 1.2 这份文档**不**回答什么

- ❌ 怎么**操作**求解器（看 sim-skills）
- ❌ 怎么**读**陌生格式（看 extraction_reference.md）
- ❌ 哪些字段从哪个文件抓（看 extraction_reference.md）
- ❌ Query Agent 的 LLM 调度细节（看 sim-knowledge 自身设计文档，Phase 3+ 实化）

### 1.3 受众

- sim-parse Tier 5 QOI 规则的实现者（写 `core/canonical/qoi_rules/*.yaml` 的人）
- sim-knowledge/physics 内容贡献者
- Query Agent 的设计者（理解可判物理状态的全集）
- 想了解"sim-parse 能自动告诉我哪些物理判断"的工程师

### 1.4 Phase 2 表达式判据（已实化 v0.7+）

✅ 已实现：YAML 判据可以用**算术表达式**组合多个信号。

```yaml
- name: low_temperature_excess
  expression: "T_max - T_inlet"      # 真物理判据
  inputs:
    T_max:    {path: "tier_4.variable_ranges.T.max"}
    T_inlet:  {path: "tier_3.bc.T_inlet", fallback: 300.0}
  op: "<"
  threshold: 100.0
```

支持：
- 数值字面量 (int/float)
- 命名变量（从 tier 路径取）+ fallback 默认值
- 二元运算: `+ - * / // % **`
- 一元运算: `+ -`
- 白名单函数: `abs`, `min`, `max`, `sum`, `sqrt`, `pow`, `log`, `log10`, `log2`, `exp`

**禁止**：属性访问、subscript、import、lambda、自定义函数 —— 沙盒限制由 `core/canonical/safe_eval.py` 强制。

这一升级让 `is_extinguished` 等判据使用真实物理公式（`T_max - T_inlet < 100K`）而不是 Phase 1 的绝对阈值妥协（`T.max < 400K`）。

---

### 1.5 三类内容的关系

```
extraction_reference.md   ──提取信号──►  Tier 4 输出
                                               │
                                               ▼
                                  ┌───────────────────────┐
                                  │  本文档 criteria.md    │
                                  │  规定信号如何组合判定  │
                                  │  (规则 + 阈值 + 文献)  │
                                  └───────┬───────────────┘
                                          │
                              ┌───────────┴───────────┐
                              ▼                       ▼
                   sim-parse Tier 5 QOI         Query Agent 临场推断
                   (固化规则 YAML)               (检索+套用本文档)
```

---

## 2. 通用约定

### 2.1 Intent 概念

**Intent** = "用户/系统想知道的物理判断"，是判据的最小单元。

| 形式 | 示例 |
|---|---|
| 布尔 (boolean) | `is_extinguished`, `is_converged` |
| 枚举 (categorical) | `flow_regime` ∈ {subsonic, transonic, supersonic, hypersonic} |
| 标量带阈值告警 (scalar+alarm) | `courant_max` + `courant_excessive_alarm` |
| 等级 (grade) | `convergence_grade` ∈ {A, B, C, F} |

### 2.2 Intent 的元数据 schema

每个 intent 应该被这样描述（YAML 化后是 sim-knowledge/physics/<domain>/criteria.yaml 的条目）：

```yaml
<intent_id>:
  description: 中文说明
  output_type: boolean | enum | scalar | grade
  applicable_when:
    - 必要前提条件 (例如 has_combustion_model: true)
  signals_required:
    - signal_name: 引用 extraction_reference.md 中的信号名
      tier: 4
  rule:
    type: voting | threshold | regex | computed
    detail: ...
  thresholds:
    default: ...
    by_condition:
      - if: {fuel: CH4, pressure_atm: [0.5, 2]}
        then: ...
  reference: 文献引用
  confidence_when_matched: HIGH | MED | LOW
  validation_cases:
    - labeled_cases/.../case_id_001
  limitations:
    - 已知不适用情况
```

### 2.3 置信度等级

Agent / Tier 5 输出时给每个 intent 标置信度：

| 等级 | 含义 |
|---|---|
| **HIGH** | 判据强（多信号一致、有文献支持、已校准过） |
| **MED** | 判据中等（单信号、有文献，未充分校准） |
| **LOW** | 判据弱（启发式推断，无校准） |
| **PENDING** | 信号缺失，无法判定 |

### 2.4 阈值的可配置性

阈值**绝不写死在代码里**。每个 intent 的 thresholds 应该：
- 提供 `default`（保守值）
- 按 `by_condition` 分组（不同燃料/压力/尺度有不同阈值）
- 用户可以在 case-level 通过 `overrides.yaml` 覆盖

### 2.5 状态标记

| 标记 | 含义 |
|---|---|
| ✅ | 判据规则清晰、有理论支持、可直接实现 |
| ⚠️ | 判据存在但有歧义 / 阈值需校准 |
| 🔬 | 仅理论框架，需更多 labeled cases 才能定阈值 |
| ❌ | 暂不定义（超出当前范围） |

---

## 3. Fluid Dynamics（流体动力学）

### 3.1 域简介

**适用**：CFD 流场算例（不可压 / 可压 / 多相中的连续相）、稳态或瞬态。  
**典型求解器**：simpleFoam / pisoFoam / pimpleFoam / rhoSimpleFoam（OpenFOAM）、Fluent、CFX、SU2、STAR-CCM+。

### 3.2 Intent 全集

| Intent ID | 输出类型 | 描述 | 状态 |
|---|---|---|---|
| `is_steady_state_converged` | boolean | 稳态算例是否收敛 | ✅ |
| `mach_regime` | enum | 流动速度区间（亚音速/跨音速/超音速/高超音速） | ✅ |
| `has_recirculation_at_outlet` | boolean | 出口存在回流（可能数值不稳） | ⚠️ |
| `has_separation_on_walls` | boolean | 壁面流动分离 | 🔬 |
| `mass_imbalance_alarm` | boolean | 进出口质量不守恒超阈值 | ✅ |
| `yplus_resolution_adequate` | boolean | 边界层 y+ 满足模型要求 | ⚠️ |
| `pressure_drop_excessive` | boolean | 压损异常大（数值发散前兆） | ⚠️ |
| `reynolds_number_estimated` | scalar | 估算雷诺数 | ✅ |

### 3.3 详细判据

#### 3.3.1 `is_steady_state_converged`

**一句话**：稳态算例的所有控制方程残差下降到允许的阶数以下并稳定。

**依赖信号**（Tier 4）：

| 信号 | extraction 来源 |
|---|---|
| `residual_history.<var>` (per equation) | extraction §3.3.E "残差历史" |
| `convergence_orders.<var>` | extraction §3.3.E |
| 解算器配置中的 `convergence_tolerance` | extraction §3.3.C |

**判据**（YAML 形式）：

```yaml
is_steady_state_converged:
  description: "稳态算例收敛"
  output_type: boolean
  applicable_when:
    - time_formulation: steady
  signals_required:
    - residual_history (per equation: U/p/k/epsilon/omega/h)
    - convergence_tolerance (from controlDict / fvSolution)
  rule:
    type: voting
    voting_threshold: "all"
    rules:
      - signal: "convergence_orders.U"
        condition: ">= 3"        # 至少下降 3 个数量级
      - signal: "convergence_orders.p"
        condition: ">= 3"
      - signal: "convergence_orders.continuity"  # 连续性方程
        condition: ">= 4"
      - signal: "residual_history.<var>.last_50_iterations.std"
        condition: "< 0.1 * residual_history.<var>.last"  # 残差稳定不振荡
        all_vars: true
  thresholds:
    default: {orders: 3, stability_factor: 0.1}
    by_condition:
      - if: {turbulence_model: ["LES", "DES"]}
        then: {orders: 2}        # LES/DES 残差不会下降很多
  reference: "Patankar 1980 §6.4; Versteeg & Malalasekera 2007 §11"
  confidence_when_matched: HIGH
  limitations:
    - "LES / DES 残差判据放宽，更应该看物理量统计稳定性"
    - "瞬态算例不适用（用 is_pseudo_time_converged 代替）"
```

#### 3.3.2 `mach_regime`

**一句话**：根据流场最大马赫数划分流动速度区间。

**依赖信号**：

| 信号 | extraction 来源 |
|---|---|
| `Ma_max` (or computed from `U_max` and `T_max`) | extraction §3.3.B 变量统计 |
| `gamma`, `R_gas`（来自 thermophysics） | extraction §3.3.C |

**判据**：

```yaml
mach_regime:
  output_type: enum
  enum_values: ["incompressible", "subsonic", "transonic", "supersonic", "hypersonic"]
  signals_required:
    - Ma_max (direct or computed)
  rule:
    type: enum_threshold
    rules:
      - if: "Ma_max < 0.3"
        then: incompressible
      - if: "0.3 <= Ma_max < 0.8"
        then: subsonic
      - if: "0.8 <= Ma_max < 1.2"
        then: transonic
      - if: "1.2 <= Ma_max < 5"
        then: supersonic
      - if: "Ma_max >= 5"
        then: hypersonic
  reference: "Anderson 2001 Modern Compressible Flow"
  confidence_when_matched: HIGH
  limitations:
    - "仅看最大马赫数，局部区域可能有不同 regime"
```

#### 3.3.3 `mass_imbalance_alarm`

**一句话**：进出口质量流不守恒超过容许误差。

**依赖信号**：

| 信号 | extraction 来源 |
|---|---|
| `mass_flow_in` (sum over all inlets) | extraction §14.1 受控变量 |
| `mass_flow_out` (sum over all outlets) | 同上 |

**判据**：

```yaml
mass_imbalance_alarm:
  output_type: boolean
  signals_required:
    - mass_flow_in
    - mass_flow_out
  rule:
    type: threshold
    formula: "abs(mass_flow_in - mass_flow_out) / abs(mass_flow_in) > tolerance"
  thresholds:
    default: 0.01    # 1% 误差容忍
    by_condition:
      - if: {has_porous_zone: true}
        then: 0.05    # 多孔区有数值损失，放宽
  reference: "数值守恒基本要求；连续性方程残差判据的等价表达"
  confidence_when_matched: HIGH
```

### 3.4 简略 intents

#### `has_recirculation_at_outlet`
- 信号：outlet patch 上速度场法向分量有负值（或 reverse_flow_count > 0）
- 阈值：reverse_flow 比例 > 5%（默认）
- 限制：partial outlet 不一定是问题，需结合压力边界判断

#### `has_separation_on_walls`
- 信号：wall_shear_stress 在某 patch 上反向 / 接近 0
- 限制：需要场积分，不是单值；高级判据要 skin friction coefficient
- 状态：🔬（需专门提取 wall_shear 时序）

#### `yplus_resolution_adequate`
- 信号：y+ 第一层网格值（per wall patch）
- 阈值：取决于湍流模型
  - kEpsilon (wall function): 30 < y+ < 300
  - kOmegaSST (low-Re): y+ < 1
  - LES: y+ < 1
- 引用：Pope 2000 *Turbulent Flows* §11

#### `pressure_drop_excessive`
- 阈值：依案例，需 case-specific 校准

#### `reynolds_number_estimated`
- 信号：U_inlet, characteristic_length, kinematic_viscosity
- 公式：Re = U * L / ν
- 限制：characteristic_length 需要用户提供或 from bbox

### 3.5 跨求解器变量映射

参见 [extraction_reference.md §14.1](extraction_reference.md#141-流体动力学)。

### 3.6 当前 Tier 5 实现状态

Phase 1：仅 `is_steady_state_converged` + `mach_regime` 实化  
Phase 2：补 `mass_imbalance_alarm` + `yplus_resolution_adequate`  
Phase 3+：其他

---

## 4. Heat Transfer（传热）

### 4.1 域简介

**适用**：含热场的 CFD（共轭传热 cht / 热对流 / 辐射）、纯固体导热、热-结构耦合。  
**典型求解器**：chtMultiRegionFoam / buoyantSimpleFoam / Fluent thermal / Abaqus heat transfer。

### 4.2 Intent 全集

| Intent ID | 输出类型 | 描述 | 状态 |
|---|---|---|---|
| `has_thermal_extreme` | boolean | 出现非物理高温（材料超极限） | ✅ |
| `convection_dominant` | boolean | 流动以对流为主（vs 导热） | ⚠️（按 Pe 数） |
| `thermal_equilibrium_reached` | boolean | 多区耦合达到热平衡 | ⚠️ |
| `excessive_wall_heat_flux` | boolean | 壁面热流超工程容许 | 🔬 |
| `temperature_uniformity_at_outlet` | grade | 出口温度均匀性等级 | ⚠️ |
| `peclet_number_estimated` | scalar | 估算 Peclet 数 | ✅ |

### 4.3 详细判据

#### 4.3.1 `has_thermal_extreme`

**一句话**：温度场最大值超过材料的物理或工程上限。

**依赖信号**：

| 信号 | extraction 来源 |
|---|---|
| `T.max` | extraction §3.3.B 变量统计 |
| `T_inlet`, `T_ambient`（如适用） | extraction §3.3.C BC |

**判据**：

```yaml
has_thermal_extreme:
  output_type: boolean
  signals_required:
    - T.max
    - T.min
  rule:
    type: threshold
    formula: "T.max > T_max_acceptable OR T.min < T_min_acceptable"
  thresholds:
    default: {T_max_acceptable: 3000, T_min_acceptable: 0}  # K
    by_condition:
      - if: {fuel: CH4, combustion: enabled}
        then: {T_max_acceptable: 2500}      # 甲烷-空气理论火焰温度 ~2200K，3000+ 异常
      - if: {fuel: H2, combustion: enabled}
        then: {T_max_acceptable: 2700}
      - if: {has_solid_region: true, material: steel}
        then: {T_max_acceptable: 1800}      # 钢熔点
  reference: "化学反应理论火焰温度 + 工程材料极限"
  confidence_when_matched: MED   # 需 case-specific 阈值
  limitations:
    - "理论火焰温度因当量比变化，1500-2500K 范围内"
    - "材料极限因案例不同，应用户提供"
```

#### 4.3.2 `convection_dominant`

**一句话**：通过 Peclet 数判断对流相对导热的主导性。

**依赖信号**：

| 信号 | 来源 |
|---|---|
| `velocity_characteristic` (U_inlet 或域平均) | extraction §3.3.C BC + B 变量 |
| `length_characteristic` (用户提供 or bbox) | extraction §3.3.A bbox |
| `thermal_diffusivity` α = k / (ρ Cp) | extraction §3.3.C thermophysics |

**判据**：

```yaml
convection_dominant:
  output_type: boolean
  signals_required:
    - velocity_characteristic
    - length_characteristic
    - thermal_diffusivity
  rule:
    type: threshold
    formula: "Pe = (U * L) / alpha"
    boolean_threshold: "Pe > 1"
  thresholds:
    default: {Pe_threshold: 1}
  reference: "Bejan 2013 *Convection Heat Transfer* §1"
  confidence_when_matched: HIGH
  limitations:
    - "characteristic length / velocity 选择有主观性"
```

### 4.4 简略 intents

#### `thermal_equilibrium_reached`
- 信号：多 region 间界面温度差时序 → 收敛
- 阈值：界面温差 < 1K 持续 N 步

#### `temperature_uniformity_at_outlet`
- 信号：outlet patch 上 T 的方差 / 平均值
- grade：A (变异系数 < 5%) / B (5-15%) / C (15-30%) / F (>30%)

#### `excessive_wall_heat_flux`
- 信号：wall_heat_flux 时序最大值
- 阈值：与材料极限相关，需 case-specific

### 4.5 跨求解器变量映射

参见 [extraction_reference.md §14.2](extraction_reference.md#142-热)。

---

## 5. Combustion（燃烧）

### 5.1 域简介

**适用**：所有燃烧算例 —— 预混 / 扩散 / 部分预混；气相 / 液相喷雾 / 颗粒。  
**典型求解器**：reactingFoam / sprayFoam / fireFoam（OpenFOAM）、Fluent species / Cantera。  
**关键文献**：Poinsot & Veynante 2005 *Theoretical and Numerical Combustion*；Williams 1985 *Combustion Theory*；Peters 2000 *Turbulent Combustion*。

### 5.2 Intent 全集

| Intent ID | 输出类型 | 描述 | 状态 |
|---|---|---|---|
| `is_extinguished` | boolean | 火焰熄灭 | ✅ 重点 |
| `is_ignited` | boolean | 点火成功 | ✅ |
| `ignition_delay_acceptable` | boolean | 点火延迟在容许范围 | ⚠️ |
| `excessive_co_emissions` | boolean | CO 排放超标 | ⚠️ |
| `complete_combustion` | boolean | 燃料燃尽 | ✅ |
| `flame_unsteady` | boolean | 火焰位置/强度振荡 | 🔬 |
| `equivalence_ratio_in_flammable_range` | boolean | 当量比在可燃范围 | ✅ |
| `soot_formation_significant` | boolean | 显著结碳 | 🔬 |

### 5.3 详细判据

#### 5.3.1 `is_extinguished`（核心，本项目重点示例）

**一句话**：火焰熄灭 —— 反应停止，温度回落，无自由基。

**依赖信号**：

| 信号 | extraction 来源 |
|---|---|
| `Qdot.max_over_time` | extraction §11.2 燃烧场景信号 |
| `T.max_over_time` | 同上 |
| `T_inlet` | extraction §3.3.C BC |
| `OH.max_over_time` | extraction §3.3.B variables |

**判据**：

```yaml
is_extinguished:
  description: "火焰熄灭判定"
  output_type: boolean
  applicable_when:
    has_combustion_model: true
    has_chemistry: true
  signals_required:
    primary:
      - Qdot.last.max
      - T.last.max
      - T_inlet
      - OH.last.max (如可用)
    secondary:
      - Qdot.peak.max  # 用于趋势判据
  rule:
    type: voting
    voting_threshold: 2          # 至少 2 条满足
    criteria:
      - id: low_heat_release
        signal: "Qdot.last.max"
        condition: "< threshold_qdot"
        weight: 1.0
      - id: low_temperature_excess
        formula: "T.last.max - T_inlet"
        condition: "< threshold_T_excess"
        weight: 1.0
      - id: no_oh_radical
        signal: "OH.last.max"
        condition: "< threshold_OH"
        weight: 1.0
        applicable_when: "OH in available_variables"
      - id: heat_release_collapse
        formula: "Qdot.last.max / Qdot.peak.max"
        condition: "< 0.05"
        weight: 1.5             # 趋势判据权重高
  thresholds:
    default:
      threshold_qdot: 1.0e6        # W/m^3
      threshold_T_excess: 100      # K
      threshold_OH: 1.0e-6         # mass fraction
    by_condition:
      - if: {fuel: ["CH4", "methane"], pressure_atm_range: [0.5, 2]}
        then:
          threshold_qdot: 1.0e6
          threshold_T_excess: 100
      - if: {fuel: ["H2", "hydrogen"]}
        then:
          threshold_qdot: 5.0e6     # H2 火焰更剧烈
          threshold_T_excess: 150
      - if: {fuel: ["kerosene", "C12H23"], pressure_atm_range: [10, 50]}
        then:
          threshold_qdot: 1.0e7    # 高压 kerosene 火焰能量密度高
          threshold_T_excess: 200
  reference:
    - "Williams 1985 *Combustion Theory* Ch.5 (临界拉伸率)"
    - "Peters 2000 *Turbulent Combustion* §2.4 (Karlovitz 数)"
    - "Poinsot & Veynante 2005 *Theoretical and Numerical Combustion* Ch.5"
    - "OpenFOAM tutorial flameletFoam (工程经验阈值)"
  confidence_when_matched: HIGH
  validation_cases:
    - labeled_cases/combustion_extinction/CH4_jet_normal/    # 期望 false
    - labeled_cases/combustion_extinction/CH4_jet_blowout/   # 期望 true
    - labeled_cases/combustion_extinction/DLR_A_LTS_normal/  # 期望 false
  limitations:
    - "间歇性熄火（pulsation）只看最后时间步会误判，应配合 last_N_steps 趋势"
    - "OH 在简化机理（如 1-step CH4-air）中不存在；需要详细机理才有"
    - "对 LES 算例，瞬时熄火可能正常；要看时间平均"
    - "小尺度纯化学反应（reactor 算例）不适用 —— Qdot 阈值需要重设"
```

#### 5.3.2 `is_ignited`

**一句话**：从初始条件（无火）成功点燃形成自维持火焰。

**依赖信号**：

| 信号 | 来源 |
|---|---|
| `T.max_over_time` （时间序列） | extraction §3.3.B |
| `Qdot.max_over_time` | 同上 |
| `T_inlet` | BC |

**判据**：

```yaml
is_ignited:
  output_type: boolean
  applicable_when:
    has_combustion_model: true
    initial_condition_type: "cold"  # 即 0/T 不含点火源
  signals_required:
    - T.max_time_series
    - Qdot.max_time_series
  rule:
    type: voting
    voting_threshold: 2
    criteria:
      - id: temperature_rose
        formula: "max(T.time_series) - T_inlet > T_rise_threshold"
        condition: "true"
      - id: heat_release_started
        formula: "max(Qdot.time_series) > Qdot_threshold"
        condition: "true"
      - id: heat_release_sustained
        formula: "Qdot.last_50pct_steps.mean / Qdot.peak > sustain_ratio"
        condition: "true"
  thresholds:
    default:
      T_rise_threshold: 500          # K
      Qdot_threshold: 1.0e6
      sustain_ratio: 0.3             # 末段保持峰值的 30% 以上
  reference: "Westbrook 2000 *Combustion Modeling* Ch.9"
  confidence_when_matched: HIGH
  limitations:
    - "已有点火源的 case 应该用 is_extinguished 反推（看是否后续维持）"
    - "Pulsating ignition / re-ignition 可能误判"
```

#### 5.3.3 `complete_combustion`

**一句话**：燃料在出口/域内被燃尽（残余 < 阈值）。

**依赖信号**：

| 信号 | 来源 |
|---|---|
| `Y_<fuel>.outlet.mean` | extraction §14.1 mean_outlet 类 |
| `Y_<fuel>.inlet` | BC |

**判据**：

```yaml
complete_combustion:
  output_type: boolean
  signals_required:
    - Y_<fuel_species>_outlet_mean
    - Y_<fuel_species>_inlet
  rule:
    type: threshold
    formula: "Y_<fuel>_outlet / Y_<fuel>_inlet < unburnt_ratio_threshold"
  thresholds:
    default: 0.01             # 出口残余 < 1% 入口浓度
    by_condition:
      - if: {regime: "fuel_lean", equivalence_ratio: [0.5, 0.9]}
        then: 0.005             # 贫燃工况要求更严
      - if: {regime: "fuel_rich", equivalence_ratio: [1.1, 2.0]}
        then: 0.05              # 富燃工况允许更多残余
  reference: "化学动力学基本概念"
  confidence_when_matched: HIGH
  limitations:
    - "需要识别主燃料（CH4 vs C12H23 vs H2）—— 看 chemistry 机理"
    - "复杂燃料（航空煤油代理物）有多种主组分，要列表"
```

### 5.4 简略 intents

#### `ignition_delay_acceptable`
- 信号：`Qdot` 时序穿越阈值的时刻 vs 算例 endTime
- 阈值：与设计要求 / 实验值对比

#### `excessive_co_emissions`
- 信号：`Y_CO.outlet.mean` × mass flow → emission index (g/kg-fuel)
- 阈值：行业标准（航空 < 50 g/kg；地面燃烧 < 100 g/kg）

#### `equivalence_ratio_in_flammable_range`
- 信号：从 inlet `Y_<fuel>` 和 `Y_O2` 算 phi
- 可燃极限：CH4-air phi ∈ [0.5, 1.6]；H2-air phi ∈ [0.1, 7]；按燃料查表

#### `soot_formation_significant`
- 信号：soot mass fraction (如有 soot model) / C2H2 浓度作 surrogate
- 状态：🔬 需要 soot model 启用

### 5.5 跨求解器变量映射

参见 [extraction_reference.md §14.3](extraction_reference.md#143-燃烧)。

---

## 6. Numerics（数值收敛/守恒）

### 6.1 域简介

**适用**：所有 CFD/CSM 算例（无关物理 —— 数值健康检查是普世的）。  
**关键文献**：Patankar 1980 *Numerical Heat Transfer and Fluid Flow*；Versteeg & Malalasekera 2007；Roache 1998 *Verification and Validation*。

### 6.2 Intent 全集

| Intent ID | 输出类型 | 描述 | 状态 |
|---|---|---|---|
| `is_converged` | boolean | 算例（稳态或瞬态步内）已收敛 | ✅ |
| `is_diverged` | boolean | 算例发散（残差爆涨 / NaN） | ✅ |
| `excessive_courant` | boolean | Courant 数飙升（瞬态稳定性差） | ✅ |
| `residual_oscillating` | boolean | 残差稳定但振荡 | ⚠️ |
| `mass_continuity_violated` | boolean | 连续性方程残差异常大 | ✅ |
| `excessive_iteration_count` | boolean | 迭代次数远超期望 | ⚠️ |
| `timestep_collapsed` | boolean | (transient) deltaT 被自动调到极小 | ⚠️ |
| `nan_in_field` | boolean | 场中出现 NaN/Inf | ✅ |

### 6.3 详细判据

#### 6.3.1 `is_diverged`

**一句话**：残差异常增长或场出现 NaN，求解过程已经失控。

**依赖信号**：

| 信号 | 来源 |
|---|---|
| `residual_history.<var>` | extraction §3.3.E |
| `field_max_abs` per variable per time step | Tier 4 |

**判据**：

```yaml
is_diverged:
  output_type: boolean
  signals_required:
    - residual_history per variable
    - field_max_abs per variable
  rule:
    type: voting
    voting_threshold: 1            # 任一条满足即判发散
    criteria:
      - id: residual_explosion
        formula: "residual.last / residual.initial > explosion_factor"
        condition: "true"
      - id: nan_in_field
        formula: "any(isnan(field) or isinf(field))"
        condition: "true"
      - id: variable_unphysical
        formula: "abs(T.max) > 1e8 OR abs(p.max) > 1e15 OR abs(U.magnitude.max) > 1e6"
        condition: "true"
  thresholds:
    default: {explosion_factor: 100}
  reference: "Roache 1998; OpenFOAM FATAL ERROR catalog"
  confidence_when_matched: HIGH
  limitations:
    - "瞬态算例残差先升后降是正常的，不能只看最后一步"
```

#### 6.3.2 `excessive_courant`

**一句话**：Courant 数超过 1（显式）或某用户阈值（隐式），瞬态求解可能不稳。

**依赖信号**：

| 信号 | 来源 |
|---|---|
| `courant_max_time_series` | extraction §11.2 燃烧场景信号（也适用所有 CFD） |
| 解算器类型（显式 vs 隐式） | extraction §3.3.C |

**判据**：

```yaml
excessive_courant:
  output_type: boolean
  signals_required:
    - courant_max_time_series
  rule:
    type: threshold
    formula: "courant_max.last > courant_threshold OR courant_max.max > courant_alarm"
  thresholds:
    default:
      courant_threshold: 1.0          # 显式 default
      courant_alarm: 5.0              # 即使隐式，超 5 也告警
    by_condition:
      - if: {solver_class: "implicit_pimple"}
        then: {courant_threshold: 5.0, courant_alarm: 50.0}
      - if: {solver_class: "explicit_lts"}
        then: {courant_threshold: 0.5, courant_alarm: 1.0}
  reference: "Courant-Friedrichs-Lewy 1928"
  confidence_when_matched: HIGH
```

#### 6.3.3 `mass_continuity_violated`

**一句话**：连续性方程残差远高于动量方程，质量不守恒。

**依赖信号**：

| 信号 | 来源 |
|---|---|
| `residual_history.continuity` (or `residual_history.p`) | extraction §3.3.E |
| `mass_flow_in / mass_flow_out` | extraction §14.1 |

**判据**：

```yaml
mass_continuity_violated:
  output_type: boolean
  signals_required:
    - residual_history.continuity (or residual_history.p as proxy)
    - mass_flow_in
    - mass_flow_out
  rule:
    type: voting
    voting_threshold: 1
    criteria:
      - id: residual_too_high
        formula: "residual.continuity.last > residual_threshold"
      - id: mass_imbalance
        formula: "abs(mass_flow_in - mass_flow_out) / abs(mass_flow_in) > imbalance_threshold"
  thresholds:
    default:
      residual_threshold: 1.0e-3
      imbalance_threshold: 0.01
  reference: "Patankar 1980 §6.7 SIMPLE algorithm"
  confidence_when_matched: HIGH
```

### 6.4 简略 intents

#### `is_converged` (聚合判据)
- 通用形式：`(NOT is_diverged) AND (residual_orders >= N) AND (NOT residual_oscillating)`
- 引用流体动力学的 `is_steady_state_converged` 或瞬态步内收敛
- N 默认 3，可调

#### `nan_in_field`
- 信号：任意场 isnan / isinf
- 判据：直接

#### `residual_oscillating`
- 信号：残差最后 N 步的标准差 / 均值
- 阈值：CV > 50% 视为振荡

#### `timestep_collapsed`
- 信号：deltaT_history 时序最后值 / 初始值
- 阈值：< 0.01（瞬态 case，自适应步长）

#### `excessive_iteration_count`
- 信号：iteration count vs 用户期望（需 prior knowledge）

### 6.5 跨求解器变量映射

参见 [extraction_reference.md §14.4](extraction_reference.md#144-数值收敛)。

---

## 7. Structural（结构动力学）

### 7.1 域简介

**适用**：固体力学（静态 / 显式动力学 / 隐式动力学 / 模态 / 屈曲）。  
**典型求解器**：Abaqus / LS-DYNA / MAPDL / CalculiX。  
**关键文献**：Bathe 2014 *Finite Element Procedures*；Belytschko et al. 2014 *Nonlinear Finite Elements*。

### 7.2 Intent 全集

| Intent ID | 输出类型 | 描述 | 状态 |
|---|---|---|---|
| `material_failed` | boolean | 任何单元达到材料失效准则 | ⚠️ |
| `max_stress_exceeds_yield` | boolean | 最大 von Mises 应力超屈服强度 | ✅ |
| `excessive_displacement` | boolean | 最大位移超容许 | ⚠️ |
| `buckling_detected` | boolean | (静态) 检测到屈曲（载荷-位移曲线斜率反转） | 🔬 |
| `contact_chattering` | boolean | (动态) 接触振荡 | 🔬 |
| `modal_collision_with_excitation` | boolean | (模态) 固有频率落在激励频带 | ✅ |
| `excessive_strain_energy_density` | boolean | 局部应变能密度超阈值 | ⚠️ |
| `negative_jacobian` | boolean | (大变形) 单元出现负雅可比 | ✅ |

### 7.3 详细判据

#### 7.3.1 `max_stress_exceeds_yield`

**一句话**：场中最大 von Mises 应力达到或超过材料屈服强度。

**依赖信号**：

| 信号 | extraction 来源 |
|---|---|
| `max_von_mises_stress` | extraction §14.5 |
| 材料 `yield_strength`（来自 setup） | extraction §3.3.C 材料定义 |

**判据**：

```yaml
max_stress_exceeds_yield:
  output_type: boolean
  signals_required:
    - max_von_mises_stress
    - material_yield_strength
  rule:
    type: threshold
    formula: "max_von_mises_stress > yield_factor * yield_strength"
  thresholds:
    default: {yield_factor: 1.0}
    by_condition:
      - if: {analysis_type: "static_design"}
        then: {yield_factor: 0.8}      # 工程安全系数
      - if: {analysis_type: "limit_state"}
        then: {yield_factor: 1.0}
  reference: "Beer et al. 2014 *Mechanics of Materials* Ch.7"
  confidence_when_matched: HIGH
  limitations:
    - "仅看 von Mises，对脆性材料应该用最大主应力准则"
    - "动态算例的 stress wave 局部峰值可能误判，应该看时间平均"
```

#### 7.3.2 `modal_collision_with_excitation`

**一句话**：固有频率落在已知激励频带内（共振风险）。

**依赖信号**：

| 信号 | 来源 |
|---|---|
| `mode_frequencies` (list, 模态分析输出) | extraction §14.5 |
| `excitation_frequency_range` (用户提供 or 工艺已知) | 外部输入 |

**判据**：

```yaml
modal_collision_with_excitation:
  output_type: boolean
  applicable_when:
    analysis_type: modal
  signals_required:
    - mode_frequencies (top N)
    - excitation_frequency_range (user-provided)
  rule:
    type: any_match
    formula: "any(f in excitation_range for f in mode_frequencies[:N])"
  thresholds:
    default: {N: 10, safety_band_percent: 10}
  reference: "Rao 2017 *Mechanical Vibrations* Ch.6"
  confidence_when_matched: HIGH
  limitations:
    - "需要用户告知激励频带 —— 没法自动从结果文件推断"
```

### 7.4 简略 intents

#### `material_failed`
- 信号：损伤变量 D > 1（如有 damage model）or 应变超失效应变
- 状态：⚠️ 取决于材料模型（弹塑性 vs 损伤 vs 失效）

#### `excessive_displacement`
- 信号：max(|U|) vs 几何特征长度
- 阈值：例如 max_disp / characteristic_length > 0.1

#### `buckling_detected`
- 信号：load-displacement 曲线斜率反转 / 第一阶屈曲特征值 < 1
- 状态：🔬 需要专门提取 load-disp 时序

#### `contact_chattering`
- 信号：接触力时序高频振荡 / 接触状态频繁切换
- 状态：🔬 需要接触力时序

#### `negative_jacobian`
- 信号：单元雅可比 min < 0 (大变形 case)
- 来自：求解器 fatal error log

#### `excessive_strain_energy_density`
- 信号：max(strain_energy / element_volume)
- 阈值：与材料 toughness 相关

### 7.5 跨求解器变量映射

参见 [extraction_reference.md §14.5](extraction_reference.md#145-结构)。

---

## 8. Multiphase（多相）

**当前状态**：🔬 仅占位

多相流问题（VOF / Mixture / Eulerian / DPM）的判据需要单独整理。先列出未来扩展的 intent 候选：

| Intent ID（候选） | 描述 |
|---|---|
| `interface_resolution_adequate` | VOF 界面分辨率（界面厚度 vs 网格） |
| `bubble_dispersion_uniform` | DPM 颗粒空间分布均匀性 |
| `phase_fraction_balanced` | 相分数总和守恒 |
| `dispersed_phase_volume_fraction_alarm` | 离散相体积分数超阈值（导致 Lagrangian 假设失效） |
| `cavitation_detected` | (cavitation cases) 出现 cavitation |
| `liquid_droplet_evaporation_complete` | (spray cases) 液滴完全蒸发 |
| `spray_penetration_depth` | 喷雾穿透距离 |

详细判据 Phase 3+ 实化。

---

## 9. 跨域考虑

### 9.1 多物理耦合的判据组合

实际工程算例往往跨多个物理域，需要**组合判据**：

| 组合场景 | 涉及域 | 复合 intent 例 |
|---|---|---|
| 喷雾燃烧 | combustion + multiphase + heat | `is_extinguished` ∧ `liquid_droplet_evaporation_complete` |
| 共轭传热 | fluid + heat + structural (热应力) | `thermal_equilibrium_reached` ∧ `max_stress_exceeds_yield` |
| 空气动力学 + 气动声学 | fluid + acoustic | `mach_regime` + `acoustic_overall_sound_pressure_level` |

复合 intent 的实现：

```yaml
spray_combustion_healthy:
  output_type: boolean
  composite_of:
    - is_extinguished: false
    - mass_imbalance_alarm: false
    - liquid_droplet_evaporation_complete: true
  applicable_when:
    has_combustion_model: true
    has_lagrangian: true
```

### 9.2 信号缺失时的降级

如果某个 intent 依赖的信号在算例中**不存在**（如 OH 在简化机理中不存在），判据应：

1. 检查 `applicable_when` 是否满足
2. 如不满足 → 输出 `PENDING + applicability_reason: "OH not in mechanism"`
3. 如部分满足（4 个判据中 3 个有信号）→ 用现有的 weighted voting，但置信度降级为 MED

不允许"信号缺失就静默通过"。

### 9.3 时间维度的判据

**稳态算例**：所有判据都看末值（last time step）。

**瞬态算例**：判据应明确选用以下之一：
- `last_value`（最后一帧）
- `last_N_steps_mean`（最后 N 步均值，去抖动）
- `time_window_mean`（指定时间窗均值）
- `last_period_mean`（如有周期，平均一个周期）

每个 intent YAML 应该指定 `time_aggregation_method`。

---

## 10. 阈值校准哲学

### 10.1 阈值绝不写死

**反模式**：

```python
# ❌ 不要这样
def is_extinguished(qdot_max):
    return qdot_max < 1e6
```

**正模式**：

```python
# ✅ 阈值从 YAML 读
def is_extinguished(qdot_max, criterion: dict):
    threshold = criterion["thresholds"]["default"]["threshold_qdot"]
    # ... 应用 by_condition 覆盖
    return qdot_max < threshold
```

### 10.2 阈值的三层来源

| 来源 | 优先级 | 例 |
|---|---|---|
| **case-level overrides** | 最高 | 用户在 case 目录里提供 `criteria_overrides.yaml` |
| **by_condition match** | 中 | 燃料/压力匹配条件下的阈值 |
| **default** | 最低 | 文献给的安全默认值 |

### 10.3 阈值校准流程

新增/调整阈值时的工作流：

```
1. 读相关文献，给一个理论默认
2. 在 labeled_cases 里跑一遍，看准确率
3. 如准确率 < 90%，扩 labeled_cases 或调整 by_condition 分组
4. 标记 thresholds.calibration_status: "validated_on_<n>_cases"
5. 提 PR 到 sim-knowledge/physics/<domain>/criteria.yaml
```

### 10.4 阈值的局限承认

每个判据 YAML 必须包含 `limitations:` 字段，说明何时**不适用**。LLM Agent 看到 limitations 应该：
- 先检查当前 case 是否触发某个 limitation
- 如触发 → 降级 confidence 或拒绝判断

---

## 11. labeled_cases 联动

### 11.1 labeled_cases 是判据的真值

每个 intent 必须配套**至少 5 个 labeled cases**（含 positive + negative 真值）：

```
sim-knowledge/labeled_cases/
└── combustion_extinction/
    ├── CH4_jet_normal/
    │   ├── case_data/                  # 算例文件夹（或软链）
    │   └── ground_truth.yaml           # {is_extinguished: false, fuel: CH4, ...}
    ├── CH4_jet_blowout/
    │   ├── case_data/
    │   └── ground_truth.yaml           # {is_extinguished: true, fuel: CH4, ...}
    ├── DLR_A_LTS_normal/
    ├── H2_O2_extinguished/
    └── kerosene_flame_normal/
```

### 11.2 ground_truth.yaml schema

```yaml
case_id: CH4_jet_normal
case_path: D:/path/to/case        # 绝对路径或软链
labels:
  is_extinguished: false
  is_ignited: true
  is_steady_state_converged: true
  mass_imbalance_alarm: false
context:
  fuel: CH4
  pressure_atm: 1.0
  combustion_model: EDC
  chemistry_mechanism: GRI-Mech 3.0
labeled_by: 张三
labeled_at: 2026-04-27
notes: "标准 DLR-A 火焰算例，已收敛、有火焰"
```

### 11.3 标注成本

每个 case 标注 5-15 分钟（看 ParaView 截图、对照 ground truth）。

每个 intent 5-10 cases × 5 个 intents × 10 分钟 = **每个物理域约 5-8 小时人工**。

这是**机制 #2 校准检验**的真值基础（design.md §11），**不可省**。

### 11.4 推荐的最小 labeled set

| 域 | 最小 labeled cases 数 | 含 |
|---|---|---|
| Combustion | 8 | 2× extinguished, 2× ignited, 2× incomplete combustion, 2× normal |
| Fluid Dynamics | 6 | 2× converged, 2× diverged, 2× recirculating |
| Heat Transfer | 5 | 1× thermal extreme, 4× normal |
| Numerics | 6 | 3× converged, 3× diverged |
| Structural | 5 | 2× yielded, 3× elastic |

---

## 12. 迁移到 sim-knowledge

### 12.1 迁移目标

本文档内容是 `sim-knowledge/physics/<domain>/` 的素材：

```
sim-knowledge/physics/
├── fluid_dynamics/
│   ├── criteria.yaml           ← 来自本文档 §3 的 YAML 块
│   ├── variables.yaml          ← 来自 extraction_reference §14.1
│   └── thresholds.yaml         ← 来自 §3 的 thresholds 段
├── heat_transfer/              ← 来自 §4
├── combustion/                 ← 来自 §5
├── numerics/                   ← 来自 §6
└── structural/                 ← 来自 §7
```

### 12.2 双载体内容

跟 extraction_reference.md 一样，本文档与 sim-knowledge 是**双载体**关系：
- markdown 叙事版（本文档）：人读 + 设计阶段参考
- YAML 结构化版（sim-knowledge）：机读 + 运行时检索

### 12.3 迁移时机

| 时机 | 动作 |
|---|---|
| **当前**（design 阶段） | 保留本文档作为单一参考，便于 review |
| **Phase 1 完成后** | 把核心 intent（is_extinguished / is_diverged / is_converged 等 ~10 个）的 YAML 块抽出，放进 sim-parse/core/canonical/qoi_rules/ 作为种子 |
| **Phase 2** | 建 sim-knowledge 仓库，从本文档拷全部 YAML 块；建立 labeled_cases；CI 检查双载体一致 |
| **Phase 3+** | 拓展：补 multiphase / acoustic / thermal-structural 跨域；补 LLM 用的 few_shot 示范 |

### 12.4 高优先级迁移项

按 ROI 排序（见 design.md §11）：

| 优先级 | 内容 | 目标 |
|---|---|---|
| ⭐⭐⭐⭐⭐ | §5.3.1 `is_extinguished` 完整 YAML | sim-knowledge/physics/combustion/criteria.yaml |
| ⭐⭐⭐⭐⭐ | §6.3.1 `is_diverged` + §6.3.2 `excessive_courant` | sim-knowledge/physics/numerics/criteria.yaml |
| ⭐⭐⭐⭐ | §3.3.1 `is_steady_state_converged` | sim-knowledge/physics/fluid_dynamics/criteria.yaml |
| ⭐⭐⭐⭐ | §3.3.2 `mach_regime` | 同上 |
| ⭐⭐⭐ | §4.3.1 `has_thermal_extreme` | sim-knowledge/physics/heat_transfer/criteria.yaml |
| ⭐⭐⭐ | §7.3.1 `max_stress_exceeds_yield` | sim-knowledge/physics/structural/criteria.yaml |

---

## 13. 变更历史

- **v0.1** — 初版，覆盖 5 个物理域（Fluid Dynamics / Heat Transfer / Combustion / Numerics / Structural）+ Multiphase 占位；详细 intent 共 11 个（含 YAML 完整定义），简略 intent 共 30+ 个；含跨域组合、阈值校准哲学、labeled_cases schema 与 sim-knowledge 迁移规划
