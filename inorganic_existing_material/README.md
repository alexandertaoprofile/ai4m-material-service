# inorganic_existing_material

无机已有材料服务，面向“已有化学式 / 候选材料体系”的快速筛选、性质补全、结果解释与前端资产下发。

当前版本已完成主链路收敛，核心流程为：
- 候选提取（用户输入 + 上下文）
- MP 数据库检索与结构资产生成
- ALIGNN 补全关键性质（在缺失字段时）
- 结构化结果与图片/GLB 下发

## 1. 功能边界

本服务负责：
- 从输入文本中提取可检索材料候选
- 对候选执行 Materials Project 检索
- 基于候选结构进行性质补全与工程化解释
- 将 summary / 图片 / GLB 按前端协议下发

本服务不负责：
- 文献初筛与论文理解
- 新材料生成式发现
- 实验执行与实验数据采集

## 2. 当前主链路

1. 输入归一化与路由识别（支持 `/mp` 强制单候选路径）
2. 候选提取与筛选（规则 + LLM 校正）
3. 调用 `tools/mp_export_assets.py` 进行 MP 检索与产物导出
4. 读取 manifest，上传并下发 PNG/GLB/说明内容
5. 对主候选做 ALIGNN 性质补全（formation energy / band gap / bulk / shear 等）
6. 输出最终需求-结果对照总结

## 3. 代码结构（已拆分后的职责）

### 3.1 入口与编排

- `main.py`
  - FastAPI/WebSocket 服务入口
- `team_config.py`
  - 兼容桥接入口（转发到 `src/team_config.py`）
- `src/team_config.py`
  - 业务总编排（Role/Action、流程控制、前端时序）

### 3.2 已拆出的 utils / tools 模块

- `src/utils/subprocess_runner.py`
  - MP 脚本流式执行与进度事件封装
- `src/utils/alignn_runner.py`
  - ALIGNN 执行、模型回退、预测值解析
- `src/utils/material_candidate_extractor.py`
  - 候选提取（targets/in-LS）
- `src/utils/material_candidate_selector.py`
  - 候选筛选与 MP token 构建（规则 + LLM）
- `src/utils/formula_utils.py`
  - 化学式标准化、判定、提取文本构建
- `src/utils/team_config_runtime_helpers.py`
  - 输入归一化、route 解析、进度条等通用运行时 helper
- `src/tools/team_config_helpers.py`
  - repo 路径、case root、文本安全转换等 helper
- `src/roles/mns_role_prompts.py`
  - Prompt 常量（已从主编排文件迁出）

## 4. 目录与产物约定

### 4.1 检索与计算产物

主要落盘目录：
- `src/MNS_CaseHub/cases/material_discovery_demo/results/`

常见子目录（按 pipeline）：
- `results/mp/...`
- `results/in-LS/...`

### 4.2 前端静态资源

- 推荐静态示意图目录：`public/databasepic/`
- `main.py` 已挂载 `/public` 静态目录

## 5. 关键环境与配置

### 5.1 配置文件

- `config/config.yaml`
  - `BACKEND_URL`
  - `SOURCE_CODE_PATH`
  - `base_url_1` / `api_key`（LLM 调用）

### 5.2 运行环境（当前代码会引用）

- `mp-api-py311`
  - 用于 MP 检索脚本执行
- `ALIGNN_ENV`（可选，默认 `alignn-gpu-test`）
  - 用于 ALIGNN 预测执行

### 5.3 常用环境变量（按需）

- `ALIGNN_ENV`
- `ALIGNN_TIMEOUT_SEC`
- `GLB_PUBLIC_BASE_URL`
- `IMAGE_PUBLIC_BASE_URL`
- `PICTURE_PUBLIC_BASE_URL`
- `MINIO_ENDPOINT`（如启用对象存储上传）

## 6. 启动方式

开发态可直接运行：

```bash
python main.py
```

或使用脚本：

```bash
bash start.sh
```

## 7. 协议与输出说明（概览）

WebSocket 侧主要发送两类信息：
- `progress`：流程进度事件
- `asset`：图片/GLB 等资源下发

右侧内容区使用内容分段标记：
- `<<<CONTENT_START:STEP_ID>>>`
- `<<<CONTENT_END:STEP_ID>>>`

## 8. 近期重构状态（已完成）

已完成：
- MP 执行链路下沉到 `subprocess_runner.py`
- ALIGNN 执行链路下沉到 `alignn_runner.py`
- 候选选择逻辑下沉到 `material_candidate_selector.py`
- Prompt 常量迁移到 `src/roles/mns_role_prompts.py`
- 删除多段不可达历史 dead code（不影响功能）

当前效果：
- `src/team_config.py` 已从超大单体持续瘦身
- 主链路行为保持不变，维护性提升

## 9. 后续建议（可选）

若继续收敛 `src/team_config.py`：
- 将 `send_results_to_frontend` 抽到独立模块
- 将 `_material_alignn_completion_stage` 的编排输出层拆分
- 将 `run()` 内多个 `_stream_*` 输出函数统一迁移到渲染模块

---

如果后续切换/恢复旧流程（如 ADiT/MACE），请同步更新本文档，保持“实现与文档一致”。
