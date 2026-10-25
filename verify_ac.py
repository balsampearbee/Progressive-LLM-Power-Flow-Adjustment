"""Independent physical replay: no import of Act or candidate proposal functions."""
from pathlib import Path
import sys,json,copy,argparse,math
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'src'))
from schemas import Config,ControlState,data
from blackbox_adapter import IEEE39BlackBox

def close(a,b,label):
    a=data(a);b=data(b)
    if isinstance(b,dict):
        assert set(a)==set(b),(label,'keys')
        for k in b:close(a[k],b[k],label+'.'+str(k))
    elif isinstance(b,list):
        assert len(a)==len(b),(label,'length')
        for k,(x,y) in enumerate(zip(a,b)):close(x,y,label+f'[{k}]')
    elif isinstance(b,(int,float)) and not isinstance(b,bool):
        assert math.isclose(a,b,abs_tol=1e-8,rel_tol=1e-10),(label,a,b)
    else:assert a==b,(label,a,b)

def metrics(p,cfg):
    if not p.converged:return None
    dev=lambda v:max(cfg.vmin-v,v-cfg.vmax,0.)
    vals=[p.voltage_pu[b] for b in cfg.monitored];ds=[dev(v) for v in vals]
    return {'balancing_pg':p.balancing_pg,
      'balancing_violation_mw':sum(max(cfg.balancing_min_mw-v,v-cfg.balancing_max_mw,0.) for v in p.balancing_pg.values()),
      'voltage_violation_count':sum(d>1e-8 for d in ds),'voltage_severity_pu':sum(ds),
      'min_voltage_pu':min(vals),'max_voltage_pu':max(vals),'violating_buses':[b for b,d in zip(cfg.monitored,ds) if d>1e-8]}

def patch(base,c,n,task,decision,cfg):
    out=copy.deepcopy(base);d=n.devices[c['device_id']];delta=c['signed_magnitude']
    assert c['device_id']==decision['device_id']
    assert (delta>0)==decision['direction'].startswith('increase')
    mapping=out.generator_pg if d.kind=='generator' else out.shunt_steps if d.kind=='shunt' else out.hvdc_mw
    val=mapping[d.device_id]+delta
    assert d.lower-1e-8<=val<=d.upper+1e-8
    if d.kind=='shunt':assert delta==round(delta)
    else:assert abs(delta)<=cfg.max_p_step_mw+1e-8
    mapping[d.device_id]=int(round(val)) if d.kind=='shunt' else val
    paired=c['paired_generator_id']
    if d.kind=='hvdc':
        assert paired
        g=n.devices[paired];assert g.area!=task['area_id']
        factor=-1. if task['area_id']==d.from_area else 1.
        assert math.isclose(c['paired_delta_pg'],factor*delta,abs_tol=1e-8)
        val=out.generator_pg[paired]+c['paired_delta_pg'];assert g.lower-1e-8<=val<=g.upper+1e-8
        out.generator_pg[paired]=val
    else:assert paired is None and c['paired_delta_pg']==0.
    return out

def run(directory):
    result=json.loads((directory/'result.json').read_text());cfg=Config(**{**result['config'],'monitored':tuple(result['config']['monitored'])})
    solver=IEEE39BlackBox();n=solver.network;c=copy.deepcopy(solver.initial_controls);p=solver(c)
    close(c,result['initial_state']['controls'],'initial.controls');close(p,result['initial_state']['pf'],'initial.pf')
    records=[]
    for ev in result['events']:
        if 'act' not in ev:continue
        before=data(c);bm=metrics(p,cfg);task=ev['act']['task'];de=ev['reply']['decision'];computed={}
        assert c.version(n.network_id)==ev['state_id_before']==de['state_id']
        for tr in ev['act']['trials']:
            cand=tr['candidate'];cc=patch(c,cand,n,task,de,cfg);pp=solver(cc);mm=metrics(pp,cfg)
            close(pp,tr['observation'],'trial.PF');close(mm,tr['metrics'],'trial.metrics');close(c,before,'base_isolation')
            if not pp.converged:
                acceptable=False
            elif task['stage']=='P':
                acceptable=mm['balancing_violation_mw']<bm['balancing_violation_mw']-1e-6
            else:
                v=pp.voltage_pu[task['target_id']];gap=max(cfg.vmin-v,v-cfg.vmax,0.)
                acceptable=(mm['balancing_violation_mw']<=1e-6 and gap<task['violation_magnitude']-1e-8 and
                    (mm['voltage_violation_count']<bm['voltage_violation_count'] or
                     (mm['voltage_violation_count']==bm['voltage_violation_count'] and mm['voltage_severity_pu']<bm['voltage_severity_pu']-1e-8)))
            assert acceptable==tr['accepted'],'independently_recomputed_acceptance'
            computed[cand['candidate_id']]=(cc,pp,acceptable)
        if task['stage']=='Q':
            sp=ev['act']['action_space'];assert sorted(abs(t['candidate']['signed_magnitude']) for t in ev['act']['trials'])==list(map(float,sp['discrete_magnitudes']))
        sel=ev['act']['selected']
        if sel:
            c,p,accepted=computed[sel['candidate']['candidate_id']];assert accepted
        else:close(c,before,'rejection_preserves_state')
        close(c,ev['accepted_state_after']['controls'],'committed.controls');close(p,ev['accepted_state_after']['pf'],'committed.pf')
        records.append({'round':ev['round'],'stage':task['stage'],'candidate_count':len(computed),
            'independent_acceptance_check':True,'no_Act_import':True,'committed':sel is not None})
    p=solver(c);m=metrics(p,cfg)
    close(p,result['final_state']['pf'],'final.explicit_PF');close(m,result['final_metrics'],'final.metrics')
    terminal=(p.converged and m['balancing_violation_mw']<=1e-7 and m['voltage_violation_count']==0)
    assert terminal==(result['status']=='terminal_feasible')
    return {'passed':True,'verification_type':'direct candidate replay without Act import',
      'new_api_calls':0,'new_AC_PF_calls_for_verification':solver.calls,'rounds':records,'final_metrics':m,
      'terminal_feasible_independently_confirmed':terminal,
      'solver_scope':'fresh calls to the same frozen Newton AC engine, not a second commercial solver'}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run-dir',required=True,type=Path);ap.add_argument('--out',required=True,type=Path);a=ap.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    report=run(a.run_dir);a.out.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
