# 在新机器恢复和运行材料服务

本指南面向 Linux 上的独立服务副本，按“源码 → 环境 → 资源 → 配置 → 检查 → 启动”操作。四个服务可以分别准备。当前版本的检查结果见[复现验证记录](REPRODUCTION_VERIFICATION_20261008.md)。

## 1. 准备机器和源码

Web 服务及 1111 数值计算以 CPU 为主；1115 结构生成和 MatterSim 使用 NVIDIA GPU。已测试硬件为 RTX 4090 24 GB，驱动 550.142。先用 `nvidia-smi` 确认驱动可用，再测试各模型环境中的 `torch.cuda.is_available()`。

准备 Git、GitHub CLI、Python 或 micromamba，以及中文字体。1111 图表优先读取 Linux 的 `/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf`，Debian/Ubuntu 可由 `fonts-droid-fallback` 提供。环境、下载、缓存和运行结果放在数据盘；完整安装建议预留至少 60 GB，多个环境的 CUDA 库会占用较大空间。

```bash
mkdir -p /data/material-repro
gh auth login
gh repo clone AI4SciFoundation/Material-Computation /data/material-repro/Material-Computation
cd /data/material-repro/Material-Computation
git checkout runtime-20261008
```

Release 标签同时固定代码和资源版本。平时评审最新代码可以看 `main`；复现一个已交付版本使用对应标签。

## 2. 安装所需环境

使用 Python 3.10 或更新版本运行安装工具。脚本默认显示命令计划，增加 `--execute` 才会安装。默认需要 micromamba 在 PATH 中，由它创建表中指定版本的 Python；也可以用 `--python /绝对路径/python` 指定相同主次版本的已有解释器，创建独立 venv。新环境不会继承原解释器的第三方包。

| 环境名 | Python | 哪些服务需要 | 工作内容 |
| --- | --- | --- | --- |
| `web` | 3.10 | 四个服务 | FastAPI、Alpha、需求解析、报告和接口。 |
| `numeric` | 3.10 | 1111、1115 | 专项 joblib 计算、MatterSim、结构分析与三维文件。 |
| `recipe` | 3.13 | 1111 | 固定合金/玻璃 Transformer 与 NSGA-II 优化。 |
| `omatg` | 3.12 | 1115 | OMatG CSP/DNG 生成。 |
| `alignn` | 3.10 | 1115 | 五类结构性质预测。 |

```bash
python tools/install_environment.py web --prefix /data/material-repro/envs/web --execute
python tools/install_environment.py numeric --prefix /data/material-repro/envs/numeric --execute
python tools/install_environment.py recipe --prefix /data/material-repro/envs/recipe --execute
python tools/install_environment.py omatg --prefix /data/material-repro/envs/omatg --execute
python tools/install_environment.py alignn --prefix /data/material-repro/envs/alignn --cpu --execute
```

只运行 1105 或 1116 时准备 `web` 即可。上述 ALIGNN 命令采用已测试的 CPU 预测环境，结构生成和松弛仍使用 GPU。安装需要访问 conda-forge、Python 软件包索引、PyTorch 与 PyG 的官方轮子站点。普通包可用 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple` 切换镜像。安装被网络中断后使用 `--resume` 继续已创建的环境；ALIGNN 恢复安装时同样保留 `--cpu`。

[环境原始记录](../env/current/installed-20261008.json)保留服务器的全部安装元信息；[独立环境记录](../env/validated/installed-20261008.json)与 `env/validated/*.txt` 固定本次实际测试的软件包组合，安装工具自动使用这些依赖版本约束。Web 环境使用 NumPy 1.26.4、python-dotenv 1.0.0，配合旧 Alpha 所调用的 Semantic Kernel 0.4.3；并自动恢复原部署的 Pydantic 兼容调整。调整前检查第三方文件 SHA256，遇到另一版本时停止并提示检查。数值模型使用独立环境，实际验证状态见检查记录。

## 3. 下载、恢复和核对资源

```bash
python tools/runtime_resources.py download --cache /data/material-repro/downloads
python tools/runtime_resources.py install --cache /data/material-repro/downloads --destination /data/material-repro/resources
python tools/runtime_resources.py verify --destination /data/material-repro/resources
```

工具先检查资源包，再恢复列入清单的文件；稳定性参考库自动拼接并检查整体 SHA256。安装时保留已有文件，不覆盖本机资源。分服务下载的方法见[资源说明](../resources/README.md)。

```bash
python tools/prepare_runtime.py --resource-root /data/material-repro/resources --env-root /data/material-repro/envs
```

这个命令组合 1111 的源码、模型和数据，复制 1105 的 SQLite 到服务读取的位置，恢复增量材料目录，并在各服务生成 `.env.runtime.example`。1105 的 `MATURE_MATERIAL_CATALOG_ROOT` 指向资源包中的完整目录快照；Git 中的历史基础 CSV 保留原版本。仅准备部分服务时增加 `--services 1115` 等参数。源码与权重在资源目录中按原相对结构组合，服务器的历史目录可与新目录并存。

## 4. 配置连接与本机路径

把相应服务生成的 `.env.runtime.example` 复制为该服务的 `.env`，再从服务原有 `.env.example` 补齐语言模型、对象存储及公开资源地址。`.env` 保存真实凭据，本机维护。

| 配置 | 作用 | 本地数值检查是否需要 |
| --- | --- | --- |
| 模型解释器、源码、权重、数据路径 | 找到实际计算资源。 | 需要。 |
| 语言模型地址、模型名、API key | 理解文字需求及组织流式报告。 | 结构化数值测试可以直接调用执行器；完整对话需要。 |
| MinIO/S3 配置、图片公开地址 | 前端获取图片和三维结果。 | 检查本地文件可以直接查看；前端交付需要。 |
| 端口、结果目录 | 服务监听和任务文件保存。 | 启动服务时需要。 |

1111 移植启动器把本机的优化与报告路径传给已有 `RecipeShadowDispatcher` 构造函数。专项执行器使用生成配置指定的 `tools/worker_python.py`，按选定环境启动 Python，兼容 venv 和 conda 环境。75 原部署仍按原方式启动；新机器用下节的统一启动器读取 `.env`。

## 5. 检查环境、资源和实际计算

先检查 Web 应用能否导入、角色接口能否响应。`--check` 使用进程内测试客户端；角色构造需要 API key 时使用本地检查占位值，此检查不发送语言模型请求。

```bash
/data/material-repro/envs/web/bin/python tools/launch_service.py 1105 --env-file material_selection/.env --check
/data/material-repro/envs/web/bin/python tools/launch_service.py 1111 --env-file material_composition_optimization/.env --check
/data/material-repro/envs/web/bin/python tools/launch_service.py 1115 --env-file material_discovery/.env --check
/data/material-repro/envs/web/bin/python tools/launch_service.py 1116 --env-file material_validation/.env --check
```

1115 另做模型预检：

```bash
/data/material-repro/envs/web/bin/python material_discovery/tools/preflight_omatg.py --env-file material_discovery/.env
```

完整数值检查会实际调用八类专项、CSP/DNG、MatterSim 和 ALIGNN，生成结果保存在资源目录的 `run/smoke/`。测试与线上共享 GPU 时，把 `--gpu-lock` 设置成所有进程使用的同一个锁文件；独立机器使用下面的本机路径。

```bash
/data/material-repro/envs/web/bin/python tools/smoke_runtime.py \
  --resources /data/material-repro/resources \
  --web-python /data/material-repro/envs/web/bin/python \
  --numeric-python /data/material-repro/envs/numeric/bin/python \
  --recipe-python /data/material-repro/envs/recipe/bin/python \
  --crystals --candidates 10 \
  --omatg-python /data/material-repro/envs/omatg/bin/python \
  --alignn-python /data/material-repro/envs/alignn/bin/python \
  --gpu-lock /data/material-repro/resources/run/1115/gpu.lock \
  --output /data/material-repro/smoke-results.json
```

生成数量与完成全部性质计算的结构数量分别记录。部分候选可能未收敛、结构重复或不满足几何条件；结果清单保留实际原因。

程序测试也可以单独运行：

```bash
/data/material-repro/envs/web/bin/python -m unittest discover -s tools/tests
cd material_selection
PYTHONPATH=. /data/material-repro/envs/web/bin/python -m pytest -q tests/test_mature_material_service.py
```

1111 的一次完整配比优化及图文报告可以单独检查：

```bash
/data/material-repro/envs/web/bin/python tools/check_recipe_runtime.py \
  --resources /data/material-repro/resources \
  --numeric-python /data/material-repro/envs/numeric/bin/python \
  --recipe-python /data/material-repro/envs/recipe/bin/python \
  --output-dir /data/material-repro/recipe-check
```

测试命令从仓库根目录运行；上面的 `cd material_selection` 检查结束后先返回根目录。输出目录使用新的路径，便于保留每次检查结果。全部服务测试由 `tools/check_service_tests.py --output /data/material-repro/service-tests.json` 分进程运行，使用 Web 环境执行。历史测试与当前 OMatG 路线的差异见验证记录。

## 6. 启动服务

在仓库根目录，每个命令对应一个服务进程。首次联调先选空闲端口，在本机 `.env` 调整 `PORT`。

```bash
/data/material-repro/envs/web/bin/python tools/launch_service.py 1105 --env-file material_selection/.env
/data/material-repro/envs/web/bin/python tools/launch_service.py 1111 --env-file material_composition_optimization/.env
/data/material-repro/envs/web/bin/python tools/launch_service.py 1115 --env-file material_discovery/.env
/data/material-repro/envs/web/bin/python tools/launch_service.py 1116 --env-file material_validation/.env
```

以上命令分别执行，不放在同一终端顺序等待。接口和用户请求示例见各服务 README：[1105](../material_selection/README.md)、[1111](../material_composition_optimization/README.md)、[1115](../material_discovery/README.md)、[1116](../material_validation/README.md)。

1116 当前恢复的是 W-14 参考案例证据服务。完整训练研究的追加资料见[研究资源交接](research_resources.md)。
