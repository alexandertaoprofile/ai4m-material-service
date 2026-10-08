"""Map already-normalized specialist inputs; never change public results.

Do not pass an unparsed user prompt or guess implicit balance/default process.
The service must pass the actual inputs used by its specialist prediction.
"""
import copy
import math
from alloy_v10_shadow import InputError, _number as number

OUTPUT_KEYS = {
    'aerospace_aluminum_composition_v1': {'yield_strength_0p2_MPa':'yield_strength_MPa', 'ultimate_tensile_strength_MPa':'uts_MPa'},
    'reusable_rocket_stainless': {'yield_strength_0p2_MPa':'yield_0p2_MPa', 'ultimate_tensile_strength_MPa':'uts_MPa'},
    'ni_superalloy_hot_end': {'yield_strength_0p2_MPa':'proof_strength_0p2_MPa', 'ultimate_tensile_strength_MPa':'ultimate_tensile_strength_MPa'},
}

def adapt(service_domain, actual_inputs, request_id=None):
    """Returns a raw model request plus explicit information-loss audit."""
    if service_domain not in OUTPUT_KEYS:
        raise InputError('unsupported_service_domain', 'Only aluminum, stainless and nickel shadow mappings exist')
    if not isinstance(actual_inputs, dict):
        raise InputError('invalid_specialist_inputs', 'Pass the resolved actual specialist inputs')
    original = copy.deepcopy(actual_inputs)
    comp = actual_inputs.get('composition_wt_percent')
    if not isinstance(comp, dict):
        raise InputError('missing_composition', 'Use explicit closed composition_wt_percent including the balance element')
    audit = {'service_domain':service_domain, 'specialist_actual_inputs':original,
        'mapped_fields':['composition_wt_percent'], 'unmodeled_fields':[], 'defaults_used':[],
        'comparison_scope':'composition_and_encoded_context_only', 'equivalent_full_process_conditions':False}
    if service_domain == 'aerospace_aluminum_composition_v1':
        domain = 'aluminum'
        label = actual_inputs.get('processing_category')
        if not isinstance(label, str) or not label.strip():
            raise InputError('missing_processing_category', 'Pass the actual aluminum processing_category')
        temp = number(actual_inputs.get('test_temperature_C', 25.0), 'test_temperature_C')
        if 'test_temperature_C' not in actual_inputs:
            audit['defaults_used'].append('25 C: existing aluminum model room-temperature scope')
        if abs(temp-25.0) > 1e-6:
            raise InputError('aluminum_temperature_scope', 'Current aluminum specialist comparison is room temperature (25 C convention) only')
        temperature = {'value':temp, 'unit':'C'}
        audit['mapped_fields'] += ['processing_category', 'test_temperature_C']
        audit['equivalent_full_process_conditions'] = True  # same categorical input, not a detailed process simulation
        audit['comparison_scope'] = 'same_composition_room_temperature_processing_category'
    elif service_domain == 'reusable_rocket_stainless':
        domain = 'stainless'
        temp = number(actual_inputs.get('test_temperature_K'), 'test_temperature_K')
        if not 293 <= temp <= 1273:
            raise InputError('stainless_temperature_scope', 'Cryogenic/reference and >1273 K branches are excluded')
        process = actual_inputs.get('processing')
        if not isinstance(process, dict) or process.get('quench') not in ('water','air'):
            raise InputError('missing_resolved_process', 'Pass actual processing including quench and product_form_code')
        form = number(process.get('product_form_code'), 'product_form_code')
        if not form.is_integer():
            raise InputError('product_form_code', 'Expected integer product_form_code')
        water = process['quench'] == 'water'
        # Exact training string, including decimal quench values. No ST-WQ relabeling.
        label = f'form={int(form)}|wq={float(water)}|aq={float(not water)}'
        temperature = {'value':temp, 'unit':'K'}
        audit['mapped_fields'] += ['test_temperature_K', 'processing.product_form_code', 'processing.quench']
        audit['unmodeled_fields'] += ['processing.'+k for k in process if k not in ('product_form_code','quench')]
        audit['defaults_used'] = copy.deepcopy(actual_inputs.get('platform_defaults_used', []))
        audit['comparison_scope'] = 'same_composition_temperature_form_quench_not_solution_temperature_time'
    else:
        domain = 'nickel_superalloy'
        label = actual_inputs.get('manufacturing_route')
        if label not in ('cast', 'directionally_solidified', 'single_crystal'):
            raise InputError('manufacturing_route', 'Pass the actual supported manufacturing_route')
        temperature = {'value':number(actual_inputs.get('test_temperature_C'), 'test_temperature_C'), 'unit':'C'}
        audit['mapped_fields'] += ['manufacturing_route','test_temperature_C']
        audit['unmodeled_fields'] += [k for k in ('heat_treatment','casting_gradient_K_per_mm','applied_stress_MPa') if k in actual_inputs]
        audit['comparison_scope'] = 'same_composition_temperature_manufacturing_route_not_multistage_heat_treatment'
    represented = {'composition_wt_percent'}
    if domain == 'aluminum':
        represented |= {'processing_category','test_temperature_C'}
    elif domain == 'stainless':
        represented |= {'processing','test_temperature_K','platform_defaults_used'}
    else:
        represented |= {'manufacturing_route','test_temperature_C'}
    audit['unmodeled_fields'] = sorted(set(audit['unmodeled_fields']) | (set(actual_inputs)-represented))
    if domain == 'aluminum' and audit['unmodeled_fields']:
        audit['equivalent_full_process_conditions'] = False
    request = {'request_id':request_id, 'domain':domain, 'composition':copy.deepcopy(comp),
        'composition_basis':'wt_percent', 'temperature':temperature, 'process':{'text':label,'parameters':{}},
        'context':audit}
    return request, audit

def compare_many(model, items):
    """Internal comparison only. items = service_domain, actual_inputs, legacy_properties.

    legacy_properties is an already-extracted MPa scalar dictionary. This helper
    intentionally does not mutate/rewrite the specialist candidate or property card.
    """
    if not isinstance(items, list) or not 1 <= len(items) <= 4096:
        raise InputError('batch_size', 'items must contain 1–4096 objects')
    output, requests, positions = [], [], []
    for i, item in enumerate(items):
        row = {'index':i, 'display_allowed':False, 'production_release_authorized':False}
        try:
            if not isinstance(item, dict): raise InputError('invalid_item', 'Comparison item must be an object')
            req, audit = adapt(item.get('service_domain'), item.get('actual_inputs'), item.get('request_id'))
            legacy = item.get('legacy_properties', {})
            if not isinstance(legacy, dict): raise InputError('legacy_properties', 'Expected MPa scalar dictionary')
            expected_keys = set(OUTPUT_KEYS[item['service_domain']].values())
            if set(legacy)-expected_keys: raise InputError('legacy_properties', 'Supply only the two mapped strength properties')
            for k,v in legacy.items(): number(v, k)
            row['mapping_audit'] = audit
            requests.append(req)
            positions.append(i)
        except InputError as exc:
            row.update({'status':'skipped_mapping', 'error':{'code':exc.code,'message':str(exc)}})
        output.append(row)
    if requests:
        batch = model.predict_many(requests, mode='shadow')
        for i, prediction in zip(positions, batch['results']):
            row = output[i]
            row['shadow_result'] = prediction
            row['status'] = prediction['status']
            collisions = prediction.get('input_audit', {}).get('original_process_labels_sharing_encoding', [])
            if len(collisions) > 1:
                row['mapping_audit']['equivalent_full_process_conditions'] = False
                row['mapping_audit']['original_process_labels_collapsed_by_encoder'] = collisions
            if prediction['status'] == 'predicted':
                mapping = OUTPUT_KEYS[items[i]['service_domain']]
                values = {mapping[k]:v for k,v in prediction['prediction'].items()}
                row['internal_property_projection_MPa'] = values
                row['transformer_minus_legacy_MPa'] = {k:v-items[i]['legacy_properties'][k] for k,v in values.items() if k in items[i].get('legacy_properties', {})}
                row['difference_is_measured_error'] = False
    return {'mode':'shadow', 'display_allowed':False, 'production_release_authorized':False,
        'model':{'version':model.config['version'], 'source_checkpoint_sha256':model.config['source_checkpoint_sha256'],
            'serving_weights_sha256':model.weight_sha256, 'single_checkpoint':True}, 'comparisons':output}
