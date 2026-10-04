# 1115 OMatG 筛选图：面向非专业用户的展示调整

本改动仅在 `/data/se42/omatg_local/1115_shadow_chart_20260929/service` 验证。线上服务、原始计算结果、生成与筛选条件、性质模型、排序及事件协议保持不变。

## 展示原则

- 标题先说生成数量与满足全部初筛条件的方案数量，下一行给出实际建议验证对象。
- 展示固定化学式或允许元素范围；DNG 分别展示必含元素与最低元素种数。
- 六个步骤分别为生成方案、元素符合要求、原子排布通过、结构优化完成、稳定性有结果、满足初筛条件。每步计数从保存的候选及其对应验证记录计算。
- 左侧按实际结果解释其余方案的去向：条件未通过、计算结果待补充、相似结构合并互相区分，同一候选只计一次。完整失败和缺失项仍保留在原报告明细表中。
- 右侧用中性中文元素名称、原始化学式及候选编号共同标识方案。只展示对应结构已收敛的候选，缺失稳定性结果明确标注待补充。
- 保留真实能量值、单位与原筛选上限。零值显示圆点，不画假长度柱子；低于稳定性上限也不自动改为通过其他条件。
- 保留既有 `candidate_comparison.png`、`MaterialsPNG` 资产类型和资产生成/消费路径，只改善图片内容与客户可见名称。

实现文件：`src/material_workflow/omatg_chart.py`、`tools/render_new_material_assets.py`；离线预览工具为 `tools/preview_omatg_chart.py`。

## 真实记录核对

| 保存的任务 | 六步计数 | 条件未通过 | 计算待补充 | 相似结构合并 |
| --- | --- | ---: | ---: | ---: |
| diversity-default-s51-r02 | 10 → 10 → 7 → 6 → 6 → 1 | 8 | 1 | 0 |
| output-open-s42-full-20260929-r01 | 10 → 10 → 9 → 5 → 4 → 2 | 3 | 5 | 0 |
| diversity-ternary-s51-r01 | 10 → 10 → 6 → 5 → 5 → 0 | 9 | 1 | 0 |

原始任务均位于 `/data/se42/omatg_local/1115_shadow_diversity_20260929/results/`。重绘目录为新影子目录下的 `previews/restricted`、`previews/open`、`previews/no_pass`。没有重新调用 OMatG、MatterSim 或 ALIGNN 推理，也没有上传公开资产。

每个预览目录的 `chart_data.json` 保存源 manifest 的 SHA256、图表数据及 `scientific_result_changed=false`、`uploads=false`。预览工具重绘后检查源文件字节保持一致，原图与原报告仍保留。

## 验证

- API 环境全套：**148 passed, 16 skipped, 2 warnings, 51 subtests passed**。
- 已有可视化环境：`test_omatg_chart.py`、`test_structure_visualization.py`，**28 passed, 72 subtests passed**。
- OMatG 科学环境：图表数据、几何、采样和 CPU/CUDA worker 测试，**39 passed, 9 warnings, 15 subtests passed**。
- API 环境跳过的 10 个可视化、4 个几何、2 个 worker 测试分别在相应已有环境中运行通过。三张真实结果图已逐张检查，包含没有通过初筛的情况；另有十行候选文字边界检查。
- 第一次全套回归因未设置客户端初始化键，三个角色初始化测试报错；后续仅设置非真实测试占位值 `OPENAI_API_KEY=shadow-test-placeholder` 后通过，没有使用真实密钥或调用外部模型。

复现示例（只写独立影子预览目录）：

```bash
cd /data/se42/omatg_local/1115_shadow_chart_20260929/service
PYTHONPATH=. /data/mamba/envs/mattergen-py310/bin/python tools/preview_omatg_chart.py \
  --manifest /data/se42/omatg_local/1115_shadow_diversity_20260929/results/output-open-s42-full-20260929-r01/new_material_pipeline_manifest.json \
  --output-dir /data/se42/omatg_local/1115_shadow_chart_20260929/previews/open
```

这里的 Python 环境名称沿用旧命名，本次用于现有绘图依赖，不运行 MatterGen。

## 后续接入

图片由既有 `render_new_material_assets.py` 入口生成；不需要前端新增事件或新的图表协议。本图表补丁以前一版 `1115_shadow_diversity_20260929/service` 为基线，只适用于已包含该版 OMatG 改动的代码。部署前需在确认后的目标代码上先执行 `git apply --check`，保留同事的未提交变更；本次没有执行线上应用或重启。
