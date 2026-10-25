from pathlib import Path
import sys,argparse,getpass,os
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from schemas import Config
from blackbox_adapter import IEEE39BlackBox
from loop import run_loop
from think import ScriptedThinkClient,DeepSeekFlashClient

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['offline','live'],required=True)
    p.add_argument('--out-dir',required=True);p.add_argument('--ask-key',action='store_true')
    p.add_argument('--max-steps',type=int,default=90);args=p.parse_args()
    dst=Path(args.out_dir)
    if dst.exists() and any(dst.iterdir()):p.error('Output directory must be new/empty to preserve evidence')
    solver=IEEE39BlackBox();cfg=Config(max_steps=args.max_steps)
    if args.mode=='live':
        key=getpass.getpass('DeepSeek API key (hidden): ') if args.ask_key else os.getenv('DEEPSEEK_API_KEY','')
        if not key:p.error('missing_api_key')
        client=DeepSeekFlashClient(key,audit_dir=dst/'api_audit');key=''
    else:client=ScriptedThinkClient()
    result=run_loop(solver.network,solver.initial_controls,solver,client,cfg,dst)
    return 0 if result['status']=='terminal_feasible' else 2
if __name__=='__main__':raise SystemExit(main())
