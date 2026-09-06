# material_validation

难熔金属跨尺度性能计算与验证服务。首版仅开放 W / W-14 标杆案例的证据读取与四阶段结果编排：材料任务定义、DFT 基准、MLIP 与 MD、实验/文献对标。

它不把 DFT 当作实验真值，也不将 MLIP 误差和实验误差混为一类。DFT 数据、训练指标、冻结势、MD 输出和文献来源必须分别归档，缺失项将明确显示为待补充。

## 开发运行

```bash
cp .env.example .env
python main.py
python -m unittest discover -s tests -v
```

默认仅读取 `data/reference_cases/w14_phase_i/evidence_manifest.json`，不会启动 DeepMD 或 LAMMPS。将完整证据放入 `raw/`、`models/`、`literature/` 后，服务会记录其 SHA-256；真实执行器仍需单独配置和验收。

## 接口

- `GET /health`、`GET /roles`（默认端口 `1116`）
- `POST /refractory-validation/requirements/preview`
- `POST /refractory-validation/evaluate`
- `WS /start`、`WS /refractory-validation/start`

## 用户可见结果结构

页面使用与 1111 / 1115 一致的流式 Markdown 内容块和最终 `result` 事件：先展示本次实际配置的计算方法定义，再展示跨尺度验证结论、四阶段证据状态、性质对标表和可信度判断。图表只在相应原始数据已归档时生成，计划包含 DFT–MLIP parity、误差分布、温度—性质曲线与 MD—实验对标图；没有实际数据时不展示空白或示意科研图。
