"""Executable method objects. All algorithm-facing PF fields are public outputs."""
from __future__ import annotations
from dataclasses import dataclass,field,asdict
import hashlib,json

def data(x):
    if hasattr(x,'__dataclass_fields__'):return data(asdict(x))
    if isinstance(x,dict):return {str(k):data(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)):return [data(v) for v in x]
    return x

@dataclass(frozen=True)
class Config:
    K:int=3
    H:int=4
    balancing_min_mw:float=0.
    balancing_max_mw:float=1100.
    vmin:float=.95
    vmax:float=1.05
    monitored:tuple=tuple(range(1,30))
    max_p_step_mw:float=100.
    continuous_budget:int=4
    p_reserve_fraction:float=.05
    search_resolution_mw:float=.5
    discrete_budget:int=8
    max_steps:int=90

@dataclass(frozen=True)
class Device:
    device_id:str
    kind:str
    bus:int
    area:str
    lower:float
    upper:float
    step_mvar:float=0.
    from_bus:int|None=None
    to_bus:int|None=None
    from_area:str|None=None
    to_area:str|None=None

@dataclass
class Network:
    network_id:str
    nodes:dict
    branches:list
    devices:dict[str,Device]
    balancing_buses:dict

@dataclass
class ControlState:
    generator_pg:dict
    shunt_steps:dict
    hvdc_mw:dict=field(default_factory=dict)
    def version(self,network_id):
        raw=json.dumps({'network':network_id,'controls':data(self)},sort_keys=True,separators=(',',':'))
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

@dataclass
class PFObservation:
    converged:bool
    voltage_pu:dict
    angle_deg:dict
    balancing_pg:dict
    generator_pg:dict=field(default_factory=dict)
    branch_flows:list=field(default_factory=list)

@dataclass
class OperatingState:
    state_id:str
    controls:ControlState
    pf:PFObservation

@dataclass(frozen=True)
class TaskTarget:
    stage:str
    target_id:int|None
    violation_type:str
    current_value:float|None
    limits:tuple
    violation_magnitude:float
    area_id:str|None=None

@dataclass
class RelationSubgraph:
    task:TaskTarget
    selected_node_ids:list
    branch_relations:list
    candidate_ids:list
    candidate_paths:dict
    candidate_roles:dict
    hvdc_relations:list
    extraction_metadata:dict

@dataclass
class PathStateFeatures:
    path_node_ids:list
    hop_count:int|None
    node_states:list
    path_summary:dict
    endpoint_state:dict
    observed_branch_flows:list
    area_relation:dict

@dataclass
class CandidateRelationCode:
    candidate_id:str
    candidate_type:str
    relation_role:str
    bus:int
    area_id:str
    current_setting:float
    lower_limit:float
    upper_limit:float
    upward_margin:float
    downward_margin:float
    legal_direction_mask:list
    legal_discrete_positions:list
    step_mvar:float
    path_state:PathStateFeatures
    recent_feedback:list
    paired_resource_states:list=field(default_factory=list)

@dataclass
class CompactDecisionContext:
    state_id:str
    target:TaskTarget
    candidates:list[CandidateRelationCode]
    recent_feedback:list
    extraction_summary:dict

@dataclass(frozen=True)
class DeviceDirectionDecision:
    device_id:str
    direction:str
    state_id:str

@dataclass
class LegalActionSpace:
    state_id:str
    device_id:str
    device_type:str
    direction:str
    sign:int
    lower_magnitude:float
    upper_magnitude:float
    discrete_magnitudes:list
    max_trials:int
    resolution:float
    paired_generator_id:str|None=None
    paired_pg_per_transfer:float=0.

@dataclass
class ActionCandidate:
    candidate_id:str
    device_id:str
    signed_magnitude:float
    batch_id:int
    proposal_reason:str
    paired_generator_id:str|None=None
    paired_delta_pg:float=0.

@dataclass
class PFTrialResult:
    candidate:ActionCandidate
    convergence:bool
    metrics:dict|None
    priority_stage_metric:dict
    protected_metrics:dict
    accepted:bool
    reason:str
    observation:PFObservation

@dataclass
class ActionSelectionResult:
    state_id:str
    task:TaskTarget
    action_space:LegalActionSpace
    trials:list[PFTrialResult]
    selected:PFTrialResult|None
    status:str
    refinement_log:list
    stop_reason:str=''
    search_policy:str=''
