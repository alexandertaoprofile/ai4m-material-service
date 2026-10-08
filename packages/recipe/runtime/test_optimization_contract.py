from optimization_contract import map_resolved_objectives,build_optimization_shadow
from bounded_nsga import objectives
import pytest

def test_existing_goal_keys_normalized_without_new_objective():
    units={'YS_MPa':'MPa','UTS_MPa':'MPa'}
    r=map_resolved_objectives({'objectives':{'yield_strength_MPa':{'goal':'maximize'}}},units)
    assert r==[dict(property='YS_MPa',mode='max',scale=1.,unit='MPa')]
    assert len(objectives(r,units))==1
def test_single_target_preserved():
    units={'YS_MPa':'MPa'}
    r=map_resolved_objectives({'objectives':{'yield_strength_MPa':{'goal':'target','target':1000}}},units)
    assert r[0]['mode']=='target' and r[0]['target']==1000
@pytest.mark.parametrize('raw',[{},None,{'unknown':{'goal':'maximize'}},{'YS_MPa':{'goal':'guess'}},{'YS_MPa':0},{'YS_MPa':{'goal':'maximize','unit':'GPa'}}])
def test_missing_ambiguous_or_unsupported_goals_fail_closed(raw):
    with pytest.raises(ValueError):
        objectives(map_resolved_objectives({'objectives':raw},{'YS_MPa':'MPa'}),{'YS_MPa':'MPa'})
def test_no_objective_no_fake_optimization():
    assert build_optimization_shadow({'model_domain':'x'})==({'items':[]},None)
