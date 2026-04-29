# 求解器结果解析参考（How to Read）

> **状态**：参考文档 v0.1
> **配套文档**：[design.md](design.md) —— 高层架构、Tier/Path 定义在 design.md，本文档**只展开"读"动作的细节**
> **对应三轴**：design.md §2 中**"读"** 这一动作的实施依据
> **目的**：为 sim-parse 各 solver 的 Path A 实现提供**确定性提取需求规格**，并为未来 sim-knowledge/formats/ 提供素材

---

## 目录

1. [文档目的与边界](#1-文档目的与边界)
2. [通用约定](#2-通用约定)
3. [OpenFOAM](#3-openfoam)
4. [Fluent](#4-fluent)
5. [CGNS](#5-cgns)
6. [HDF5 通用](#6-hdf5-通用)
7. [LS-DYNA](#7-ls-dyna)
8. [STAR-CCM+](#8-star-ccm)
9. [Abaqus](#9-abaqus)
10. [MAPDL / ANSYS Mechanical](#10-mapdl--ansys-mechanical)
11. [Tecplot](#11-tecplot)
12. [EnSight Gold](#12-ensight-gold)
13. [Plot3D](#13-plot3d)
14. [跨求解器 QOI 统一映射表](#14-跨求解器-qoi-统一映射表)
15. [阶段交付路线图](#15-阶段交付路线图)
16. [迁移候选 → sim-knowledge](#16-迁移候选--sim-knowledge)
17. [变更历史](#17-变更历史)

---

## 1. 文档目的与边界

### 1.1 这份文档回答什么

每种求解器的**结果文件**里能提取出哪些信息、来源在哪个文件/字段、对应哪个 Tier、用什么工具能拿到。

### 1.2 这份文档**不**回答什么

- ❌ 怎么**操作**求解器（看 sim-skills）
- ❌ 怎么**判断**物理状态（看 sim-knowledge/physics）
- ❌ LLM Discovery（Path C）的策略（看 sim-knowledge/formats）
- ❌ Tier / Path / 三轴架构 / 错误契约等高层定义（看 design.md）

### 1.3 受众

- sim-parse 的 solver-specific Path A 实现者（写 `solvers/<name>/tier*.py` 的人）
- sim-knowledge/formats 内容贡献者
- 想了解"sim-parse 究竟从我的算例里抓什么"的工程师

---

## 2. 通用约定

### 2.1 信息族编码（与 design.md §8 一致）

| 编码 | 名称 | 含义 |
|---|---|---|
| **A** | Mesh | 网格量、节点、面、边界、分解 |
| **B** | Fields | 物理场（变量名、类型、量纲、BC、统计） |
| **C** | Setup | 求解器/物理设置（控制、模型、本构、化学） |
| **D** | Lagrangian | 拉格朗日相 / 离散相 |
| **E** | Run state | 运行状态（时间步、收敛、残差） |
| **F** | Post-processing | 后处理输出（forces、probes、monitors） |
| **G** | Auxiliary | 周边文件（log、scripts、README、外部资源） |
| **H** | QOI | 派生量化指标（Tier 5，跨求解器统一） |
| **I** | Semantic | 语义解释（Tier 7，LLM） |

### 2.2 Tier 编码（与 design.md §7 一致）

| Tier | 名称 | 判定规则 | 重工具？ |
|---|---|---|---|
| 1 | Identify | 仅看签名/魔数/目录结构 | ❌ |
| 2 | Inventory | 仅扫描清单 | ❌ |
| 3 | Metadata | 读文本字典/header | ❌ |
| 4 | FieldStats | 解码数组算统计 | ✅ |
| 5 | QOI | 查映射表归一化 | 部分 |
| 6 | FullData | 输出完整 mesh+场 | ✅ |
| 7 | Semantic | LLM 推断 | ❌（LLM） |

### 2.3 状态标记

| 标记 | 含义 |
|---|---|
| ✅ | Path A 静态 parser 完整覆盖 |
| ⚠️ | 部分覆盖 / 需要约定 / 有限制 |
| ❌ | 当前阶段不实现 |
| 🔧 | 需要外部工具预转 |

### 2.4 工具体系

本文档涉及的核心工具：

| 工具 | 类型 | 用途 |
|---|---|---|
| `vtkmodules.*` | Python (官方 VTK) | 主力 reader |
| `h5py` | Python | HDF5 通用 |
| `numpy` | Python | 数组统计 |
| `pyyaml` | Python | 字典解析（OpenFOAM-style） |
| `re` (regex) | stdlib | log / 头部解析 |
| `subprocess` | stdlib | 调外部工具（odb2vtk / abaqus python） |

求解器特定工具（**Phase 4+**才考虑集成）：

| 工具 | 求解器 | 备注 |
|---|---|---|
| `odbAccess` | Abaqus | 必须在 Abaqus 环境运行 |
| `odb2vtk` | Abaqus | 社区，依赖 Abaqus |
| `ansys-dpf-core` (PyDPF) | MAPDL/Mechanical/Fluent | 官方，全平台 |

---

## 3. OpenFOAM

### 3.1 格式特征

- **目录式结构**（不是单文件）
- 识别签名：`<case>/system/controlDict` AND `<case>/constant/` 同时存在
- 内部混合：text dictionaries（system/、constant/、time directories headers）+ binary fields（time directories internal data）+ log files（顶层 `log.*`）
- 版本范围：v2206 - v2406（ESI fork），Foundation fork 也兼容
- 多 region 支持：`constant/<region>/polyMesh/`
- 并行支持：`processor*/` 子目录

### 3.2 工具

| Tier | 工具 |
|---|---|
| T1-T3 | 自写 `_foam_dict.py` text parser + `_polymesh.py` header reader |
| T4 | `vtkmodules.vtkIOGeometry.vtkOpenFOAMReader` |
| T6 | 同上 → `vtkXMLUnstructuredGridWriter` |

VTK 调用要点：
```python
# 需要 .foam marker 文件作为入口（如不存在自动创建）
foam_marker = case_root / f"{case_root.name}.foam"
foam_marker.touch(exist_ok=True)
reader = vtk.vtkOpenFOAMReader()
reader.SetFileName(str(foam_marker))
reader.UpdateInformation()         # 不读数据，仅元信息
times = reader.GetTimeValues()
reader.SetTimeValue(t)             # 选时间步
reader.SetCellArrayStatus(name, 1) # 启用变量
reader.Update()                    # 此时才真正读
```

### 3.3 信息族

#### 族 A — 网格 (Mesh)

| 字段 | Tier | 来源 | 状态 |
|---|---|---|---|
| `nCells` | 3 | `constant/polyMesh/owner` 头部 `note "nCells: NNN nFaces: NNN ..."` | ✅ |
| `nPoints` | 3 | `constant/polyMesh/points` header / 第一行 | ✅ |
| `nFaces` | 3 | `constant/polyMesh/faces` 头部 | ✅ |
| `nInternalFaces` | 3 | `constant/polyMesh/owner` 头部 | ✅ |
| `bounding_box` | 4 | VTK 读 `points` 算 min/max | ✅ |
| `mesh_type`（hex/tet/poly/混合） | 4 | VTK CellTypes 直方图 | ⚠️（启发式） |
| `boundary_patches`（list of {name, type, nFaces, startFace}） | 3 | `constant/polyMesh/boundary` 文本字典 | ✅ |
| `cellZones`, `faceZones`, `pointSets` | 2-3 | `constant/polyMesh/{cellZones,faceZones,sets/}*` | ✅ |
| `multi_region` | 1 | `constant/<region>/polyMesh/` 子目录 | ✅ |
| `decomposition` | 3 | `system/decomposeParDict` + `processor*` 数 | ✅ |

#### 族 B — 物理场 (Fields)

| 字段 | Tier | 来源 | 状态 |
|---|---|---|---|
| `variables` | 2 | 时间目录里的文件名（去 .gz） | ✅ |
| `variable_class` (volScalarField/volVectorField/...) | 3 | 场文件 `class` header | ✅ |
| `variable_dimensions` ([kg m s K mol A cd]) | 3 | 场文件 `dimensions` 行 | ✅ |
| `boundary_conditions` (per patch per field) | 3 | 场文件 `boundaryField{...}` 块 | ✅ |
| `internal_field_kind` (uniform/nonuniform) | 3 | 场文件 `internalField` 行 | ✅ |
| `variable_ranges` (per-field min/max/mean) | 4 | VTK 读 internalField → numpy | ✅ |
| `variable_histograms` | 4 | numpy histogram | ⚠️（按需） |
| `time_evolution_stats` | 4 | 遍历所有时间步 | ⚠️（按需） |
| `full_field_arrays` (point/cell-centered) | 6 | VTK → VTU | ✅ |

#### 族 C — 求解器/物理设置 (Setup)

| 字段 | Tier | 来源 | 状态 |
|---|---|---|---|
| `application` | 1-3 | `system/controlDict::application` | ✅ |
| `time_control` (startTime/endTime/deltaT/writeInterval/adjustTimeStep/writeFormat) | 3 | `system/controlDict` | ✅ |
| `discretization_schemes` (div/grad/laplacian) | 3 | `system/fvSchemes` | ✅ |
| `linear_solvers` | 3 | `system/fvSolution` | ✅ |
| `turbulence` (RAS/LES + model + coeffs) | 3 | `constant/turbulenceProperties` + `RASProperties`/`LESProperties` | ✅ |
| `thermophysics` (type/mixture/transport/thermo/EOS) | 3 | `constant/thermophysicalProperties` | ✅ |
| `chemistry` (enabled / mechanism / solver / reduction / tabulation) | 3 | `constant/chemistryProperties` | ✅ |
| `combustion_model` (PaSR/EDC/laminar) | 3 | `constant/combustionProperties` | ✅ |
| `spray_settings` | 3 | `constant/sprayCloudProperties` | ✅ |
| `gravity` | 3 | `constant/g` | ✅ |
| `MRF`, `fvOptions` | 3 | `constant/MRFProperties`, `system/fvOptions` | ✅ |

#### 族 D — 拉格朗日 / 离散相 (Lagrangian)

| 字段 | Tier | 来源 | 状态 |
|---|---|---|---|
| `has_lagrangian` | 1 | `<time>/lagrangian/` 是否存在 | ✅ |
| `cloud_names` | 2 | `<time>/lagrangian/<cloud>/` 子目录 | ✅ |
| `cloud_variables` | 2 | cloud 子目录文件名 | ✅ |
| `particle_count` | 4 | VTK 读 `nParticle` 或 `positions` 长度 | ✅ |
| `particle_distributions` (直径/温度/速度直方图) | 4 | VTK + numpy | ⚠️（按需） |

#### 族 E — 运行状态 (Run state)

| 字段 | Tier | 来源 | 状态 |
|---|---|---|---|
| `time_steps_written` | 2 | 顶层目录浮点目录扫描 | ✅ |
| `latest_time` | 2 | 排序取最大 | ✅ |
| `n_time_steps` | 2 | 计数 | ✅ |
| `is_completed` | 3 | latest_time vs `controlDict::endTime` | ✅ |
| `residual_history` (per variable) | 4 | log 文件正则 `Solving for ...` | ✅ |
| `convergence_orders` | 4 | 残差初值/末值 log10 | ✅ |
| `convergence_status` (converged/not_converged/unknown) | 4 | 残差末值 vs 收敛阈值 | ⚠️（启发式） |

#### 族 F — 后处理 (Post-processing)

| 字段 | Tier | 来源 | 状态 |
|---|---|---|---|
| `function_objects` | 2-3 | `system/controlDict::functions` 或独立 dict | ✅ |
| `forces_history` (CL/CD/CM 时序) | 4 | `postProcessing/forces*/<time>/forceCoeffs.dat` | ✅ |
| `probes_data` | 4 | `postProcessing/probes/<time>/<var>` | ✅ |
| `sampling_planes` | 2 | `system/cuttingPlane`、`postProcessing/cuttingPlane/` | ✅ |
| `monitors` (mass flow / 平均量) | 4 | `postProcessing/<name>/<time>/*.dat` | ✅ |
| `residual_logs` (functionObject 写出) | 4 | `postProcessing/residuals/<time>/residuals.dat` | ✅ |

#### 族 G — 周边文件 (Auxiliary)

| 字段 | Tier | 来源 | 状态 |
|---|---|---|---|
| `log_files` | 2 | 顶层 `log.*` + `processor*/log.*` | ✅ |
| `scripts` (Allrun/Allclean/Allmesh) | 2 | 顶层脚本 | ✅ |
| `readme_text` | 2 | `README*`, `*.txt` | ✅ |
| `openfoam_version` | 1-3 | 字典 header `version` 字段 / log banner | ✅ |
| `case_origin_path` (log banner `Case :` 行) | 3 | log 头部 banner | ✅ |
| `external_resources` | 2 | `chemkin/`, `constant/triSurface/` | ✅ |

### 3.4 已知 quirks（实战陷阱）

1. **求解器主 log 不在顶层** —— 并行案例的 `runParallel` 把 log 放在 `processor*/log.<solver>`，主 case 跑完 `reconstructPar` 后**主 log 不会被合并出来**。Tier 2 inventory 必须扫两处
2. **`endTime=5000, deltaT=1` 不一定是物理时间** —— LTS（局部时间步）求解器把 endTime 当迭代步数，路径名常含 `LTS` 字样。Tier 7 LLM 可推断；纯静态会误读为"5000 秒"
3. **0/T 是 nonuniform** —— 表明 `setFieldsDict` 干预了初值（数值点火等）。这是 case-specific 强信号，应作为元数据维度
4. **chemistryProperties 含 `importantSpecies`** —— 该列表跟 `constant/reactions` 里的实际物种数差很多（前者是关键物种、后者是完整机理）
5. **`format binary` 配 `arch "LSB;label=32;scalar=64"`** —— 跨平台读时要注意字节序和精度
6. **`.gz` 压缩** —— 老版 OpenFOAM 默认压缩，新版默认不压；VTK reader 自动处理，自写 parser 需要 gzip 模块
7. **`runTimeModifiable true`** —— 表示 controlDict 在跑过程中可被改动；Tier 3 提取的值是"最后一次写入的值"，不一定是初始值
8. **多 region 的 polyMesh 在 region 子目录里** —— `constant/<region>/polyMesh/`，根 `constant/polyMesh/` 可能不存在
9. **`v2212` vs `2212`** —— 字典 header `version` 有时带 `v` 有时不带，正则要兼容
10. **`controlDict::application` 可能是符号链接** —— 罕见但存在，要 resolve
11. **vtkOpenFOAMReader 输出 stderr 警告 `boundaryField <patch> not found`** —— 反应流案例的某些组分场（例如 OH/Qdot/EDC_kappa）在某些 patch 上可能没有 boundaryField 块，VTK 给出的是 WARN 级别提示。**对 internalField 解码不影响**，但会污染日志。生产中可用 `vtkOutputWindow.SetGlobalWarningDisplay(0)` 关掉
12. **boundary 文件 patch 数 vs blockMeshDict patch 数** —— blockMesh 可能添加 `defaultFaces` 等内部 patch，导致 polyMesh/boundary 比 blockMeshDict::boundary 多 1-2 项（实测 DLR_A_cavity：blockMeshDict 9 个，polyMesh/boundary 10 个）
13. **`endTime` 在 controlDict 里通常是 int** —— 即 `endTime 5000;` 而非 `5000.0;`。下游比较时要兼容 int/float
14. **`is_completed` 判据对 LTS 求解器有歧义** —— LTS pseudo-time 算例 `latest_time >= endTime` 同时为真，但物理意义是"迭代收敛"不是"物理时间到点"。该字段的语义因 case 而异，需 Tier 7 LLM 区分

### 3.5 Tier 覆盖总览

| Tier | 状态 | 备注 |
|---|---|---|
| T1 Identify | ✅ Phase 1 | 目录结构 + version banner |
| T2 Inventory | ✅ Phase 1 | 时间步 / 变量 / log / scripts |
| T3 Metadata | ✅ Phase 1 | 所有字典 + polyMesh header |
| T4 FieldStats | ✅ Phase 1 | VTK reader |
| T5 QOI | ⚠️ Phase 1 接口 / Phase 2 实化 | 物理映射表 |
| T6 FullData | ⚠️ Phase 2 | VTK → VTU |
| T7 Semantic | ❌ Phase 3+ | LLM |

---

## 4. Fluent

### 4.1 格式特征

两种主要格式：

#### Legacy（V18 及更早默认）
- **`<case>.cas`** —— mesh + boundary + materials + 求解器设置（二进制或 ASCII）
- **`<case>.dat`** —— 流场结果（二进制）
- 两个文件**必须配对**

#### CFF / HDF5（V19+ 默认）
- **`<case>.cas.h5`** + **`<case>.dat.h5`** —— HDF5 后端
- 也可单文件 `<case>.h5` 含全部
- 识别：HDF5 magic `89 48 44 46 ...`

### 4.2 工具

| Tier | 工具 |
|---|---|
| T1 | 后缀 + magic |
| T2-T3 | `vtkmodules.vtkIOGeometry.vtkFLUENTReader`（legacy）；`vtkmodules.vtkIOFLUENTCFF.vtkFLUENTCFFReader`（CFF） |
| T4 | 同上（reader 提供 cell data 数组） |
| T6 | 同上 → VTU |

VTK 调用要点：
```python
# Legacy
from vtkmodules.vtkIOGeometry import vtkFLUENTReader
reader = vtkFLUENTReader()
reader.SetFileName("case.cas")     # 自动找同名 .dat
reader.Update()
mb = reader.GetOutput()            # vtkMultiBlockDataSet (per cell zone)

# CFF / HDF5
from vtkmodules.vtkIOFLUENTCFF import vtkFLUENTCFFReader
reader = vtkFLUENTCFFReader()
reader.SetFileName("case.cas.h5")
```

### 4.3 信息族

#### 族 A — 网格

| 字段 | Tier | 来源 |
|---|---|---|
| `nCells` | 3 | `.cas` section 12（cells） |
| `nFaces` | 3 | `.cas` section 13（faces） |
| `nNodes` | 3 | `.cas` section 10（nodes） |
| `cell_zones` (id, name, type=fluid/solid, n_cells) | 3 | `.cas` section 39（cell zone conditions） + 12 |
| `face_zones` (boundary patches: id, name, type, n_faces) | 3 | `.cas` section 13 + 39 |
| `bounding_box` | 4 | VTK |
| `mesh_type` | 4 | cell type IDs |

#### 族 B — 物理场

| 字段 | Tier | 来源 |
|---|---|---|
| `variables` | 2 | `.dat` 提供的 cell data 数组名 |
| `variable_storage` (cell-centered) | 3 | Fluent 默认 cell-centered |
| `variable_units` | 3 | 通过 Fluent 单位系统映射 |
| `variable_ranges` | 4 | VTK + numpy |
| `full_field_arrays` | 6 | VTK → VTU |

#### 族 C — 求解器/物理设置

| 字段 | Tier | 来源 |
|---|---|---|
| `solver_type` (pressure-based / density-based) | 3 | `.cas` solver settings |
| `time_formulation` (steady / transient) | 3 | 同上 |
| `turbulence_model` | 3 | 同上 |
| `multiphase_model` (VOF / Mixture / Eulerian) | 3 | 同上 |
| `species_model` | 3 | 同上 |
| `boundary_condition_settings` (per face_zone) | 3 | `.cas` section 39 |
| `materials` | 3 | `.cas` material library |
| `udf_libraries` | 3 | `.cas` UDF section（如有） |

#### 族 E — 运行状态

| 字段 | Tier | 来源 |
|---|---|---|
| `iteration_count` (steady) | 3 | `.dat` flow time / iteration |
| `flow_time` (transient) | 3 | 同上 |
| `residual_history` | 4 | `.trn` transcript file（如有） |
| `convergence_status` | 4 | `.trn` 末尾 |

#### 族 F — 后处理

| 字段 | Tier | 来源 |
|---|---|---|
| `report_files` | 2 | 顶层 `*.out`、`report-*.out` |
| `report_definitions` | 4 | `.out` 文件 CSV 解析 |
| `force_reports`, `moment_reports`, `mass_flow_reports` | 4 | 各自 `.out` 文件 |

#### 族 G — 周边文件

| 字段 | Tier | 来源 |
|---|---|---|
| `journal_files` (.jou) | 2 | TUI 命令历史 |
| `transcript_files` (.trn) | 2 | 求解器输出 transcript |
| `fluent_version` | 3 | `.cas` header / .trn banner |
| `case_settings_export` | 2 | 用户可能导出 `.scm` Scheme 设置 |

### 4.4 已知 quirks

1. **`.cas` 和 `.dat` 必须配对** —— 缺一不可，命名不匹配会报错
2. **CFF (.cas.h5) 是 V19+ 才有** —— 老版本写出的文件 vtkFLUENTCFFReader 读不了
3. **多 cell zone 是常态** —— Fluent 用 cell zones 区分流体/固体/旋转域；输出是 vtkMultiBlockDataSet 不是单 grid
4. **face zone 的 BC 类型 ID** —— 数字 ID 映射到 Fluent 内部枚举，需要查表（见 [Fluent UDF Manual](https://) appendix）
5. **UDF 编译产物** —— `libudf/` 子目录含 .so/.dll；`.cas` 引用了 UDF 但运行时若 lib 缺失会失败（不影响 sim-parse 解析）
6. **稳态 vs 瞬态的时间字段不同** —— 稳态用 iteration count，瞬态用 flow time + time step
7. **`.trn` 不一定保留** —— 取决于用户是否打开 transcript 录制
8. **Polyhedral mesh 在新版广泛使用** —— VTK 老版本 reader 处理 poly 元素可能不全
9. **国际化变量名** —— 部分 Fluent 版本在汉化界面下 `.cas` 字符串可能含中文，需 UTF-8 解码
10. **UDM (User-Defined Memory)** —— 用户脚本可写自定义场到 `udm-0`/`udm-1`/...，名字不语义化

### 4.5 Tier 覆盖总览

| Tier | 状态 | 备注 |
|---|---|---|
| T1 Identify | ✅ Phase 2 | 后缀 + magic |
| T2 Inventory | ✅ Phase 2 | reader.UpdateInformation() |
| T3 Metadata | ✅ Phase 2 | reader 元信息 + .trn |
| T4 FieldStats | ✅ Phase 2 | VTK reader |
| T5 QOI | ✅ Phase 2 | force/mass flow 报告 |
| T6 FullData | ✅ Phase 2 | VTU 导出 |

---

## 5. CGNS

### 5.1 格式特征

- **HDF5 后端**（CGNS v3.0+），少数旧文件用 ADF（不支持）
- 标准化目录结构：`/CGNSBase_<n>/Zone_<n>/...`
- 识别：HDF5 magic + 顶层 group `/CGNSBase_*`
- 多 zone 支持
- 结构化（IJK） + 非结构化（element-based）混合

### 5.2 工具

| Tier | 工具 |
|---|---|
| T1 | h5py 检查 group 结构 |
| T2-T3 | `vtkmodules.vtkIOCGNSReader.vtkCGNSReader`（推荐）；备选 h5py 直接遍历 |
| T4 | vtkCGNSReader |
| T6 | vtkCGNSReader → VTU |

### 5.3 信息族

#### 族 A — 网格

| 字段 | Tier | 来源 |
|---|---|---|
| `n_bases` | 1 | `/CGNSBase_*` 数 |
| `zones` (per base: name, type=Structured/Unstructured, dimensions) | 2-3 | `/CGNSBase_*/Zone_*` |
| `zone_size` (IJK for structured / vertex+cell counts for unstructured) | 3 | Zone header |
| `boundary_conditions` (per zone) | 3 | `/Zone_*/ZoneBC_*/BC_*` |
| `families` | 3 | `/CGNSBase_*/Family_*` |
| `bounding_box` | 4 | VTK |

#### 族 B — 物理场

| 字段 | Tier | 来源 |
|---|---|---|
| `flow_solutions` (per zone) | 2 | `/Zone_*/FlowSolution_*` |
| `variables` (per FlowSolution) | 2 | DataArray 名 |
| `data_location` (CellCenter / Vertex) | 3 | FlowSolution 属性 |
| `variable_ranges` | 4 | VTK |
| `non_dimensional_state` (Mach, Reynolds, ...) | 3 | `/CGNSBase_*/ReferenceState` |

#### 族 C — 求解器/物理设置

| 字段 | Tier | 来源 |
|---|---|---|
| `cgns_version` | 1 | `/CGNSLibraryVersion` |
| `creator_solver` | 3 | `/CGNSBase_*/Descriptor_*` 中常含求解器标识 |
| `governing_equations` | 3 | `/CGNSBase_*/GoverningEquations` |
| `gas_model` | 3 | `/CGNSBase_*/FlowEquationSet/GasModel` |

#### 族 E — 运行状态

| 字段 | Tier | 来源 |
|---|---|---|
| `time_iter_values` (transient) | 2 | `/CGNSBase_*/BaseIterativeData/TimeValues` |
| `n_time_steps` | 2 | 同上长度 |

### 5.4 已知 quirks

1. **vtkCGNSReader 不一定全覆盖标准** —— 罕见的 element 类型可能不支持
2. **混合 base** —— 同一文件可能含 CFD base + 结构 base
3. **多语言写入器**（pyCGNS / cgnslib / 自定义）写出的细微差异
4. **Family BC 的层次** —— BC 可以引用 Family 而不是直接定义类型

### 5.5 Tier 覆盖总览

| Tier | 状态 |
|---|---|
| T1-T4 | ✅ Phase 2 |
| T5 | ⚠️ Phase 2（受控变量名 ↔ CGNS Standard Name 映射） |
| T6 | ✅ Phase 2 |

---

## 6. HDF5 通用

### 6.1 格式特征

- 任何 HDF5 文件，含义不绑求解器
- Magic：`89 48 44 46 0D 0A 1A 0A` (字节)
- 路径如 `/group1/group2/dataset`
- 属性 (attrs) 附在 group / dataset 上

### 6.2 工具

`h5py`，本节**不**使用 vtkCGNSReader（那是已知是 CGNS 才用）。

### 6.3 信息族（仅 T1-T2 可靠，T3+ 退化）

| 字段 | Tier | 来源 |
|---|---|---|
| `is_hdf5` | 1 | magic bytes |
| `is_cgns_like` | 1 | top-level `/CGNSBase_*` 存在 |
| `is_fluent_cff` | 1 | 顶层 attr `Solver=Fluent` |
| `is_h5part` | 1 | `/Step#*/positions` 模式 |
| `top_level_groups` | 2 | `f.keys()` |
| `dataset_tree` (path, shape, dtype, chunks) | 2 | `f.visititems()` |
| `attributes` (root + per-group) | 2 | `f.attrs` |
| `dataset_value_ranges` | 4 | numpy min/max（对小数据集） |

### 6.4 用法

- **Path B 兜底**：当 Tier 1 识别为 HDF5 但不匹配任何已知变种（CGNS / Fluent CFF / h5part）时，走通用 dump
- **Discovery Agent 启发式输入**：dump 出的 tree + attrs 是 LLM 推断 solver/structure 的素材

### 6.5 已知 quirks

1. **大文件读 metadata 也慢** —— 用 `with h5py.File(path, 'r', swmr=True)` 提速
2. **External links** —— 可能引用其他 HDF5 文件（`f['group'].file != f.file`）
3. **Compound dtype** —— numpy structured array，shape 表示外部维度
4. **Strings 编码** —— Python 3 + h5py 3.x 默认 bytes，需要 `.decode()`
5. **Chunks vs contiguous** —— 不影响读取但影响性能

---

## 7. LS-DYNA

### 7.1 格式特征

- **输入**：`.k` / `.key` / `.dyn` —— Keyword 格式（文本）
- **结果**：
  - `d3plot` —— 主结果（位移、应力、应变 ……）；分布式（`d3plot01`, `d3plot02`, ...）
  - `d3thdt` —— 时间历史
  - `d3hsp` —— 求解日志（文本）
  - `binout` —— 二进制输出（节点/单元历程）
  - `glstat`, `matsum`, `nodout`, `elout` —— ASCII 报告
- 识别：`d3plot` 文件名前缀 + 二进制 header

### 7.2 工具

| Tier | 工具 |
|---|---|
| T1 | 文件名前缀检测 |
| T2-T3 | `vtkmodules.vtkIOLSDyna.vtkLSDynaReader` |
| T4 | 同上 |
| T6 | 同上 → VTU |

```python
from vtkmodules.vtkIOLSDyna import vtkLSDynaReader
reader = vtkLSDynaReader()
reader.SetFileName("d3plot")  # 自动 stitch d3plot01, d3plot02, ...
reader.UpdateInformation()
times = reader.GetTimeValues()
```

### 7.3 信息族

#### 族 A — 网格

| 字段 | Tier | 来源 |
|---|---|---|
| `n_nodes` | 3 | d3plot header |
| `n_solid_elements`, `n_shell_elements`, `n_beam_elements`, `n_thick_shells` | 3 | d3plot header |
| `parts` (id, name, material) | 2-3 | d3plot ID + .k 文件 `*PART` |
| `bounding_box` | 4 | VTK |

#### 族 B — 物理场

| 字段 | Tier | 来源 |
|---|---|---|
| `nodal_variables` (displacement / velocity / acceleration) | 2 | d3plot header flags |
| `element_variables` (stress / strain / pressure / SDV) | 2 | d3plot header flags |
| `damage_failure` | 2 | d3plot 扩展 flags |
| `variable_ranges` | 4 | VTK |

#### 族 C — 求解器/物理设置

| 字段 | Tier | 来源 |
|---|---|---|
| `solver_type` (explicit / implicit) | 3 | `.k` `*CONTROL_SOLUTION` |
| `time_step_control` | 3 | `.k` `*CONTROL_TIMESTEP` |
| `materials` (per part) | 3 | `.k` `*MAT_*` |
| `contact_definitions` | 3 | `.k` `*CONTACT_*` |
| `boundary_conditions` | 3 | `.k` `*BOUNDARY_*` |
| `loads` | 3 | `.k` `*LOAD_*` |

#### 族 E — 运行状态

| 字段 | Tier | 来源 |
|---|---|---|
| `n_cycles` (时间步数) | 2 | d3plot 状态数 |
| `final_time` | 2 | 最后 state 的 time |
| `cpu_time` | 4 | `d3hsp` 文本 |
| `energy_balance` (kinetic, internal, total) | 4 | `glstat` ASCII |

#### 族 F — 后处理

| 字段 | Tier | 来源 |
|---|---|---|
| `nodal_history` (specific nodes) | 4 | `nodout` ASCII / `binout` |
| `element_history` | 4 | `elout` / `binout` |
| `contact_force_history` | 4 | `rcforc` ASCII |
| `material_summary` | 4 | `matsum` ASCII |

### 7.4 已知 quirks

1. **d3plot 是分布式的** —— `d3plot`, `d3plot01`, `d3plot02`, ... vtkLSDynaReader 自动 stitch，但要点对入口文件
2. **大变形** —— 节点位移会让 bounding_box 在不同时间步差很多
3. **状态保存频率不均** —— `*DATABASE_BINARY_D3PLOT` 控制写出频率
4. **MPP vs SMP** —— 并行版本输出格式略有差异
5. **`.k` keyword 文件大小写敏感** —— `*MAT_ELASTIC` vs `*mat_elastic` 都接受但社区习惯大写
6. **Include 文件链** —— `*INCLUDE` 可能引用十几个其他 .k 文件，需要递归解析
7. **SDV (state variable)** —— 用户自定义状态变量，名字 SDV1/SDV2/... 不语义化

### 7.5 Tier 覆盖总览

| Tier | 状态 |
|---|---|
| T1-T4 | ✅ Phase 2 |
| T5 | ⚠️ Phase 2（结构动力学 QOI：max stress / energy balance） |
| T6 | ✅ Phase 2 |

---

## 8. STAR-CCM+

### 8.1 格式特征

- **`.sim`** —— 完全封闭的二进制项目文件（含 mesh + setup + 结果一体）
- 没有公开规格，**没有任何第三方 reader**

### 8.2 工具

| Tier | 工具 |
|---|---|
| T1 | 后缀名 + 文件大小启发 |
| T2+ | 🔧 **必须用户预导出** |

预导出选项：

```java
// 用户在 STAR-CCM+ 里跑 macro，导出到中性格式
// 选项 1: 导出 EnSight Gold
sim.exportEnsightGold("output_dir");
// 选项 2: 导出 CGNS
sim.exportCGNS("output.cgns");
// 选项 3: 导出 CSV reports
report.export("report.csv");
```

预导出后，sim-parse **走对应的中性格式 parser**（EnSight / CGNS / CSV）。

### 8.3 信息族

| 字段 | Tier | 来源 | 状态 |
|---|---|---|---|
| `is_starccm_sim` | 1 | 后缀 `.sim` | ✅（仅识别） |
| `file_size`, `mtime` | 1 | filesystem | ✅ |
| 其他字段 | 2+ | **需用户预导出** | 🔧 |

### 8.4 已知 quirks

1. **完全封闭** —— Siemens 不公开 .sim 格式
2. **必须 STAR-CCM+ 在场** —— 没有 STAR-CCM+ 安装就不能解析
3. **macro 导出是用户责任** —— sim-parse 不能自动调用 STAR-CCM+

### 8.5 Tier 覆盖总览

| Tier | 状态 | 备注 |
|---|---|---|
| T1 | ✅ 仅识别 | Phase 1 也能加（无成本） |
| T2-T6 | ❌ | 用户预导出后走 EnSight/CGNS 路径 |

### 8.6 推荐文档输出（给用户）

sim-parse 遇到 .sim 文件应输出明确指引：

```json
{
  "format": "starccm_sim",
  "supported": false,
  "reason": "STAR-CCM+ .sim is closed format; no reader available",
  "recommended_action": "Export from STAR-CCM+ to EnSight Gold or CGNS using a Java macro, then re-ingest the exported folder.",
  "macro_template_url": "see sim-parse/docs/starccm_export_macro.md"
}
```

---

## 9. Abaqus

### 9.1 格式特征

#### 输入卡（文本，可读）
- **`.inp`** —— Abaqus keyword 格式
- 周边：`.cae`（项目，binary，CAE 内部）

#### 结果文件
- **`.odb`** —— 主结果（封闭二进制）
- **`.dat`** —— 文本输出（节点/单元历程，partial）
- **`.msg`** —— 求解器消息（文本）
- **`.sta`** —— 状态/收敛历史（文本）
- **`.fil`** —— legacy 格式（文本/二进制混合）
- **`.log`** —— 控制台日志

### 9.2 工具

| 数据源 | 工具 |
|---|---|
| `.inp` | 自写 Keyword parser（文本） |
| `.dat` / `.msg` / `.sta` / `.log` | 文本 + regex |
| `.odb` | 🔧 **VTK 没有 reader**，三选一：<br>- `odbAccess`（Abaqus 自带 Python，需 `abaqus python` 调用）<br>- `odb2vtk`（社区，依赖 Abaqus 安装）<br>- 用户预导出 |
| `.fil` | 老式 ASCII 解析（已弃用，不推荐） |

VTK 没有 vtkAbaqusReader（实测验证，见 design.md §17）。

### 9.3 信息族

#### 来自 `.inp`（无需 Abaqus）

| 字段 | Tier | 来源 |
|---|---|---|
| `analysis_type` (Static / Dynamic / Modal / Heat Transfer) | 3 | `*STEP` 子卡（`*STATIC`/`*DYNAMIC EXPLICIT`/...） |
| `parts` | 3 | `*PART` 块 |
| `assembly` | 3 | `*ASSEMBLY` 块 |
| `materials` (elastic / plastic / hyperelastic / orthotropic) | 3 | `*MATERIAL` + 子卡 |
| `sections` (solid / shell / beam) | 3 | `*SOLID SECTION` 等 |
| `boundary_conditions` | 3 | `*BOUNDARY` |
| `loads` | 3 | `*CLOAD`, `*DLOAD`, `*DSLOAD`, `*TEMPERATURE` |
| `interactions` (contact / tie) | 3 | `*CONTACT PAIR`, `*TIE` |
| `output_requests` | 3 | `*OUTPUT`, `*ELEMENT OUTPUT`, `*NODE OUTPUT` |
| `node_count` | 3 | `*NODE` 块计数 |
| `element_count` | 3 | `*ELEMENT` 块计数 |
| `element_types` | 3 | `*ELEMENT, TYPE=...` 标签 |

#### 来自 `.dat` / `.sta` / `.msg`（无需 Abaqus）

| 字段 | Tier | 来源 |
|---|---|---|
| `step_summary` | 3 | `.sta` 每行一个增量 |
| `convergence_history` | 4 | `.msg` 残差 |
| `wall_clock_time` | 4 | `.dat` 末尾 |
| `partial_results` | 4 | `.dat` 节点/单元历程（如 `*NODE PRINT` 启用了） |

#### 来自 `.odb`（**需要 Abaqus 环境**）

| 字段 | Tier | 来源 |
|---|---|---|
| `mesh_full` | 6 | `odbAccess` |
| `field_outputs` (S/E/U/V/A) | 4-6 | `odbAccess.frames[].fieldOutputs` |
| `history_outputs` (per node/element) | 4 | `odbAccess.steps[].historyRegions` |
| `step_results` | 3-4 | `odbAccess.steps` |

### 9.4 已知 quirks

1. **`.odb` 完全封闭** —— 必须有 Abaqus（哪怕是 Student Edition）才能读
2. **`odbAccess` 只在 `abaqus python` 内可用** —— 不能 `pip install`，必须用 Abaqus 自带 Python，通常通过 subprocess 调用
3. **`abaqus python -c "from odbAccess import ..."`** —— 这条命令的脚本通常以 JSON 输出结果
4. **`*INCLUDE` 链** —— `.inp` 可能 include 多个文件，递归解析
5. **`*PARAMETER`** —— 参数化输入卡，需要先求值
6. **Nodes 和 Elements 编号** —— 不连续是常态（用户可指定）
7. **Step 概念** —— 一个分析可有多个 step，每 step 有独立载荷/边界
8. **大小写不敏感** —— Abaqus keyword 大小写都接受，但社区习惯大写
9. **`.fil` 已弃用** —— Abaqus 6.13+ 可能不再生成；不要依赖
10. **`.odb` 版本兼容性** —— Abaqus 2024 写的 .odb 可能不能用 Abaqus 2018 的 odbAccess 读

### 9.5 Tier 覆盖总览

| Tier | 状态 | 备注 |
|---|---|---|
| T1 Identify | ✅ Phase 3 | 后缀 |
| T2 Inventory | ✅ Phase 3 | `.inp` + 周边文件清单 |
| T3 Metadata | ✅ Phase 3 | `.inp` keyword 解析 + `.sta`/`.msg` |
| T4 FieldStats | ❌ Phase 4 | 需 odb2vtk 或 abaqus python subprocess |
| T5 QOI | ⚠️ Phase 4 | 部分来自 `.dat` 文本 |
| T6 FullData | ❌ Phase 4 | 需 odb2vtk |

### 9.6 推荐策略（Phase 1-3）

```
用户给一个 Abaqus job 目录
  ↓
sim-parse Path A:
  ├─ .inp 存在 → Tier 3 解析（设置、材料、BC、载荷）
  ├─ .dat 存在 → Tier 3 部分（step summary）
  ├─ .sta 存在 → Tier 4 部分（收敛历史）
  ├─ .msg 存在 → Tier 4 部分（残差）
  └─ .odb 存在 但 Phase 3 不读 →
       输出："Abaqus .odb detected; full result extraction requires Abaqus environment.
              Phase 4 will support via odb2vtk pre-conversion."
```

---

## 10. MAPDL / ANSYS Mechanical

### 10.1 格式特征

#### 输入
- **`.dat`** / `.inp` —— APDL 命令脚本（文本）
- **`.mac`** —— APDL 宏（文本）

#### 结果
- **`.rst`** —— 结构结果（封闭二进制）
- **`.rth`** —— 热结果
- **`.rmg`** —— 电磁结果
- **`.full`** —— 矩阵文件（瞬态/模态）
- **`.mode`** —— 模态向量
- **`.esav`** —— element save file

#### 项目
- **`.mechdat`** / **`.wbpz`** —— Workbench 项目（封闭）
- **`.mechdb`** —— Mechanical 数据库

### 10.2 工具

| 数据源 | 工具 |
|---|---|
| `.dat` / `.inp` (APDL) | 自写 APDL 文本 parser |
| `.rst` / `.rth` | **PyDPF**（`ansys-dpf-core`），官方工具 |
| `.full` / `.mode` | PyDPF |

VTK 无 ANSYS reader。PyDPF 是官方支持，但是**额外大依赖**（包括 DPF 服务器）。

```python
# 注意：不在 Phase 1-3 范围
from ansys.dpf import core as dpf
model = dpf.Model("file.rst")
mesh = model.metadata.meshed_region
displacement = model.results.displacement.eval()
```

### 10.3 信息族

#### 来自 `.dat` / `.inp` (APDL)

| 字段 | Tier | 来源 |
|---|---|---|
| `analysis_type` | 3 | `ANTYPE,...` 命令 |
| `element_types` | 3 | `ET,...` |
| `materials` | 3 | `MP,...`, `MPDATA,...` |
| `keypoints`, `lines`, `areas`, `volumes` | 3 | `K,...`, `L,...`, `AL,...`, `VA,...` |
| `boundary_conditions` | 3 | `D,...`, `F,...` |
| `solution_settings` | 3 | `SOLU,...`, `SOLVE` |

#### 来自 `.rst`（需 PyDPF）

| 字段 | Tier | 来源 |
|---|---|---|
| `n_nodes`, `n_elements` | 3 | header |
| `result_sets` (每个载荷步/子步) | 3 | DPF |
| `nodal_displacements`, `nodal_velocities`, `nodal_accelerations` | 4-6 | DPF results |
| `stress_tensors`, `strain_tensors` | 4-6 | DPF |
| `mode_frequencies` (modal analysis) | 3-4 | DPF |

### 10.4 已知 quirks

1. **APDL 命令格式** —— 逗号分隔，缩写命令（`ET`, `MAT`, `ESEL`），需要查参考
2. **载荷步/子步** —— 跟 Abaqus 的 step 概念类似
3. **`.rst` 的版本** —— 不同 ANSYS 版本写出的 .rst 不完全兼容
4. **PyDPF 服务器** —— 后台需要启动 DPF 服务器（自动），但容器化时要注意
5. **Workbench 项目** —— `.wbpz` 是 zip，里面打包多个 `.mechdat` + `.rst`

### 10.5 Tier 覆盖总览

| Tier | 状态 | 备注 |
|---|---|---|
| T1 | ✅ Phase 3 | 后缀 |
| T2-T3 | ⚠️ Phase 3 | 仅 APDL 文本输入卡 |
| T4-T6 | ❌ Phase 4 | 需 PyDPF |

---

## 11. Tecplot

### 11.1 格式特征

- **`.plt`** —— 二进制（Tecplot binary）
- **`.dat`** / `.tec` —— ASCII（Tecplot ASCII）
- 识别 plt：magic `#!TDV` (Version)
- Zone-based（多区数据）

### 11.2 工具

| Tier | 工具 |
|---|---|
| T1 | magic `#!TDV` |
| T2-T6 | `vtkmodules.vtkIOGeometry.vtkTecplotReader`（binary）<br>`vtkmodules.vtkIOTecplotTable.vtkTecplotTableReader`（ASCII） |

### 11.3 信息族

| 字段 | Tier | 来源 |
|---|---|---|
| `tecplot_version` | 1 | magic 后字节（"#!TDV112"） |
| `zones` (name, type=ORDERED/FELINESEG/FETRIANGLE/.../FEPOLYGON, dimensions) | 2-3 | reader |
| `variables` | 2 | header |
| `variable_ranges` | 4 | VTK |
| `time_strands` (transient) | 2-3 | zone strand IDs |

### 11.4 已知 quirks

1. **多种内部 layout** —— BLOCK vs POINT；ORDERED (IJK) vs Finite Element
2. **`.plt` 版本众多** —— 75/96/101/102/107/111/112，每版略有差异
3. **变量共享** —— 多 zone 可共享某些变量，节省存储但解析需注意

### 11.5 Tier 覆盖总览

| Tier | 状态 |
|---|---|
| T1-T6 | ⚠️ Phase 3（VTK reader 覆盖度参差，复杂 case 可能不全） |

---

## 12. EnSight Gold

### 12.1 格式特征

- 多文件格式：
  - `<case>.case` —— 主索引（ASCII）
  - `<case>.geo` 或 `<case>.geo.gz` —— 几何（ASCII 或 binary）
  - `<case>.<var>` 或 `<case>_<step>.<var>` —— 各变量分别一文件
- 时间步可分别保存（time-step 友好）
- 识别：`.case` 后缀 + 文件第一行 `FORMAT\ntype: ensight gold`

### 12.2 工具

`vtkmodules.vtkIOEnSight.vtkGenericEnSightReader`

### 12.3 信息族

| 字段 | Tier | 来源 |
|---|---|---|
| `parts` (per part: name, dimensions, n_nodes, n_elements) | 2-3 | `.case` + `.geo` header |
| `variables` (per part) | 2 | `.case` 中 VARIABLE block |
| `variable_centering` (per element / per node) | 3 | `.case` |
| `time_set` (n_steps, time_values) | 2 | `.case` TIME block |
| `material_sets` (如有) | 3 | `.case` MATERIAL block |
| `variable_ranges` | 4 | VTK |

### 12.4 已知 quirks

1. **ASCII vs binary 同后缀** —— 看 .case 里 `format` 标识或 .geo 第一行
2. **transient 文件命名约定** —— `<case>_<step>.<var>` 或 `<case>.<var>{以星号填充}`
3. **structured 和 unstructured 都支持** —— 复杂的 hybrid 也行

---

## 13. Plot3D

### 13.1 格式特征

- 极简结构化网格格式
- **`.xyz`** / `.x` —— 网格（IJK 坐标）
- **`.q`** —— 流场解（5 个变量：rho, rho*u, rho*v, rho*w, rho*E）
- **`.f`** —— 通用 function file（任意变量数）
- 多 block 支持
- 二进制 + ASCII 两种 + 单精度/双精度

### 13.2 工具

`vtkmodules.vtkIOParallel.vtkMultiBlockPLOT3DReader`

### 13.3 信息族

| 字段 | Tier | 来源 |
|---|---|---|
| `n_blocks` | 1 | header 第一个 int |
| `block_dimensions` (per block: I, J, K) | 2 | header |
| `n_variables` (.q 是 5；.f 可变) | 2 | header |
| `variables` | 2 | 必须用户告知（Plot3D 不存变量名） |
| `variable_ranges` | 4 | VTK |

### 13.4 已知 quirks

1. **变量没名字** —— Plot3D 是位置编码，变量含义靠约定
2. **字节序敏感** —— big-endian / little-endian 都见
3. **iblank** —— 可选的"网格点是否激活"标志，特别处理

---

## 14. 跨求解器 QOI 统一映射表

把 §3-13 各求解器原生输出**翻译成受控变量名**，是 sim-parse Tier 5 的核心任务。下表是首批 QOI 的跨求解器映射：

### 14.1 流体动力学

| 受控变量名 | 单位 | OpenFOAM | Fluent | CGNS | Tecplot | EnSight |
|---|---|---|---|---|---|---|
| `lift_coefficient` | 1 | `forces/forceCoeffs.dat:Cl[-1]` | `report-cl.out:lift[-1]` | `/CGNSBase/IntegralData/LiftCoeff` | 用户 zone 变量 | 用户变量 |
| `drag_coefficient` | 1 | `forces/forceCoeffs.dat:Cd[-1]` | `report-cd.out:drag[-1]` | 同上 | 同上 | 同上 |
| `mean_outlet_velocity` | m/s | outlet patch 上 `U` 加权平均 | outlet face zone 上 velocity-magnitude | 同上 | 同上 | 同上 |
| `pressure_drop` | Pa | inlet `p` - outlet `p` | inlet-outlet 总压差 | 同上 | 同上 | 同上 |
| `mass_flow_rate` | kg/s | inlet ∫ ρU·n dA | mass-flow report | 同上 | 同上 | 同上 |

### 14.2 热

| 受控变量名 | 单位 | OpenFOAM | Fluent |
|---|---|---|---|
| `max_temperature` | K | `T.internalField.max()` | `temperature.max` |
| `wall_heat_flux_total` | W | wall patch ∫ q dA | wall heat flux report |
| `nusselt_number` | 1 | 派生计算 | 内置 report |

### 14.3 燃烧

| 受控变量名 | 单位 | OpenFOAM | Fluent |
|---|---|---|---|
| `max_heat_release_rate` | W/m³ | `Qdot.internalField.max()` | `heat-of-reaction.max` |
| `ignition_delay` | s | `Qdot` 时序穿越阈值时刻 | 同上 |
| `co_emission_index` | g/kg-fuel | 出口 CO 质量 / 入口燃料质量 | 同上 |

### 14.4 数值收敛

| 受控变量名 | 单位 | OpenFOAM | Fluent |
|---|---|---|---|
| `convergence_orders_<var>` | 1 | log 残差 log10(initial/final) | `.trn` 残差 log10 |
| `is_converged` | bool | 残差末值 vs tolerance | 同上 |
| `courant_max` | 1 | log 中 Courant 报告 | TUI 输出 |

### 14.5 结构

| 受控变量名 | 单位 | LS-DYNA | Abaqus | MAPDL |
|---|---|---|---|---|
| `max_von_mises_stress` | Pa | d3plot stress.vonMises.max | `.odb` field S.MISES.max | `.rst` SEQV.max |
| `max_displacement` | m | d3plot displacement.magnitude.max | U.magnitude.max | UMX.max |
| `total_kinetic_energy` | J | glstat:KE | `.dat` ALLKE | NKE |
| `mode_frequencies` (list) | Hz | N/A | `.odb` modal frequencies | `.rst` modal |

完整映射表会随 sim-parse 实现演进 —— 此表是 v0.1 起点。

---

## 15. 阶段交付路线图

| 求解器 | Phase 1 (MVP) | Phase 2 (4-6w) | Phase 3 (弹性) | Phase 4 (远期) |
|---|---|---|---|---|
| **OpenFOAM** | T1-T4 ✅ | T5-T6 ✅ | — | — |
| **HDF5 通用** | T1-T2 ✅ | T3 ⚠️ | — | — |
| **CGNS** | — | T1-T6 ✅ | — | — |
| **Fluent (.cas/.dat + .cas.h5)** | — | T1-T6 ✅ | — | — |
| **LS-DYNA** | — | T1-T6 ✅ | — | — |
| **Tecplot** | — | — | T1-T6 ⚠️ | — |
| **EnSight** | — | — | T1-T6 ✅ | — |
| **Plot3D** | — | — | T1-T6 ✅ | — |
| **Abaqus (.inp)** | — | — | T1-T3 ✅ | — |
| **Abaqus (.odb)** | — | — | — | T4-T6 🔧 odb2vtk |
| **MAPDL/Mechanical** | — | — | T1-T3 ⚠️ APDL only | T4-T6 🔧 PyDPF |
| **STAR-CCM+** | — | — | T1 ✅ 仅识别 | 永远 🔧（用户预导出） |

---

## 16. 迁移候选 → sim-knowledge

本节标记**本文档中哪些内容未来应该迁往 sim-knowledge 仓库**（详见 [design.md §2](design.md) 三轴架构）。当前阶段不动，等 sim-parse Phase 1 实现完成、sim-knowledge 仓库正式建立时执行迁移。

### 16.1 迁移原则

| 内容性质 | 归属 |
|---|---|
| sim-parse 实现规格（Tier 覆盖、工具调用、API） | sim-parse/docs/（留） |
| 求解器/格式的**事实**（不依赖 sim-parse 是否实现） | **sim-knowledge** |
| 给 LLM 检索用的结构化素材 | **sim-knowledge** |
| 跨求解器物理映射（QOI、变量字典、单位） | **sim-knowledge/physics/** |
| 单求解器格式约定、quirks、识别签名 | **sim-knowledge/formats/** |

### 16.2 高优先级迁移项 ⭐

#### 16.2.1 各求解器 quirks → `sim-knowledge/formats/<name>/quirks.yaml`

每节的 §X.4 "已知 quirks" 是**最该挪的内容** —— 这些"领域智慧"挪到结构化 YAML 后，Discovery Agent 和 Query Agent 才能精准检索。

| 本文档 | 目标 | 条目数 |
|---|---|---|
| §3.4 OpenFOAM quirks | `formats/openfoam/quirks.yaml` | 10 |
| §4.4 Fluent quirks | `formats/fluent/quirks.yaml` | 10 |
| §5.4 CGNS quirks | `formats/cgns/quirks.yaml` | 4 |
| §6.5 HDF5 quirks | `formats/hdf5/quirks.yaml` | 5 |
| §7.4 LS-DYNA quirks | `formats/lsdyna/quirks.yaml` | 7 |
| §9.4 Abaqus quirks | `formats/abaqus/quirks.yaml` | 10 |
| §10.4 MAPDL quirks | `formats/mapdl/quirks.yaml` | 5 |
| §11.4 Tecplot quirks | `formats/tecplot/quirks.yaml` | 3 |
| §12.4 EnSight quirks | `formats/ensight/quirks.yaml` | 3 |
| §13.4 Plot3D quirks | `formats/plot3d/quirks.yaml` | 3 |

迁移示例（markdown → YAML）见 §16.6。

#### 16.2.2 跨求解器 QOI 映射 → `sim-knowledge/physics/<domain>/variables.yaml`

§14 整张表按物理域拆分迁移：

| 本文档 | 目标 | 受控变量数（v0.1） |
|---|---|---|
| §14.1 流体动力学 | `physics/fluid_dynamics/variables.yaml` | 5 |
| §14.2 热 | `physics/heat_transfer/variables.yaml` | 3 |
| §14.3 燃烧 | `physics/combustion/variables.yaml` | 3 |
| §14.4 数值收敛 | `physics/numerics/variables.yaml` | 3 |
| §14.5 结构 | `physics/structural/variables.yaml` | 4 |

#### 16.2.3 格式识别签名 → `sim-knowledge/formats/<name>/structure.yaml`

每节 §X.1 "格式特征"中的**识别部分**（magic、文件签名、目录结构）是 Discovery Agent 识别陌生文件的核心依据。

| 本文档 | 目标 | 关键内容 |
|---|---|---|
| §3.1 OpenFOAM | `formats/openfoam/structure.yaml` | 目录式签名（system/+constant/） |
| §4.1 Fluent | `formats/fluent/structure.yaml` | .cas/.dat 后缀 + CFF magic |
| §5.1 CGNS | `formats/cgns/structure.yaml` | HDF5 magic + /CGNSBase_* group |
| §6.1 HDF5 通用 | `formats/hdf5/structure.yaml` | HDF5 magic + 通用约定 |
| §7.1 LS-DYNA | `formats/lsdyna/structure.yaml` | d3plot 文件名前缀 |
| §11.1 Tecplot | `formats/tecplot/structure.yaml` | `#!TDV` magic |
| §12.1 EnSight | `formats/ensight/structure.yaml` | .case 第一行格式 |
| §13.1 Plot3D | `formats/plot3d/structure.yaml` | .xyz/.q 配对 |

### 16.3 双载体内容（两边都保留，CI 检查同步）

下面这些内容**同时**作为 sim-parse 实现规格（markdown 叙事）和 sim-knowledge 检索素材（YAML 结构化）存在 —— 两边都需要，但要 CI 检查不漂移。

| 本文档 | 双载体目标 |
|---|---|
| 各 §X.3 信息族表的"字段→来源"映射 | `formats/<name>/extraction_map.yaml` |
| 各 §X.1 中的"内部混合 / 编码 / 字节序"等格式细节 | `formats/<name>/structure.yaml`（与识别签名同文件） |

### 16.4 留在 sim-parse/docs/extraction_reference.md（不迁移）

| 内容 | 原因 |
|---|---|
| §1 文档目的与边界 | sim-parse 自身定位 |
| §2 通用约定（Tier / Path / 状态码） | sim-parse 实现概念 |
| 各 §X.2 工具（VTK 调用代码） | sim-parse 实现细节 |
| 各 §X.3 信息族表的 Tier 列、状态列 | sim-parse 实现进度 |
| 各 §X.5 Tier 覆盖总览 | sim-parse 项目状态 |
| §15 阶段交付路线图 | sim-parse 项目计划 |
| §17 变更历史 | 文档元数据 |

### 16.5 目标 sim-knowledge 目录结构预览

迁移完成后，sim-knowledge 仓库会长这样：

```
sim-knowledge/                                  (待建)
├── formats/
│   ├── openfoam/
│   │   ├── structure.yaml          ← 来自本文档 §3.1
│   │   ├── quirks.yaml             ← 来自 §3.4
│   │   └── extraction_map.yaml     ← 来自 §3.3 字段→来源 列
│   ├── fluent/
│   │   ├── structure.yaml          ← §4.1
│   │   ├── quirks.yaml             ← §4.4
│   │   └── extraction_map.yaml     ← §4.3
│   ├── cgns/
│   │   ├── structure.yaml          ← §5.1
│   │   ├── quirks.yaml             ← §5.4
│   │   └── extraction_map.yaml     ← §5.3
│   ├── hdf5/
│   │   ├── structure.yaml          ← §6.1
│   │   ├── quirks.yaml             ← §6.5
│   │   └── playbooks/
│   │       └── unknown_hdf5_inspection.yaml   (新增，给 Discovery Agent)
│   ├── lsdyna/                     ← §7.x
│   ├── abaqus/                     ← §9.x
│   ├── mapdl/                      ← §10.x
│   ├── tecplot/                    ← §11.x
│   ├── ensight/                    ← §12.x
│   ├── plot3d/                     ← §13.x
│   ├── conventions/                (新增)
│   │   ├── log_residual_patterns.yaml    (跨求解器 log 残差正则)
│   │   ├── time_directory_patterns.yaml
│   │   └── csv_report_patterns.yaml
│   └── examples/                   (新增)
│       └── successful_discoveries.json    (Discovery Agent few-shot)
├── physics/
│   ├── fluid_dynamics/
│   │   ├── variables.yaml          ← 来自 §14.1
│   │   ├── criteria.yaml           (新增：is_converged, has_recirculation 等)
│   │   └── thresholds.yaml         (新增)
│   ├── heat_transfer/
│   │   ├── variables.yaml          ← §14.2
│   │   ├── criteria.yaml
│   │   └── thresholds.yaml
│   ├── combustion/
│   │   ├── variables.yaml          ← §14.3
│   │   ├── criteria.yaml           (新增：is_extinguished, ignition_delay_methods)
│   │   └── thresholds.yaml
│   ├── numerics/
│   │   ├── variables.yaml          ← §14.4
│   │   └── criteria.yaml           (新增：is_diverged, courant_alarm)
│   └── structural/
│       ├── variables.yaml          ← §14.5
│       └── criteria.yaml
├── labeled_cases/                  (新增：人工标注真值，§11 design.md)
│   ├── combustion_extinction/
│   ├── convergence_failure/
│   └── ...
└── few_shot/
    └── *.json
```

### 16.6 迁移示例（markdown → YAML）

#### 例 1: OpenFOAM quirk 迁移

**本文档 §3.4 markdown 形态**：

> 2. **`endTime=5000, deltaT=1` 不一定是物理时间** —— LTS（局部时间步）求解器把 endTime 当迭代步数，路径名常含 `LTS` 字样。Tier 7 LLM 可推断；纯静态会误读为"5000 秒"

**迁移后 `sim-knowledge/formats/openfoam/quirks.yaml` 形态**：

```yaml
quirks:
  - id: lts_pseudo_time
    description: "LTS 求解器 endTime/deltaT 是迭代步数不是物理时间"
    detect:
      - rule: "controlDict::application matches '*LTS'"
      - rule: "case_path contains 'LTS'"
    affects:
      - tier: 3
        field: time_control.semantics
    interpretation: "treat endTime as iteration count, not seconds"
    confidence_when_matched: MED
    examples:
      - case_path: "tutorials/combustion/reactingFoam/RAS/DLR_A_LTS"
        observed:
          endTime: 5000
          deltaT: 1
        actual_meaning: "5000 LTS iterations"
    references: ["openfoam.com/documentation/lts"]
```

#### 例 2: QOI 映射迁移

**本文档 §14.1 markdown 形态**：

> | `lift_coefficient` | 1 | `forces/forceCoeffs.dat:Cl[-1]` | `report-cl.out:lift[-1]` | `/CGNSBase/IntegralData/LiftCoeff` |

**迁移后 `sim-knowledge/physics/fluid_dynamics/variables.yaml` 形态**：

```yaml
lift_coefficient:
  description: "升力系数（无量纲）"
  unit: "1"
  applicable_when:
    has_solid_walls: true
    flow_type: ["incompressible", "compressible"]
  sources:
    openfoam:
      - file: "postProcessing/forces*/<time>/forceCoeffs.dat"
        column: "Cl"
        aggregator: "last"
        confidence: HIGH
    fluent:
      - file: "report-cl*.out"
        column: "lift"
        aggregator: "last"
        confidence: HIGH
    cgns:
      - path: "/CGNSBase_*/IntegralData/LiftCoeff"
        confidence: HIGH
    starccm_exported_csv:
      - file: "report_*.csv"
        column_pattern: "(?i)lift.*coeff"
        aggregator: "last"
        confidence: MED
```

### 16.7 迁移时机

不要现在就拆，理由是 sim-knowledge 仓库还没建。建议节奏：

| 时机 | 动作 |
|---|---|
| **当前**（design 阶段） | 保留 extraction_reference.md 完整版作为单一参考 |
| **Phase 1 完成后** | 建 sim-knowledge 仓库雏形，从本文档抽种子（Silver 档，LLM 起草 + 人审） |
| **Phase 2** | 完成 §16.2 高优先级迁移；CI 检查双载体一致性 |
| **Phase 3+** | 拓展 sim-knowledge：新增 criteria.yaml / playbooks / labeled_cases，超出本文档范围的内容 |

### 16.8 标记约定（未来用）

将来在迁移时，可以在本文档对应位置加 HTML 注释标记：

```markdown
<!-- TO_KNOWLEDGE: sim-knowledge/formats/openfoam/quirks.yaml -->
1. **求解器主 log 不在顶层** ...

<!-- TO_KNOWLEDGE: sim-knowledge/physics/fluid_dynamics/variables.yaml -->
| `lift_coefficient` | 1 | ... |
```

这种注释在渲染中不可见，但 grep 友好，便于自动化迁移工具识别。**当前不加**，等开始迁移时再批量加。

---

## 17. 变更历史

- **v0.1** — 初版，覆盖 OpenFOAM / Fluent / CGNS / HDF5 通用 / LS-DYNA / STAR-CCM+ / Abaqus / MAPDL / Tecplot / EnSight / Plot3D 共 11 种求解器/格式；含跨求解器 QOI 映射首批；含阶段路线图
- **v0.2** — 新增 §16 迁移候选清单：明确 quirks / QOI 映射 / 格式识别签名 三类高优先级迁移项；列出双载体内容；预览目标 sim-knowledge 目录结构；含 markdown→YAML 迁移示例
- **v0.3** — 实施回填：补 OpenFOAM quirk 11-14（VTK stderr 警告、boundary patch 数差异、endTime 类型、LTS pseudo-time 歧义）；从 sim-parse Phase 1 实施过程中归纳的真实坑
