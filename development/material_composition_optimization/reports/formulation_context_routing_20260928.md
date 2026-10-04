# 1111 材料配方优化：上游调用与材料上下文衔接

日期：2026-09-28。

代码目标目录：`/data/se42/alpha_project/material_service_hub/material_composition_optimization/`。

验证与备份目录：`/data/se42/experiments/formulation_routing_context_20260928/`。

## 本次改动

- `/roles` 的 profile、desc、goal、match_when 仅说明材料配方/配比/组分优化能力、适用范围与输出，不包含材料名称从何处恢复或用户是否需要重复名称等输入解析细节。这些工作由服务内部处理。
- 加入“配方优化、配方设计、配比设计、组分优化、组分设计、调整组分比例、调整配方、优化配方”关键词。
- 不再用整个材料家族排除通用配方任务。八个实际模型的输入范围、适用域和模型清单没有扩展；未训练家族只能提供规则辅助试验设计。
- 对“上述材料、上一步选择的材料、继续”和没有重复材料名的配方任务，读取已有上游材料选择结果或最近相关上下文。
- 当前用户明确换材料时，不继承旧材料。一般助手能力介绍、拒绝文字和系统消息不作为材料依据；明确的上游材料选择可在用户引用时使用。
- 多个未选定候选或没有可用材料身份时，复用既有 `waiting_for_input` / `routing_confirmation` 结果，不生成虚假的合金模板或性能数值。
- 上游已选材料记录中，只有本服务已经接受的明确字段可继承；当前显式输入优先。不推断缺失成分，不执行单位转换。
- 收紧普通玻璃、连续纤维/环氧体系和常规钛合金的误路由，防止分别误用芯片玻璃、短碳纤维热塑性或 HEA 专项模型。

## 推荐上游输入

优先传结构化的已选材料，而不是整个历史报告或所有候选。下面沿用已有 `material_conclusion` 字段，不新增必需角色契约：

```json
{
  "idea": "针对上述材料做一个配方优化",
  "material_conclusion": {
    "selected_material": {
      "material_family": "芯片封装玻璃基板",
      "material_name": "低硼无碱铝硼硅酸盐"
    }
  },
  "constraints": {
    "oxide_bounds_mol_percent": {
      "B2O3": [0, 3]
    }
  }
}
```

也兼容 `material_conclusion.summary`、`upstream_result` 中的已选材料、最近用户历史、明确“已选定/优先选择”的上游结论，以及现有母服务文本分区。当前有正式模型域映射时，可继续传 `constraints.model_domain`，无需再从文本猜测。

仅把“上述材料”一句传给 1111、却不传任何材料选择结果或相关上下文，服务无法恢复被省略的材料身份，会请求确认。

当前没有新增“同时优化任意多个材料”的执行器；多个未唯一选择的材料会请上游明确对象或拆成分别调用。

## 兼容性

保留 `/roles`、`/start`、`/alloy/start`、REST 路径、`alloy_composition_optimization_v1`、`AlloyCompositionOptimizationRole`、`Coding` 和 `FILAMENT_SELECTION_OPTIMIZATION`。没有新增事件类型或等待状态；模型权重、适用域阈值、候选优化算法及正式/影子切换配置均未修改。

## 验证

改动前原有测试：**61 passed**。

隔离候选代码完整测试：**114 passed**（原有 61 项 + 新增 53 项）。新增测试覆盖八个域的材料引用、上下文优先级、结构化选择、助手/工具明确选择、Alpha 消息适配、错误模型边界和直连/Agent/REST 等待结果。

合并后服务器正式代码目录复跑：**114 passed，24.48 秒，0 失败**。报告：`/data/se42/experiments/formulation_routing_context_20260928/server_regression.xml`。13 项警告为既有字符串转义和 Google SDK / Python 弃用提示，未改变这些无关代码。

测试使用现有服务 Python 3.10 环境，在独立目录复用已有 pytest 纯 Python 依赖，没有安装或修改生产环境依赖。

```bash
cd /data/se42/alpha_project/material_service_hub/material_composition_optimization
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:/data/se42/experiments/formulation_routing_context_20260928/test_dependencies \
/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python -m pytest -q tests
```

本次不导入数据，数据目录及样本数量未改变。两个既有 Google SDK / Python 版本弃用警告不属于本次失败，后续环境升级可单独处理。

## 合并与生效

合并脚本先核对七个原文件的 SHA-256，再将原文件备份到 `prechange_backup/`，最后仅合并七个改动文件与一个新增测试文件；保留同事已有未提交改动。没有创建提交或推送 GitHub。

没有重启当前 1111，也没有修改 tmux。代码合并不等于运行中的进程已加载新版本。

合并后只读检查：当前 `/health` 为 `ok`；运行进程的 `/roles` 仍未包含新增“配方优化”关键词，确认它尚未加载本次代码。没有把代码级测试通过写成已经上线生效。

生效时由服务负责人按现有启动方式重启 **1111 对应进程**，并让母服务重新发现 `/roles`（若其缓存角色描述，需要刷新该缓存）。不要通过改内部角色 ID 强制刷新。

重启后的检查：

1. `/roles` 包含“配方优化”，profile/desc 明确支持上游材料上下文。
2. `/alloy/requirements/preview` 对上面的玻璃示例返回 `chip_glass_thermomechanical_family_v1`。
3. 上游不写材料名、但携带已选不锈钢或玻璃的任务，分别进入相应模型；未提供材料身份时返回确认问题。
4. 用母服务执行一次完整角色选择与调用，确认其加载的是新 roles。当前测试证明子服务解析与协议正确，不替代母服务的真实任务选择验收。
