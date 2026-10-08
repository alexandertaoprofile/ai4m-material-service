"""Reporting-aware composition support shared by both stainless predictors.

This is geometry, not a calibrated prediction interval or phase-stability model.
Nitrogen is an active alloy constituent and is always checked. Unspecified P/S
are excluded from comparison, not filled with invented impurity measurements.
"""
from functools import lru_cache
from pathlib import Path
import json
import math
import numpy as np

VERSION = 'stainless_reporting_aware_wt_v1_20260928'
ROOT = Path(__file__).resolve().parent
ALLOWED_STATUSES = {'specified', 'not_specified'}

def canonical_composition(composition, statuses=None):
    if not isinstance(composition, dict) or not composition:
        raise ValueError('composition must be a nonempty wt.% object')
    statuses = dict(statuses or {})
    if any(s not in ALLOWED_STATUSES for s in statuses.values()):
        raise ValueError('composition_status must be specified or not_specified')
    clean = {}
    for key, value in composition.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid wt.% for ' + str(key))
        if statuses.get(key) == 'not_specified':
            raise ValueError('a numerical amount contradicts not_specified: ' + key)
        clean[key] = float(value)
        statuses[key] = 'specified'
    for element in ('Cr', 'Ni', 'C', 'N'):
        if element not in clean:
            raise ValueError('stainless design must specify ' + element + '; missing is not zero')
    if clean['Cr'] <= 0 or clean['Ni'] <= 0:
        raise ValueError('Cr and Ni must be positive')
    solutes = sum(v for e, v in clean.items() if e != 'Fe')
    balance = 100 - solutes
    if balance <= 0 or ('Fe' in clean and abs(clean['Fe'] - balance) > .01):
        raise ValueError('Fe must close the nominal formulation to 100 wt.%')
    clean['Fe'] = balance
    statuses['Fe'] = 'specified'
    for element in ('P', 'S'):
        statuses.setdefault(element, 'not_specified' if element not in clean else 'specified')
    if set(statuses) - set(clean) - {'P', 'S'}:
        raise ValueError('only P/S may currently be not_specified in a stainless design')
    return clean, statuses

@lru_cache(maxsize=1)
def load_profile():
    return json.loads((ROOT / 'stainless_support_profile.json').read_text())

def evaluate(composition, temperature_C, statuses=None, profile=None):
    profile = profile or load_profile()
    clean, status = canonical_composition(composition, statuses)
    elements = profile['elements']
    extra = sorted(set(clean) - set(elements) - {'Fe'})
    if extra:
        raise ValueError('elements outside stainless support vocabulary: ' + ','.join(extra))
    mask = [e for e in elements if not (e in {'P', 'S'} and status[e] == 'not_specified')]
    mask_key = ','.join(e for e in ('P', 'S') if e in mask)
    reference = np.asarray(profile['reference_wt_percent'], float)
    indexes = [elements.index(e) for e in mask]
    scale = np.asarray(profile['scale_wt_percent'], float)[indexes]
    query = np.asarray([clean.get(e, 0.) for e in mask])
    distances = np.sqrt(np.mean(((reference[:, indexes] - query) / scale) ** 2, axis=1))
    nearest = int(distances.argmin())
    limits = profile['mask_thresholds'][mask_key]
    distance = float(distances[nearest])
    minimum = np.asarray(profile['minimum_wt_percent'], float)
    maximum = np.asarray(profile['maximum_wt_percent'], float)
    out_elements = [e for e in mask if not minimum[elements.index(e)] - 1e-8 <= clean.get(e, 0.) <= maximum[elements.index(e)] + 1e-8]
    if isinstance(temperature_C, bool) or not isinstance(temperature_C, (float, int)) or not math.isfinite(temperature_C):
        raise ValueError('test temperature must be explicit and finite')
    temperature_ok = profile['temperature_C_range'][0] <= temperature_C <= profile['temperature_C_range'][1]
    level = 'inside' if distance <= limits['p95'] else 'boundary' if distance <= limits['p99'] else 'outside'
    if out_elements or not temperature_ok:
        level = 'outside'
    nitrogen_idx = elements.index('N')
    return dict(version=VERSION, level=level, admitted=level != 'outside',
                distance=distance, threshold_p95=limits['p95'], threshold_p99=limits['p99'],
                distance_basis='masked standardized wt.% root-mean-square distance',
                compared_elements=mask, composition_status=status,
                unspecified_elements=[e for e in ('P', 'S') if status[e] == 'not_specified'],
                nitrogen_checked=True, nitrogen_wt_percent=clean['N'],
                nitrogen_training_range=[float(minimum[nitrogen_idx]), float(maximum[nitrogen_idx])],
                outside_element_ranges=out_elements, temperature_supported=bool(temperature_ok),
                nearest_reference_key=profile['reference_keys'][nearest],
                nearest_reference_wt_percent=dict(zip(elements, reference[nearest].tolist())),
                calibrated_candidate_uncertainty=False,
                composition_semantics='nominal design; unspecified impurities are not measured zeros')
