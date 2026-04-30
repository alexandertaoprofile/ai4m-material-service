# organic_existing_material

有机已有材料服务，面向“已有有机材料/分子候选”的检索、预测与结果下发。

## 当前主链路（按当前实现）

1. 提取用户输入中的聚合物名称/PSMILES 线索
2. 在 OpenPoly 数据中按 Name / PSMILES 检索候选
3. 对命中候选进行性质整理；缺失项走 XGB 预测补全
4. 生成并下发结果文本、阶段图片、分子结构 GLB 等资产

## 主要代码入口

- `main.py`：FastAPI/WebSocket 服务入口
- `team_config.py`：兼容桥接入口（转发到 `src/team_config.py`）
- `src/team_config.py`：有机链路编排逻辑

## 关键子环境（代码中实际引用）

- `OPENPOLY_XGB_ENV`：有机预测环境名（默认 `organic-predict-py310`）
- `ALIGNN_ENV`：可选 ALIGNN 推理环境（默认 `alignn-gpu-test`）

## MP 能力说明

- 代码中保留了 `mp_export_assets.py` 调用能力（`mp-api-py311`）
- 但对你当前业务而言，MP 属于兼容/兜底路径，不是有机主入口

## 外部资产说明（部署时关注）

- OpenPoly 相关模型/数据可能位于仓库外目录（如 `Openpoly_benchmark`）
- 生产部署需确保对应目录或等价资源可用（可 rsync 或在 113 重新拉取）

## 运行方式（开发态）

```bash
python main.py
```

或：

```bash
bash start.sh
```

## 说明

- 本 README 已按当前代码路径更新：主流程为 OpenPoly 检索 + 性质补全。
- 如后续调整 OpenPoly 数据源、XGB 模型路径或兜底逻辑，请同步更新本文档。
