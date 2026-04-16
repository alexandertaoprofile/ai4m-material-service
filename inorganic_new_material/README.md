# Inorganic New Material Service（阶段1骨架）

本仓库当前处于“从已有材料服务拷贝后进行瘦身”的第一阶段，目标是演化为：

**无机新材料发现服务（Inorganic New Material Discovery Service）**。

## 当前阶段目标

当前只保留并强化下面这些能力骨架：

- Service 主入口（FastAPI）
- Handler / Pipeline 基本结构
- 输入 schema 的位置与约定
- 候选生成接口占位（stub）
- 候选校验/排序接口占位（stub）
- 结构化 JSON 输出框架
- 日志与基础配置

> 说明：本阶段不接入真实生成模型，不追求完整业务闭环，优先做仓库收敛与结构治理。

## 当前主要入口

- `main.py`：服务启动入口
- `team_config.py`：兼容入口（桥接到 `src/team_config.py`）
- `src/MNS_CaseHub/cases/material_discovery_demo/`：现有材料发现 demo 管线（后续会逐步抽象为通用 discovery pipeline）

## 目录说明（阶段1）

- `config/`：基础配置
- `src/`：主要业务代码
- `tools/`：工具脚本（后续会继续精简，只保留新服务需要的）
- `alpha/`：历史框架代码（暂保留，后续按需下沉至 legacy）

## 运行（开发态）

```bash
python /data/se42/alpha_project/inorganic_new_material/main.py
```

或：

```bash
bash /data/se42/alpha_project/inorganic_new_material/start.sh
```

## 依赖说明

- 历史全量依赖：`pip_requirements.txt`（非常大，不建议新环境直接全量安装）
- 最小骨架依赖：`requirements.minimal.txt`（本次清理新增）

建议新环境先从最小依赖启动，再按缺失模块逐步补充。

## 下一步（阶段2/3）

- 把 `material_discovery_demo` 抽象成标准化 `handler/pipeline/services/schemas`
- 收敛旧有“已有材料检索/补全/展示强耦合”逻辑
- 逐步引入真实候选生成与校验模块
