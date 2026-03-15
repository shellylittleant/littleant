"""LittleAnt V14 - Command Executor"""
from __future__ import annotations
import subprocess, os, logging
from littleant.models.project import ExecuteSpec

logger = logging.getLogger(__name__)

def run_execute(spec: ExecuteSpec) -> dict:
    executors = {"run_shell": _exec_shell, "write_file": _exec_write, "make_dir": _exec_mkdir,
                 "read_file": _exec_read, "http_request": _exec_http}
    fn = executors.get(spec.type)
    if not fn: return {"success": False, "error": f"Unknown execute type: {spec.type}"}
    try: return fn(spec)
    except Exception as e: return {"success": False, "error": str(e), "stdout": "", "stderr": str(e)}

def _exec_shell(spec):
    r = subprocess.run(spec.command, shell=True, capture_output=True, text=True, timeout=300)
    return {"success": r.returncode == 0, "return_code": r.returncode,
            "stdout": r.stdout[:10000], "stderr": r.stderr[:5000]}

def _exec_write(spec):
    os.makedirs(os.path.dirname(spec.path or "/tmp/x"), exist_ok=True)
    with open(spec.path, "w") as f: f.write(spec.content or "")
    return {"success": True, "stdout": f"Written to {spec.path}", "stderr": ""}

def _exec_mkdir(spec):
    os.makedirs(spec.path, exist_ok=True)
    return {"success": True, "stdout": f"Created {spec.path}", "stderr": ""}

def _exec_read(spec):
    with open(spec.path, "r") as f: content = f.read()[:10000]
    return {"success": True, "stdout": content, "stderr": ""}

def _exec_http(spec):
    import urllib.request, json
    req = urllib.request.Request(spec.url, method=spec.method or "GET",
        headers={"Content-Type": "application/json"})
    if spec.body: req.data = json.dumps(spec.body).encode()
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()[:10000]
    return {"success": True, "stdout": body, "stderr": "", "status_code": resp.status}
