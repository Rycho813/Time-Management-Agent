import hashlib, json, os, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from timemap_daily_transport import Session, encode
def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()
def atomic(path,value,mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".daily-tmp")
    with tmp.open("xb") as f:
        os.fchmod(f.fileno(),mode)
        f.write(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode()+b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,path)
def others(value,day):
    return {**value,"days":{k:v for k,v in value["days"].items() if k!=day}}
def check(reply,seed,day):
    if reply.get("schema")!=1 or reply.get("ok") is not True or reply.get("date")!=day:
        raise RuntimeError("Daily response invalid")
    updated=reply["calendar"]
    target=updated["days"].get(day,{})
    if updated.get("version")!=1 or target.get("status")!="complete" or type(target.get("effective_minutes")) is not int:
        raise RuntimeError("Calendar day incomplete")
    if others(seed,day)!=others(updated,day) or digest(seed) not in {reply["input_sha256"],reply["output_sha256"]} or digest(updated)!=reply["output_sha256"]:
        raise RuntimeError("History mismatch")
    if not isinstance(reply["report"],str) or not reply["report"].strip() or hashlib.sha256(reply["report"].encode()).hexdigest()!=reply["report_sha256"]:
        raise RuntimeError("Report mismatch")
    if not isinstance(reply["web_calendar"],dict):
        raise RuntimeError("Web calendar invalid")
def fetch(root,cache,env,remote):
    today=datetime.now(ZoneInfo("Asia/Shanghai")).date()
    day=env.get("DAILY_TARGET_DATE","").strip() or (today-timedelta(days=1)).isoformat()
    if date.fromisoformat(day).isoformat()!=day or date.fromisoformat(day)>today:
        raise RuntimeError("Target date invalid")
    seed=json.loads((root/"data/life_calendar_daily.json").read_bytes())
    if seed.get("version")!=1 or not isinstance(seed.get("days"),dict) or not seed["days"]:
        raise RuntimeError("History required")
    status=remote.request({"schema":1,"action":"status"})
    if status.get("business_execution_enabled") is not True:
        raise RuntimeError("Server execution disabled")
    reply=remote.request({"schema":1,"action":"run","date":day,"calendar":seed})
    check(reply,seed,day)
    atomic(cache,reply,0o600)
    atomic(root/"data/life_calendar_daily.json",reply["calendar"])
    atomic(root/"web/data/life_calendar.json",reply["web_calendar"])
    return {"date":day,"cached":reply["cached"]}
def send(cache,env,remote):
    flag=env.get("DAILY_SEND_FEISHU","false").lower()
    if flag not in {"true","false"}:
        raise RuntimeError("Send flag invalid")
    if flag=="false": return {"delivery":"disabled"}
    reply=json.loads(cache.read_bytes())
    report=reply["report"]
    if not isinstance(report,str) or hashlib.sha256(report.encode()).hexdigest()!=reply["report_sha256"]:
        raise RuntimeError("Report mismatch")
    if reply["send_state"]=="confirmed":
        return {"delivery":"already_confirmed"}
    claim={"schema":1,"action":"claim-send","date":reply["date"],
           "report_sha256":reply["report_sha256"]}
    reserved=remote.request(claim)
    if reserved.get("send") is not True:
        raise RuntimeError("Send already reserved; verify prior delivery")
    token=reserved.get("token")
    if not isinstance(token,str) or len(token)!=32:
        raise RuntimeError("Send claim invalid")
    remote.deliver(report)
    confirmed=remote.request({**claim,"action":"confirm-send","token":token})
    if confirmed.get("confirmed") is not True:
        raise RuntimeError("Delivery acknowledgement failed")
    atomic(cache,{**reply,"send_state":"confirmed"},0o600)
    return {"delivery":"confirmed"}
def main():
    os.umask(0o077)
    if len(sys.argv)!=2 or sys.argv[1] not in {"status","fetch","send"}:
        raise RuntimeError("Expected status, fetch or send")
    env=os.environ
    cache=Path(env["RUNNER_TEMP"])/"timemap-daily-response.json"
    with Session(env) as remote:
        result=remote.request({"schema":1,"action":"status"}) if sys.argv[1]=="status" else fetch(Path.cwd(),cache,env,remote) if sys.argv[1]=="fetch" else send(cache,env,remote)
    if sys.argv[1]=="fetch" and env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"],"a") as f: f.write("target_date="+result["date"]+"\n")
    print(json.dumps(result))
if __name__=="__main__":
    try: main()
    except Exception as exc:
        print("DAILY_CLIENT_FAILED: "+(str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__),file=sys.stderr)
        sys.exit(1)
