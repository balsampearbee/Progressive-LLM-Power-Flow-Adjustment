"""V7 policy tests. Synthetic PF doubles below are UNIT TESTS, never live evidence."""
import sys,unittest,copy,tempfile
from pathlib import Path
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from schemas import *
from blackbox_adapter import IEEE39BlackBox
from see import identify_task,state_metrics
from act import *

class LightweightPTests(unittest.TestCase):
    def setUp(self):
        self.solver=IEEE39BlackBox();self.n=self.solver.network;self.cfg=Config()
        c=copy.deepcopy(self.solver.initial_controls)
        self.state=OperatingState(c.version(self.n.network_id),c,self.solver(c))
    def toy(self,pg=1110.,coefficient=1.,nonconverge_above=None):
        s=copy.deepcopy(self.state);s.pf.balancing_pg={'A':pg}
        task=identify_task(s,self.n,self.cfg)
        def pf(c):
            delta=c.generator_pg['G30']-s.controls.generator_pg['G30']
            if nonconverge_above is not None and abs(delta)>nonconverge_above:return PFObservation(False,{},{},{})
            out=copy.deepcopy(s.pf);out.balancing_pg={'A':pg-coefficient*delta};return out
        return s,task,pf
    def runP(self,s,t,pf,cfg=None,direction='increase'):
        return realize_action(self.n,s,t,DeviceDirectionDecision('G30',direction,s.state_id),cfg or self.cfg,pf)
    def test_real_round6_state_stops_after_first_restoring_trial(self):
        c=copy.deepcopy(self.state.controls);c.generator_pg['G30']+=400.
        s=OperatingState(c.version(self.n.network_id),c,self.solver(c));t=identify_task(s,self.n,self.cfg)
        a=self.runP(s,t,self.solver)
        self.assertEqual(len(a.trials),1);self.assertEqual(a.stop_reason,'P_target_restored')
        self.assertAlmostEqual(a.selected.candidate.signed_magnitude,t.violation_magnitude*1.05)
        self.assertEqual(a.selected.metrics['balancing_violation_mw'],0.)
    def test_large_gap_uses_legal_endpoint_once_and_accepts_partial_progress(self):
        t=identify_task(self.state,self.n,self.cfg);a=self.runP(self.state,t,self.solver)
        self.assertEqual(len(a.trials),1);self.assertEqual(a.selected.candidate.signed_magnitude,100.)
        self.assertEqual(a.stop_reason,'single_step_limit_reached')
        self.assertGreater(a.selected.metrics['balancing_violation_mw'],0.)
    def test_initial_candidate_scales_with_measured_gap(self):
        vals=[]
        for pg in (1110.,1140.):
            s,t,pf=self.toy(pg);a=self.runP(s,t,pf);vals.append(a.trials[0].candidate.signed_magnitude)
        self.assertEqual(vals,[10.5,42.])
    def test_one_observed_balance_correction_then_stop(self):
        s,t,pf=self.toy(coefficient=.5);a=self.runP(s,t,pf)
        self.assertEqual([x.candidate.signed_magnitude for x in a.trials],[10.5,21.])
        self.assertEqual(a.stop_reason,'P_target_restored')
        self.assertIn('observed_base_to_trial',a.trials[1].candidate.proposal_reason)
    def test_overshoot_is_corrected_only_when_target_not_feasible(self):
        s,t,pf=self.toy(pg=1110.,coefficient=120.)
        # First large response can cross both limits; correction is necessary
        # to find feasibility and is not post-feasible magnitude optimization.
        a=self.runP(s,t,pf)
        self.assertTrue(a.selected);self.assertTrue(target_restored(a.selected,t))
        self.assertGreater(len(a.trials),1);self.assertLessEqual(len(a.trials),4)
    def test_wrong_direction_rejected_after_two_trials_without_mutation(self):
        s,t,pf=self.toy();old=data(s);a=self.runP(s,t,pf,direction='decrease')
        self.assertIsNone(a.selected);self.assertEqual(len(a.trials),2)
        self.assertEqual(a.stop_reason,'no_effective_progress');self.assertEqual(data(s),old)
    def test_flat_response_rejected_after_two_trials(self):
        s,t,pf=self.toy(coefficient=0.);a=self.runP(s,t,pf)
        self.assertIsNone(a.selected);self.assertEqual(len(a.trials),2)
    def test_all_nonconvergent_stops_early(self):
        s,t,pf=self.toy(nonconverge_above=0.);a=self.runP(s,t,pf)
        self.assertIsNone(a.selected);self.assertEqual(len(a.trials),2)
        self.assertEqual(a.stop_reason,'nonconvergent_trials_rejected')
    def test_nonconvergence_backoff_preserves_best_valid_partial_result(self):
        s,t,pf=self.toy(nonconverge_above=8.);a=self.runP(s,t,pf)
        self.assertTrue(a.selected);self.assertTrue(a.selected.convergence)
        self.assertLessEqual(len(a.trials),4)
        vals=[x.candidate.signed_magnitude for x in a.trials]
        self.assertEqual(len(vals),len(set(vals)))
    def test_budget_exhaustion_has_explicit_reason(self):
        s,t,pf=self.toy(coefficient=.5);a=self.runP(s,t,pf,replace(self.cfg,continuous_budget=1))
        self.assertEqual(len(a.trials),1);self.assertTrue(a.selected)
        self.assertEqual(a.stop_reason,'trial_budget_exhausted')
    def test_below_lower_bound_uses_selected_decrease_direction(self):
        s,t,pf=self.toy(pg=-10.);a=self.runP(s,t,pf,direction='decrease')
        self.assertEqual(a.selected.candidate.signed_magnitude,-10.5)
        self.assertTrue(target_restored(a.selected,t))
    def test_endpoint_restoration_takes_precedence_over_limit_stop(self):
        s,t,pf=self.toy(pg=1200.);a=self.runP(s,t,pf)
        self.assertEqual(a.stop_reason,'P_target_restored');self.assertEqual(len(a.trials),1)
    def test_stale_measurement_rejected(self):
        s,t,pf=self.toy();t=replace(t,current_value=1111.)
        with self.assertRaisesRegex(ValueError,'stale_P_observation'):self.runP(s,t,pf)
    def test_commit_rejects_unacceptable_selected_trial(self):
        s,t,pf=self.toy();a=self.runP(s,t,pf);a.selected.accepted=False
        with self.assertRaisesRegex(ValueError,'unacceptable_selected_patch'):commit_selected_patch(self.n,s,a)

class QAndHVDCPolicyTests(unittest.TestCase):
    def test_Q_full_enumeration_keeps_global_voltage_and_P_guards(self):
        solver=IEEE39BlackBox();n=solver.network;c=copy.deepcopy(solver.initial_controls);cfg=Config()
        p=solver(c);p.balancing_pg={'A':1090.};p.voltage_pu={b:1. for b in p.voltage_pu};p.voltage_pu[12]=.94
        s=OperatingState(c.version(n.network_id),c,p);t=identify_task(s,n,cfg);old=data(s)
        def pf(cc):
            out=copy.deepcopy(p);out.voltage_pu[12]=.96
            if cc.shunt_steps['S5']==1:out.voltage_pu[1]=1.08
            if cc.shunt_steps['S5']==3:out.balancing_pg={'A':1110.}
            return out
        a=realize_action(n,s,t,DeviceDirectionDecision('S5','increase_step',s.state_id),cfg,pf)
        self.assertEqual([x.candidate.signed_magnitude for x in a.trials],[1.,2.,3.])
        self.assertEqual([x.reason for x in a.trials],['no_global_voltage_progress','accepted','balancing_guard'])
        self.assertEqual(a.selected.candidate.signed_magnitude,2.)
        self.assertEqual(a.stop_reason,'complete_legal_discrete_enumeration');self.assertEqual(data(s),old)
    def test_HVDC_joint_patch_respects_pair_headroom_and_algebra(self):
        dev={'D':Device('D','hvdc',2,'A',-400,400,from_bus=2,to_bus=3,from_area='A',to_area='B'),
             'GB':Device('GB','generator',4,'B',0,120.)}
        n=Network('unit_HVDC',{i:{'area':'A' if i<3 else 'B','pd_mw':0.,'qd_mvar':0.} for i in range(1,5)},[],dev,{'A':1,'B':3})
        c=ControlState({'GB':100.},{},{'D':250.});pf0=PFObservation(True,{i:1. for i in range(1,5)},{},{'A':1200.,'B':100.})
        s=OperatingState(c.version(n.network_id),c,pf0);cfg=replace(Config(),monitored=(1,2,3,4));t=identify_task(s,n,cfg)
        def pf(cc):
            dc=cc.hvdc_mw['D']-250.;dg=cc.generator_pg['GB']-100.
            return PFObservation(True,{i:1. for i in range(1,5)},{},{'A':1200.+dc,'B':100.+dc+dg})
        a=realize_action(n,s,t,DeviceDirectionDecision('D','decrease',s.state_id),cfg,pf)
        self.assertEqual(len(a.trials),1);self.assertEqual(a.action_space.upper_magnitude,20.)
        self.assertEqual(a.selected.candidate.paired_delta_pg,20.)
        nxt=commit_selected_patch(n,s,a)
        self.assertEqual(nxt.controls.generator_pg['GB'],120.)
        self.assertEqual(nxt.pf.balancing_pg['B'],100.)
        self.assertEqual(a.stop_reason,'single_step_limit_reached')

if __name__=='__main__':unittest.main()
