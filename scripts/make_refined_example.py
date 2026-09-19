"""A complete optional-refinement snapshot and ZIP for offline acceptance."""
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.compute import run
from app.export import build_zip

ROOT=Path(__file__).resolve().parents[1]
def main():
    t=datetime(2024,5,3,12,tzinfo=timezone.utc)
    result=run('history_review',t,360,480,[0,480],now=t,refinement_policy={})
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    out=ROOT/'examples/refinement';out.mkdir(exist_ok=True)
    (out/'quiet.json').write_text(json.dumps({**result.S,'git_commit':sha},ensure_ascii=False,indent=2,default=str)+'\n')
    (out/'quiet.zip').write_bytes(build_zip(result.S,result.raw_records))
    print(result.S['grid_refinement']['status'], result.S['grid_refinement']['selected_step_seconds'],
          result.rec.verdict, sha, flush=True)
if __name__=='__main__':main()
