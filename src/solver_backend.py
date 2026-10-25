import json,re,copy,time,os
from pathlib import Path
import numpy as np
import networkx as nx
from scipy.linalg import solve
np.set_printoptions(precision=5,suppress=True)
B=100.0
BASEFILE=Path(os.environ.get('MATPOWER_CASE39_FILE', Path(__file__).parent/'case39_local.m'))
SC=Path(os.environ.get('PF_SCREEN_SCENARIO', Path(__file__).parent/'demo_scenario.json'))
SPEC=np.array([[1,4,100,0,5],[2,5,100,0,3],[3,7,100,0,5],[4,8,100,0,5],[5,12,100,0,3],[6,15,100,0,5],[7,17,-100,0,1],[8,20,100,0,5],[9,21,100,0,5],[10,23,-100,0,1],[11,25,100,0,3],[12,27,100,0,3]],dtype=float)

def parse_mpc(p=None):
 p=BASEFILE if p is None else p
 s=Path(p).read_text();ret={}
 for f in ['bus','gen','branch']:
  m=re.search(r'mpc\.'+f+r'\s*=\s*\[(.*?)\];',s,re.S)
  if not m: raise ValueError(f)
  ret[f]=np.array([[float(t) for t in z.split()] for z in m.group(1).split(';') if z.strip()])
 ret['bus'][ret['bus'][:,0]==39,1]=3;ret['bus'][ret['bus'][:,0]==31,1]=2
 return ret

def ybus_from_branch(bus,branch):
 ids={int(b[0]):i for i,b in enumerate(bus)}
 y=np.zeros((len(bus),len(bus)),dtype=complex)
 for br in branch:
  if br[10]<0.5:continue
  i,j=ids[int(br[0])],ids[int(br[1])]
  ser=1/(br[2]+1j*br[3]);sh=1j*br[4]/2
  tap=br[8] if br[8] else 1
  shift=np.exp(1j*np.deg2rad(br[9]));tap*=shift
  y[i,i]+=(ser+sh)/(abs(tap)**2);y[j,j]+=ser+sh
  y[i,j]+=-ser/np.conj(tap);y[j,i]+=-ser/tap
 for k,row in enumerate(bus):
  y[k,k]+=(row[4]+1j*row[5])/B
 return y

def build_case(alpha=0.0,q_only=False,shunts=None):
 c=parse_mpc();s=json.loads(SC.read_text()); buses=c['bus'];gen=c['gen'];
 for row in buses:
  name=int(row[0]);item=s['bus_loads'][name-1]
  assert int(item['bus_id'])==name
  if q_only: row[3]=(1-alpha)*row[3]+alpha*float(item['qd_mvar'])
  else:
   row[2]=(1-alpha)*row[2]+alpha*float(item['pd_mw'])
   row[3]=(1-alpha)*row[3]+alpha*float(item['qd_mvar'])
 if not q_only:
  for item in s['generator_pg']:
   rows=np.where(gen[:,0]==int(item['bus_id']))[0]
   assert len(rows)==1
   gen[rows[0],1]=(1-alpha)*gen[rows[0],1]+alpha*float(item['pg_mw'])
 if shunts is not None:
  for k,n in enumerate(shunts): buses[int(SPEC[k,1])-1,5]+=SPEC[k,2]*n
 return c

def ac_pf(c,maxit=35,tol=1e-9):
 bus,gen=c['bus'],c['gen']
 y=ybus_from_branch(bus,c['branch']);N=len(bus)
 ref=np.where(bus[:,1]==3)[0];pv=np.where(bus[:,1]==2)[0];pq=np.where(bus[:,1]==1)[0];pvpq=np.r_[pv,pq]
 assert len(ref)==1
 Vm=bus[:,7].copy();Va=np.deg2rad(bus[:,8].copy());
 for g in gen: Vm[int(g[0])-1]=g[5]
 V=Vm*np.exp(1j*Va)
 spec=-(bus[:,2]+1j*bus[:,3])/B
 for g in gen:spec[int(g[0])-1]+=g[1]/B
 converged=False;norm=float('nan')
 for t in range(maxit+1):
  I=y@V;calc=V*np.conj(I);miss=spec-calc
  F=np.r_[miss.real[pvpq],miss.imag[pq]]
  norm=np.max(np.abs(F))
  if norm<tol:converged=True;break
  if t==maxit:break
  vnorm=V/np.maximum(abs(V),1e-12)
  dVm=(V[:,None]*np.conj(y*vnorm[None,:]))+np.diag(np.conj(I)*vnorm)
  dVa=1j*V[:,None]*np.conj(np.diag(I)-y*V[None,:])
  jmat=np.block([[dVa.real[np.ix_(pvpq,pvpq)],dVm.real[np.ix_(pvpq,pq)]],
                 [dVa.imag[np.ix_(pq,pvpq)],dVm.imag[np.ix_(pq,pq)]]])
  try: dx=solve(jmat,F,assume_a='gen',check_finite=False)
  except Exception:break
  Va[pvpq]+=dx[:len(pvpq)]
  Vm[pq]+=dx[len(pvpq):]
  V=Vm*np.exp(1j*Va)
  if not np.isfinite(V).all() or max(abs(V))>50:break
 slack=int(ref[0]);slackpg=bus[slack,2]+(V*np.conj(y@V))[slack].real*B
 return {'ok':converged,'iterations':t,'mismatch':norm,'V':V,'Vm':abs(V),'J':jmat if 'jmat' in locals() else None, 'pv':pv,'pq':pq,'pvpq':pvpq,'slack_pg':slackpg,'Y':y}

