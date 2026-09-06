# 难熔金属跨尺度性能计算与验证

## 1. 材料体系与计算任务定义

本轮针对 bcc 纯钨开展跨尺度性能验证，覆盖 300、600、900 K，重点评估晶格参数、弹性性质、声速与热容。

计算链路为：**DFT 基准数据 → DeepMD 机器学习原子势 → LAMMPS 分子动力学 → 实际性能对标**。

### 本轮服务结论

W-14 势函数已完成小应变数据域的能量与 virial 拟合，并得到 300–900 K 的热弹性性质。300 K 下，C11、体积模量、剪切波声速和纵波声速与实际性能值的偏差为 **1.6%–3.5%**，可为纯钨小应变热弹性问题提供工程级初步计算依据。

---

## 2. DFT 数据与基准验证

MLIP 的原子级参考来自 ColabFit Exchange 的 **W-14 Slice DFT** 数据集，体系为单元素 bcc-W 静态小体变、小剪切构型，应变范围约为 ±0.02。

当前已挂载 1,800 帧结构盒、原子坐标、能量、virial 与 force 数组。

DFT 基准以 Kohn–Sham 方程描述电子基态：

$$
\left[-\frac{\hbar^2}{2m_e}\nabla^2+V_{\mathrm{eff}}(\mathbf r)\right]\psi_n(\mathbf r)=\varepsilon_n\psi_n(\mathbf r)
$$

由 DFT 总能量导出的原子力与应力分别为：

$$
\mathbf F_i^{\mathrm{DFT}}=-\frac{\partial E_{\mathrm{DFT}}}{\partial \mathbf R_i},\qquad
\sigma_{\alpha\beta}^{\mathrm{DFT}}=\frac{1}{V}\frac{\partial E_{\mathrm{DFT}}}{\partial\varepsilon_{\alpha\beta}}
$$

| DFT 基准内容 | 在本服务中的作用 |
|---|---|
| Energy | 约束平衡结构附近的势能面 |
| Virial / Stress | 约束小应变应力响应，支持弹性常数计算 |
| Force | 当前静态构型标签为零，作为数据记录保留 |

本轮以已归档的结构、能量和 virial 数据作为 DFT 基准，支撑后续 MLIP 训练与小应变性质计算。

---

## 3. MLIP 训练与 MD 跨尺度模拟

### 3.1 DeepMD 势函数

| 模型项 | 实际配置 |
|---|---|
| MLIP 模型 | DeepMD-kit DP-SE，局域描述符 `se_e2_a` |
| 截断半径 | $r_c=6.0$ Å；平滑起点 0.5 Å；邻居上限 `[64]` |
| 描述符网络 | `[25, 50, 100]` |
| 拟合网络 | `[120, 120, 120]` |
| 学习率 | 指数衰减 $5\times10^{-4} \rightarrow 5\times10^{-9}$，衰减步长 20,000 steps |
| 训练设置 | batch size 2；训练 300,000 steps |

### 3.2 Loss 与训练收敛

训练目标为能量、力与 virial 的加权组合：

$$
\mathcal{L}=p_E\mathcal{L}_E+p_F\mathcal{L}_F+p_V\mathcal{L}_V
$$

本轮训练权重由 $p_E:0.5\rightarrow1.0$、$p_F:0.0\rightarrow0.05$、$p_V:0.5\rightarrow2.0$ 逐步调整。

| 300,000 steps 末步 RMSE | validation | train |
|---|---:|---:|
| Energy | $7.68\times10^{-5}$ | $6.10\times10^{-4}$ |
| Virial | $3.80\times10^{-3}$ | $1.38\times10^{-2}$ |
| Force | $4.96\times10^{-17}$ | 0 |

![图 1：DeepMD 训练收敛——Energy 与 Virial 的训练/验证 RMSE](/data/se42/alpha_project/material_service_hub/material_validation/results/w14-customer-preview/presentation/training_convergence.png)

### 3.3 LAMMPS MD 与性质计算

使用 LAMMPS 对 $6\times6\times6$ bcc-W 超胞开展 NPT、NVT 与小应变计算。

晶格参数与密度：

$$
a(T)=\frac{\langle L(T)\rangle}{6},\qquad
\rho(T)=\frac{M}{\langle V(T)\rangle}
$$

弹性与派生模量：

$$
C_{ij}=\frac{\partial\sigma_i}{\partial\varepsilon_j},\qquad
K=\frac{C_{11}+2C_{12}}{3},\qquad
E=\frac{9KG}{3K+G}
$$

声速与热容：

$$
v_s=\sqrt{\frac{G}{\rho}},\qquad
v_p=\sqrt{\frac{K+4G/3}{\rho}},\qquad
C_V=\frac{\langle(E-\langle E\rangle)^2\rangle}{k_BT^2}
$$

### 3.4 温度相关结构响应

| 实际平均温度 | 晶格参数 a | 密度 |
|---:|---:|---:|
| 300 K | 3.1813 Å | 18.9637 g/cm³ |
| 601 K | 3.1822 Å | 18.9474 g/cm³ |
| 901 K | 3.1830 Å | 18.9328 g/cm³ |

![图 2：NPT 热响应——晶格参数与密度](/data/se42/alpha_project/material_service_hub/material_validation/results/w14-customer-preview/presentation/npt_thermal_response.png)

### 3.5 300 K 弹性、声学与热学结果

| C11 | C12 | C44 | K | G | E | ν | vs | vp | Cv |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 513.7 GPa | 192.8 GPa | 144.0 GPa | 299.7 GPa | 150.4 GPa | 386.5 GPa | 0.285 | 2816 m/s | 5136 m/s | 12.86 J/mol/K |

![图 3：弹性常数与 VRH 多晶等效模量](/data/se42/alpha_project/material_service_hub/material_validation/results/w14-customer-preview/presentation/elastic_response.png)

![图 4：横波、纵波声速与定容热容](/data/se42/alpha_project/material_service_hub/material_validation/results/w14-customer-preview/presentation/acoustic_thermal_response.png)

---

## 4. 性能验证与可信度评估

### 4.1 300 K 实际性能对标

| 性质 | MLIP/MD 计算值 | 实际性能值 | 相对误差 | 判断 |
|---|---:|---:|---:|---|
| C11 弹性常数 | 513.7 GPa | 522 GPa | 1.6% | 一致 |
| 体积模量 K | 299.7 GPa | 310.6 GPa | 3.5% | 基本一致 |
| 剪切波声速 vs | 2816 m/s | 2890 m/s | 2.6% | 一致 |
| 纵波声速 vp | 5136 m/s | 5220 m/s | 1.6% | 一致 |

![图 5：300 K 计算值与实际性能值对比](/data/se42/alpha_project/material_service_hub/material_validation/results/w14-customer-preview/presentation/actual_performance_comparison.png)

### 4.2 本轮适用范围与下一步

当前结论适用于纯钨、0–900 K、小应变弹性与声学响应。面向高缺陷浓度、熔点附近、辐照损伤或 W 基合金等场景时，将补充对应 DFT 构型并重新训练、验证模型。

### 本轮结论

本轮结果表明，W-14 DeepMD 势函数可将原子级 DFT 基准推进到 LAMMPS 大规模 MD，并在 300 K 实际性能对标中给出 1.6%–3.5% 的偏差水平。该工作流可作为 W 基材料后续热弹性评估、模型迭代和多尺度耦合的基础。
