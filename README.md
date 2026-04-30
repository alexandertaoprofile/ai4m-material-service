# alpha_material_sync

`alpha_material_sync` 是材料服务聚合仓，当前包含 3 个可独立运行的子服务：

- `inorganic_existing_material/`：无机已有材料筛选与性质补全
- `inorganic_new_material/`：无机新材料发现服务（演进中）
- `organic_existing_material/`：有机已有材料服务（OpenPoly 检索 + 性质补全主链）

## 仓库结构

```text
alpha_material_sync/
  inorganic_existing_material/
  inorganic_new_material/
  organic_existing_material/
  env/                         # 部署环境文件集中目录
```

## env 目录说明

`env/` 目录用于给部署机（如 113）提供统一的环境定义与快照文件，不放业务密钥。

## 运行与部署原则

1. 三个子服务各自独立启动、独立配置。
2. 代码与环境定义可入仓；`.env`、AK/SK、Token 等敏感配置不入仓。
3. 生产部署优先使用固定分支/tag，保证可回滚。

## 文档入口

- 无机已有材料服务：`inorganic_existing_material/README.md`
- 无机新材料服务：`inorganic_new_material/README.md`
- 有机已有材料服务：`organic_existing_material/README.md`
