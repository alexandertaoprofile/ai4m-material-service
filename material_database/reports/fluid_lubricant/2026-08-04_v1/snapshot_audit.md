# 导电润滑介质原始快照审查

- 快照目录：`/data/se42/alpha_project/material_service_hub/material_database/data/raw/incoming/material_platform/conductive_lubricant/2026-08-04_v1`
- 审查时间：2026-08-04T03:54:15.951137+00:00
- 审查模式：只读；未修改、删除或归一化任何原始记录。

## 文件清单

| 逻辑表 | 文件 | 数据行 | SHA-256 |
|---|---|---:|---|
| fluid_property_source | `material.fluid_property_source.csv` | 32 | `91c5911c5fb879cbc45be86680d54195787887449c51c2621e17f614a81bb886` |
| fluid_conductivity | `material.fluid_conductivity.csv` | 138,376 | `b305637e595dda830308296c5e4477aaa711699b4aa9a1d12112542da4e96d6c` |
| fluid_viscosity | `material.fluid_viscosity.csv` | 193,692 | `0c8960fe6cadd753fbab71b3477d87e4a327825b44f069723b8fe243ea822ed5` |
| fluid_stability | `material.fluid_stability.csv` | 8,204 | `9c8619d5271fef1a2317bba27c27f256dbd3bedb36297810bc237bc3ed65875f` |
| fluid_missing_field | `material.fluid_missing_field.csv` | 78 | `b6b49c7b21e561743edcae7ba7b79770cd10ff6dd058769bf274da82431ddd91` |
| fluid_duplicate_record | `material.fluid_duplicate_record.csv` | 38,052 | `306ccab34c7fbac319af0c6aa2f0c5960b55c7f3a7cb2ba2a7ebbe6bee96df2b` |

## fluid_property_source

字段数：28；数据行：32。

### 分类字段分布

- `source_id`：SRC001：2；SRC002：2；SRC003：2；SRC004：2；SRC005：2；SRC006：2；SRC007：2；SRC008：2；SRC009：2；SRC010：2；SRC011：2；SRC012：2；SRC013：2；SRC014：2；SRC015：2；SRC016：2
- `experimental_or_predicted`：experimental：24；mixed：6；predicted：2
- `pure_component_or_mixture`：pure：14；mixture：14；both：4

### 混合物组成完整度

- components=0; fractions=0：14

## fluid_conductivity

字段数：29；数据行：138,376。

### 分类字段分布

- `source_id`：SRC004：122,938；SRC011：10,070；SRC003：5,368
- `experimental_or_predicted`：experimental：138,376
- `extraction_method`：database_export：133,008；table：5,368
- `manual_review_required`：yes：128,306；no：10,070
- `pure_component_or_mixture`：mixture：133,008；pure：5,368

### 数值覆盖

| 字段 | 非空数 | 最小值 | 最大值 |
|---|---:|---:|---:|
| `temperature_k` | 138,376 | 203.4 | 571.3 |
| `conductivity_s_m` | 133,008 | 0.0 | 79500.0 |
| `resistivity_ohm_m` | 132,594 | 1.2578616352201259e-05 | 333333333.3333333 |

### 组成基准

- `<missing>`：86,436
- `mole_fraction`：33,272
- `mass_fraction`：18,304
- `volume_fraction`：364

### 混合物组成完整度

- components=3; fractions=0：58,482
- components=2; fractions=1：37,086
- components=2; fractions=0：22,586
- components=2; fractions=2：10,070
- components=3; fractions=1：3,828
- components=3; fractions=2：956

## fluid_viscosity

字段数：32；数据行：193,692。

### 分类字段分布

- `source_id`：SRC004：176,350；SRC003：17,302；SRC014：40
- `experimental_or_predicted`：experimental：193,692
- `extraction_method`：database_export：176,350；table：17,302；manual_transcription：40
- `manual_review_required`：yes：193,692
- `pure_component_or_mixture`：mixture：176,386；pure：17,306

### 数值覆盖

| 字段 | 非空数 | 最小值 | 最大值 |
|---|---:|---:|---:|
| `temperature_k` | 193,692 | 198.25 | 573.0 |
| `dynamic_viscosity_mpa_s` | 176,350 | 0.04 | 130000000.0 |
| `kinematic_viscosity_mm2_s` | 40 | 12.1 | 73.4 |

### 组成基准

- `mole_fraction`：117,004
- `<missing>`：66,202
- `mass_fraction`：9,942
- `volume_fraction`：544

### 混合物组成完整度

- components=2; fractions=1：115,640
- components=3; fractions=0：32,982
- components=2; fractions=0：15,914
- components=3; fractions=2：9,910
- components=3; fractions=1：1,904
- components=2; fractions=2：36

## fluid_stability

字段数：28；数据行：8,204。

### 分类字段分布

- `source_id`：SRC003：8,204
- `experimental_or_predicted`：experimental：8,204
- `extraction_method`：table：8,204
- `manual_review_required`：yes：5,732；no：2,472
- `pure_component_or_mixture`：pure：8,204

### 数值覆盖

| 字段 | 非空数 | 最小值 | 最大值 |
|---|---:|---:|---:|
| `test_temperature_k` | 0 | - | - |
| `decomposition_temperature_k` | 2,472 | 316.15 | 760.15 |
| `melting_temperature_k` | 0 | - | - |
| `glass_transition_temperature_k` | 0 | - | - |

## fluid_missing_field

字段数：8；数据行：78。

## fluid_duplicate_record

字段数：11；数据行：38,052。

## 下一阶段决策

1. 仅将单位明确、实验类型明确、温度与组成可比较的记录纳入候选筛选视图。
2. `manual_review_required=yes` 与重复/冲突记录保留为质量标记，不能静默删除。
3. 电导率与黏度必须按来源、组分、组成基准、组成和温度严格匹配；本审查不进行跨表合并。
4. 热分解温度不能替代 135 °C 长期老化证据。
