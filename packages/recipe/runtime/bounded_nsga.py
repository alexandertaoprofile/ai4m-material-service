"""Budgeted numerical search. Prediction, admissibility and user preferences
are separate; Pareto dominance does not certify experimental performance.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import time
import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import Problem
from pymoo.core.termination import Termination
from pymoo.optimize import minimize
from pymoo.util.nds.non_dominated_sorting import NonDominatedSorting


@dataclass(frozen=True)
class Limits:
    population: int = 32
    model_evaluations: int = 256
    proposals: int = 20480
    seconds: float = 30.0

    def __post_init__(self):
        if any(isinstance(v,bool) or not isinstance(v,int) for v in
               (self.population,self.model_evaluations,self.proposals)):
            raise ValueError('population and evaluation/proposal counts must be JSON integers')
        if isinstance(self.seconds,bool) or not isinstance(self.seconds,(int,float)) or not math.isfinite(self.seconds):
            raise ValueError('wall budget must be a finite number')
        if not 4 <= self.population <= 128 or not 1 <= self.model_evaluations <= 4096:
            raise ValueError('population or evaluation budget out of bounds')
        if not self.model_evaluations <= self.proposals <= 100000 or not 0 < self.seconds <= 45:
            raise ValueError('invalid proposal or wall budget')


def objectives(specs, allowed):
    if not isinstance(specs, list) or not 1 <= len(specs) <= 4:
        raise ValueError('one to four explicit objectives required')
    clean=[]
    for item in specs:
        if not isinstance(item,dict) or set(item)-{'property','mode','target','scale','unit'}:
            raise ValueError('unknown objective fields')
        name=item.get('property'); mode=item.get('mode')
        if name not in allowed or item.get('unit')!=allowed[name] or mode not in {'min','max','target'}:
            raise ValueError('unknown property, unit mismatch or unsupported objective')
        scale=item.get('scale',1.0)
        if isinstance(scale,bool) or not isinstance(scale,(int,float)) or not math.isfinite(scale) or scale<=0:
            raise ValueError('objective scale must be positive and finite')
        if mode=='target':
            target=item.get('target')
            if isinstance(target,bool) or not isinstance(target,(int,float)) or not math.isfinite(target):
                raise ValueError('target objective requires an explicit finite target')
        elif 'target' in item:
            raise ValueError('target supplied for a non-target objective')
        clean.append({**item,'scale':float(scale)})
    if len({x['property'] for x in clean})!=len(clean):
        raise ValueError('duplicate property objectives')
    return clean


def objective_vector(values, specs):
    output=[]
    for item in specs:
        v=float(values[item['property']])
        if not math.isfinite(v): raise ValueError('nonfinite model output')
        # Parallel tree summation can differ in the last floating-point bits.
        # Tie handling must not turn those arithmetic bits into different
        # evolutionary paths. This is ranking resolution, not uncertainty.
        digits=3 if item['unit']=='MPa' else 6
        v=round(v,digits)
        value=abs(v-item['target']) if item['mode']=='target' else (-v if item['mode']=='max' else v)
        output.append(value/item['scale'])
    return output


def constraint_cost(row):
    """Performance gaps guide search; invalid/support-rejected rows stay out."""
    if row.get('feasible') is True:return 0.
    if row.get('admissible') is not True:return 1e20
    value=float(row.get('constraint_violation',1.))
    if not math.isfinite(value) or value<0:raise ValueError('invalid constraint violation')
    return min(max(value,1e-12),1e12)


class BudgetProblem(Problem):
    def __init__(self, space, evaluate, specs, limits, initial=None):
        self.space=space; self.predict=evaluate; self.specs=specs; self.limits=limits
        self.started=time.perf_counter(); self.forward_rows=0; self.proposed=0
        self.history=[]; self.stop_reason=None; self.cache={};self.proposal_log=[];self.batch_log=[];self.generation=0
        super().__init__(n_var=len(space.lower),n_obj=len(specs),n_ieq_constr=1,
                         xl=np.asarray(space.lower),xu=np.asarray(space.upper))

    def reason(self):
        if self.forward_rows>=self.limits.model_evaluations:return 'model_evaluation_budget'
        if self.proposed>=self.limits.proposals:return 'proposal_budget'
        if time.perf_counter()-self.started>=self.limits.seconds:return 'soft_wall_budget'
        return None

    def _evaluate(self, X, out, *args, **kwargs):
        X=np.atleast_2d(X); F=np.full((len(X),len(self.specs)),1e12); G=np.ones((len(X),1))
        selected=[];batch_started=time.perf_counter();generation=self.generation;self.generation+=1
        for i,x in enumerate(X):
            reason=self.reason()
            if reason:
                self.stop_reason=reason;continue
            self.proposed+=1
            comp=self.space.decode(x)
            event=dict(proposal_index=self.proposed,generation=generation,normalized_search_vector=x.tolist())
            self.proposal_log.append(event)
            if comp is None:
                event.update(status='invalid_composition',composition=None);continue
            event['composition']=comp
            # Count evaluated compositions (not model heads) identically for
            # random and NSGA-II. Repeated candidates are cached, not a free
            # opportunity to exceed the real forward budget.
            key=self.space.key(comp)
            if key in self.cache:
                item=self.cache[key];F[i]=item['F'];G[i]=constraint_cost(item)
                event.update(status='cached',candidate_id=item['candidate_id'])
                continue
            if self.forward_rows+len(selected)>=self.limits.model_evaluations:
                event.update(status='budget_deferred');continue
            if any(entry[2]==key for entry in selected):
                event.update(status='batch_duplicate');continue
            selected.append((i,comp,key,event))
        if selected:
            rows=self.predict([item[1] for item in selected])
            if len(rows)!=len(selected):raise ValueError('predictor length mismatch')
            self.forward_rows+=len(rows)
            for (i,comp,key,event),row in zip(selected,rows):
                vals=row['values']; fv=objective_vector(vals,self.specs)
                okay=row.get('feasible') is True
                item=dict(composition=comp,values=vals,F=fv,feasible=okay,
                          support=row.get('support'),property_sources=row.get('property_sources',{}),
                          support_admitted=row.get('support_admitted'),physical_valid=row.get('physical_valid'),
                          admissible=row.get('admissible'),performance_feasible=row.get('performance_feasible'),
                          constraint_gaps=row.get('constraint_gaps',{}),constraint_violation=row.get('constraint_violation',0.),
                          candidate_id='candidate-'+str(len(self.history)+1).zfill(6),generation=generation,
                          evaluation_seconds=(time.perf_counter()-batch_started)/len(rows))
                event.update(status='evaluated_feasible' if okay else 'evaluated_infeasible',candidate_id=item['candidate_id'])
                self.cache[key]=item;self.history.append(item)
                F[i]=fv;G[i]=constraint_cost(item)
        self.batch_log.append(dict(generation=generation,proposals_total=self.proposed,model_evaluations_total=self.forward_rows,
                                   feasible_total=sum(x['feasible'] for x in self.history),
                                   elapsed_seconds=time.perf_counter()-self.started,batch_seconds=time.perf_counter()-batch_started))
        out['F']=F;out['G']=G


class BudgetTermination(Termination):
    def _update(self, algorithm):
        p=algorithm.problem;r=p.reason()
        if r:p.stop_reason=r;return 1.0
        return min(.99,max(p.forward_rows/p.limits.model_evaluations,p.proposed/p.limits.proposals,
                           (time.perf_counter()-p.started)/p.limits.seconds))


def search(space,evaluate,specs,limits,seed,method='nsga2'):
    if method not in {'nsga2','random'}:raise ValueError('unknown search method')
    if hasattr(space,'reset'):space.reset()
    rng=np.random.default_rng(seed);initial=space.sample(rng,limits.population)
    problem=BudgetProblem(space,evaluate,specs,limits)
    if method=='nsga2':
        minimize(problem,NSGA2(pop_size=limits.population,sampling=initial,
                              eliminate_duplicates=False),termination=BudgetTermination(),
                 seed=seed,copy_algorithm=False,copy_termination=False,verbose=False)
    else:
        batch=initial
        while not problem.reason():
            out={};problem._evaluate(batch,out)
            batch=space.sample(rng,limits.population)
        problem.stop_reason=problem.reason()
    feasible=[r for r in problem.history if r['feasible']]
    indices=NonDominatedSorting().do(np.asarray([r['F'] for r in feasible]),only_non_dominated_front=True) if feasible else []
    front=[feasible[int(i)] for i in indices]
    # Preference ranking is deliberately absent: there is no unique "best"
    # multi-objective recipe without an explicit user choice.
    return dict(ok=True,mode='shadow',display_allowed=False,production_release_authorized=False,
                public_effect=False,method=method,seed=seed,status='completed' if front else 'no_feasible_candidate',
                objectives=specs,stop_reason=problem.stop_reason,
                objective_comparison_decimals={'MPa':3,'other_units':6},
                counters=dict(proposals=problem.proposed,model_evaluations=problem.forward_rows,
                              feasible=len(feasible),pareto_count=len(front)),
                elapsed_seconds=time.perf_counter()-problem.started,
                pareto_front=front,history=problem.history,proposal_log=problem.proposal_log,batch_log=problem.batch_log,
                prediction_is_experimental_validation=False)
