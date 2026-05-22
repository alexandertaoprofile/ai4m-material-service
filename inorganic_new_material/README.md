# inorganic_new_material

无机新材料服务（演进中）。当前定位是为“新材料发现链路”提供服务骨架与可运行入口。

## 当前状态

- 服务可启动（FastAPI/WebSocket 主入口存在）
- 保留了 `handler/pipeline` 相关结构，便于后续扩展
- 已新增 `src/material_workflow/` 作为新材料主线的规范接口层
- MatterGen 生成、ADiT/pymatgen 验证仍待正式接入，不在当前运行链路中伪造结果
- `team_config_en.py` 暂保留，后续用于英文流程设计

## 主要入口

- `main.py`：服务启动入口
- `team_config.py`：兼容入口（桥接 `src/team_config.py`）
- `src/`：核心业务与案例管线代码

## 新材料主线规划

目标链路：

1. 需求解析：把用户输入整理为生成约束与验证目标
2. 候选生成：通过 MatterGen 或兼容生成器产生候选结构
3. 结构验证：通过 ADiT/pymatgen 或后续验证模块补全性质与稳定性信息
4. 候选排序：基于真实验证结果和用户目标进行确定性排序
5. 前端输出：基于 manifest 渲染摘要、动图和可追溯资源

当前第一轮规范化只建立接口层，不改现有服务行为：

- `src/material_workflow/schemas.py`：Generation/Validation/Ranking/Pipeline schema
- `src/material_workflow/generation.py`：MatterGen 接入边界
- `src/material_workflow/validation.py`：ADiT/pymatgen 接入边界
- `src/material_workflow/ranking.py`：确定性排序帮助函数
- `src/material_workflow/emitters.py`：前端 payload 与 manifest 输出
- `src/material_workflow/pipeline.py`：新材料主线编排骨架
- `docs/new_material_pipeline_plan.md`：后续接入计划

## 目录说明

- `config/`：配置文件
- `src/`：业务实现
- `src/material_workflow/`：新材料主线规范接口层
- `tools/`：工具脚本
- `alpha/`：历史框架代码（暂保留）
- `docs/`：结构规范与迁移计划

## 运行方式（开发态）

```bash
python main.py
```

或：

```bash
bash start.sh
```

## 依赖建议

- `requirements.minimal.txt`：最小运行依赖（建议优先）
- `pip_requirements.txt`：历史全量依赖（体积大，建议按需补装）

## 后续演进方向

1. 将 `team_config.py` 中的新材料需求解析接到 `GenerationConstraint`
2. 接入 MatterGen runner，输出真实候选 CIF/结构资源
3. 接入 ADiT/pymatgen validator，避免依赖已有材料 MP manifest
4. 将候选排序和前端渲染统一基于 pipeline manifest
5. 与已有材料服务共享的公共能力逐步抽到通用层
