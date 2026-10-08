import numpy as np
import pytest
from bounded_nsga import Limits,objectives,objective_vector,search

class Space:
    lower=[0.,0.];upper=[1.,1.]
    def sample(self,rng,n):return rng.uniform(0,1,(n,2))
    def decode(self,x):return dict(x=list(map(float,x)))
    def key(self,x):return tuple(x['x'])
def predictor(rows):return [dict(values={'strength':r['x'][0]*1000,'mass':r['x'][1]},feasible=True) for r in rows]
SPECS=objectives([dict(property='strength',mode='max',unit='MPa'),dict(property='mass',mode='min',unit='kg')],{'strength':'MPa','mass':'kg'})
@pytest.mark.parametrize('method',['random','nsga2'])
def test_exact_budget_and_nondominance(method):
    r=search(Space(),predictor,SPECS,Limits(population=8,model_evaluations=19,proposals=100,seconds=5),17,method)
    assert r['counters']['model_evaluations']==19
    assert r['stop_reason']=='model_evaluation_budget'
    assert r['display_allowed'] is False and r['production_release_authorized'] is False
    F=np.asarray([x['F'] for x in r['pareto_front']])
    assert all(not any(np.all(p<=q) and np.any(p<q) for p in F) for q in F)
@pytest.mark.parametrize('method',['random','nsga2'])
def test_zero_feasible_not_fake_best(method):
    def no(rows):return [dict(values={'strength':1.,'mass':1.},feasible=False) for _ in rows]
    r=search(Space(),no,SPECS,Limits(population=8,model_evaluations=16,proposals=100,seconds=5),17,method)
    assert r['status']=='no_feasible_candidate' and r['pareto_front']==[]
def test_invalid_compositions_stop_on_proposals():
    class Bad(Space):
        def decode(self,x):return None
    r=search(Bad(),predictor,SPECS,Limits(population=8,model_evaluations=16,proposals=32,seconds=5),17)
    assert r['stop_reason']=='proposal_budget' and r['counters']['model_evaluations']==0
def test_single_target_not_strength_maximization():
    spec=objectives([dict(property='strength',mode='target',target=1000,scale=100,unit='MPa')],{'strength':'MPa'})
    assert objective_vector({'strength':1000},spec)==[0]
    assert objective_vector({'strength':900},spec)==[1]
    assert objective_vector({'strength':1100},spec)==[1]

def test_fractional_boolean_and_nonfinite_budgets_are_rejected():
    for changes in ({'population':32.5},{'model_evaluations':True},{'model_evaluations':256.5},
                    {'proposals':20480.1},{'seconds':True},{'seconds':float('nan')}):
        with pytest.raises(ValueError):Limits(**changes)
@pytest.mark.parametrize('item',[
    dict(property='x',mode='max',unit='MPa'),dict(property='strength',mode='max',unit='GPa'),
    dict(property='strength',mode='target',unit='MPa'),dict(property='strength',mode='max',unit='MPa',scale=0),
    dict(property='strength',mode='max',unit='MPa',target=2),dict(property='strength',mode='max',unit='MPa',scale=float('nan'))])
def test_invalid_objective_rejected(item):
    with pytest.raises(ValueError):objectives([item],{'strength':'MPa'})
