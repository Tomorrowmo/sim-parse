# 接入新求解器手册（Solver Onboarding Playbook）

> 目的：当你想给 sim-parse 加一种新的求解器格式（CGNS / Star-CCM+ / EnSight / Plot3D / Abaqus ...）时，按照本文档的清单一项一项做，确保新求解器和已有的 OpenFOAM / Fluent 在 5 个机制上**行为一致**、**可验证**、**带可勾选的 checklist 通过证据**。

> 当你拿到一个**不认识的算例目录**而 sim-parse 报 `format: null` 时，跳到 §6 "**Unknown case 处理**"。

## 0. 5 个机制速查（每个新求解器都必须考虑）

| # | 机制 | 必须实现 | 跨求解器统一规范 |
|---|---|---|---|
| 1 | 字段 kind 标签 | Tier 2 `variables_meta` + Tier 4 `variables_kind_verified` | 见 §2 |
| 2 | 静态规则去伪 | 由 qoi_engine 自动处理；新求解器**无需改代码**，只在 sim-knowledge 里写规则时正确标注 `temporal_requirement` | 见 §4 |
| 3 | mesh_zones 跨求解器 schema | Tier 3 出 `mesh_zones`（见 §3） | 必须 |
| 4 | 未收敛告警 | Tier 4 出 `convergence_status` + `convergence_summary`，前提是能拿到残差 | 见 §3.4 |
| 5 | 可验证回路（rule_file / inputs_resolved / labeled_cases） | 由 qoi_engine 自动处理；新求解器**无需改代码**，只在 sim-knowledge 里加 alias + 真值 case | 见 §5 |

> **关键设计原则**：机制 #2 和 #5 是 qoi_engine 通用层面的事，新求解器**不需要重新实现**。新求解器只要在 #1 #3 #4 上把数据塞进对应字段即可——其余自动生效。

## 1. 文件骨架（强制）

每个新求解器必须创建：

```
sim-parse/src/sim_parse/solvers/<solver_name>/
├── __init__.py            ← 必须 register_parser(...)
├── tier1_identify.py      ← 强制
├── tier2_inventory.py     ← 强制
├── tier3_metadata.py      ← 强制
├── tier4_field_stats.py   ← 视情况（无 reader 可只产 stub）
├── tier5_qoi.py           ← 可选（一般无需，qoi_engine 通用走通）
└── tier6_export.py        ← 可选（VTU 导出）
```

`__init__.py` 模板：

```python
from sim_parse.core.registry import register_solver
from . import tier1_identify, tier2_inventory, tier3_metadata, tier4_field_stats

register_solver(
    "<solver_name>",
    identify=tier1_identify.identify,
    inventory=tier2_inventory.inventory,
    metadata=tier3_metadata.metadata,
    field_stats=tier4_field_stats.field_stats,   # 可选
    # qoi=...        # 可选；不写就用 qoi_engine 的通用规则评估
    # priority=70,   # 可选；默认 50；高优先级 solver 在 cascade identify 中先试
)
```

> 说明：`register_solver` 是给内置 solver 用的（直接传函数）。`register_parser`
> 是给外部插件用的装饰器形式（包一个 class，class 实例方法对应 identify/inventory/...）。
> 我们这里讲的是接入新内置 solver，用 `register_solver`。

## 2. Schema 不变量（每个 Tier 必须返回什么）

### 2.1 Tier 1 `identify(case_root) -> dict | None`

返回 **None** 表示这不是我管的格式（cascade 会试下一个 solver）；返回 dict 表示命中。

**强制字段**：
| key | 类型 | 含义 |
|---|---|---|
| `format` | str | 与 `register_parser` 的 name 一致 |
| `_path` | "A" \| "B" | 一般 "A" |

**强烈建议字段**：
- `solver` - 具体子求解器（如 `reactingFoam`、`fluent legacy`、`StarCCM v17.04`）
- `version` - 解算器版本（从 log/config 抓）
- `sub_format` - 同格式下的子分类（Fluent: `legacy/legacy_gz/cff`；HDF5: `cgns/h5part/...`）

`_provenance` 字段必须给每个值标注来源（参考 `core/provenance.py::FieldProvenance`）。

### 2.2 Tier 2 `inventory(case_root, identity) -> dict`

**强制字段**：
| key | 类型 | 含义 |
|---|---|---|
| `variables` | list[str] | case 里所有可能的变量名（去重 union） |
| `variables_meta` | dict[name, {kind, evidence, ...}] | **机制 #1：每个变量的 kind 标签** |

`variables_meta[name].kind` 必须是以下之一：

| kind | 语义 |
|---|---|
| `cell_field` | 默认 — 真实的 per-cell 物理量；Tier 4 会进一步细分 scalar/vector/tensor |
| `face_flux` | 面 flux（OpenFOAM `phi`、Fluent face-side 量） |
| `diagnostic` | 求解器内部诊断量（CourantNumber、cellCpuTimes、TabulationResults 等），非物理 |
| `template` | 字典模板，不是 cell-data（OpenFOAM 的 `Ydefault`） |
| `ic_only` | 只在初始时步出现，从未被推进过的占位字段 |

每个 entry 还要带 `evidence`（一句话说为什么打了这个标签——便于用户验证）。

**怎么判断 kind**：
- 文件存在模式：只在 `0/`、`0.orig/` 出现 → `ic_only`
- 名字白名单：例如 `phi.*` → `face_flux`；`TabulationResults / cellCpuTimes / rDeltaT` → `diagnostic`
- 内容嗅探（可选）：文件第一行 `dimensions [...]` + `internalField` → 真 cell field；只有 `boundaryField` 模板 → `template`
- 默认：`cell_field`，留给 Tier 4 再交叉验证

### 2.3 Tier 3 `metadata(case_root, identity, inventory) -> dict`

**强制字段**：
| key | 类型 | 含义 |
|---|---|---|
| `mesh_cells` | int | volume 单元总数（**不要把 face zones 也算进去**！) |
| `mesh_points` | int | 唯一节点数（不是 sum-over-blocks） |
| `mesh_zones` | list[dict] | **机制 #3：跨求解器 zone schema** |

`mesh_zones` 的每条目 schema：

```python
{
    "name": str,              # zone 唯一标识
    "role": "volume" | "boundary" | "interface" | "unknown",
    "n_cells": int | None,    # volume zone 才有
    "n_faces": int | None,    # boundary/interface zone 才有
    "n_points": int | None,   # 此 zone 独有的节点数；vtkFLUENTReader 这类共享 point 的，标 geometry_aliased=True
    "bounding_box": [xmin,xmax,ymin,ymax,zmin,zmax] | None,
    "element_types": {"vtkWedge": 94, "vtkHexahedron": 3372} | None,
    "patch_type": str | None, # solver-specific（wall/inlet/symmetry/pressure-far-field/...）
    "geometry_aliased": bool, # 可选；True 表示 bbox/n_points 是全局而非该 zone 独有
    "_source": str,           # 来源描述
}
```

**强烈建议字段**：

| key | 含义 |
|---|---|
| `time_control` | 起止时间 / 时步 / 写出频率（controlDict 等价物） |
| `turbulence` / `combustion` / `chemistry` / `thermophysics` | 物理子模型配置 |
| `decomposition` | 并行分解 |
| `boundaries` | 详细边界（与 mesh_zones 不重复——前者是物理 BC，后者是几何 zone） |

### 2.4 Tier 4 `field_stats(case_root, identity, metadata, *, time=None, fields=None, inventory=None) -> dict`

**注意签名**：必须接受 `inventory` 关键字参数（机制 #1 通过它把 variables_meta 传过来交叉验证）。

**强制字段**（如果有 reader 的话）：
| key | 类型 | 含义 |
|---|---|---|
| `variable_ranges` | dict[var, stats] | per-variable min/max/mean；vector 还要给 magnitude_*  / component_*  |
| `has_nan_field` | bool | 任一变量有 NaN/Inf 即 True |
| `nan_fields` | list[str] | 触发的变量名 |

**机制 #1 后半部分**：

| key | 含义 |
|---|---|
| `variables_kind_verified` | dict[name, {physical_kind, data_shape, phantom, tier2_kind, evidence}] |

设计要点：
- `physical_kind` 来自 Tier 2 的意图标签（physical / diagnostic / template / face_flux）
- `data_shape` 来自 reader 的实际形状（cell_scalar / cell_vector / cell_tensor / cell_unknown / not_in_vtk）
- `phantom = (Tier 2 说是 physical) AND (reader 没暴露)` —— 这是给用户的"伪字段"报警

**机制 #4（如果能拿到残差）**：

| key | 含义 |
|---|---|
| `convergence_orders` | dict[var, float]（log10(initial / final)） |
| `convergence_status` | dict[var, {value, status, threshold_used}]，status ∈ {converged, marginal, diverged} |
| `convergence_summary` | {n_converged, n_marginal, n_diverged, *_vars: list[str]} |

阈值：默认 diverged < 0、marginal ∈ [0,3)、converged ≥ 3，可由 sim-knowledge 覆盖。

任何 `diverged_vars` 不为空时，必须 `add_warning(out, "...")` —— 这个 warning 会被 cascade 自动 promote 到顶层 `parse_result.warnings`。

### 2.5 Tier 5/6/7

通常不用自己写——qoi_engine 通用层会基于 Tier 3+4 的输出按 sim-knowledge 规则跑。

如果新求解器有 solver-specific QOI 逻辑（如 Fluent 的 `report_qoi`），可以在 `tier5_qoi.py` 里实现，registry 会优先调它，**还会再合并** qoi_engine 通用规则。

## 3. 三步上手流程（推荐顺序）

### Step 1: Identify（30 分钟）

写 `tier1_identify.py`：扫描典型文件签名（`*.cas` for Fluent，`system/controlDict` for OpenFOAM，`CGNSBase_*` HDF5 group for CGNS，`*.sim` for Star-CCM ...），返回 `{format, sub_format, _path: "A"}`。

测试：
```python
def test_identifies(my_case_fixture):
    r = identify(my_case_fixture)
    assert r["format"] == "<solver_name>"

def test_returns_none_on_empty_dir(tmp_path):
    assert identify(tmp_path) is None
```

### Step 2: Inventory + Metadata 文本层（1-2 小时）

不用 reader，纯文本读 case 配置文件出 Tier 2/3。**这一步先把 mesh_zones 骨架填出来**（n_cells / n_faces 从 case 文件读，几何字段先填 None）。

测试：
```python
def test_mesh_zones_count(my_case_fixture):
    r = metadata(my_case_fixture, identity={"format":"x"}, inventory={})
    zones = r["mesh_zones"]
    volumes = [z for z in zones if z["role"] == "volume"]
    assert len(volumes) == 1
    assert volumes[0]["n_cells"] == EXPECTED_CELL_COUNT
```

### Step 3: Field Stats（视情况）

如果有 VTK reader 可用：调 reader → 列 cell array → 算 stats → 顺便 enrich mesh_zones 几何字段。如果没有 reader（如 .sim 私有格式），field_stats 返回 `init_tier_output("A")` + warning"reader unavailable for this format" 即可——cascade 不会崩。

## 4. sim-knowledge 集成

### 4.1 变量别名（让物理判据跨求解器生效）

新求解器的 native 变量名往往和 canonical 名不同（OpenFOAM `T` vs Fluent `TEMPERATURE` vs Star-CCM `Temperature`）。在 `sim-knowledge/physics/<domain>/variables.yaml` 加 alias：

```yaml
variables:
  temperature:
    canonical_name: T
    aliases:
      openfoam: [T]
      fluent: [TEMPERATURE]
      starccm: [Temperature, "Static Temperature"]
      cgns:    [Temperature]
```

加完后 sim-knowledge 里所有用 `tier_4.variable_ranges.T.max` 的规则在新求解器上自动找到对应字段。

### 4.2 物理规则（机制 #2 友好）

如果你写了一条规则需要时序数据（多时步比较，如 `Q_last / Q_peak`），**必须**标注：

```yaml
- name: my_rule
  type: expression
  temporal_requirement: time_series   # ← 这一行
  expression: "Q_last / Q_peak"
  inputs:
    Q_last: { path: "tier_4.time_series.Q.last_value" }
    Q_peak: { path: "tier_4.time_series.Q.peak_value" }
```

否则 qoi_engine 在静态快照模式下会调不出来值，但你的规则又会"假装"出一个常量值，机制 #2 就保护不到了。

### 4.3 Labeled case（机制 #5 接入）

为新求解器至少加 1 个 labeled case（在 `sim-knowledge/labeled_cases/<domain>/<case_id>/ground_truth.yaml`）。schema 见 `sim-knowledge/labeled_cases/_schema.yaml`，关键字段：

```yaml
case_id: my_case_normal
case_path: D:/path/to/case   # 必须是绝对路径
labels:
  is_extinguished: false
  max_temperature: 1500
  ...
context:
  fuel: CH4
  solver: <solver_name>
  ...
tolerances:                  # 可选；覆盖默认 5% 相对容差
  max_temperature: 0.10      # 改成 10%
```

这样 `simparse_validate` 自动跑出 match/mismatch 报告。

## 5. 测试脚手架（必须有）

每个新求解器**至少**配 2 类测试：

### 5.1 单元测试 (`tests/unit/test_<solver>_*.py`)

不依赖具体 case 数据。测纯函数：identify 边界（空目录 / 假文件 / 多种 sub_format）、kind 分类逻辑、mesh_zones 构建器、convergence_orders 阈值。

### 5.2 集成测试 (`tests/integration/test_<solver>_*.py`)

依赖一个具体 case fixture（在 `conftest.py` 加一个 `@pytest.fixture` 指到 `D:\Git\SimGraph2\test_data\...` 之类，**case 不存在时 `pytest.skip`** 而不是失败）。验证：
- T1 format/solver 正确
- T2 variables_meta kind 分类对（看 docstring 里给的"应该是 X 而被标了 Y"边界）
- T3 mesh_cells 与已知真值匹配
- T3 mesh_zones 计数与 role 分布与已知真值匹配
- T4 convergence_summary（如适用）有合理 n_diverged
- T5 经过 sim-knowledge 的 labeled_case 校验，n_match ≥ N（容许 0 mismatch）

## 6. Unknown case 处理（不认识的解算器目录）

当 sim-parse 跑出 `format: null`，意味着所有已注册 solver 的 `identify()` 都返回 None，且 Path B magic 也没命中（顶层不是单个 HDF5/XML/etc）。这时 4 种选项：

### 6.1 钻进子目录再试（用户最容易做）

很多公司的算例归档习惯把 case 放在二层目录里。如果你拿到 `D:\some\dir`，先看 `ls`：

- 子目录里有 `*.cas / *.cas.gz / *.cas.h5` → 钻一层重试
- 子目录里有 `system/ + constant/ + 0/` → 那个子目录才是 OpenFOAM case
- 顶层全是 log / 输出 / 中间产物 → 这不是个完整 case 目录

### 6.2 强制走某个 solver 试错

```bash
simparse <dir> --force-solver fluent
simparse <dir> --force-solver openfoam
```

或者 Python：
```python
parse_case(dir, force_solver="fluent")
```

让 cascade 跳过 identify 直接进 Tier 2/3。如果 case 真的是该格式但只是文件名不规整，可能能跑通。

### 6.3 Path B 钻取单文件

把单个 case 文件而不是目录传进去：
```bash
simparse path/to/file.cgns
simparse path/to/file.sim
```

magic 探测会按 ext + 头字节认出 hdf5 / cgns / starccm_sim / ensight_gold 等。Tier 1 能给 format，Tier 2-4 大多没有原生 reader 时会留空。

### 6.4 它真的就是个新格式 → 走本文档接入它

按 §1-5 加新求解器。最小 MVP（只到 Tier 1）就 30-50 行：写一个 identify()，注册它，跑一个测试确认能识别。后续再渐进补 Tier 2/3/4。

### Discovery Agent（Path C，未来）

当前 sim-parse 留了 Path C discovery hook 但还没实现（参考 `cascade.py` 中 `discovery=auto/on/off`）。设计意图：把 unknown case 的目录树喂给 LLM，让它结合 `sim-knowledge/formats/` 里的 quirks/playbooks 自动猜 + 自动写 identify rules，**人工审核后**沉淀进新求解器。这会是机制 #1-#5 之外的第六个机制，预期 sim-knowledge v0.3+ 推出。

## 7. PR Checklist（提交新求解器 PR 前过一遍）

- [ ] `solvers/<name>/__init__.py` 调用 `register_parser`
- [ ] `tier1_identify.py` + 测试（含 None 边界）
- [ ] `tier2_inventory.py`：emit `variables` + `variables_meta`，每个变量带 `kind` 和 `evidence`
- [ ] `tier3_metadata.py`：emit `mesh_cells`、`mesh_points`、`mesh_zones`（schema 见 §2.3）
- [ ] `tier4_field_stats.py`（如有 reader）：emit `variable_ranges`、`has_nan_field`、`variables_kind_verified`、（有残差则）`convergence_status` + `convergence_summary` + warning
- [ ] sim-knowledge `physics/<domain>/variables.yaml` 加新求解器的 alias
- [ ] sim-knowledge `labeled_cases/<domain>/<case_id>/ground_truth.yaml` 至少 1 个真值
- [ ] `tests/unit/test_<solver>_*.py` ≥ 5 个测试
- [ ] `tests/integration/test_<solver>_*.py` ≥ 1 个 happy-path（带 `pytest.skip` 当数据不在）
- [ ] 在 `tests/integration/test_labeled_cases.py`（如存在）加新 case_id 到表里
- [ ] `pytest tests` 全绿
- [ ] 文档：`docs/extraction_reference.md` 加一节描述新求解器格式、quirks、能 / 不能拿到的字段
- [ ] 跑 [simparse-mcp](../../simparse-mcp/) 通过 `simparse_summary` 验证 Cherry Studio 端可用

## 附：文件位置速查

| 你想看 | 在哪里 |
|---|---|
| 已注册 solver 例子 | `sim-parse/src/sim_parse/solvers/openfoam/` & `.../fluent/` |
| Tier 输出 schema 约定 | `sim-parse/src/sim_parse/core/provenance.py` |
| qoi_engine（机制 #2 / #5） | `sim-parse/src/sim_parse/core/canonical/qoi_engine.py` |
| 验证逻辑（机制 #5） | `sim-parse/src/sim_parse/core/canonical/validation.py` |
| 跨格式 quirks 知识库 | `sim-knowledge/formats/` |
| 物理判据 | `sim-knowledge/physics/<domain>/criteria.yaml` |
| 真值 case | `sim-knowledge/labeled_cases/<domain>/<case_id>/ground_truth.yaml` |
| 已有测试模式参考 | `sim-parse/tests/integration/test_dlr_a_cavity.py` |
