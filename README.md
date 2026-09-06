# material_service_hub

材料服务聚合仓。当前维护三个可独立运行、独立配置、独立部署的服务：

| 服务 | 业务边界 | 主职责 |
| --- | --- | --- |
| `material_composition_optimization/` | 高熵合金/多主元合金（HEA/MPEA）及明确合金体系 | 成分空间、元素约束、代理评估、候选配比提议。 |
| `material_database/` | 已有商品/牌号材料 | 目录检索、证据核验、性质比较；不生成新材料或配方。 |
| `material_discovery/` | 数据库外无机晶体 | 生成、结构准入、热力学初筛和候选排序；不做已有材料查询或合金配比优化。 |
| `material_validation/` | 难熔金属跨尺度性能计算与验证 | 对已有 W / 难熔金属候选按 DFT、MLIP、MD 与实验/文献证据链进行验证；首版为 W-14 标杆案例。 |

## 统一职责路线

三个服务均遵循同一职责方向，不要求使用完全相同的目录名：

```text
main.py（HTTP / WebSocket 传输入口）
  ↓
src/team_config.py（服务级编排与角色适配）
  ↓
业务用例 / 领域工作流 / 数据执行器
  ↓
展示资产、对象存储与前端协议适配
```

- `main.py` 保持路由、连接生命周期和既有前端事件契约，不堆放领域计算。
- `src/team_config.py` 显式呈现业务阶段与调用顺序；不反向 import 自身的 `main.py`。
- `src/team_config_en.py`、`src/llm_utils.py`、`alpha/`、`config/config.yaml` 是兼容保留项。新业务代码不应继续向它们耦合；未经调用方核验不得删除。
- 领域预测、目录事实、生成/验证结果都必须如实标注证据边界，不能包装为实验或 DFT 最终结论。

## 运行与配置

- 每个服务使用自己的本机 `.env`；`.env` 不提交，`.env.example` 不包含密钥。
- 生产/联调由 tmux 启动，不新增 Docker 或启动脚本作为标准路径。
- 前端 WebSocket 的地址、字段、事件顺序、`[start]/[end]`、内容块标记和资产类型均属于兼容契约；改动前必须补契约测试。
- 当前图片资产使用各服务的 MinIO/S3 适配器；不能按文件名推断并删除对象存储实现。

## 服务文档

- [材料成分配比优化](material_composition_optimization/README.md)：另见其[代码审阅指南](material_composition_optimization/docs/alloy_composition_optimization_review_guide.md)与[清理审计](material_composition_optimization/docs/alloy_composition_optimization_cleanup_audit.md)。
- [材料数据库](material_database/README.md)
- [材料发现](material_discovery/README.md)
- [难熔金属跨尺度性能计算与验证](material_validation/README.md)
- [统一架构规范](docs/material_service_architecture_standard.md)

## 对齐状态

| 服务 | 编排层 | 业务模块 | 前端契约测试 |
| --- | --- | --- | --- |
| material_composition_optimization | `src/team_config.py`、`src/alloy_workflow/runtime.py` | `application.py`、`contracts.py`、`runner.py` | 已覆盖文本标记、PNG 资产与最终 result。 |
| material_database | `src/team_config.py:MaterialMature` | `src/catalog/` | 已覆盖目录命中/未命中、资产和事件顺序。 |
| material_discovery | `src/team_config.py:InorganicNewMaterialService` | `src/material_workflow/` | 已覆盖生成输入、角色注册、事件契约和服务边界。 |
| material_validation | `src/team_config.py` | `application/`、`infrastructure/`、`presentation/` | 已覆盖 W 标杆请求、角色发现和流式事件边界；待 MLIP 证据导入后补性质图表回归。 |

目录中的历史部署文件或旧英文兼容文件不等同于当前生产主链。删除它们前，应先在代码、启动器和外部调用方中提供引用审计证据。
