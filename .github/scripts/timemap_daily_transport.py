import ipaddress, json, os, subprocess, tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
class TransportError(RuntimeError): pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
class Session:
    def __init__(self,env,opener=None):
        self.env=env
        self.opener=opener or build_opener(NoRedirect()).open
    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory(prefix="timemap-ssh-")
        try:
            p=Path(self.temp.name)
            self.key,self.known=p/"key",p/"known_hosts"
            self.key.write_text(self.env["TIMEMAP_DAILY_SSH_KEY"].rstrip()+"\n")
            self.known.write_text(self.env["TIMEMAP_DAILY_SSH_KNOWN_HOSTS"].rstrip()+"\n")
            os.chmod(self.key,0o600)
            os.chmod(self.known,0o600)
            self.host=str(ipaddress.ip_address(self.env["TIMEMAP_DAILY_SSH_HOST"]))
            self.port=int(self.env["TIMEMAP_DAILY_SSH_PORT"])
            self.user=self.env["TIMEMAP_DAILY_SSH_USER"]
            if self.user!="timemap-daily" or not 1<=self.port<=65535:
                raise ValueError()
            return self
        except Exception:
            self.temp.cleanup()
            raise TransportError("SSH configuration invalid") from None
    def __exit__(self,*args): self.temp.cleanup()
    def request(self,value):
        data=encode(value)+b"\n"
        if len(data)>4*1024*1024:
            raise TransportError("Request too large")
        args=["ssh","-F","/dev/null","-T","-i",str(self.key),"-p",str(self.port),
          "-o","BatchMode=yes","-o","IdentitiesOnly=yes",
          "-o","StrictHostKeyChecking=yes","-o","UserKnownHostsFile="+str(self.known),
          "-o","ConnectTimeout=15","-o","ServerAliveInterval=30",
          "-o","ServerAliveCountMax=3",self.user+"@"+self.host,"daily-v1"]
        try:
            p=subprocess.run(args,input=data,capture_output=True,timeout=1020)
            if p.returncode or len(p.stdout)>16*1024*1024:
                raise ValueError()
            result=json.loads(p.stdout)
            if result.get("schema")!=1 or result.get("ok") is not True:
                raise ValueError()
            return result
        except Exception:
            raise TransportError("SSH request failed; no automatic retry") from None
    def deliver(self,report):
        try:
            url=self.env["FEISHU_WEBHOOK_URL"]
            parts=urlsplit(url)
            if parts.scheme!="https" or parts.hostname not in {"open.feishu.cn","open.larksuite.com"} or parts.username or parts.password:
                raise ValueError()
            body=json.dumps({"msg_type":"text","content":{"text":report}},ensure_ascii=False).encode()
            req=Request(url,data=body,headers={"Content-Type":"application/json; charset=utf-8"},method="POST")
            with self.opener(req,timeout=30) as response:
                data=response.read(65537)
                if response.status!=200 or len(data)>65536:
                    raise ValueError()
                result=json.loads(data)
                if type(result.get("code")) is not int or result["code"]!=0:
                    raise ValueError()
        except Exception:
            raise TransportError("Feishu delivery not confirmed; no automatic retry") from None
