# 数据、代码与模型来源说明

本包为内部候选模型和验证交接，不含 SciGlass 原始或衍生数据库、不含商业训练许可已经获批的声明。

| 项目 | 本次使用的来源 | 处理和注意事项 |
|---|---|---|
| 合金 v10 | `FREEZE.json` 所列 v10 单权重、预处理及 v8 修复表 | 未修改权重和原表；重新核验全部原始输入与训练编码的一致性。上游逐条文献和许可仍须数据负责人补齐 |
| 玻璃 strict v1 | 已有严格 SciGlass 观测表，固定超参数，来源留出且清除重复成分 | 修复原 v0 数值缩放使用测试成分的泄漏；新权重和审计有独立哈希 |
| 专项性质模型 | 当前 `hea_surrogate` 的版本化制品 | 不复制成新训练数据；保留原性质定义和适用边界。依赖哈希见 `EXTERNAL_DEPENDENCIES.json` |
| NSGA-II | pymoo 0.6.2 与本次预算/语义封装 | 固定预测器下的数值搜索；非实验制备验证，不构成许可证授权 |

SciGlass 官方许可文件含 `ODC Open Database License (ODbL)` 标题和 MIT 式许可正文，GitHub 自动标签也显示 MIT，存在表述不一致。保守按 ODbL 的数据库义务审查，保留 `Copyright (c) 2019 EPAM Systems` 和来源，但不能据此自动认定数据档案、衍生数据或商业模型发布的所有权利均已确认。正式部署前应由负责人/法务确认具体数据版本、署名和衍生数据库义务。代码许可与数据库内容权利也不能互相替代。

可核对的官方依据：[SciGlass 许可文件](https://github.com/epam/SciGlass/blob/master/LICENSE)、[ODbL 1.0 正式条款](https://opendatacommons.org/licenses/odbl/1-0/)。

优化器技术定义参考：[pymoo NSGA-II](https://pymoo.org/algorithms/moo/nsga2.html)、[终止条件](https://pymoo.org/interface/termination.html)。

许可检查不等于预测精度检查，也不等于上线批准。本包及新模型未上传公开仓库。
