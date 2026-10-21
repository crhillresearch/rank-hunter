from __future__ import annotations
import argparse, json
from rank42.db import connect
from rank42.plugins import get_plugin, plugin_state, set_plugin_state, validate_plugin

def main():
    ap=argparse.ArgumentParser(description="Validate and activate a Rank Hunter plugin")
    ap.add_argument("--project-root",default=".")
    ap.add_argument("--db",default="rank42.db")
    ap.add_argument("--plugin",required=True)
    ap.add_argument("--disable",action="store_true")
    args=ap.parse_args()
    db=connect(args.db)
    if args.disable:
        set_plugin_state(db,args.plugin,enabled=False,status="disabled")
        print(json.dumps({"plugin":args.plugin,"status":"disabled"},sort_keys=True)); return
    try:
        plugin=get_plugin(args.project_root,args.plugin)
        result=validate_plugin(plugin,import_science=True)
        set_plugin_state(db,plugin.id,enabled=True,status="ready",validation=result)
        print(json.dumps(result,sort_keys=True))
    except Exception as exc:
        row=plugin_state(db,args.plugin)
        try:
            previous=json.loads(row["validation_json"] or "{}") if row is not None else {}
        except (TypeError,ValueError,json.JSONDecodeError):
            previous={}
        if not isinstance(previous,dict):
            previous={}
        previous["last_validation_error"]=repr(exc)
        set_plugin_state(
            db,args.plugin,
            enabled=False,
            status="invalid",
            validation=previous,
        )
        raise

if __name__=="__main__": main()
