"""LittleAnt V12.1 - Read-Only Executor for Front-end AI"""
from __future__ import annotations
import subprocess, logging

logger = logging.getLogger(__name__)

READONLY_WHITELIST = {
    "cat","head","tail","less","more","ls","ll","dir","tree","find","locate",
    "wc","grep","awk","whoami","id","hostname","uptime","date",
    "uname","arch","lscpu","lsblk","lspci","lsusb",
    "free","df","du","top","htop","vmstat","iostat","mpstat",
    "ps","pgrep","which","whereis","type",
    "ip","ifconfig","ping","dig","nslookup","host","netstat","ss","curl",
    "traceroute","mtr","systemctl","journalctl",
    "dpkg","apt-cache","pip","npm","crontab",
    "file","stat","md5sum","sha256sum",
    "php","python3","python","node","nginx","mysql",
}

DANGEROUS_PATTERNS = [
    "rm ","rm\t","rmdir","mkfs","dd if=","> ",">> ",
    "chmod","chown","chgrp","kill","killall","pkill",
    "reboot","shutdown","halt","poweroff",
    "passwd","useradd","userdel","usermod",
    "apt-get install","apt install","apt-get remove","apt remove",
    "pip install","npm install",
    "systemctl start","systemctl stop","systemctl restart","systemctl enable","systemctl disable",
    "crontab -e","crontab -r",
    "mysql -e","mysql --execute","&& ","|| ","; ","$(","` ",
]

def is_safe_readonly(command):
    cmd = command.strip()
    if not cmd: return False, "empty command"
    main = cmd.split()[0].split("/")[-1]
    if main not in READONLY_WHITELIST: return False, f"'{main}' not in read-only whitelist"
    for p in DANGEROUS_PATTERNS:
        if p in cmd: return False, f"contains dangerous pattern: '{p.strip()}'"
    if main == "systemctl":
        ok = {"status","is-active","is-enabled","list-units","list-unit-files","show"}
        parts = cmd.split()
        if len(parts) > 1 and parts[1] not in ok: return False, f"systemctl only allows: {ok}"
    if main == "crontab" and "-l" not in cmd.split(): return False, "crontab only allows -l"
    return True, "safe"

def run_readonly(command):
    safe, reason = is_safe_readonly(command)
    if not safe: return {"success": False, "output": "", "error": f"Denied: {reason}"}
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        return {"success": r.returncode == 0, "output": r.stdout[:5000],
                "error": r.stderr[:2000] if r.returncode != 0 else ""}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "Command timed out (30s)"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
