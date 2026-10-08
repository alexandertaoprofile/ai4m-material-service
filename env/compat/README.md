# 旧 Alpha 的环境兼容调整

1115 的 Alpha 搜索模块导入 Semantic Kernel 0.4.3 的装饰器。该版本原始 `sk_pydantic.py` 使用较早的 Pydantic URL 类位置，直接搭配当前服务的 Pydantic 2.11 会导入失败。

75 已使用 `AnyUrl` 别名及相同的 URL 约束解决此问题。本目录保留该适配；`tools/apply_environment_compatibility.py` 先检查原文件 SHA256，再在指定的新环境中恢复它。安装脚本自动调用该工具；已适配的同一文件可以重复检查。

源码保持 Microsoft 的 MIT 授权。对应服务源码和 Alpha 模块保持原部署版本，安装步骤及验证状态见[复现指南](../../docs/reproduction.md)。
