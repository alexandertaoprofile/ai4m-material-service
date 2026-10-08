"""Explicit original-field semantics for generated versus supplied recipes."""
from copy import deepcopy
from stainless_support import canonical_composition

def prepare_stainless_input(actual_inputs, *, generated_design=False, declared_bounds=None):
    actual=deepcopy(actual_inputs)
    composition=deepcopy(actual['composition_wt_percent'])
    statuses=deepcopy(actual.get('composition_status') or {})
    if generated_design:
        if not isinstance(declared_bounds,dict):
            raise ValueError('generated_design requires the actual resolved element bounds')
        for element in ('P','S'):
            if element not in declared_bounds and composition.get(element,0.)==0.:
                composition.pop(element,None)
                statuses[element]='not_specified'
    normalized,status=canonical_composition(composition,statuses)
    actual['composition_wt_percent']=normalized
    actual['composition_status']=status
    actual['composition_semantics']='nominal_design'
    return actual
