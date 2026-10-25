"""One-way solver boundary. No algorithm sees backend matrices or iteration arrays."""
import copy,hashlib
import numpy as np
from schemas import Network,Device,ControlState,PFObservation
import solver_backend as backend

class IEEE39BlackBox:
    def __init__(self):
        self.base=backend.build_case(alpha=1.)
        b=self.base
        nodes={int(r[0]):{'area':'A','pd_mw':float(r[2]),'qd_mvar':float(r[3])} for r in b['bus']}
        branches=[{'id':f'B{k+1}','from':int(r[0]),'to':int(r[1]),
            'kind':'transformer' if r[8] else 'line','in_service':bool(r[10])} for k,r in enumerate(b['branch'])]
        dev={f'G{int(r[0])}':Device(f'G{int(r[0])}','generator',int(r[0]),'A',float(r[9]),float(r[8]))
             for r in b['gen'] if r[7]>.5 and int(r[0])!=39}
        dev.update({f'S{int(r[0])}':Device(f'S{int(r[0])}','shunt',int(r[1]),'A',int(r[3]),int(r[4]),float(r[2])) for r in backend.SPEC})
        h=hashlib.sha256(b''.join(b[k].tobytes() for k in ('bus','gen','branch'))).hexdigest()
        self.network=Network(h,nodes,branches,dev,{'A':39})
        self.initial_controls=ControlState({f'G{int(r[0])}':float(r[1]) for r in b['gen'] if int(r[0])!=39},
                                          {f'S{int(r[0])}':0 for r in backend.SPEC})
        self.calls=0
    def case(self,controls):
        c=copy.deepcopy(self.base)
        for name,pg in controls.generator_pg.items():
            row=next(r for r in c['gen'] if int(r[0])==int(name[1:]));row[1]=pg
        for name,n in controls.shunt_steps.items():
            d=self.network.devices[name];row=next(r for r in c['bus'] if int(r[0])==d.bus)
            row[5]+=d.step_mvar*n
        for name,p in controls.hvdc_mw.items():
            d=self.network.devices[name]
            next(r for r in c['bus'] if int(r[0])==d.from_bus)[2]+=p
            next(r for r in c['bus'] if int(r[0])==d.to_bus)[2]-=p
        return c
    def __call__(self,controls):
        self.calls+=1
        r=backend.ac_pf(self.case(controls))
        # Public fields only; no D-stage dependency on iterations or residuals.
        return PFObservation(bool(r['ok']),{i+1:float(v) for i,v in enumerate(r['Vm'])},
            {i+1:float(v) for i,v in enumerate(np.angle(r['V'],deg=True))},
            {'A':float(r['slack_pg'])},{**controls.generator_pg,'G39':float(r['slack_pg'])})
