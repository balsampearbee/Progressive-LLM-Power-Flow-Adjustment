import sys,unittest,copy,json,tempfile
from pathlib import Path
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from schemas import *
from blackbox_adapter import IEEE39BlackBox
from see import *
from act import *
from think import parse_decision,ThinkError,DeepSeekFlashClient,ScriptedThinkClient
from commercial_io_adapter import extract_report_context

class SeeTests(unittest.TestCase):
    def setUp(self):
        self.solver=IEEE39BlackBox();self.n=self.solver.network;self.cfg=Config()
        c=copy.deepcopy(self.solver.initial_controls)
        self.state=OperatingState(c.version(self.n.network_id),c,self.solver(c))
        self.task=identify_task(self.state,self.n,self.cfg)
    def test_initial_exact_task(self):
        self.assertEqual((self.task.stage,self.task.target_id),('P',39))
        self.assertAlmostEqual(self.task.current_value,1512.243124,places=5)
    def test_public_solver_contract(self):
        self.assertEqual(set(data(self.state.pf)),{'converged','voltage_pu','angle_deg','balancing_pg','generator_pg','branch_flows'})
    def test_objects_paths_and_nonempty_codes(self):
        ctx,g,f=build_decision_context(self.n,self.state,self.task,self.cfg)
        self.assertIsInstance(g,RelationSubgraph);self.assertEqual(len(ctx.candidates),3)
        graph=ac_graph(self.n)
        for r in ctx.candidates:
            self.assertIsInstance(r,CandidateRelationCode)
            self.assertEqual(r.path_state.path_node_ids[0],39)
            self.assertEqual(r.path_state.hop_count,nx.shortest_path_length(graph,39,r.bus))
            self.assertTrue(all(graph.has_edge(a,b) for a,b in zip(r.path_state.path_node_ids[:-1],r.path_state.path_node_ids[1:])))
    def test_legal_mask_margins_and_feedback(self):
        fb=[{'device_id':'G30','result':'rejected','direction':'decrease'}]
        ctx,g,f=build_decision_context(self.n,self.state,self.task,self.cfg,fb)
        c=next(x for x in ctx.candidates if x.candidate_id=='G30')
        self.assertEqual(c.upward_margin,790.)
        self.assertIn('increase',c.legal_direction_mask);self.assertIn('decrease',c.legal_direction_mask)
        self.assertEqual(c.recent_feedback,fb)
        self.assertEqual(json.loads(json.dumps(data(ctx)))['recent_feedback'],fb)
    def test_no_action_effect_fields(self):
        ctx,_,_=build_decision_context(self.n,self.state,self.task,self.cfg)
        text=json.dumps(data(ctx)).lower()
        for forbidden in ['predicted','jacobian','sensitivity','needed_mw','coverage_fraction','d_slack','response_columns']:
            self.assertNotIn(forbidden,text)
    def test_q_bfs_stable_order_and_directions(self):
        c=copy.deepcopy(self.state.controls);c.generator_pg['G30']+=425
        s=OperatingState(c.version(self.n.network_id),c,self.solver(c));task=identify_task(s,self.n,self.cfg)
        self.assertEqual(task.stage,'Q')
        ctx,g,f=build_decision_context(self.n,s,task,self.cfg)
        self.assertTrue(g.extraction_metadata['BFS_layers'])
        self.assertTrue(all(x.path_state.hop_count<=4 for x in ctx.candidates))
        self.assertEqual([x.candidate_id for x in ctx.candidates],sorted(x.candidate_id for x in ctx.candidates))
        self.assertTrue(all(x.legal_direction_mask==['increase_step'] for x in ctx.candidates))
    def test_same_state_failure_rescreen_and_no_masked_direction(self):
        ctx,g,f=build_decision_context(self.n,self.state,self.task,self.cfg)
        blocked={(c.candidate_id,d) for c in ctx.candidates for d in c.legal_direction_mask}
        ctx2,g2,f2=build_decision_context(self.n,self.state,self.task,self.cfg,blocked=blocked)
        self.assertTrue(ctx2.candidates)
        self.assertFalse(set(x.candidate_id for x in ctx.candidates)&set(x.candidate_id for x in ctx2.candidates))
    def test_version_and_protocol_guards(self):
        ctx,_,_=build_decision_context(self.n,self.state,self.task,self.cfg)
        for x in [{'device_id':'G999','direction':'increase'},{'device_id':'G30','direction':'increase','MW':50}]:
            with self.assertRaises(ThinkError):parse_decision(x,ctx)
        d=DeviceDirectionDecision('G30','increase','old')
        with self.assertRaises(ValueError):validate_llm_decision(d,ctx)
    def test_no_see_pf_trials(self):
        before=self.solver.calls
        build_decision_context(self.n,self.state,self.task,self.cfg)
        self.assertEqual(self.solver.calls,before)
    def test_effect_independent_selection(self):
        a,_,_=build_decision_context(self.n,self.state,self.task,self.cfg)
        altered=copy.deepcopy(self.state)
        altered.pf.voltage_pu={b:.99 if b%2 else 1.02 for b in self.n.nodes}
        b,_,_=build_decision_context(self.n,altered,self.task,self.cfg)
        self.assertEqual([x.candidate_id for x in a.candidates],[x.candidate_id for x in b.candidates])

class ActTests(unittest.TestCase):
    setUp=SeeTests.setUp
    def test_generator_legal_endpoint_small_headroom(self):
        c=copy.deepcopy(self.state.controls);c.generator_pg['G30']=1039.8
        d=self.n.devices['G30'];s=construct_legal_action_space(d,'increase',c,self.cfg,self.n,self.task,'s')
        self.assertAlmostEqual(s.upper_magnitude,.2)
        self.assertTrue(all(0<x.signed_magnitude<=.200001 for x in propose_candidate_batch(s,[],10,self.task)))
    def test_trial_isolation_and_commit_guard(self):
        d=DeviceDirectionDecision('G30','increase',self.state.state_id);old=data(self.state)
        r=realize_action(self.n,self.state,self.task,d,self.cfg,self.solver)
        self.assertEqual(data(self.state),old)
        nxt=commit_selected_patch(self.n,self.state,r)
        self.assertNotEqual(nxt.state_id,self.state.state_id)
        with self.assertRaises(ValueError):commit_selected_patch(self.n,nxt,r)
    def test_all_failure_reject_and_no_state_mutation(self):
        old=data(self.state);d=DeviceDirectionDecision('G30','increase',self.state.state_id)
        fail=lambda c:PFObservation(False,{},{},{})
        r=realize_action(self.n,self.state,self.task,d,self.cfg,fail)
        self.assertIsNone(r.selected);self.assertEqual(data(self.state),old)
        with self.assertRaises(ValueError):commit_selected_patch(self.n,self.state,r)
    def test_budget_hard_cap(self):
        cfg=replace(self.cfg,continuous_budget=4)
        r=realize_action(self.n,self.state,self.task,DeviceDirectionDecision('G30','decrease',self.state.state_id),cfg,self.solver)
        self.assertLessEqual(len(r.trials),4)
        self.assertIsNone(r.selected)
    def q_state(self):
        c=copy.deepcopy(self.state.controls);c.generator_pg['G30']+=425
        s=OperatingState(c.version(self.n.network_id),c,self.solver(c))
        return s,identify_task(s,self.n,self.cfg)
    def test_shunt_complete_enumeration(self):
        s,t=self.q_state();r=realize_action(self.n,s,t,DeviceDirectionDecision('S5','increase_step',s.state_id),self.cfg,self.solver)
        self.assertEqual([x.candidate.signed_magnitude for x in r.trials],[1.,2.,3.])
        self.assertEqual(len(r.refinement_log),1);self.assertTrue(r.selected)
    def test_higher_priority_active_guard(self):
        s,t=self.q_state()
        def bad(c):
            p=copy.deepcopy(s.pf);p.balancing_pg={'A':1200.};p.voltage_pu={b:1. for b in p.voltage_pu};return p
        r=realize_action(self.n,s,t,DeviceDirectionDecision('S5','increase_step',s.state_id),self.cfg,bad)
        self.assertIsNone(r.selected);self.assertTrue(all(x.reason=='balancing_guard' for x in r.trials))
    def test_terminal_candidates_choose_smallest(self):
        s,t=self.q_state()
        def good(c):
            p=copy.deepcopy(s.pf);p.voltage_pu={b:1. for b in p.voltage_pu};return p
        r=realize_action(self.n,s,t,DeviceDirectionDecision('S5','increase_step',s.state_id),self.cfg,good)
        self.assertEqual(r.selected.candidate.signed_magnitude,1.)
    def test_recorded_trial_metrics(self):
        r=realize_action(self.n,self.state,self.task,DeviceDirectionDecision('G30','increase',self.state.state_id),self.cfg,self.solver)
        for t in r.trials:
            self.assertIn('balancing_violation_mw',t.priority_stage_metric)
            self.assertIn('convergence',t.protected_metrics)

class ContractTests(unittest.TestCase):
    def test_dat_pf(self):
        r=extract_report_context(ROOT/'fixtures/ieee39.dat',ROOT/'fixtures/ieee39.pf')
        self.assertEqual(r['coverage'],{'buses_in_dat':39,'branches_in_dat':46,'shunt_banks_in_dat':12,'voltage_buses_in_pf':39,'generator_rows_in_pf':10,'branch_flows_in_pf':46})
        self.assertTrue(r['solution']['converged'])
    def test_hvdc_graph_pair_and_logical_patch(self):
        dev={'DC1':Device('DC1','hvdc',2,'A',-400,400,from_bus=2,to_bus=3,from_area='A',to_area='B'),
             'GB':Device('GB','generator',4,'B',0,500)}
        n=Network('two_area_contract',{i:{'area':'A' if i<3 else 'B','pd_mw':0.,'qd_mvar':0.} for i in range(1,5)},
             [{'id':'a','from':1,'to':2,'kind':'line','in_service':True},{'id':'b','from':3,'to':4,'kind':'line','in_service':True}],dev,{'A':1,'B':3})
        c=ControlState({'GB':100.},{},{'DC1':250.});pf=PFObservation(True,{i:1. for i in range(1,5)},{i:0. for i in range(1,5)},{'A':1200.,'B':100.})
        st=OperatingState(c.version(n.network_id),c,pf);cfg=replace(Config(),monitored=(1,2,3,4))
        task=identify_task(st,n,cfg);ctx,g,f=build_decision_context(n,st,task,cfg)
        self.assertEqual(g.candidate_ids,['DC1']);self.assertEqual(g.candidate_paths['DC1']['paired_paths'],{'GB':[3,4]})
        space=construct_legal_action_space(dev['DC1'],'decrease',c,cfg,n,task,st.state_id)
        self.assertEqual(space.paired_generator_id,'GB')
        cand=propose_candidate_batch(space,[],10,task)[0]
        nc=apply_candidate(c,cand,n)
        self.assertAlmostEqual(nc.generator_pg['GB']-100.,-(nc.hvdc_mw['DC1']-250.))
    def test_live_protocol_mock_only(self):
        solver=IEEE39BlackBox();c=solver.initial_controls;s=OperatingState(c.version(solver.network.network_id),c,solver(c))
        ctx,_,_=build_decision_context(solver.network,s,identify_task(s,solver.network,Config()),Config())
        def fake(url,body,headers,timeout):
            q=json.loads(body);self.assertEqual(q['thinking'],{'type':'disabled'})
            self.assertEqual(q['model'],'deepseek-flash')
            p=json.loads(q['messages'][1]['content']);self.assertEqual(p['state_id'],ctx.state_id)
            return json.dumps({'id':'TEST_MOCK','model':'deepseek-flash','usage':{'total_tokens':12},
                'choices':[{'finish_reason':'stop','message':{'content':'{"device_id":"G30","direction":"increase"}'}}]}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            client=DeepSeekFlashClient('test_placeholder',tmp,transport=fake);reply=client.decide(ctx)
            self.assertFalse(client.is_live);self.assertEqual(reply.decision.device_id,'G30')
            text=next(Path(tmp).glob('*.json')).read_text();self.assertNotIn('test_placeholder',text)
            self.assertEqual(json.loads(text)['source'],'mock_transport')

if __name__=='__main__':unittest.main()
