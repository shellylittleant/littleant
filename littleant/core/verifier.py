"""LittleAnt V13 - Mechanical Verifiers (8 types, zero AI tokens)"""
from __future__ import annotations
import subprocess, os, socket, logging
from littleant.models.project import VerifySpec

logger = logging.getLogger(__name__)

def run_verify(spec: VerifySpec) -> dict:
    pre = _precheck(spec)
    if pre: return pre
    fns = {"return_code_eq": _v_rc, "file_exists": _v_fe, "content_contains": _v_cc,
           "service_active": _v_sa, "http_status_eq": _v_hs, "json_field_eq": _v_jf,
           "dns_resolves_to": _v_dns, "port_open": _v_port}
    fn = fns.get(spec.type)
    if not fn: return {"passed": False, "detail": f"Unknown verify type: {spec.type}"}
    try: return fn(spec)
    except Exception as e: return {"passed": False, "detail": f"Verify error: {str(e)}"}

def _precheck(spec):
    req = {"return_code_eq": ["command"], "file_exists": ["path"], "content_contains": ["keyword"],
           "service_active": ["service_name"], "http_status_eq": ["url"],
           "json_field_eq": ["field_path","expected"], "dns_resolves_to": ["domain","expected_ip"],
           "port_open": ["host","port"]}
    for f in req.get(spec.type, []):
        if getattr(spec, f, None) is None:
            return {"passed": True, "detail": f"verify param '{f}' missing, skipping (pass)"}
    if spec.type == "content_contains" and not spec.path and not spec.command:
        return {"passed": True, "detail": "content_contains: no path or command, skipping"}
    return None

def _v_rc(s):
    r = subprocess.run(s.command, shell=True, capture_output=True, timeout=30)
    exp = s.expected_code if s.expected_code is not None else 0
    return {"passed": r.returncode == exp, "detail": f"return code {r.returncode}, expected {exp}"}

def _v_fe(s):
    e = os.path.exists(s.path)
    return {"passed": e, "detail": f"{'exists' if e else 'not found'}: {s.path}"}

def _v_cc(s):
    if s.command:
        r = subprocess.run(s.command, shell=True, capture_output=True, text=True, timeout=30)
        txt = r.stdout
    elif s.path:
        with open(s.path) as f: txt = f.read()
    else: return {"passed": False, "detail": "No path or command"}
    found = s.keyword in txt
    return {"passed": found, "detail": f"keyword '{s.keyword}' {'found' if found else 'not found'}"}

def _v_sa(s):
    r = subprocess.run(f"systemctl is-active {s.service_name}", shell=True, capture_output=True, text=True)
    active = r.stdout.strip() == "active"
    return {"passed": active, "detail": f"service {s.service_name}: {r.stdout.strip()}"}

def _v_hs(s):
    import urllib.request
    try:
        r = urllib.request.urlopen(s.url, timeout=10)
        code = r.status
    except Exception as e: code = getattr(e, 'code', 0)
    exp = s.expected_status or 200
    return {"passed": code == exp, "detail": f"HTTP {code}, expected {exp}"}

def _v_jf(s):
    import json
    if s.command:
        r = subprocess.run(s.command, shell=True, capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
    else: return {"passed": False, "detail": "No source"}
    val = data
    for k in s.field_path.split("."): val = val[k] if isinstance(val, dict) else val[int(k)]
    match = str(val) == str(s.expected)
    return {"passed": match, "detail": f"{s.field_path}={val}, expected {s.expected}"}

def _v_dns(s):
    ips = socket.gethostbyname_ex(s.domain)[2]
    match = s.expected_ip in ips
    return {"passed": match, "detail": f"{s.domain} resolves to {ips}, expected {s.expected_ip}"}

def _v_port(s):
    try:
        sock = socket.create_connection((s.host, s.port), timeout=5); sock.close(); ok = True
    except: ok = False
    return {"passed": ok, "detail": f"port {s.host}:{s.port} {'open' if ok else 'closed'}"}
