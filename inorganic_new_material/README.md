# inorganic_new_material

无机新材料服务（演进中）。当前定位是为“新材料发现链路”提供服务骨架与可运行入口。

## 当前状态

- 服务可启动（FastAPI/WebSocket 主入口存在）
- 保留了 `handler/pipeline` 相关结构，便于后续扩展
- 部分能力仍处于占位或迁移阶段，目标是逐步从“已有材料链路”解耦

## 主要入口

- `main.py`：服务启动入口
- `team_config.py`：兼容入口（桥接 `src/team_config.py`）
- `src/`：核心业务与案例管线代码

## 目录说明

- `config/`：配置文件
- `src/`：业务实现
- `tools/`：工具脚本
- `alpha/`：历史框架代码（暂保留）

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

1. 新材料候选生成与校验模块独立化
2. 与已有材料服务共享的公共能力抽到通用层
3. 完善输入/输出 schema 与部署文档
