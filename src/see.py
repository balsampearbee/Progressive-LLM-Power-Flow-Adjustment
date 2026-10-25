"""Task-conditioned relation extraction and observed-state encoding only."""
from collections import Counter,deque
import networkx as nx
from schemas import (TaskTarget,RelationSubgraph,PathStateFeatures,CandidateRelationCode,
                     CompactDecisionContext)

def deviation(v,cfg):return max(cfg.vmin-v,v-cfg.vmax,0.)

def state_metrics(pf,cfg):
    if not pf.converged:return None
    vals=[pf.voltage_pu[b] for b in cfg.monitored]
    dv=[deviation(v,cfg) for v in vals]
    pv={a:max(cfg.balancing_min_mw-p,p-cfg.balancing_max_mw,0.) for a,p in pf.balancing_pg.items()}
    return {'balancing_pg':pf.balancing_pg,'balancing_violation_mw':sum(pv.values()),
            'voltage_violation_count':sum(d>1e-8 for d in dv),'voltage_severity_pu':sum(dv),
            'min_voltage_pu':min(vals),'max_voltage_pu':max(vals),
            'violating_buses':[b for b,d in zip(cfg.monitored,dv) if d>1e-8]}

def identify_task(state,network,cfg):
    pf=state.pf
    if not pf.converged:return TaskTarget('D',None,'nonconvergent',None,(),0.)
    pv=[(max(cfg.balancing_min_mw-p,p-cfg.balancing_max_mw,0.),a,p) for a,p in pf.balancing_pg.items()]
    gap,area,p=max(pv,key=lambda x:(x[0],x[1]))
    if gap>1e-7:
        return TaskTarget('P',network.balancing_buses[area],'above_upper' if p>cfg.balancing_max_mw else 'below_lower',
                          p,(cfg.balancing_min_mw,cfg.balancing_max_mw),gap,area)
    b=max(cfg.monitored,key=lambda b:(deviation(pf.voltage_pu[b],cfg),-b))
    v=pf.voltage_pu[b];gap=deviation(v,cfg)
    if gap>1e-8:return TaskTarget('Q',b,'below_lower' if v<cfg.vmin else 'above_upper',v,(cfg.vmin,cfg.vmax),gap,network.nodes[b]['area'])
    return TaskTarget('STOP',None,'terminal_feasible',None,(),0.)

def ac_graph(network):
    g=nx.Graph();g.add_nodes_from(sorted(network.nodes))
    for e in sorted(network.branches,key=lambda x:x['id']):
        if e['in_service']:g.add_edge(e['from'],e['to'])
    return g

def setting(d,controls):
    return (controls.generator_pg if d.kind=='generator' else controls.shunt_steps if d.kind=='shunt' else controls.hvdc_mw)[d.device_id]

def legal_directions(d,controls):
    value=setting(d,controls);suf='_step' if d.kind=='shunt' else ''
    return (['increase'+suf] if value<d.upper-1e-9 else [])+(['decrease'+suf] if value>d.lower+1e-9 else [])

def remote_generators(network,d,target_area,controls):
    remote=d.to_area if target_area==d.from_area else d.from_area
    return [g for g in sorted(network.devices.values(),key=lambda x:x.device_id)
            if g.kind=='generator' and g.area==remote and legal_directions(g,controls)]

def extract_relation_subgraph(network,state,task,cfg,blocked=()):
    if task.stage not in ('P','Q'):raise ValueError('nonactionable_task')
    g=ac_graph(network);dist=dict(nx.single_source_shortest_path_length(g,task.target_id))
    eligible=[];excluded=Counter();roles={};paths={};hvdc=[]
    for d in sorted(network.devices.values(),key=lambda x:x.device_id):
        legal=[x for x in legal_directions(d,state.controls) if (d.device_id,x) not in blocked]
        if not legal:excluded['no_untried_legal_direction']+=1;continue
        if task.stage=='Q':
            if d.kind!='shunt':continue
            if d.bus not in dist or dist[d.bus]>cfg.H:excluded['outside_AC_radius']+=1;continue
            eligible.append(d);roles[d.device_id]='local_shunt'
            paths[d.device_id]={'target_path':nx.shortest_path(g,task.target_id,d.bus),'paired_paths':{}}
        elif d.kind=='generator':
            if d.area!=task.area_id:excluded['other_area_generator']+=1;continue
            if d.bus not in dist:excluded['AC_disconnected']+=1;continue
            eligible.append(d);roles[d.device_id]='in_area_generator'
            paths[d.device_id]={'target_path':nx.shortest_path(g,task.target_id,d.bus),'paired_paths':{}}
        elif d.kind=='hvdc' and task.area_id in (d.from_area,d.to_area):
            endpoint=d.from_bus if task.area_id==d.from_area else d.to_bus
            gs=remote_generators(network,d,task.area_id,state.controls)
            if endpoint not in dist or not gs:excluded['unavailable_HVDC_pair']+=1;continue
            other=d.to_bus if task.area_id==d.from_area else d.from_bus
            pp={x.device_id:nx.shortest_path(g,other,x.bus) for x in gs if nx.has_path(g,other,x.bus)}
            if not pp:excluded['remote_pair_disconnected']+=1;continue
            eligible.append(d);roles[d.device_id]='HVDC_with_remote_paired_generator'
            paths[d.device_id]={'target_path':nx.shortest_path(g,task.target_id,endpoint),'paired_paths':pp}
            hvdc.append({'device_id':d.device_id,'from_bus':d.from_bus,'to_bus':d.to_bus,
                         'from_area':d.from_area,'to_area':d.to_area,'remote_paired_generators':list(pp)})
    if task.stage=='Q':
        # Hop selects locality. Presentation is stable ID, with no effect score.
        chosen=sorted(eligible,key=lambda d:(dist[d.bus],d.device_id))[:cfg.K]
    else:
        # Preserve resource-class coverage when HVDC is present, then stable ID.
        groups=[deque(sorted([d for d in eligible if d.kind==k],key=lambda d:d.device_id)) for k in ('generator','hvdc')]
        chosen=[]
        while len(chosen)<cfg.K and any(groups):
            for group in groups:
                if group and len(chosen)<cfg.K:chosen.append(group.popleft())
    chosen=sorted(chosen,key=lambda d:(roles[d.device_id],d.device_id))
    ids=[d.device_id for d in chosen];selected_paths={x:paths[x] for x in ids}
    nodes={task.target_id}
    for p in selected_paths.values():
        nodes.update(p['target_path'])
        for q in p['paired_paths'].values():nodes.update(q)
    edges=[e for e in network.branches if e['in_service'] and e['from'] in nodes and e['to'] in nodes]
    layers={str(h):sorted(b for b,n in dist.items() if n==h) for h in range(cfg.H+1)} if task.stage=='Q' else {}
    return RelationSubgraph(task,sorted(nodes),edges,ids,selected_paths,{x:roles[x] for x in ids},
        [h for h in hvdc if h['device_id'] in ids],{'K':cfg.K,'H':cfg.H,'eligible_count':len(eligible),
        'excluded':dict(excluded),'BFS_layers':layers,'presentation_order':'relation_class_then_device_id',
        'same_state_rejected_direction_count':len(blocked),'balancing_area':task.area_id})

def node_state(network,state,b):
    n=network.nodes[b]
    return {'bus':b,'vm_pu':round(state.pf.voltage_pu[b],6),
            'angle_deg':round(state.pf.angle_deg.get(b,0.),4),
            'pd_mw':n['pd_mw'],'qd_mvar':n['qd_mvar']}

def build_path_state_features(network,state,task,subgraph,candidate_id):
    path=subgraph.candidate_paths[candidate_id]['target_path'];d=network.devices[candidate_id]
    ns=[node_state(network,state,b) for b in path];vs=[x['vm_pu'] for x in ns]
    used={frozenset((a,b)) for a,b in zip(path[:-1],path[1:])}
    flows=[r for r in state.pf.branch_flows if frozenset((r['from'],r['to'])) in used]
    return PathStateFeatures(path,len(path)-1,ns,{'min_voltage_pu':min(vs),'max_voltage_pu':max(vs),
        'load_p_mw':sum(x['pd_mw'] for x in ns),'load_q_mvar':sum(x['qd_mvar'] for x in ns)},
        node_state(network,state,path[-1]),flows,{'target_area':task.area_id,'device_area':d.area,
        'paired_paths':subgraph.candidate_paths[candidate_id]['paired_paths']})

def encode_candidate_relation(task,candidate,path_features,control_state,recent_feedback,network,blocked=()):
    d=candidate;val=setting(d,control_state)
    paired=[]
    if d.kind=='hvdc':
        for x in remote_generators(network,d,task.area_id,control_state):
            if x.device_id not in path_features.area_relation['paired_paths']:continue
            p=setting(x,control_state)
            paired.append({'device_id':x.device_id,'area':x.area,'pg_mw':p,'lower':x.lower,'upper':x.upper})
    return CandidateRelationCode(d.device_id,d.kind,'local_shunt' if d.kind=='shunt' else
        'in_area_generator' if d.kind=='generator' else 'HVDC_with_remote_paired_generator',d.bus,d.area,val,d.lower,d.upper,
        max(0.,d.upper-val),max(0.,val-d.lower),[x for x in legal_directions(d,control_state) if (d.device_id,x) not in blocked],
        list(range(int(d.lower),int(d.upper)+1)) if d.kind=='shunt' else [],d.step_mvar,path_features,
        [x for x in recent_feedback if x.get('device_id')==d.device_id][-2:],paired)

def build_decision_context(network,state,task,cfg,feedback=(),blocked=()):
    graph=extract_relation_subgraph(network,state,task,cfg,blocked)
    features={x:build_path_state_features(network,state,task,graph,x) for x in graph.candidate_ids}
    codes=[encode_candidate_relation(task,network.devices[x],features[x],state.controls,feedback,network,blocked) for x in graph.candidate_ids]
    context=CompactDecisionContext(state.state_id,task,codes,list(feedback[-3:]),{
        'full_node_count':len(network.nodes),'selected_node_count':len(graph.selected_node_ids),
        'K':cfg.K,'H':cfg.H,'candidate_order':graph.extraction_metadata['presentation_order'],
        'current_constraints':state_metrics(state.pf,cfg)})
    return context,graph,features
