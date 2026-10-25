"""Read observed commercial DSP .dat/.pf inputs WITHOUT reconstructing PF Jacobian.
This is an inspectable example parser for the supplied IEEE39 reports, not a
complete production reader for every DSP version or device card.
"""
from __future__ import annotations
import re,json,math
from pathlib import Path

VOLT_START='>>>节点电压'
VOLT_END='>>>发电机出力'
_FLOAT=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'

def parse_dat(path):
    s=Path(path).read_text(encoding='utf-8-sig')
    buses={};edges=[];shunts=[];prev=None
    for line in s.splitlines():
        m=re.match(r'^\s*(B|BQ|BS)\s+(new\d+)\s+('+_FLOAT+r')',line)
        if m:
            prev=int(m.group(2)[3:]);buses[prev]={'bus':prev,'nominal_kv':float(m.group(3)),
                'record_type':m.group(1)}
            continue
        ms=re.search(r'可投切(电容|电抗):\s*(\d+)\s*\*\s*('+_FLOAT+r')\s*Mvar',line)
        if ms and prev is not None:
            shunts.append({'bus':prev,'kind':'capacitive' if ms.group(1)=='电容' else 'reactive',
              'step_count':int(ms.group(2)),'mvar_per_step':float(ms.group(3))});continue
        e=re.match(r'^\s*([LT])\s+(new\d+)\s+'+_FLOAT+r'\s+(new\d+)\s+'+_FLOAT,line)
        if e:
            edges.append({'from':int(e.group(2)[3:]),'to':int(e.group(3)[3:]),'type':e.group(1)})
    return {'bus':list(buses.values()),'edges':edges,'shunts':shunts,
            'source':Path(path).name,'has_explicit_ybus':False,'has_explicit_jacobian':False}


def parse_pf(path):
    b=Path(path).read_bytes();s=b.decode('gb18030',errors='replace')
    converged=('计算结果收敛' in s or 'POWER FLOW SOLUTION IS REACHED' in s)
    start=s.index(VOLT_START);end=s.index(VOLT_END,start)
    vm={};angles={}
    for line in s[start:end].splitlines():
        m=re.match(r'^\s*\d+\s+new(\d+)\s+'+_FLOAT+r'\s+('+_FLOAT+r')\s+'+_FLOAT+r'\s+('+_FLOAT+r')\s*$',line)
        if m:
            i=int(m.group(1));vm[i]=float(m.group(2));angles[i]=float(m.group(3))
    branch_flows=[]
    ftxt=s[s.index('>>>支路潮流'):s.index('*  节点潮流数据列表')]
    rx=(r'^\s*\d+\s+new(\d+)\s+'+_FLOAT+r'\s+\d+\s+new(\d+)\s+'+_FLOAT+
        r'\s+('+_FLOAT+r')\s+('+_FLOAT+r')\s+('+_FLOAT+r')\s+('+_FLOAT+r')')
    for line in ftxt.splitlines():
        bmatch=re.match(rx,line)
        if bmatch:
            b1,b2,p1,q1,p2,q2=bmatch.groups()
            branch_flows.append({'from':int(b1),'to':int(b2),
                 'p_from_mw':float(p1),'q_from_mvar':float(q1),
                 'p_to_mw':float(p2),'q_to_mvar':float(q2)})
    gen={}
    gs=s[s.index('>>>发电机出力'):s.index('>>>支路潮流')]
    for line in gs.splitlines():
        m=re.match(r"^\s*(\d+)\s+new(\d+)\s+"+_FLOAT+r"\s+'[^']*'\s+("+_FLOAT+r")\s+("+_FLOAT+r")",line)
        if m:gen[int(m.group(2))]={'pg_mw':float(m.group(3)),'qg_mvar':float(m.group(4))}
    slack=None
    m=re.search(r'\bnew(\d+)\s+'+_FLOAT+r'\s+'+_FLOAT+r'\s+'+_FLOAT+r'\s+'+_FLOAT+r'\s+'+_FLOAT+r'',s[s.index('各区域平衡机出力数据列表'):])
    if m:slack=int(m.group(1))
    return {'converged':converged,'vm_pu':vm,'angles_deg':angles,'generator':gen,'branch_flows':branch_flows,
            'balancing_bus':slack,'source':Path(path).name,
            'has_explicit_ybus':False,'has_explicit_jacobian':False,
            'complex_voltage_available':len(vm)==len(angles) and bool(vm)}


def extract_report_context(dat_path,pf_path):
    a=parse_dat(dat_path);b=parse_pf(pf_path)
    return {'case':a,'solution':b,
      'coverage':{'buses_in_dat':len(a['bus']),'branches_in_dat':len(a['edges']),
                  'shunt_banks_in_dat':len(a['shunts']),'voltage_buses_in_pf':len(b['vm_pu']),
                  'generator_rows_in_pf':len(b['generator']),'branch_flows_in_pf':len(b['branch_flows'])},
      'source_scope_warning':'Dat/PF are a separate sample case; do not identify their shunt IDs or dispatch settings with mixed_full_demo without mapping.'}

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('dat');ap.add_argument('pf');a=ap.parse_args()
    d=extract_report_context(a.dat,a.pf)
    print(json.dumps({'coverage':d['coverage'],'converged':d['solution']['converged'],
        'balancing_bus':d['solution']['balancing_bus'],
        'voltage_sample':{str(k):d['solution']['vm_pu'][k] for k in [12,25,39]},
        'shunt_sites':[s['bus'] for s in d['case']['shunts']]},ensure_ascii=False,indent=2))
