# 1115 OMatG 版本部署与回退

## 版本状态

2026-10-04 正式仓库代码整合 OMatG 接入与已验证的后续显示改进；Git 归档与推送不触发部署。75 服务器此前已切换为 OMatG，最近的显示流程与异常原因改进仍需按以下清单单独验收部署。

正式代码分支：`update/material-services-2026-08`。备份分支 `main` 保留原有内容。
当前生产目录：`/data/se42/alpha_project/material_service_hub/material_discovery`。
整合验证目录：`/data/se42/worktrees/material_service_hub_release_20261004/material_discovery`。
显示改进的原始影子目录：`/data/se42/omatg_local/1115_display_shadow_20261001/service`。

生产目录还保留先前的本机编辑；禁止在其中直接 `git pull` 或用整目录覆盖。先核对版本差异、备份目标文件并保留其他人的改动，再实施经批准的增量部署。

## 生成与评价链

- 确定化学式走 CSP；元素范围或已确认的开放化学空间走 DNG。
- 默认每阶段 10 个候选、每胞最多 20 个原子。DNG 直接评价已生成的结构；追加 CSP 仅由用户明确开启。
- DNG 元素域由本地掩码适配器实现，另检查必含与排除条件；生成后保留组成核验结果。
- 周期几何检查后，MatterSim 优化晶格及坐标，再以只读 MP2020 数据作混合参考凸包估算；ALIGNN 从实际保留结构预测已接入性质。
- `0.05 eV/atom` 默认作为稳定性复核参考线，超线仍继续评价。用户明确给出的硬性上限另行执行。
- 当前采用 OMatG 预训练推理和后处理，IRL、PropMolFlow、DFT、有限温自由能与声子计算尚未并入此主链。

## 资源与配置

配置变量与服务器路径见 [../.env.omatg.example](../.env.omatg.example)；外部接口与密钥变量见 [../.env.example](../.env.example)。

主服务复用 `ai4m-service-py310`；生成使用 `/data/mamba/envs/omatg-py312/bin/python`；MatterSim 使用现有 `mattergen-py310` 数值环境；ALIGNN 采用健康的 `dsc2` 解释器与已缓存的属性头。

模型源码与权重、参考库、缓存和临时文件均留在 `/data`，不随 Git 分发。复用 `/data/mattersim_reference_cache/reference_MP2020correction`，避免重复解压。介电属性头以本机可用权重为准；缺失时保留真实状态，不以其他性质代替。

只读环境预检：

```bash
cd /data/se42/worktrees/material_service_hub_release_20261004/material_discovery
/home/ubuntu/miniconda3/envs/ai4m-service-py310/bin/python tools/preflight_omatg.py --env-file .env.omatg.example
```

## 获批后的部署检查

1. 读取 `/data/se42/AGENTS.md`，确认目标仅为1115，并与弹窗调用方核对最新字段与协议。
2. 保存目标源文件快照、当前进程与启动命令。生产 `.env` 单独安全备份，不打印、不入 Git。
3. 从正式分支形成明确文件清单和增量补丁，先检查冲突。保留 `.env`、端口、存储和同事新增配置，按需要合并专项变量。
4. 执行 README 所列回归与只读预检，通过后按原 tmux 启动方式仅重启1115。
5. 检查 `/roles`、CSP/DNG 约束预览、缺少输入、冲突输入、重复任务标识以及补充后的原需求保留。
6. 从实际前端各提交一个 CSP 与 DNG；确认默认 DNG 未追加 CSP，必含/禁止元素、数值上下限与开放授权按实际条件执行。
7. 对照原始 manifest 检查默认稳定性参考线、真实预测值、未收敛与计算失败原因。报告、比较图、旋转 GIF 与 GLB 必须指向同一候选编号。
8. 验收最终流式 Markdown：章节、公式、表格、图片正常；保留既有 `FILAMENT_SELECTION_OPTIMIZATION`、CONTENT 标记和事件类型；报告与资产交付后再发完成事件。
9. 留下本次部署提交、PID、验收任务标识及差异清单，再确定上线完成。

## 回退

显示改进回退优先恢复部署前明确目标文件的快照，只重启1115；已有新编辑时先合并，不能整仓库复位。

旧 MatterGen 后端及其资源保留。经批准可将 `NEW_MATERIAL_GENERATOR=mattergen` 切回旧生成链，并单独验证角色、请求与前端协议。切换后端不等于恢复旧显示代码，二者需分别确认。

模型与任务结果不删除；生产目录、系统盘和其他服务不作为清理目标。
