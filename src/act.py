""" balance-sized P trials and exhaustive discrete Q validation after one decision."""
import copy
from schemas import Config,LegalActionSpace,ActionCandidate,PFTrialResult,ActionSelectionResult,PFObservation
from see import state_metrics,deviation,legal_directions,setting,remote_generators

def validate_llm_decision(decision,context):
    if decision.state_id!=context.state_id:raise ValueError('stale_decision')
    rec=next((x for x in context.candidates if x.candidate_id==decision.device_id),None)
    if rec is None:raise ValueError('outside_screened_resources')
    if decision.direction not in rec.legal_direction_mask:raise ValueError('illegal_or_previously_rejected_direction')
    return decision

def construct_legal_action_space(device,direction,controls,cfg,network,task,state_id):
    d=device
    if direction not in legal_directions(d,controls):raise ValueError('illegal_direction')
    sign=1 if direction.startswith('increase') else -1
    curr=setting(d,controls);bound=d.upper-curr if sign>0 else curr-d.lower
    paired=None;factor=0.
    if d.kind=='hvdc':
        # Positive transfer draws at from_bus and injects at to_bus.
        # The other area's generator compensates that area's net transfer change.
        factor=-1. if task.area_id==d.from_area else 1.
        eligible=[]
        for g in remote_generators(network,d,task.area_id,controls):
            pg=setting(g,controls);delta_sign=factor*sign
            margin=g.upper-pg if delta_sign>0 else pg-g.lower
            if margin>1e-9:eligible.append((g.device_id,margin))
        if not eligible:raise ValueError('no_legal_HVDC_pair')
        paired,pair_margin=sorted(eligible)[0];bound=min(bound,pair_margin)
    if d.kind=='shunt':
        vals=list(range(1,int(bound)+1))
        if len(vals)>cfg.discrete_budget:raise ValueError('discrete_space_exceeds_declared_enumeration_budget')
        budget=len(vals);resolution=1.
    else:
        bound=min(bound,cfg.max_p_step_mw);vals=[];budget=cfg.continuous_budget;resolution=cfg.search_resolution_mw
    if bound<=1e-9 or budget<1:raise ValueError('empty_action_space')
    return LegalActionSpace(state_id,d.device_id,d.kind,direction,sign,0.,float(bound),vals,budget,resolution,paired,factor)

def apply_candidate(controls,candidate,network):
    new=copy.deepcopy(controls);d=network.devices[candidate.device_id];x=candidate.signed_magnitude
    mapping=new.generator_pg if d.kind=='generator' else new.shunt_steps if d.kind=='shunt' else new.hvdc_mw
    value=mapping[d.device_id]+x
    if not d.lower-1e-8<=value<=d.upper+1e-8:raise ValueError('candidate_device_bound')
    if d.kind=='shunt' and abs(x-round(x))>1e-9:raise ValueError('noninteger_shunt')
    mapping[d.device_id]=int(round(value)) if d.kind=='shunt' else value
    if candidate.paired_generator_id:
        g=network.devices[candidate.paired_generator_id]
        pg=new.generator_pg[g.device_id]+candidate.paired_delta_pg
        if not g.lower-1e-8<=pg<=g.upper+1e-8:raise ValueError('paired_generator_bound')
        new.generator_pg[g.device_id]=pg
    return new

def blackbox_pf_evaluate(base_state,candidate,solver,network,task,cfg):
    before_key=base_state.controls.version(network.network_id)
    controls=apply_candidate(base_state.controls,candidate,network)
    try:obs=solver(controls)
    except Exception as e:
        obs=PFObservation(False,{}, {},{});error='solver_exception_'+type(e).__name__
    else:error='ac_nonconvergent'
    if base_state.controls.version(network.network_id)!=before_key:raise AssertionError('trial_contaminated_base')
    b=state_metrics(base_state.pf,cfg);m=state_metrics(obs,cfg)
    if not obs.converged:
        return PFTrialResult(candidate,False,None,{}, {'convergence':False},False,error,obs)
    protected={'convergence':True,'balancing_violation_mw':m['balancing_violation_mw']}
    if task.stage=='P':
        progress=m['balancing_violation_mw']<b['balancing_violation_mw']-1e-6
        reason='accepted' if progress else 'no_active_progress'
        priority={'balancing_violation_mw':m['balancing_violation_mw']}
    else:
        p_ok=m['balancing_violation_mw']<=1e-6
        target_gap=deviation(obs.voltage_pu[task.target_id],cfg)
        target_improved=target_gap<task.violation_magnitude-1e-8
        global_improved=(m['voltage_violation_count']<b['voltage_violation_count'] or
             (m['voltage_violation_count']==b['voltage_violation_count'] and m['voltage_severity_pu']<b['voltage_severity_pu']-1e-8))
        reason='balancing_guard' if not p_ok else 'no_target_voltage_progress' if not target_improved else 'no_global_voltage_progress' if not global_improved else 'accepted'
        priority={'target_deviation_pu':target_gap,'voltage_violation_count':m['voltage_violation_count'],'voltage_severity_pu':m['voltage_severity_pu']}
    return PFTrialResult(candidate,True,m,priority,protected,reason=='accepted',reason,obs)

def stage_feasible(trial,task):
    if not trial.convergence or not trial.accepted:return False
    m=trial.metrics
    return m['balancing_violation_mw']<=1e-6 and (task.stage=='P' or m['voltage_violation_count']==0)

def trial_rank(trial,task):
    a=abs(trial.candidate.signed_magnitude)
    if not trial.accepted:return (2, float('inf'),a)
    if stage_feasible(trial,task):return (0,a,trial.metrics['voltage_severity_pu'])
    m=trial.metrics
    return (1,m['balancing_violation_mw'],a,m['voltage_severity_pu']) if task.stage=='P' else (1,m['voltage_violation_count'],m['voltage_severity_pu'],a)

def select_or_refine_action(trials,task,action_space,remaining_budget,state_id=None):
    valid=[t for t in trials if t.accepted]
    selected=min(valid,key=lambda t:trial_rank(t,task)) if valid else None
    status='accepted' if selected else 'reject'
    return ActionSelectionResult(state_id or action_space.state_id,task,action_space,trials,selected,status,[])

def target_residual(pg,task):
    """Signed distance to the originally violated P bound, from public outputs."""
    return pg-task.limits[1] if task.violation_type=='above_upper' else task.limits[0]-pg

def target_restored(trial,task):
    if not trial.convergence or not trial.accepted:return False
    pg=trial.observation.balancing_pg[task.area_id]
    return task.limits[0]-1e-7<=pg<=task.limits[1]+1e-7

def next_active_power_proposal(space,trials,remaining_budget,task,cfg,batch_id=0):
    """One candidate at a time, from the same accepted base; never minimize MW.

    Initial magnitude uses the measured imbalance plus a small reserve. Only
    after a real trial may a base-to-trial power-balance correction be used.
    Nonconvergence/nonprogress allows one smaller confirmation, not a grid.
    Returns (candidate batch, stopping reason); no PF call happens here.
    """
    if task is None or task.stage!='P':raise ValueError('continuous_P_task_required')
    if not 0.<=cfg.p_reserve_fraction<1.:raise ValueError('invalid_P_reserve_fraction')
    if any(target_restored(t,task) for t in trials):return [],'P_target_restored'
    gap=target_residual(task.current_value,task)
    if gap<=0.:raise ValueError('P_target_not_violated')
    reserve=min(cfg.p_reserve_fraction*gap,.25*(task.limits[1]-task.limits[0]))
    positive=[t for t in trials if t.accepted and
              gap-target_residual(t.observation.balancing_pg[task.area_id],task)>1e-6]
    # A legal endpoint that improves the still-violated target is a valid
    # progressive action; searching smaller amplitudes has no useful purpose.
    if any(abs(t.candidate.signed_magnitude)>=space.upper_magnitude-1e-9 and
           target_residual(t.observation.balancing_pg[task.area_id],task)>0 for t in positive):
        return [],'single_step_limit_reached'
    if len(trials)>=2 and not positive:
        return [],'no_effective_progress' if any(t.convergence for t in trials) else 'nonconvergent_trials_rejected'
    if remaining_budget<=0:return [],'trial_budget_exhausted'
    seen=[abs(t.candidate.signed_magnitude) for t in trials]
    if not trials:
        val=min(space.upper_magnitude,gap+reserve)
        reason='measured_P_gap_plus_reserve_clipped_to_legal_margin'
    elif not positive:
        val=seen[0]/2.
        reason='one_smaller_check_after_nonprogress_or_nonconvergence'
    else:
        def distance(t):
            pg=t.observation.balancing_pg[task.area_id]
            return max(task.limits[0]-pg,pg-task.limits[1],0.)
        anchor=min(positive,key=lambda t:(distance(t),-abs(t.candidate.signed_magnitude)))
        mag=abs(anchor.candidate.signed_magnitude)
        residual=target_residual(anchor.observation.balancing_pg[task.area_id],task)
        observed_reduction=gap-residual
        val=min(space.upper_magnitude,mag*(gap+reserve)/observed_reduction)
        reason='correct_using_observed_base_to_trial_power_balance'
        # If a larger tried value was unusable, remain inside that observed
        # interval. This is recovery of feasibility, never post-feasible tuning.
        bad_above=[abs(t.candidate.signed_magnitude) for t in trials
                   if not t.accepted and abs(t.candidate.signed_magnitude)>mag]
        if bad_above and val>=min(bad_above)-1e-9:
            val=(mag+min(bad_above))/2.
            reason='backoff_inside_observed_unusable_upper_trial'
    if val<=1e-9 or any(abs(val-x)<=1e-9 for x in seen):return [],'no_distinct_useful_candidate'
    signed=space.sign*float(val)
    cand=ActionCandidate(f'{space.device_id}:b{batch_id}:a{val:.9f}',space.device_id,signed,batch_id,reason,
                         space.paired_generator_id,space.paired_pg_per_transfer*signed)
    return [cand],''

def propose_candidate_batch(action_space,previous_trial_results,remaining_budget,task=None,batch_id=0,cfg=None):
    s=action_space
    if s.discrete_magnitudes:
        seen={round(abs(t.candidate.signed_magnitude),10) for t in previous_trial_results}
        values=[float(v) for v in s.discrete_magnitudes if round(v,10) not in seen][:max(0,remaining_budget)]
        return [ActionCandidate(f'{s.device_id}:b{batch_id}:a{v:.9f}',s.device_id,s.sign*v,batch_id,
                'complete_legal_discrete_enumeration',s.paired_generator_id,s.paired_pg_per_transfer*s.sign*v) for v in values]
    return next_active_power_proposal(s,previous_trial_results,remaining_budget,task,cfg or Config(),batch_id)[0]

def realize_action(network,state,task,decision,cfg,solver):
    if decision.state_id!=state.state_id or state.state_id!=state.controls.version(network.network_id):raise ValueError('stale_operating_state')
    space=construct_legal_action_space(network.devices[decision.device_id],decision.direction,state.controls,cfg,network,task,state.state_id)
    trials=[];log=[];batch=0;stop=''
    # Reconfirm that the search's measured input is this current accepted PF.
    if task.stage=='P' and abs(task.current_value-state.pf.balancing_pg[task.area_id])>1e-7:
        raise ValueError('stale_P_observation')
    while True:
        if space.discrete_magnitudes:
            proposals=propose_candidate_batch(space,trials,space.max_trials-len(trials),task,batch,cfg)
            if not proposals:stop='complete_legal_discrete_enumeration';break
        else:
            proposals,stop=next_active_power_proposal(space,trials,space.max_trials-len(trials),task,cfg,batch)
        if not proposals:break
        actual=[blackbox_pf_evaluate(state,c,solver,network,task,cfg) for c in proposals]
        trials.extend(actual)
        log.append({'batch_id':batch,'reason':proposals[0].proposal_reason,
                    'proposed_magnitudes':[c.signed_magnitude for c in proposals],
                    'cumulative_trials':len(trials),'remaining_budget':space.max_trials-len(trials),
                    'actual_results':[{'candidate_id':t.candidate.candidate_id,'converged':t.convergence,
                        'accepted':t.accepted,'reason':t.reason,'metrics':t.metrics} for t in actual]})
        batch+=1
    result=select_or_refine_action(trials,task,space,space.max_trials-len(trials))
    result.refinement_log=log
    result.stop_reason=stop
    result.search_policy='exhaustive_discrete_shunt' if space.discrete_magnitudes else 'measured_balance_lightweight_P'
    return result

def commit_selected_patch(network,state,result):
    if result.state_id!=state.state_id or state.controls.version(network.network_id)!=state.state_id:raise ValueError('stale_selected_patch')
    if result.selected is None:raise ValueError('no_selected_patch')
    if not result.selected.accepted or not result.selected.convergence:raise ValueError('unacceptable_selected_patch')
    from schemas import OperatingState
    controls=apply_candidate(state.controls,result.selected.candidate,network)
    return OperatingState(controls.version(network.network_id),controls,copy.deepcopy(result.selected.observation))
