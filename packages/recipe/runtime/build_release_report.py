"""Evidence-based final release decision and internal, allowlisted ZIP package."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import statistics
import xml.etree.ElementTree as ET
import zipfile
ROOT=Path(__file__).resolve().parent
def read(name):return json.loads((ROOT/name).read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def tests(name):
    suites=ET.parse(ROOT/name).getroot().findall('.//testsuite')
    total=sum(int(r.attrib['tests']) for r in suites)
    failed=sum(int(r.attrib.get('failures',0))+int(r.attrib.get('errors',0)) for r in suites)
    assert failed==0
    return total
def main():
    alloy=read('ALLOY_MODEL_AUDIT.json');glass=read('glass_v1_source_purged/training_report.json')
    benchmark=read('NSGA_FAIR_BENCHMARK.json');support=read('SUPPORT_AND_LABEL_AUDIT.json')
    preflight=read('FINAL_PREFLIGHT.json');e2e=read('E2E_PRIVATE_INTEGRATION.json');display=read('PRESENTATION_STREAM_VALIDATION.json')
    assert preflight['offline_preflight_passed'] and e2e['passed'] and display['passed']
    assert len(benchmark['domains'])==7 and all('error' not in r for r in benchmark['domains'].values())
    assert all(len(r['runs'])==5 and all(t['equal_model_evaluation_budget_reached'] for t in r['runs']) for r in benchmark['domains'].values())
    assert not alloy['input_parity_failures']
    count=tests('MODEL_AND_OPTIMIZER_TESTS.xml')+tests('SERVICE_SNAPSHOT_TESTS.xml')
    names={'aluminum':'航空铝','stainless':'奥氏体不锈钢','nickel_superalloy':'镍基高温',
           'copper':'铜基','hea_mpea':'HEA/MPEA','steel':'钢总体','titanium':'钛'}
    lines=['# 上线前验证与放行结论','', '日期：2026-09-28。验证在 75 的隔离候选目录完成。', '',
        '## 结论','',
        '已完成本轮可安全离线执行的工程与模型验证，并修复发现的封装、玻璃预处理泄漏、最大批量上限和推理环境问题。候选包可以提交维护人员合并，启动有回滚能力的私有影子计算。', '',
        '**不能宣布全部材料已通过正式预测转正。** 不锈钢适用范围、独立来源泛化、异常原始标签、许可证和部分优化效果仍有明确门槛；完成可视化也不会自动消除这些门槛。没有修改正式服务、重启进程或推送 GitHub。','',
        '## 1. 完成的验证','',
        '| 检查 | 最终结果 |','|---|---|',
        f'| 自动化测试 | {count} 项通过：核心模型/优化 {tests("MODEL_AND_OPTIMIZER_TESTS.xml")} 项，1111 隔离快照 {tests("SERVICE_SNAPSHOT_TESTS.xml")} 项 |',
        '| 合金训练—推理一致性 | 13,460 个标签行、1,820 个独立成分组；全部原始输入编码与冻结训练管线一致，无失败 |',
        '| 合金权重与成绩回放 | 权重、预处理、编码、冻结成绩一致；输出有限且满足 UTS ≥ YS > 0 |',
        '| 玻璃严格重训 | 159,461 个性质观测、67,679 个成分、8,088 个来源组；三折来源留出并清除重复成分；训练统计独立复算通过 |',
        '| 玻璃原始输入封装 | 128 条预测与独立训练编码一致；最大 500 配方 / 1,000 性质请求真实完成；超限拒绝 |',
        '| 实际私有子进程 | 11 个通道全部完成：三类合金、玻璃及七域 NSGA-II；镍基无可行解作为有效业务状态返回 |',
        '| 异常与资源控制 | 无解、无效目标、单位错误、NaN、越界字段、哈希漂移、满队列、超时、子进程清理、私有标志缺失等均有测试 |',
        '| 显示链回归 | 八域本地绘图、最终 Markdown、令牌流式输出通过；影子未进入用户结果；上传和外部生成调用使用替身 |',
        '| 现役停机规则 | 七域实际 propose 回放，记录现役真实尝试/预测次数，不用固定 256 点冒充旧服务 |',
        '| 优化器对照 | 七域 × 五种子 × 两方法，共 70 次同 256 配方预测预算对照；保留实际条件与门槛 |',
        '| 生产保护 | 两个仓库及 17 个冻结原始输入保持不变，66 个现役依赖哈希核验通过；现役 1111 /health 正常 |','',
        '健康检查包含真实成分推理，而不是只查文件是否存在。工程测试通过不等于实验准确性通过。真实浏览器公式排版、MinIO 权限、实际网络上传和生产负载未在本轮激活验证；它们需在获批的部署/验收窗口中完成。','',
        '## 2. 本次真正修复了什么','',
        '- v10 原始成分包装器严格复现 113 列输入、保存的工艺词表、缺失/零、多阶段处理、应变率/晶粒/相指标及证据语义。wq=0 不再被当成水淬证据。',
        '- 冻结模型定义和基础模块按哈希加载，不能因历史绝对路径而不小心导入后续修改的训练代码。',
        '- 玻璃旧 v0 在交叉验证时使用了包含测试成分的数值缩放统计。本次按训练成分独立拟合统计，固定原超参数重训 strict v1，并保存每折切分与统计来源。没有选用测试成绩挑选新权重。',
        '- 玻璃从逐条推理改为有上限的批量推理；影子映射器也支持现役最大 500 配方，不能只修底层而保留上层 128 配方的旧限制。',
        '- 专项推理按真实保存环境隔离：铜/玻璃 sklearn 1.8，其他专项 sklearn 1.7.2。跨版本反序列化在新 worker 中失败关闭。旧通用环境加载铜/玻璃会产生版本警告，正式新版也必须使用对应环境，不能直接全局升级依赖。',
        '- 优化使用已解析的真实用户目标和阈值，单目标与目标值匹配分开；无可行解不能伪装成达标的最佳方案。','',
        '## 3. 模型成绩与科学边界','',
        '### 3.1 合金 v10 单权重','',
        '以下是冻结成分留出测试的重新回放，不是新建的独立盲测，也不是现役线上每个专项模型的历史成绩。MAE 单位 MPa。','',
        '| 材料域 | YS MAE | YS R² | UTS MAE | UTS R² |','|---|---:|---:|---:|---:|']
    for key,label in names.items():
        y=alloy['metrics']['test/'+key+'/YS'];u=alloy['metrics']['test/'+key+'/UTS']
        lines.append(f'| {label} | {y["MAE"]:.2f} | {y["R2"]:.3f} | {u["MAE"]:.2f} | {u["R2"]:.3f} |')
    lines+=['', '总体 YS MAE 69.90、R² 0.832；UTS MAE 69.77、R² 0.841。独立来源挑战仍约 YS 219.22、UTS 261.34 MPa，且该挑战仅覆盖钢和钛，不能解释为铝/镍/不锈钢也做过独立来源盲测。', '',
        '同训练切分、同输入的 ExtraTrees 对照也已保留。Transformer 不是全面胜出：镍基、铜、钛的某些性质仍弱于该匹配对照；不能因为模型名称更先进就一并正式替换。', '',
        '### 3.2 玻璃 strict v1','',
        '| 任务 | 来源留出 MAE | R² | 本次准入 |','|---|---:|---:|---|']
    tasks={'DENSITY_20C':('20°C 密度','g/cm³','私有影子'), 'YOUNG_MODULUS':('杨氏模量','GPa','私有影子'),
           'TG':('玻璃化转变温度','°C','离线'), 'CTE_BELOW_TG':('低于 Tg 的 CTE','10⁻⁷/K','离线；不替代 0–300°C 平均 CTE'),
           'CTE_FIXED_T':('固定温度 CTE','10⁻⁷/K','离线'), 'POISSON_RATIO':('泊松比','1','离线'),
           'THERMAL_CONDUCTIVITY':('热导率','W/(m·K)','不作为服务预测/优化目标')}
    for key,(label,unit,admission) in tasks.items():
        m=glass['oof_metrics']['by_task'][key]
        # The saved report uses lower-case metric names.
        mae=m.get('MAE',m.get('mae'));r2=m.get('R2',m.get('r2'))
        lines.append(f'| {label} | {mae:.4f} {unit} | {r2:.3f} | {admission} |')
    lines+=['', '热导率整体来源留出 R² 仍约 −0.003，不能用样本总量多来掩盖该任务条件缺失、测量语义混杂和标签稀疏；增加描述符也不能代替条件与来源审计。', '',
        '## 4. 不锈钢“0/20”现在到底怎样','',
        '本次 40 个保存的正式候选均可完成私有诊断推理；没有通过扩大阈值、删除氮或伪造 P/S 来放行。完整原子分数 Hellinger 的训练留一 95% 参考门槛仍是 **0/40 通过**。只去掉 N/P/S 的主体距离诊断为 21/40，但这不是新的正式适用范围。氮是有效强化元素，不能从物理模型里当作无关杂质忽略。', '',
        '所以运行层的“无可比较结果”已解决，科学层的适用范围不匹配尚未解决。转正前必须统一未指定/未报告/实测零的语义，按主元素与微量元素、温度及工艺分别建模并用成分留出验证误差；不能只把两个模型的距离数值改成相同阈值。', '',
        '## 5. 原始标签复核','',
        '| 行 | 数据角色 | YS / UTS (MPa) | 核查结果 |','|---|---|---|',
        '| al-1067 | 训练 | 637 / 581 | 上游准入表已存在，需核对原试验/表格 |',
        '| ti-97 | 训练 | 1710 / 1665 | 原表同时报告误差 140 / 165 MPa；须核对是否同一试样/统计定义，不能仅凭均值倒置认定必然抄错 |',
        '| ss-383 | 测试 | 148 / 47 | 上游准入表已存在；来源 STMECH_AUS_SS.txt，NRIM/CDS/No15B/1988 ADF |','',
        '保留了原始行、文件哈希和 CSV 定位，见 LABEL_SOURCE_VERIFICATION.json 与 PHYSICAL_LABEL_REVIEW.csv。没有交换数字、删测试点或重算出更好成绩。镍基 YS/UTS 原记录 ID 不一致，简单配对检查没有找到配对，不能据此声称镍基不存在标签冲突。','',
        '## 6. NSGA-II 的真实效果','',
        '同一预测器、同搜索空间、同初始抽样、同目标和条件，比较随机搜索与 NSGA-II。下表是固定物理参考尺度的超体积变化，不是实验性能提升，也不是优化器普遍优越的证明。示例目标仅用于对照，不作为用户默认需求。','',
        '| 材料域 | 同预测预算对照 | NSGA-II 胜出 | 超体积相对变化中位数 | 判断 |','|---|---:|---:|---:|---|']
    opt_names=['航空铝','镍基高温','奥氏体不锈钢','铜基','HEA/MPEA','玻璃','短碳纤维']
    decisions=['本轮质量不如随机法，不能宣称升级优势','两种方法均无满足真实门槛的可行解',
               '有优化收益，但 Transformer 适用范围尚不准入','有数值搜索收益，但不代表弱预测模型已可靠',
               '收益不稳定，拉伸泛化仍弱','计算更快，但前沿质量未稳定优于随机法','有数值搜索收益；仍只在线弹性 RVE 范围']
    for (domain,r),label,decision in zip(benchmark['domains'].items(),opt_names,decisions):
        eq=sum(t['equal_model_evaluation_budget_reached'] for t in r['runs'])
        gains=[t['relative_hv_change_percent'] for t in r['runs'] if t['relative_hv_change_percent'] is not None]
        r['equal_budget_trials']=eq;r['comparable_hypervolume_trials']=len(gains)
        median=f'{statistics.median(gains):+.3f}%' if gains else '无可比较前沿'
        wins=sum(v>0 for v in gains)
        lines.append(f'| {label} | {eq}/5 | {wins}/{len(gains)} | {median} | {decision} |')
    (ROOT/'NSGA_FAIR_BENCHMARK.json').write_text(json.dumps(benchmark,ensure_ascii=False,indent=2,allow_nan=False))
    lines+=['', '镍基当前保存请求包含 UTS ≥ 900 MPa、YS ≥ 500 MPa、断裂寿命 ≥ 250 h；新预测栈在预算内没有找到同时满足这些条件的候选。不能悄悄删除强度门槛再展示高超体积。', '',
        '现役原算法真实停机规则及回放计数见 PRODUCTION_STOPPING_REPLAY_FINAL.json；新算法则使用预测配方数、提案数、软时间和整组进程硬超时四重约束。旧算法返回 K 个候选和新算法用 256 次预测预算不是同一个工作量，因此不能把这两种延迟直接相除宣称提速。', '',
        '## 7. 模型和数学定义','',
        '合金：元素属性/可学习嵌入 + 原子分数编码，注意力使用 log(原子分数) 先验；分数加权池化与数值分支、工艺/材料域嵌入融合，再用材料域残差适配器和有序强度输出。不是把所有材料直接混入原版 CrabNet。', '',
        r'$$\widehat{YS}=10\,\mathrm{softplus}(r_y/10),\qquad \widehat{UTS}=\widehat{YS}+s_d\,\mathrm{softplus}(r_\Delta)$$', '',
        '这里 r_y、r_Δ 是网络预测的隐变量，s_d 是冻结的材料域强度增量尺度，单位 MPa。架构保证非负与拉伸强度有序，不表示已经满足完整本构方程或 PDE。', '',
        '数值分支包含混合熵、原子尺寸失配、电负性/熔点/价电子等统计量、同系温度代理、热处理温度/时间、缺失掩码和正证据指标；113 列及冻结的实际掩码以 preprocessor.json 为准。Cu 细粒度增材/取向字段不在本版统一输入中，不能声称已用于推理。', '',
        r'$$x_i=\frac{w_i/M_i}{\sum_j w_j/M_j},\qquad H=-\sum_i x_i\ln x_i$$', '',
        'w_i 为质量分数，M_i 为元素原子量，x_i 为原子分数，H 是以 R 归一的理想混合熵；这些是可计算描述符，不等于直接计算相稳定性或热处理组织。玻璃使用氧化物 mol.%、网络形成体/中间体/改性体分组及组成统计的独立编码器；碳纤维使用基体参数、体积分数、长度和取向张量分量，不强塞成元素序列。', '',
        r'$$\min_{\mathbf c}\, \mathbf F(\widehat{\mathbf y}(\mathbf c,\mathbf p,T)),\qquad \sum_i c_i=100,\quad l_i\leq c_i\leq u_i$$', '',
        'c 是明确单位的配比；p 是本次固定工艺，T 是评价温度，l/u 是用户或可追溯默认边界。本版优化配比，不优化未建模工艺。目标值匹配使用 |ŷ−y_target|，提高性能使用 −ŷ，降低性能使用 ŷ；用户给出的硬门槛另作约束。Pareto 前沿是互不支配的有限搜索结果，不是解析逆解或唯一最优。', '',
        '## 8. 仍须完成的正式放行项','',
        '1. 数据负责人复核三组矛盾标签及镍基跨性质状态对应；有依据才发行新规范数据，不以删坏测试点改善报告。',
        '2. 补全相关材料的独立来源/局部工艺验证，并校准服务适用范围和候选不确定度；总体 R² 不可替代子群证据。',
        '3. 数据/法务确认 SciGlass 的具体数据版本与许可义务，合金来源也逐项留证。保守处理不等于所有权利自动已清楚。',
        '4. 获批后合并默认关闭的影子补丁，实际上线做低并发回放、资源/失败率监测及真实网络与浏览器验收。离线替身测试不能标成真实上传成功。',
        '5. 逐材料域、逐性质批准正式替换并更新前端；没有批准项时继续旧正式模型。新封装现在不开放 primary，不能靠展示完成直接绕过门槛。', '',
        '详见 SERVICE_HANDOFF.md 的接口、环境配置、合并守卫及回滚步骤；许可依据见 SOURCE_NOTICES.md。']
    (ROOT/'RELEASE_REVIEW.md').write_text('\n'.join(lines)+'\n')
    decision=dict(created_utc=datetime.now(timezone.utc).isoformat(),offline_verification_complete=True,
        offline_engineering_passed=True,candidate_ready_for_reviewed_private_shadow_merge=True,
        automated_tests_passed=count,private_e2e_channels=11,presentation_domains=8,equal_budget_optimizer_pairs=35,
        shadow_activated=False,all_domains_primary_ready=False,production_release_authorized=False,
        pending=['scientific_support_calibration','source_generalization','physical_label_review','license_review',
                 'approved_actual_shadow_canary','real_browser_and_asset_publication','domain_property_primary_implementation_and_signoff'])
    (ROOT/'RELEASE_DECISION.json').write_text(json.dumps(decision,indent=2))
    runtime=['runner.py','alloy_v10_shadow.py','oxide_glass_shadow_runner.py','oxide_glass_shadow_contract.py',
        'candidate_model_stack.py','bounded_nsga.py','optimization_runner.py','optimization_contract.py',
        'specialist_bridge.py','specialist_worker.py','private_shadow_dispatcher.py','glass_shadow_compare.py',
        'shadow_channel_configs.py','preflight_release.py','freeze_release.py','apply_shadow_candidate.py',
        'prepare_handoff.py','build_release_report.py','replay_production_stopping.py']
    docs=['RELEASE_REVIEW.md','SERVICE_HANDOFF.md','SOURCE_NOTICES.md']
    reports=['RELEASE_DECISION.json','FREEZE.json','FROZEN_INPUT_INVENTORY.json','VALIDATION_PROTOCOL.json',
        'FINAL_PREFLIGHT.json','EXTERNAL_DEPENDENCIES.json','MERGE_PLAN.json','1111_shadow_candidate.patch',
        'ALLOY_MODEL_AUDIT.json','ALLOY_HELDOUT_PREDICTIONS.json','GLASS_MODEL_AUDIT.json','GLASS_V1_MANIFEST.json',
        'GLASS_BATCH_VALIDATION.json','NSGA_FAIR_BENCHMARK.json','PRODUCTION_STOPPING_REPLAY_FINAL.json',
        'SUPPORT_AND_LABEL_AUDIT.json','LABEL_SOURCE_VERIFICATION.json','PHYSICAL_LABEL_REVIEW.csv',
        'PHYSICAL_LABEL_REVIEW_ROWS.json','E2E_PRIVATE_INTEGRATION.json','PRESENTATION_STREAM_VALIDATION.json',
        'MODEL_AND_OPTIMIZER_TESTS.xml','SERVICE_SNAPSHOT_TESTS.xml',
        'alloy_v10_health_expected.json','alloy_v10_health_request.json','glass_shadow_example_batch.json',
        'glass_shadow_health_expected.json']
    files={ROOT/name for name in runtime+docs+reports}
    for directory in ('models/alloy_v10','models/glass_v1','1111_patch','streamed_markdown','private_rendered_results'):
        files.update(p for p in (ROOT/directory).rglob('*') if p.is_file() and
                     p.suffix in {'.py','.json','.pt','.md','.png'})
    files.update(ROOT.glob('test_*.py'))
    files.update(ROOT.glob('NSGA_EXAMPLE_*.json'))
    files.update(ROOT/name for name in ('audit_alloy.py','audit_glass.py','audit_support_and_labels.py',
        'validate_glass_batch.py','validate_e2e.py','validate_presentation_stream.py','validate_service_snapshot.py',
        'trace_label_reviews.py','verify_label_sources.py','benchmark_nsga.py','train_strict_glass.py'))
    files.add(ROOT/'glass_v1_source_purged/training_report.json')
    assert all(p.is_file() for p in files)
    manifest=dict(files={str(p.relative_to(ROOT)):dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(files)},
        excludes=['raw databases','credentials','full service snapshot','other team changes','Python environments'],
        deployment_target='existing versioned directory on server 75',production_release_authorized=False)
    (ROOT/'PACKAGE_MANIFEST.json').write_text(json.dumps(manifest,indent=2))
    files.add(ROOT/'PACKAGE_MANIFEST.json')
    package=ROOT/'material_shadow_release_20260928.zip'
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(files):z.write(path,str(path.relative_to(ROOT)))
    with zipfile.ZipFile(package) as z:
        assert z.testzip() is None
        for relative,item in manifest['files'].items():
            assert hashlib.sha256(z.read(relative)).hexdigest()==item['sha256']
    package_info=dict(path=str(package),bytes=package.stat().st_size,sha256=sha(package),files=len(files),
                      zip_integrity_verified=True,all_member_hashes_verified=True)
    (ROOT/'PACKAGE_SHA256.json').write_text(json.dumps(package_info,indent=2))
    print(json.dumps(dict(release_decision=decision,package=package_info)))
if __name__=='__main__':main()
