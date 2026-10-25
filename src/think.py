"""One real HTTP request yields one device/direction decision; no numerical control."""
from dataclasses import dataclass
from pathlib import Path
import json,os,time,uuid,urllib.request,urllib.error
from schemas import DeviceDirectionDecision,data
from act import validate_llm_decision

SYSTEM_PROMPT='''You select ONE device and ONE direction for sequential AC power-flow adjustment.
The target, resource relations, path operating states, limits and recent actual AC feedback
are supplied. Candidate order is a stable identifier order, not an effectiveness ranking.
Use power-system operating knowledge and actual feedback. Priorities are convergence,
balancing-generator active-power limits, then all monitored bus voltage limits.
For shunts step_mvar is signed: positive is capacitive, negative is inductive.
The legal_direction_mask states only equipment legality; it is not a recommendation.
Return ONLY a JSON object with exactly two string fields: device_id and direction.
Use one listed candidate_id and one direction in its legal_direction_mask.
Never output MW, MVar, steps, magnitudes, a second device, or an action sequence.
The Act module alone determines numerical magnitudes through black-box AC trials.'''
ENDPOINT='https://api.deepseek.com/chat/completions'

class ThinkError(RuntimeError):pass

def payload(context):return data(context)

def parse_decision(content,context):
    try:obj=json.loads(content) if isinstance(content,str) else content
    except (TypeError,ValueError):raise ThinkError('invalid_json') from None
    if not isinstance(obj,dict) or set(obj)!={'device_id','direction'} or not all(isinstance(v,str) for v in obj.values()):raise ThinkError('exactly_device_and_direction_required')
    decision=DeviceDirectionDecision(obj['device_id'],obj['direction'],context.state_id)
    try:return validate_llm_decision(decision,context)
    except ValueError as e:raise ThinkError(str(e)) from None

@dataclass
class ThinkReply:
    decision:DeviceDirectionDecision
    model:str
    request_id:str|None
    usage:dict
    latency_s:float
    audit_file:str|None=None

class DeepSeekFlashClient:
    is_live=True
    def __init__(self,key=None,audit_dir=None,transport=None,timeout=60.):
        self._key=key or os.getenv('DEEPSEEK_API_KEY','');self.audit_dir=Path(audit_dir) if audit_dir else None
        self.transport=transport;self.is_live=transport is None;self.timeout=timeout
        self.attempts=0
    def decide(self,context):
        if not self._key:raise ThinkError('missing_api_key')
        req={'model':'deepseek-flash','messages':[{'role':'system','content':SYSTEM_PROMPT},
             {'role':'user','content':json.dumps(payload(context),ensure_ascii=False,separators=(',',':'))}],
             'response_format':{'type':'json_object'},'thinking':{'type':'disabled'},'max_tokens':350,'stream':False}
        body=json.dumps(req,ensure_ascii=False).encode();headers={'Content-Type':'application/json','Authorization':'Bearer '+self._key}
        trace={'source':'real_api' if self.is_live else 'mock_transport','state_id':context.state_id,'request_without_credentials':req}
        self.attempts+=1;t0=time.perf_counter();err=None;obj=None
        try:
            if self.transport:raw=self.transport(ENDPOINT,body,headers,self.timeout);status=None
            else:
                with urllib.request.urlopen(urllib.request.Request(ENDPOINT,data=body,headers=headers,method='POST'),timeout=self.timeout) as r:
                    raw=r.read();status=r.status
                    trace['safe_response_headers']={k:v for k,v in r.headers.items() if k.lower() in ('date','content-type','x-request-id','request-id')}
            trace.update(http_status=status,raw_response_body=raw.decode() if isinstance(raw,bytes) else raw)
            obj=json.loads(raw);trace['raw_response_json']=obj
            ch=obj['choices'][0]
            if ch.get('finish_reason')=='length':raise ThinkError('truncated_response')
            decision=parse_decision(ch['message']['content'],context)
        except urllib.error.HTTPError as e:err='API_HTTP_'+str(e.code)
        except (urllib.error.URLError,TimeoutError,OSError) as e:err='API_network_'+type(e).__name__
        except ThinkError as e:err=str(e)
        except (ValueError,KeyError,IndexError,TypeError) as e:err='API_response_structure_'+type(e).__name__
        latency=round(time.perf_counter()-t0,6);trace['latency_s']=latency;trace['error']=err
        fn=None
        if self.audit_dir:
            self.audit_dir.mkdir(parents=True,exist_ok=True);fn=f'call_{self.attempts:03d}_{uuid.uuid4().hex[:8]}.json'
            text=json.dumps(trace,ensure_ascii=False,indent=2).replace(self._key,'[REDACTED]')
            (self.audit_dir/fn).write_text(text)
        if err:raise ThinkError(err)
        return ThinkReply(decision,str(obj.get('model','deepseek-flash')),obj.get('id'),obj.get('usage') or {},latency,fn)

class ScriptedThinkClient:
    """Engineering-only deterministic rule, derived from current context, no trajectory."""
    is_live=False
    def decide(self,context):
        target=context.target;resources=context.candidates
        preferred='increase' if target.violation_type=='above_upper' and target.stage=='P' else 'decrease' if target.stage=='P' else 'increase_step' if target.violation_type=='below_lower' else 'decrease_step'
        choices=[]
        for c in resources:
            d=preferred
            if target.stage=='Q' and c.step_mvar<0:d='decrease_step' if d=='increase_step' else 'increase_step'
            if d in c.legal_direction_mask:choices.append((c,d))
        c,d=choices[0] if choices else (resources[0],resources[0].legal_direction_mask[0])
        return ThinkReply(parse_decision({'device_id':c.candidate_id,'direction':d},context),'SCRIPTED_ENGINEERING_NOT_LLM',None,{},0.)
