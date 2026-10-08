# 合金与玻璃 Transformer + NSGA-II：服务器接入交接

日期：2026-09-28。用途是维护人员审阅、合并影子候选，不是正式预测放行证明。

## 一、交付位置与范围

75 上的完整验证目录：

```text
/data/se42/experiments/material_release_validation_20260928/
```

`RELEASE_REVIEW.md` 是最终结论；`material_shadow_release_20260928.zip` 是内部交付包；`PACKAGE_MANIFEST.json` 是包内文件哈希清单。包包含合金 v10 和玻璃 strict v1 权重、预处理、原始成分推理封装、NSGA-II、六个 1111 候选文件、测试及报告。已有专项模型继续从当前 `hea_surrogate` 读取，66 个外部依赖文件用 SHA-256 固定；所以该包针对这台服务器，不是可以在任意机器直接运行的容器。

本次没有改生产仓库、正式权重、正式排序或用户页面；没有重启服务、创建 tmux、推送 GitHub、调用真实上传链或外部生成模型。

## 二、模型分工

| 服务 | 新模型后台计算 | 保留专项性质 | 后台优化 | 这次对用户结果的影响 |
|---|---|---|---|---|
| 航空铝合金 | 合金 v10 的 YS、UTS | 延伸率、锚点和原适用范围 | NSGA-II | 无 |
| 奥氏体不锈钢 | v10 的 YS、UTS，293 K 及以上；越界只能诊断 | 延伸率与深冷证据层 | NSGA-II，禁止深冷自由配比优化 | 无 |
| 镍基高温合金 | v10 的 YS、UTS，明确多阶段热处理 | 延伸率、蠕变断裂寿命 | NSGA-II | 无 |
| 铜基热端 | v10 仅供私有优化研究，不作为获准性质替换 | 现役局部配比模型 | NSGA-II 私有研究 | 无 |
| HEA/MPEA | v10 拉伸仅供私有优化研究 | 屈服、硬度、相分类；硬度不冒充 UTS | NSGA-II 私有研究 | 无 |
| 芯片玻璃 | 玻璃 strict v1：20°C 密度、杨氏模量 | 0–300°C 平均 CTE、应力光学系数、黏度特征温度 | NSGA-II | 无 |
| 短碳纤维复合材料 | 不使用元素组成 Transformer | RVE 专项的 9 项正交各向异性弹性常数 | NSGA-II，局部体积分数/长度/取向 | 无 |
| 钙钛矿 | 不变 | 现有输运/稳定性模型与规则 | 不接此 NSGA-II 连续成分优化器 | 无 |
| 钛、高强钢/马氏体时效钢 | 离线研究，不新增公共服务 | 不新增上线路线 | 不新增公共优化路线 | 无 |

这里的“Transformer + 专项”是按性质分工，不是把树模型和 Transformer 的同一性质预测任意平均。

## 三、真实接口和单位

### 3.1 合金原始输入

唯一公开给内部调用者的输入是原始成分和明确条件，不是用户手写的 113 维张量。推理入口为本目录 `runner.py`，批量请求示例见 `alloy_v10_health_request.json`。

```json
{
  "requests": [{
    "request_id": "internal-example",
    "domain": "stainless",
    "composition": {"Fe": 72.0, "Cr": 18.0, "Ni": 10.0},
    "composition_basis": "wt_percent",
    "temperature": {"value": 293.15, "unit": "K"},
    "process": {
      "text": "form=1|wq=1.0|aq=0.0",
      "parameters": {"solution_temp_C": 1050.0, "solution_time_h": 1.0}
    }
  }]
}
```

这是接口形状示例，不是已获准制备的配方。输出为 `yield_strength_0p2_MPa` 与 `ultimate_tensile_strength_MPa`，同时保留实际编码的原子分数、温度、工艺证据、冲突、缺失状态和诊断标志。缺失温度在研究回放中有独立掩码，但影子服务不把未知温度当成确定室温。缺失 P/S/N 不得自动补成残余元素，也不得为了通过距离阈值伪造数值。

镍基 `process.v6_stages` 必须是冻结训练解析器可识别的明确格式，例如：

```text
solution_stage_1_temp_C=1200; solution_stage_1_time_h=4; precipitation_stage_1_temp_C=850; precipitation_stage_1_time_h=8
```

不能只给 `STA`、`cast`，再声称模型已考虑具体固溶/时效条件。无法可靠解析的简写被拒绝，而不是静默丢掉热处理。

### 3.2 玻璃原始输入

入口为 `oxide_glass_shadow_runner.py`；使用 `composition_mol_percent`，不能直接把 wt.% 拼入 mol.% 模型。严格影子接口暂只准入 `DENSITY_20C` 和 `YOUNG_MODULUS`。示例和固定健康参考分别是 `glass_shadow_example_batch.json`、`glass_shadow_health_expected.json`。

密度输出单位 `g_cm-3`，模量输出 `GPa`。固定温度 CTE、低于 Tg 的 CTE 和 0–300°C 平均 CTE 不是同一个任务；不能用名字相近代替定义核对。热导率仍不进入该模型的服务性质或优化目标。

### 3.3 优化请求

入口为 `optimization_runner.py`。必须同时带上现役服务解析后的 `scope` 与相同域的专项结果 `specialist_result`。目标可以明确写成：

```json
{"property": "YS_MPa", "mode": "target", "target": 1000, "scale": 100, "unit": "MPa"}
```

这表示接近 1000 MPa，不是越高越好。`min`、`max`、`target` 三种语义分开；单目标不自动添加成本、密度或另一个强度目标。多目标返回 `pareto_front`；没有用户偏好时，不宣称某个方案是唯一最优。约束无解时返回 `no_feasible_candidate` 和空前沿，不放宽门槛凑出推荐。

## 四、合并和影子启动顺序

以下是维护人员获批后的操作清单，本次未执行合并或启动。

1. 审阅 `RELEASE_REVIEW.md`、`1111_shadow_candidate.patch`、`MERGE_PLAN.json`。只涉及 `src/alloy_workflow` 的六个文件，不改业务路由、页面标题、资产上传或用户角色。
2. 在本目录运行 `preflight_release.py` 和 `apply_shadow_candidate.py` 的默认只读检查。任何现役文件或依赖哈希变化都应停下来重新核对，不能强行覆盖。
3. 得到明确部署批准后，维护人员才运行 `apply_shadow_candidate.py --apply`。它先检查全部目标文件的基准哈希，保留备份，不重启、不提交、不推送。
4. 使用既有服务启动方式设置下列影子参数，不增加带有工具名称的新 tmux 会话。
5. 先低并发开启模型影子，再开启优化影子。上线后核验实际任务的私有清单、队列拒绝数、超时率、端到端延迟和资源占用；这是离线测试不能替代的验收。

```text
ALLOY_V10_SHADOW_RUNTIME_ROOT=/data/se42/experiments/material_release_validation_20260928
ALLOY_V10_SHADOW_PYTHON=/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python
ALLOY_V10_SHADOW_ENABLED=1
GLASS_TRANSFORMER_SHADOW_RUNTIME_ROOT=/data/se42/experiments/material_release_validation_20260928
GLASS_TRANSFORMER_SHADOW_PYTHON=/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python
GLASS_TRANSFORMER_SHADOW_ENABLED=1
NSGA_SHADOW_RUNTIME_ROOT=/data/se42/experiments/material_release_validation_20260928
NSGA_SHADOW_PYTHON=/data/se42/experiments/patent_optimization_v0_20260922/.venv/bin/python
NSGA_SHADOW_ENABLED=1
```

默认不配置或设置为 `0` 即保持关闭。各影子计算落在 `results/<taskid>/shadow/<channel>/<batch-id>/`，不进入正式结果对象、Markdown、PNG、排序或 MinIO 发布。

回滚第一步是把三个 `*_SHADOW_ENABLED` 置 `0`，按原来的服务管理流程更新进程环境。旧模型、旧输出接口和已有影子链未被删掉。若要撤销代码，按 `merge_backup` 恢复该次六个文件；不得回退无关团队修改。

## 五、资源与停机规则

默认种群 32、实际送入预测器的配方预算 256、优化器提案上限 20,480、算法循环软时间 30 s；外层整个子进程硬超时 60 s，包含模型加载、初始化及内部专项子进程。软时间可能超过一批推理时间，硬超时负责兜底。

计数是配方行而不是性质头；重复配方缓存不增加预测预算；最后一批也不能超过预算。NSGA-II 初始化仍有随机性，优化过程依赖交叉、变异、非支配排序及拥挤距离，不是解析逆解。它不保证比所有随机基线更快、更好，也不保证全局最优。

每个优化域最多一个后台 worker、队列容量 2；模型影子队列容量 8。超时杀死该任务拥有的整个进程组，不能留下内部专项 worker；满队列跳过影子，不拖垮正式请求。

专属模型按保存版本隔离：铜和玻璃用 sklearn 1.8；铝、不锈钢、镍基、HEA、短碳纤维用 sklearn 1.7.2。worker 遇到跨版本反序列化警告会失败关闭，不能静默屏蔽警告继续预测。HTTP 环境不需要安装深度学习模型依赖。

## 六、正式转正不能仅靠完成可视化

新封装没有 `primary` 放行模式。转正必须按“材料域 × 性质”重新审查：独立来源/局部子群成绩、微量元素与工艺语义、经过验证的适用范围、异常标签复核、许可证、目标函数与制备可行性，以及实际线上负载和资产发布验收。

通过后才增加受控的性质替换，保留原字段名并更新对应图表、排序、性质来源和验证说明。不能保留旧树模型的标准差作为 Transformer 的不确定度，不能写 `±0`，不能把训练集 MAE 当作单配方误差区间，不能把 Pareto 解当作实验确认。

因此本包足以开始有回滚能力的影子合并；它不授权所有材料或所有性质一次性正式替换。
