# 历史任务产物目录

此目录不再包含或承载可执行的服务管线。无机新材料服务的实际入口是仓库根目录
`main.py`，实际编排层是 `src/team_config.py`，领域流程位于
`src/material_workflow/`。

这里仅因已部署路径和历史任务兼容而保留：

- `results/new_material/<taskid>/`：任务 manifest、CIF、日志和展示资产；
- `public/`、`registry/` 的历史兼容数据。

任务产物根路径只能通过 `src/service_paths.py` 的
`NEW_MATERIAL_RESULTS_ROOT` 获取。不要在新代码中重建路径，也不要在这里恢复旧
`handler.py`、`pipeline.py` 或 MP/ALIGNN 案例脚本。
