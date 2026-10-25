"""Independent replay of contexts, raw API choices, every PF trial and accepted commits."""
from pathlib import Path
import sys,argparse,json,math,copy
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'src'))
from schemas import *
from blackbox_adapter import IEEE39BlackBox
from see import identify_task,build_decision_context,state_metrics
from act import realize_action,commit_selected_patch
from think import payload,parse_decision,SYSTEM_PROMPT
from loop import save

def same(a,b,label='root'):
    a=data(a);b=data(b)
    if isinstance(b,dict):
        assert set(a)==set(b),(label,set(a)^set(b))
        for k in b:same(a[k],b[k],label+'.'+str(k))
    elif isinstance(b,list):
        assert len(a)==len(b),(label,len(a),len(b))
        for k,(x,y) in enumerate(zip(a,b)):same(x,y,label+'.'+str(k))
    elif isinstance(b,(int,float)) and not isinstance(b,bool):
        assert math.isclose(a,b,abs_tol=1e-8,rel_tol=1e-10),(label,a,b)
    else:assert a==b,(label,a,b)

def verify(directory):
    result=json.loads((directory/'result.json').read_text())
    solver=IEEE39BlackBox();n=solver.network;cfg=Config(**{**result['config'],'monitored':tuple(result['config']['monitored'])})
    c=copy.deepcopy(solver.initial_controls);state=OperatingState(c.version(n.network_id),c,solver(c))
    same(state,result['initial_state'],'initial')
    feedback=[];blocked={};records=[];used=[]
    for e in result['events']:
        assert 'error' not in e, 'verification needs completed physical rounds'
        task=identify_task(state,n,cfg)
        ctx,g,f=build_decision_context(n,state,task,cfg,feedback,blocked.get(state.state_id,set()))
        same(ctx,e['context'],'context')
        rep=e['reply']
        if result['model_mode']=='live_api':
            path=directory/'api_audit'/rep['audit_file'];a=json.loads(path.read_text());req=a['request_without_credentials']
            assert a['source']=='real_api' and a['http_status']==200 and a['error'] is None
            assert req['messages'][0]['content']==SYSTEM_PROMPT
            same(json.loads(req['messages'][1]['content']),payload(ctx),'request_context')
            assert req['model']=='deepseek-flash' and req['thinking']=={'type':'disabled'}
            assert req['response_format']=={'type':'json_object'}
            raw=a['raw_response_json'];assert json.loads(a['raw_response_body'])==raw
            same(raw['usage'],rep['usage'],'provider_usage')
            assert raw['id']==rep['request_id'];used.append(path.name)
            decision=parse_decision(raw['choices'][0]['message']['content'],ctx)
        else:decision=DeviceDirectionDecision(**rep['decision'])
        same(decision,rep['decision'],'decision')
        before=data(state)
        actual=realize_action(n,state,task,decision,cfg,solver)
        same(state,before,'trial_isolation')
        same(actual,e['act'],'adaptive_search_and_all_trial_results')
        b=state_metrics(state.pf,cfg)
        if actual.selected:
            state=commit_selected_patch(n,state,actual);m=state_metrics(state.pf,cfg)
            fb={'state_id':before['state_id'],'device_id':decision.device_id,'direction':decision.direction,
                'result':'accepted','magnitude':actual.selected.candidate.signed_magnitude,
                'balancing_violation_before_after':[b['balancing_violation_mw'],m['balancing_violation_mw']],
                'voltage_count_before_after':[b['voltage_violation_count'],m['voltage_violation_count']],
                'voltage_severity_before_after':[b['voltage_severity_pu'],m['voltage_severity_pu']]}
        else:
            blocked.setdefault(state.state_id,set()).add((decision.device_id,decision.direction))
            fb={'state_id':state.state_id,'device_id':decision.device_id,'direction':decision.direction,'result':'rejected',
                'reason_counts':{r:sum(x.reason==r for x in actual.trials) for r in sorted({x.reason for x in actual.trials})}}
        same(fb,e['feedback'],'feedback');feedback.append(fb)
        same(state,e['accepted_state_after'],'accepted_state')
        records.append({'round':e['round'],'stage':task.stage,'verified_context':True,'verified_raw_decision':True,
                        'verified_every_adaptive_trial':True,'state_isolation':True,'trials':len(actual.trials),
                        'accepted':actual.selected is not None,'metrics':state_metrics(state.pf,cfg)})
    state.pf=solver(state.controls)
    same(state,result['final_state'],'final');same(feedback,result['feedback'],'feedback_history')
    assert solver.calls==result['total_ac_pf_calls']
    if result['model_mode']=='live_api':
        assert set(used)=={p.name for p in (directory/'api_audit').glob('*.json')}
        assert len(used)==result['successful_api_calls']==result['external_api_attempts']
    assert (identify_task(state,n,cfg).stage=='STOP')==(result['status']=='terminal_feasible')
    return {'passed':True,'source_directory':str(directory),'source_status':result['status'],
        'new_api_calls':0,'reverification_ac_pf_calls':solver.calls,'rounds':records,
        'final_metrics':state_metrics(state.pf,cfg),
        'solver_scope':'independent replay with same frozen black-box Newton implementation, no second solver'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',required=True,type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    r=verify(a.run_dir);save(a.out,r);print(json.dumps(r,ensure_ascii=False,indent=2))
