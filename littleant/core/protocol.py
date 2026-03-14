"""
LittleAnt V12.1 - Command Protocol
AI->Program commands (keyboard) and Program->AI feedback (display).
Format validation: valid format? required fields? values in range?
"""
from __future__ import annotations
from littleant.config import ALLOWED_EXECUTE_TYPES, ALLOWED_VERIFY_TYPES

AI_COMMANDS = {
    "create_project", "subtasks", "executable", "modify", "query",
    "execute", "stop", "resume", "switch_to_user", "switch_to_program",
    "confirm", "deny", "cancel", "retry", "skip", "abort",
    "report_to_user", "query_template", "use_template", "save_template",
}

PROGRAM_FEEDBACKS = {
    "decompose", "node_success", "node_failed",
    "project_status", "user_message", "template_result", "format_error",
}


def validate_ai_command(data: dict) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "Command must be a JSON object"
    cmd = data.get("cmd")
    if not cmd:
        return False, "Missing 'cmd' field"
    if cmd not in AI_COMMANDS:
        return False, f"Unknown command: {cmd}. Allowed: {AI_COMMANDS}"
    validators = {
        "subtasks": _v_subtasks, "executable": _v_executable, "modify": _v_modify,
        "retry": _v_noderef, "skip": _v_noderef, "create_project": _v_create,
    }
    fn = validators.get(cmd)
    return fn(data) if fn else (True, "")

def _v_subtasks(d):
    c = d.get("children")
    if not isinstance(c, list) or not c: return False, "subtasks: children must be non-empty array"
    for i, ch in enumerate(c):
        if not isinstance(ch, dict): return False, f"children[{i}] must be object"
        if "id" not in ch or "name" not in ch: return False, f"children[{i}] missing id or name"
    return True, ""

def _v_executable(d):
    if not d.get("node_id"): return False, "executable: missing node_id"
    ex = d.get("execute")
    if not isinstance(ex, dict): return False, "executable: missing execute object"
    if ex.get("type") not in ALLOWED_EXECUTE_TYPES: return False, f"Invalid execute.type: {ex.get('type')}"
    vr = d.get("verify")
    if not isinstance(vr, dict): return False, "executable: missing verify object"
    if vr.get("type") not in ALLOWED_VERIFY_TYPES: return False, f"Invalid verify.type: {vr.get('type')}"
    return True, ""

def _v_modify(d):
    if "node_id" not in d: return False, "modify: missing node_id"
    if "execute" not in d and "verify" not in d: return False, "modify: must have execute or verify"
    return True, ""

def _v_noderef(d):
    return (True, "") if "node_id" in d else (False, f"{d['cmd']}: missing node_id")

def _v_create(d):
    if "name" not in d: return False, "create_project: missing name"
    return True, ""

# ============================================================
# Build feedback messages (Program -> AI)
# ============================================================

def build_decompose(node_id, node_name, context=None):
    fb = {"feedback": "decompose", "node_id": node_id, "node_name": node_name}
    if context: fb["context"] = context
    return fb

def build_node_success(node_id, execute_output, verify_output):
    return {"feedback": "node_success", "node_id": node_id,
            "execute_output": execute_output, "verify_output": verify_output}

def build_node_failed(project, node_id, execute_output, verify_output, attempts, environment):
    return {"feedback": "node_failed", "project_id": project.id, "node_id": node_id,
            "execute_result": execute_output, "verify_result": verify_output,
            "attempts": attempts, "environment": environment,
            "project_tree": project.get_tree_summary()}

def build_project_status(project):
    done = sum(1 for n in project.nodes.values() if n.status.value == "completed")
    failed = [nid for nid, n in project.nodes.items() if n.status.value == "failed"]
    return {"feedback": "project_status", "project_id": project.id,
            "completed": done, "total": len(project.nodes), "failed": failed}

def build_format_error(original, error):
    return {"feedback": "format_error", "error": error, "original": original}

def build_template_result(result_type, data):
    return {"feedback": "template_result", "type": result_type, **data}
