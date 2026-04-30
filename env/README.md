# Environment Bundle for Production Deployment (113)

本目录用于集中管理 `alpha_material_sync` 的部署环境文件。

## 目录说明

- `ai4m-service-py310.yml`：主服务 Conda 环境（FastAPI/WebSocket 主进程）
- `mp-api-py311.requirements.txt`：`mp-api-py311` 子环境依赖（MP 检索）
- `alignn-gpu-test.conda.before.txt`：ALIGNN 子环境 Conda 包快照（历史）
- `alignn-gpu-test.freeze.before.txt`：ALIGNN 子环境 pip 冻结快照（历史）
- `organic-predict-py310.requirements.txt`：Organic 预测子环境依赖（XGB/OpenPoly 相关）

## 推荐安装顺序

1. 主环境：`ai4m-service-py310.yml`
2. 子环境：`mp-api-py311.requirements.txt`
3. 子环境：`organic-predict-py310.requirements.txt`（如果上线 organic 预测链路）
4. 子环境：`alignn-gpu-test`（按快照文件重建，若启用 ALIGNN）

## 说明

- 本目录只放“环境定义/快照”，不包含任何密钥。
- `.env`、AK/SK、Token 等敏感配置必须在服务器侧单独维护，不入仓。
- 运行代码未被修改；本次仅新增环境整理文件。
