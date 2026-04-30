# inorganic_existing_material

无机已有材料服务，面向“已有化学式/候选材料”的快速筛选、性质补全与前端结果下发。

## 当前主链路（按当前实现）

1. 从用户输入与上下文中提取化学式候选
2. 调用 MP 检索脚本生成候选结构与结果资产
3. 读取 manifest / summary / 图片 / GLB，按协议推送前端
4. 对候选结构进行性质补全（含 ALIGNN 路径，可按环境启用）

## 主要代码入口

- `main.py`：FastAPI/WebSocket 服务入口
- `team_config.py`：兼容桥接入口（转发到 `src/team_config.py`）
- `src/team_config.py`：主要业务编排逻辑

## 关键子环境（代码中实际引用）

- `mp-api-py311`：MP 检索脚本调用
- `ALIGNN_ENV`：ALIGNN 推理环境名（默认 `alignn-gpu-test`）

> 若未设置 `ALIGNN_ENV`，代码会默认使用 `alignn-gpu-test`。

## 结果与静态资源

- 运行产物主要落在 `src/MNS_CaseHub/cases/.../results/`
- 稳定展示资源建议放在 `public/`（例如 `public/databasepic/`）
- `main.py` 已支持 `/public` 静态目录挂载

## 运行方式（开发态）

```bash
python main.py
```

或：

```bash
bash start.sh
```

## 说明

- 本 README 以当前仓库实现为准，已移除旧 ADiT/MACE 主链描述。
- 如后续恢复/切换流程，请同步更新本文档。
