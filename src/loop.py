"""V7 progressive loop, with every method object persisted before/after execution."""
from pathlib import Path
import json,time,datetime
from schemas import OperatingState,data
from see import identify_task,build_decision_context,state_metrics
from act import realize_action,commit_selected_patch,validate_llm_decision

def save(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data(obj),ensure_ascii=False,indent=2,allow_nan=False));tmp.replace(path)

def run_loop(network,initial_controls,solver,client,cfg,out_dir):
    out=Path(out_dir)
    if (out/'result.json').exists():raise FileExistsError('choose_new_output_directory')
    t0=time.perf_counter();start=datetime.datetime.now(datetime.timezone.utc).isoformat()
    state=OperatingState(initial_controls.version(network.network_id),initial_controls,solver(initial_controls))
    initial=data(state);feedback=[];events=[];blocked={};stages=[];api_calls=0
    status='max_steps'
    for t in range(cfg.max_steps+1):
        task=identify_task(state,network,cfg);stages.append(task.stage)
        save(out/'checkpoints'/f'state_{t:03d}.json',{'state':state,'task':task,'metrics':state_metrics(state.pf,cfg),'feedback':feedback})
        print(f'STATE {t}: stage={task.stage} metrics={state_metrics(state.pf,cfg)}',flush=True)
        if task.stage=='STOP':status='terminal_feasible';break
        if task.stage=='D':status='nonconvergent_diagnosis_required';break
        if t==cfg.max_steps:break
        avoid=blocked.get(state.state_id,set())
        context,graph,features=build_decision_context(network,state,task,cfg,feedback,avoid)
        objects={'TaskTarget':task,'RelationSubgraph':graph,'PathStateFeatures':features,
                 'CandidateRelationCode':context.candidates,'CompactDecisionContext':context}
        save(out/'rounds'/f'round_{t+1:03d}_see.json',objects)
        if not context.candidates:status='no_untried_candidate_resources';break
        try:
            reply=client.decide(context);validate_llm_decision(reply.decision,context)
        except Exception as e:
            events.append({'round':t+1,'stage':task.stage,'state_id':state.state_id,'error':type(e).__name__+': '+str(e)})
            save(out/'rounds'/f'round_{t+1:03d}_failed.json',events[-1]);status='api_or_protocol_error';break
        api_calls+=int(client.is_live)
        before_id=state.state_id
        try:action=realize_action(network,state,task,reply.decision,cfg,solver)
        except ValueError as e:
            # A missing paired margin is a genuine legal-space rejection, not a fabricated model choice.
            from schemas import ActionSelectionResult
            events.append({'round':t+1,'stage':task.stage,'state_id':state.state_id,'reply':data(reply),'error':'legal_action_space: '+str(e)})
            save(out/'rounds'/f'round_{t+1:03d}_failed.json',events[-1]);status='legal_action_space_error';break
        b=state_metrics(state.pf,cfg)
        if action.selected is not None:
            state=commit_selected_patch(network,state,action)
            m=state_metrics(state.pf,cfg)
            fb={'state_id':before_id,'device_id':reply.decision.device_id,'direction':reply.decision.direction,
                'result':'accepted','magnitude':action.selected.candidate.signed_magnitude,
                'balancing_violation_before_after':[b['balancing_violation_mw'],m['balancing_violation_mw']],
                'voltage_count_before_after':[b['voltage_violation_count'],m['voltage_violation_count']],
                'voltage_severity_before_after':[b['voltage_severity_pu'],m['voltage_severity_pu']]}
        else:
            blocked.setdefault(before_id,set()).add((reply.decision.device_id,reply.decision.direction))
            fb={'state_id':before_id,'device_id':reply.decision.device_id,'direction':reply.decision.direction,
                'result':'rejected','reason_counts':{r:sum(x.reason==r for x in action.trials) for r in sorted({x.reason for x in action.trials})}}
        feedback.append(fb)
        event={'round':t+1,'stage':task.stage,'state_id_before':before_id,'context':context,'reply':reply,'act':action,
               'feedback':fb,'accepted_state_after':state,'metrics_after':state_metrics(state.pf,cfg)}
        events.append(data(event));save(out/'rounds'/f'round_{t+1:03d}_act.json',event)
        save(out/'checkpoint_latest.json',{'events':events,'state':state,'feedback':feedback,'round':t+1})
        print(f'ROUND {t+1}: {reply.decision.device_id} {reply.decision.direction} {fb["result"]}, trials={len(action.trials)}',flush=True)
        if task.stage=='P' and identify_task(state,network,cfg).stage in ('Q','STOP'):
            save(out/'P_RESTORED_CHECKPOINT.json',{'state':state,'events':events,'feedback':feedback})
    # Explicit final black-box validation, independent of cached selected trial.
    state.pf=solver(state.controls)
    if status=='terminal_feasible' and identify_task(state,network,cfg).stage!='STOP':raise AssertionError('final_verification_failed')
    result={'method_version':'V7','model_mode':'live_api' if client.is_live else 'scripted_engineering_not_LLM',
        'status':status,'config':cfg,'initial_state':initial,'final_state':state,'final_metrics':state_metrics(state.pf,cfg),
        'events':events,'feedback':feedback,'stage_history':stages,'successful_api_calls':api_calls,
        'external_api_attempts':getattr(client,'attempts',0) if client.is_live else 0,
        'committed_actions':sum(e.get('feedback',{}).get('result')=='accepted' for e in events),
        'total_ac_pf_calls':getattr(solver,'calls',None),'started_utc':start,
        'wall_s':time.perf_counter()-t0}
    save(out/'result.json',result)
    print('FINAL',status,'calls',api_calls,'actions',result['committed_actions'],'wall_s',result['wall_s'],flush=True)
    return data(result)
