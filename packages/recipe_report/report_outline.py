"""Material-neutral section contract for the readable recipe report.

Glass adopts this outline first. Other domain renderers migrate separately.
"""
HEADINGS = (
    '### 1. 设计需求与优化目标',
    '### 2. 配比范围与评价条件',
    '### 3. 性能预测与配方优化方法',
    '### 4. 优化结果与方案比较',
    '### 5. 配方组成、预测性能与验证建议',
)

# Read compatibility only: saved pages must remain embeddable after upgrading.
LEGACY_GLASS_HEADINGS = (
    '### 1. 玻璃配方设计需求与优化目标',
    '### 2. 玻璃材料配比范围与评价条件',
    '### 3. 性能预测与配方优化方法',
    '### 4. 玻璃配方优化结果与方案比较',
    '### 5. 玻璃配方的组成、预测性能与验证建议',
)
