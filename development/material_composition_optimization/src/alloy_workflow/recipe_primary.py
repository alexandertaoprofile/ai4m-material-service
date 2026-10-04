"""Approved, per-domain promotion of a verified recipe calculation.

The numerical package stays frozen. Its private calculation is provenance,
not a release switch. This adapter creates a separate production result and
preserves the existing candidate/property contracts at the service boundary.
"""
from copy import deepcopy
from pathlib import Path
import importlib
import json
import math
import os
import sys
import time

from src.alloy_workflow.recipe_shadow import DISPLAY_ROOT, RecipeShadowDispatcher

VERSION = 'material_recipe_primary_v1_20260928'
NICU_ADAPTER_VERSION = 'nicu_specialist_nsga_v1_20260929'
NICU = frozenset({'ni_superalloy_hot_end', 'copper_hot_end_local_composition_v1'})
ELIGIBLE = frozenset({'reusable_rocket_stainless', 'aerospace_aluminum_composition_v1',
    'chip_glass_thermomechanical_family_v1', 'hea_mpea',
    'short_cf_thermomechanical_rve_v1', 'perovskite_transport_stability_v2'}) | NICU
CONFIG = Path(__file__).resolve().parents[2] / 'config/recipe_release.json'


def mode_for(domain):
    try:
        path = Path(os.getenv('MATERIAL_RECIPE_RELEASE_CONFIG', str(CONFIG)))
        config = json.loads(path.read_text(encoding='utf-8'))
        mode = config.get('domains', {}).get(domain, 'shadow')
        if mode not in {'legacy', 'shadow', 'primary'}:
            return 'shadow'
        if mode == 'primary' and (domain not in ELIGIBLE or config.get('approved_after_acceptance') is not True):
            return 'shadow'
        # The previous six-domain approval does not approve a new adapter.
        if mode == 'primary' and domain in NICU:
            acceptance = config.get('domain_acceptance', {}).get(domain, {})
            if (acceptance.get('approved_after_acceptance') is not True
                    or acceptance.get('adapter_version') != NICU_ADAPTER_VERSION
                    or acceptance.get('model_stack') != 'specialist'
                    or not acceptance.get('authorization')):
                return 'shadow'
        return mode
    except (OSError, ValueError, TypeError):
        return 'shadow'


def helper(name):
    # Presentation only; no training modules are imported into the web process.
    root = str(DISPLAY_ROOT)
    if root not in sys.path:
        sys.path.append(root)
    module = importlib.import_module(name)
    if Path(module.__file__).resolve().parent != DISPLAY_ROOT.resolve():
        raise RuntimeError('recipe presentation module location mismatch')
    return module


def effective_mode(scope):
    """A material switch never opens its explicitly unsupported conditions."""
    domain = scope.get('model_domain')
    if domain == 'reusable_rocket_stainless':
        try:
            if float(scope.get('test_temperature_K', 293)) < 293:
                return 'legacy'
        except (TypeError, ValueError):
            return 'legacy'
    return mode_for(domain)


def bootstrap_constraints(constraints):
    """Legacy stainless seed generation expects weights, not target objects.

    Its ranking is NOT the production ranking. Remove objectives only from
    this private seed-generation copy; NSGA receives the untouched user plan.
    """
    domain = constraints.get('model_domain')
    if domain == 'chip_glass_thermomechanical_family_v1' and mode_for(domain) == 'primary':
        # The legacy glass proposal builds starting references and accepts
        # only min/max. The untouched user plan supplies NSGA target goals.
        if any(isinstance(value, dict) and value.get('goal') == 'target'
               for value in constraints.get('objectives', {}).values()):
            seed = deepcopy(constraints)
            seed['objectives'] = {}
            if isinstance(seed.get('raw_scope'), dict):
                seed['raw_scope']['objectives'] = {}
            return seed
    if domain != 'reusable_rocket_stainless' or mode_for(domain) != 'primary':
        return constraints
    if not any(isinstance(value, dict) for value in constraints.get('objectives', {}).values()):
        return constraints
    seed = deepcopy(constraints)
    seed['objectives'] = {}
    if isinstance(seed.get('raw_scope'), dict):
        seed['raw_scope']['objectives'] = {}
    return seed


def primary_description(domain):
    if domain == 'perovskite_transport_stability_v2':
        return '正在按文档化组分网格计算钙钛矿输运响应并比较配方。'
    if domain == 'short_cf_thermomechanical_rve_v1':
        return '正在以短碳纤维专项本构模型和NSGA-II优化配方与结构参数。'
    if domain in NICU:
        return '正在使用条件化专项性质模型，通过NSGA-II优化配方。'
    return '正在使用Transformer与专项性质模型，通过NSGA-II优化配方。'


def primary_preflight(domain, scope=None):
    scope = scope or {}
    conditions = ''
    if domain == 'ni_superalloy_hot_end':
        temperature = scope.get('test_temperature_C')
        stress = scope.get('applied_stress_MPa')
        if temperature is not None and stress is not None:
            conditions = f'本次评价工况为 {float(temperature):g} °C / {float(stress):g} MPa。'
    return primary_description(domain) + conditions + '本次计算采用已训练的固定模型，保留用户设定的组成范围、材料状态和性能目标。'


def predicted(value, source, unit):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('non-finite production prediction')
    return {'mean': number, 'source': source, 'unit': unit,
            'model_version': 'alloy_transformer_v10' if source == 'alloy_v10' else 'specialist',
            'uncertainty_available': False}


def downstream_summary(report, taskid, display):
    """The Action return value must stand alone without the chat's tables."""
    return (helper('recipe_conclusion').build_conclusion(report, display)
            + '\n\n完整条件与性质文件：/alloy/tasks/' + taskid + '。')


def support(row):
    raw = deepcopy(row.get('support') or {})
    shared = raw.get('shared')
    if shared:
        if not shared.get('admitted'):
            raise ValueError('cannot promote outside shared stainless support')
        return {**shared, 'nearest_training_composition_distance': shared['distance'],
                'support_provenance': raw}
    level = raw.get('specialist', 'inside')
    return {'level': 'inside' if level in {'local', 'patent_family_local_anchor'} else level,
            'support_provenance': raw}


def require_matching(values, evaluated, mapping):
    for key, actual in mapping.items():
        number = evaluated[actual]
        if isinstance(number, dict):
            number = number.get('prediction_ppm_per_K', number.get('prediction', number.get('mean')))
        if not math.isclose(float(values[key]), float(number), rel_tol=1e-7, abs_tol=2e-4):
            raise ValueError('production specialist differs from frozen optimizer: ' + key)


def compatible_candidates(runner, task, report, constraints, *, nearest=False):
    domain = task['model_domain']
    rows = report.get('nearest_candidates', []) if nearest else report['candidates']
    extra = [{} for _ in rows]
    # Re-evaluate the SELECTED compositions, not the old top candidate. This
    # preserves downstream glass estimates, HEA hardness and the CF matrix.
    if rows and domain in ({'chip_glass_thermomechanical_family_v1', 'hea_mpea', 'short_cf_thermomechanical_rve_v1'} | NICU):
        requests = []
        for row in rows:
            c = row['composition']['values']
            if domain == 'chip_glass_thermomechanical_family_v1':
                requests.append({'composition_mol_percent': c})
            elif domain == 'hea_mpea':
                requests.append({'composition': c})
            elif domain in NICU:
                requests.append({'composition_wt_percent': c})
            else:
                profiles = {}
                for previous in task.get('all_candidates') or task.get('initial_candidates') or []:
                    profiles[previous['matrix_name']] = previous['inputs']
                name, inputs = list(profiles.items())[row['composition']['anchor_index']]
                fixed = {k: v for k, v in inputs.items() if k not in c}
                requests.append({'matrix_name': name, 'matrix_properties': fixed, **c})
        base = deepcopy(constraints)
        base.update(report['conditions'])
        if domain == 'chip_glass_thermomechanical_family_v1':
            base['composition_basis'] = 'mol_percent'
        extra = runner.run(task['taskid'] + ('-near-properties' if nearest else '-properties'), 'evaluate_batch', base, requests)['results']
        if len(extra) != len(rows):
            raise ValueError('selected property batch size mismatch')
    output = []
    for row, ancillary in zip(rows, extra):
        c = deepcopy(row['composition']['values'])
        values, sources = row['values'], row['property_sources']
        candidate = {'candidate_id': row['display_id'], 'rank': len(output) + 1,
            'property_sources': deepcopy(sources), 'applicability_domain': support(row),
            'selection_score': 1 - row.get('preference_score', 0),
            'preference_score': row.get('preference_score', 0), 'score_basis': 'mean_normalized_objective_gap'}
        if nearest:
            if row.get('feasible') is not False or row.get('admissible') is not True:
                raise ValueError('nearest candidate is not an admitted near-miss')
            candidate.update(admissible=True, feasible=False, selection_score=None, preference_score=None,
                score_basis='sum_relative_constraint_shortfall',
                strict_screening={'passed': False, 'constraint_gaps': deepcopy(row.get('constraint_gaps', {})),
                    'total_relative_gap': row.get('constraint_violation', 0)})
        if domain in NICU:
            if row.get('admissible') is not True or row.get('feasible') is not (not nearest):
                raise ValueError('nickel/copper selection has invalid support or feasibility')
            if any(source != 'specialist' for source in sources.values()):
                raise ValueError('nickel/copper primary adapter currently requires specialist sources')
            if nearest and not any(gap['shortfall'] > 0 for gap in row.get('constraint_gaps', {}).values()):
                raise ValueError('nearest candidate requires an explicit unmet requirement')
            candidate.update(admissible=True, feasible=not nearest,
                strict_screening={'passed': not nearest, 'constraint_gaps': deepcopy(row.get('constraint_gaps', {})),
                    'total_relative_gap': row.get('constraint_violation', 0)},
                inputs=deepcopy(report['conditions']), adapter_version=NICU_ADAPTER_VERSION)
            if nearest:
                candidate.update(selection_score=None, preference_score=None, score_basis='sum_relative_constraint_shortfall')
        if domain == 'reusable_rocket_stainless':
            candidate.update(composition_wt_percent=c, short_time_tensile={
                target: predicted(values[key], sources[key], '%' if key == 'elongation_pct' else 'MPa')
                for key, target in (('YS_MPa', 'yield_0p2_MPa'), ('UTS_MPa', 'uts_MPa'), ('elongation_pct', 'elongation_pct'))})
        elif domain == 'aerospace_aluminum_composition_v1':
            candidate.update(composition_wt_percent=c, processing_category=report['conditions']['processing_category'],
                source_anchor={'kind': 'frozen_reference', 'anchor_index': row['composition']['anchor_index']},
                predicted_properties={target: predicted(values[key], sources[key], '%' if key == 'elongation_pct' else 'MPa')
                    for key, target in (('YS_MPa', 'yield_strength_MPa'), ('UTS_MPa', 'uts_MPa'), ('elongation_pct', 'elongation_pct'))})
        elif domain == 'chip_glass_thermomechanical_family_v1':
            if ancillary.get('mode') == 'outside_family_no_prediction':
                raise ValueError('selected glass composition outside recorded family')
            props = deepcopy(ancillary['predicted_properties'])
            require_matching(values, props, {'cte_0_300_ppm_K': 'CTE_linear_0_to_300C',
                'stress_optical_coefficient_nm_cm_per_MPa': 'stress_optical_coefficient_nm_cm_per_MPa',
                'viscosity_temperature_200_poise_C': 'viscosity_temperature_200_poise_C',
                'viscosity_temperature_35kpoise_C': 'viscosity_temperature_35kpoise_C'})
            for key, target in (('density_g_cm3', 'density_g_per_cm3'), ('young_modulus_GPa', 'young_modulus_GPa')):
                props[target] = {'prediction': values[key], 'unit': 'g/cm³' if key == 'density_g_cm3' else 'GPa',
                    'source': sources[key], 'model_version': 'glass_transformer_strict_v1'}
            candidate.update(composition_mol_percent=c, predicted_properties=props,
                engineering_estimates=deepcopy(ancillary['engineering_estimates']),
                simulation_inputs_to_measure=deepcopy(ancillary['simulation_inputs_to_measure']),
                source_anchor={'kind': 'frozen_reference', 'anchor_index': row['composition']['anchor_index']})
        elif domain == 'hea_mpea':
            candidate.update({k: deepcopy(v) for k, v in ancillary.items() if k not in {'model_version', 'uncertainty', 'constraints', 'applicability_domain'}})
            candidate['yield_strength_MPa'] = predicted(values['YS_MPa'], sources['YS_MPa'], 'MPa')
            candidate['ultimate_tensile_strength_MPa'] = predicted(values['UTS_MPa'], sources['UTS_MPa'], 'MPa')
            candidate['composition_at_pct'] = c
            candidate['applicability_domain'] = support(row)
            hardness = float(ancillary['hardness_HV']['mean'])
            row['values']['hardness_HV'] = hardness
            row['property_sources']['hardness_HV'] = 'hea_auxiliary'
            candidate['property_sources']['hardness_HV'] = 'hea_auxiliary'
            candidate['uncertainty'] = {'hardness_HV_std': ancillary['hardness_HV'].get('std')}
        elif domain == 'short_cf_thermomechanical_rve_v1':
            if not ancillary.get('physical_consistency', {}).get('positive_definite_stiffness'):
                raise ValueError('selected CF stiffness is not positive definite')
            keys = ('E11_MPa', 'E22_MPa', 'E33_MPa', 'G12_MPa', 'G13_MPa', 'G23_MPa', 'V12', 'V13', 'V23')
            require_matching(values, ancillary['predicted_engineering_constants'], {k: k for k in keys})
            candidate.update(deepcopy(ancillary))
            candidate.update(candidate_id=row['display_id'], rank=len(output) + 1,
                matrix_name=requests[len(output)]['matrix_name'], property_sources=deepcopy(sources))
        elif domain == 'ni_superalloy_hot_end':
            require_matching(values, ancillary['short_time_strength'],
                {'YS_MPa': 'proof_strength_0p2_MPa', 'UTS_MPa': 'ultimate_tensile_strength_MPa'})
            require_matching(values, ancillary['tensile_ductility'], {'elongation_pct': 'elongation_percent'})
            require_matching(values, ancillary['creep_rupture'], {'rupture_hours': 'predicted_hours'})
            if ancillary['creep_rupture'].get('applicability_domain', {}).get('level') == 'outside':
                raise ValueError('selected nickel composition outside specialist support')
            candidate.update(composition_wt_percent=c,
                ultimate_tensile_strength_MPa=deepcopy(ancillary['short_time_strength']['ultimate_tensile_strength_MPa']),
                proof_strength_0p2_MPa=deepcopy(ancillary['short_time_strength']['proof_strength_0p2_MPa']),
                creep_rupture=deepcopy(ancillary['creep_rupture']),
                elongation_percent_auxiliary=deepcopy(ancillary['tensile_ductility']),
                inputs=deepcopy(ancillary['inputs']),
                source_anchor={'kind': 'frozen_reference', 'anchor_index': row['composition']['anchor_index']})
        elif domain == 'copper_hot_end_local_composition_v1':
            require_matching(values, ancillary['short_time_tensile'],
                {'YS_MPa': 'yield_0p2_MPa', 'UTS_MPa': 'ultimate_tensile_strength_MPa'})
            if ancillary['applicability_domain'].get('level') != 'inside':
                raise ValueError('selected copper composition outside joint composition/process/temperature support')
            candidate.update(composition_wt_percent=c, material_state=ancillary['material_state'],
                source_anchor=deepcopy(ancillary['source_anchor']),
                short_time_tensile=deepcopy(ancillary['short_time_tensile']),
                additional_properties=deepcopy(ancillary['additional_properties']),
                inputs=deepcopy(ancillary['screening_conditions']))
        else:
            raise ValueError('domain lacks a production candidate contract')
        candidate['model_version'] = VERSION
        if domain in NICU:
            candidate['specialist_model_version'] = ancillary.get('model_version')
        output.append(candidate)
    return output


def promote(runner, task, constraints):
    """Return a fresh result only after the whole numerical/report check passes."""
    domain = task.get('model_domain')
    if mode_for(domain) != 'primary' or task.get('mode') == 'cryogenic_reference':
        return None
    started = time.perf_counter()
    dispatcher = RecipeShadowDispatcher(runner.results_root)
    try:
        record = dispatcher.run_sync(task['taskid'], task)
    finally:
        dispatcher.close()
    if record.get('status') != 'completed':
        raise RuntimeError('primary calculation did not pass its isolated contract')
    completed = record['results'][0]
    if completed['status'] == 'input_requires_revision':
        from src.alloy_workflow.specialist_outcome import scoped_outcome
        directory=Path(completed['presentation_directory']).parent
        response=json.loads((directory/'optimization_response.json').read_text())
        plan=task.get('requirement_interpretation') or {}
        scope=plan.get('effective_model_input') or constraints.get('raw_scope') or {'model_domain':domain}
        code=response.get('error_code','NO_ELIGIBLE_STARTING_REFERENCE')
        messages={'NO_ELIGIBLE_STARTING_REFERENCE':'指定范围内没有可用的优化起始参考配方。',
            'TEMPERATURE_SCOPE':'本次评价温度超出当前专项模型的验证温区。',
            'BOUNDS':'本次配比范围与模型可计算范围不相容。',
            'CONDITION_CONFLICT':'本次要求与模型可用的材料状态或评价条件不一致。',
            'UNSUPPORTED_CONSTRAINT':'本次包含当前优化器尚未支持的条件，原要求已保留。'}
        return scoped_outcome({'taskid':task['taskid']},scope,plan,task.get('service','alloy_optimization'),
            code,[messages.get(code,'当前专项模型无法在给定条件下形成可用配方。')],
            diagnostic=response.get('error'))[0]
    display = helper('recipe_display')
    directory = Path(completed['presentation_directory']).parent
    if domain == 'perovskite_transport_stability_v2':
        report = display.from_perovskite(task)
        candidates = deepcopy(task.get('initial_candidates', [])[:5])
        for candidate, row in zip(candidates, report['candidates']):
            candidate['candidate_id'] = row['display_id']
        nearest_candidates = []
    else:
        raw = json.loads((directory / 'optimization_response.json').read_text())
        request = json.loads((directory / 'optimization_request.json').read_text())
        if raw.get('result', {}).get('method') != 'nsga2':
            raise ValueError('approved primary optimizer was not used')
        report = display.from_rsi(raw, task, request=request)
        candidates = compatible_candidates(runner, task, report, constraints)
        nearest_candidates = compatible_candidates(runner, task, report, constraints, nearest=True)
    # Validate the SAME exact report that the frontend will render.
    markdown = display.build_markdown(report)
    display.validate_markdown(markdown)
    result = deepcopy(task)
    for key in ('_presentation_candidates', 'presentation', 'microstructure_tendency', 'derived_candidate_percentiles_at_pct',
                'nearest_candidates', 'reference_candidates', 'validation', 'uncertainty', 'nonlinear_response_function'):
        result.pop(key, None)
    result['initial_candidates'] = candidates
    result['nearest_candidates'] = nearest_candidates
    result['all_candidates'] = deepcopy(candidates or nearest_candidates)
    result['model_version'] = VERSION
    result['sampling'] = {'generated': report['counters']['model_evaluations'],
        'feasible': report['counters'].get('feasible', len(candidates)),
        'pareto_count': report['counters'].get('pareto_count', 0), 'method': report['method'],
        'stop_reason': report['stop_reason'], 'selected': len(candidates), 'nearest_count': len(nearest_candidates)}
    sources = {k: v for row in (report['candidates'] or report.get('nearest_candidates', [])) for k, v in row['property_sources'].items()}
    if domain == 'short_cf_thermomechanical_rve_v1':
        sources['fiber_volume_fraction'] = 'design_input'
        sources['anisotropy_ratio_E11_E22'] = 'derived_E11_over_E22'
        for candidate in candidates:
            candidate['property_sources'].update(fiber_volume_fraction='design_input',
                anisotropy_ratio_E11_E22='derived_E11_over_E22')
    result['recipe_release'] = {'mode': 'primary', 'version': VERSION, 'optimizer': report['method'],
        'property_sources': sources, 'model_metadata': deepcopy(report['model_metadata']),
        'authorization': 'user_approved_after_acceptance_20260928', 'public_effect': True,
        'display_allowed': True, 'production_release_authorized': True}
    if domain in NICU:
        config = json.loads(Path(os.getenv('MATERIAL_RECIPE_RELEASE_CONFIG', str(CONFIG))).read_text(encoding='utf-8'))
        acceptance = config['domain_acceptance'][domain]
        result['recipe_release'].update(adapter_version=NICU_ADAPTER_VERSION,
            authorization=acceptance['authorization'], model_stack='specialist')
    result['model_evidence'] = {'model_version': VERSION, 'property_sources': sources,
        'model_metadata': deepcopy(report['model_metadata'])}
    result['user_conclusion'] = downstream_summary(report, task['taskid'], display)
    result['downstream_handoff_text'] = ('本次配方及性质采用recipe_release记录的模型与优化方法；'
        + ('没有全部达标配方；接近方案及要求缺口保留在nearest_candidates，不作为通过方案。' if nearest_candidates else
           '组成、条件和完整性质保留在initial_candidates。'))
    result['nonlinear_response_function'] = {'name': VERSION, 'property_sources': sources,
        'mathematical_form': 'fixed predictor(composition, descriptors, temperature, process)',
        'public_evaluate_endpoints': 'existing specialist versions; consult response model_version',
        'optimizer': report['method']}
    result['elapsed_seconds'] = round(task.get('elapsed_seconds', 0) + time.perf_counter() - started, 3)
    result['_recipe_report'] = report
    return result


def render_primary(result, directory):
    """Called ONLY by runtime._render; existing publisher owns all uploads."""
    report = result['_recipe_report']
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    display = helper('recipe_display')
    assets = helper('recipe_plots').render_assets(report, directory)
    markdown = display.build_markdown(report)
    display.validate_markdown(markdown)
    path = directory / 'summary.md'
    path.write_text(markdown, encoding='utf-8')
    return {'summary': path, **{item['name']: directory / item['filename'] for item in assets}}


def embed_primary(markdown, visual_assets):
    return helper('recipe_display').embed_assets(markdown, visual_assets or [], public=True)
