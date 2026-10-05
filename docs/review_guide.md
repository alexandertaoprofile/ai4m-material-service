# 仓库评审阅读指南

这份指南帮助评审者先了解项目，再按需要进入代码。源码快照核对日期为 2026-10-04；2026-10-05 补充实际入口和运行资源说明。后续部署以[部署记录](deployment_status.md)中的日期和结果为准。

## 1. 先了解项目和版本

| 评审问题 | 阅读位置 | 重点 |
| --- | --- | --- |
| 服务分别做什么 | [仓库首页](../README.md) | 1105 查已有材料，1111 调整配比，1115 生成晶体结构，1116 检查计算与实验依据。 |
| 哪些来自线上，哪些仍在开发 | [部署记录](deployment_status.md) | 根目录保留已核对的服务器源码；开发目录保存后续更新。1116 尚需确认在线进程。 |
| 下一版实际改了什么 | [开发改动清单](development_changes.md) | 不同文件及修改目的，测试位置，需要一起更新的模块。 |
| 拉取后怎样准备环境和运行 | [服务运行与复现说明](service_setup.md)及[外部依赖说明](external_dependencies.md) | 每个服务的环境、数据与模型位置、取得方式、启动命令和最小验证。 |
| 服务器改完代码怎样同步 | [代码同步说明](code_synchronization.md) | 服务目录与发布副本的区别、文件应放的位置、提交和推送步骤。 |

评审当前维护版本用 `main`。`backup` 是固定的历史代码。历史交接文件保留当时的情况，查看当前服务时优先使用上表中的说明。

## 2. 代码的阅读顺序

先读运行说明，明确所需资源和启动方式，再看请求进入后的实际调用。各服务 main.py 是 HTTP/WebSocket 入口；team_config.py 是编排或角色适配文件。当前调用关系有差别：

```text
1105 HTTP 查询 / WebSocket
  main.py → team_config.MaterialMature → catalog / fluid_lubricant

1111 HTTP / WebSocket
  main.py → alloy_workflow.runtime.RUNTIME → 专项计算与报告
1111 Alpha Role
  team_config.AlloyCompositionOptimizationRole / Coding → 同一 RUNTIME

1115 WebSocket
  main.py → team_config.Service / Team / Role → run_upstream_request
1115 HTTP 生成
  main.py → run_upstream_request → pipeline → omatg_pipeline

1116 HTTP / WebSocket
  main.py → team_config.execute_refractory_validation → validation_workflow

业务计算读取对应数据及模型，再生成报告和文件，通过原接口交付。
```

| 服务 | 从哪里开始 | 接下来读什么 |
| --- | --- | --- |
| 1105 已有材料查询 | [服务 README](../material_database/README.md)、[请求入口](../material_database/main.py)、[执行流程](../material_database/src/team_config.py) | `src/catalog/` 的查询与条件比较，再看报告模块；导电润滑剂有独立的内部查询流程。 |
| 1111 配比优化 | [服务 README](../material_composition_optimization/README.md)、[请求入口](../material_composition_optimization/main.py)、[运行模块](../material_composition_optimization/src/alloy_workflow/runtime.py)；Role 调用再看[角色适配](../material_composition_optimization/src/team_config.py) | `src/alloy_workflow/` 的输入校验、类别选择、计算程序和报告；具体阅读路径见服务内代码审阅指南。 |
| 1115 晶体结构生成 | [服务 README](../material_discovery/README.md)、[请求入口](../material_discovery/main.py)、[WebSocket 编排](../material_discovery/src/team_config.py)、[共用业务入口](../material_discovery/src/material_workflow/upstream_api.py) | `src/material_workflow/` 的输入、OMatG 执行流程、结构评价和报告，再看 `tools/` 中的独立模型调用。 |
| 1116 计算证据核验 | [服务 README](../material_validation/README.md)、[请求入口](../material_validation/main.py)、[执行流程](../material_validation/src/team_config.py) | `src/application/` 的四阶段流程、`src/infrastructure/` 的数据读取及 `src/presentation/` 的报告。 |

`alpha/`、旧英文流程和历史启动文件是保留的兼容内容。检查某项功能从上述入口开始；删除兼容文件前先核对是否仍有代码或外部程序调用。

## 3. 测试结果怎样阅读

2026-10-04 已执行的源码核对和测试记录在[目录检查记录](MAIN_LAYOUT_VERIFICATION_20261004.md)。其中 1111 根目录测试为 264 项通过，开发副本为 271 项通过；1115 根目录和开发副本分别按已有测试模块检查。模拟模型调用的测试主要验证输入、流程、异常处理及报告，真实计算和网页显示另做检查。

评审时可先运行文档、文件差异和源码记录检查。以下命令在仓库根目录执行，需要 Python 3.10 或更高版本、Git；文档内 shell 示例的语法检查还需要 Bash。

```bash
python3 tools/verify_documentation.py
python3 tools/compare_service_versions.py
python3 tools/verify_repository_layout.py
```

`verify_repository_layout.py` 核对的是 2026-10-04 整理时保存的源码记录和开发来源。服务代码后来升级时，应保留旧记录，并为新版本建立对应记录；出现差异时先查改动来源。各服务测试命令见其 README，需要已准备好的服务或计算环境。

本仓库目前没有 GitHub Actions 自动检查。上述检查和服务测试手动执行，结果记录在交接文档中。以后加入自动检查时，先使用无需模型生成和生产凭据的代码、文档及接口测试。

## 4. 后续值得整理的地方

| 事项 | 原因 | 处理建议 |
| --- | --- | --- |
| 较长的报告模块 | 1105 和 1111 的部分展示文件超过千行，后续维护时阅读成本较高。 | 在开发目录按报告章节或材料类别逐步拆分，保持现有接口和展示结果，并补相关测试。 |
| 重复的完整服务副本 | 开发副本保留完整代码，便于一起测试，也会使文件搜索出现重复结果。 | 用开发改动清单集中记录差异；部署后及时更新状态，不再为同一任务添加第三份完整副本。 |
| 仓库外的正式运行代码 | 1111 的部分计算和展示模块仍在服务器其他目录中。 | 先明确版本和调用关系，再单独安排纳入版本管理；当前路径保持不变。 |
| 新机器安装验证 | 已记录部分环境和模型版本，仍缺经过验证的完整重建流程。 | 按外部依赖说明准备资源，在独立环境测试安装和真实请求，记录成功命令后再作为安装指南。 |

这些事项按任务逐项实现、测试和验收，进度与结果写入开发说明及部署记录。
