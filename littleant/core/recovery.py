"""
LittleAnt V12.1 - Error Recovery
AI autonomous recovery. Only escalates to user at safety valve limits.
node_failed includes project tree for root cause tracing.
"""
from __future__ import annotations
import json, logging, platform
from littleant.models.project import Project, Node, NodeStatus, ExecuteSpec, VerifySpec
from littleant.core.protocol import build_node_failed
from littleant.ai.adapter import AIAdapter, RECOVERY_PROMPT
from littleant.config import MAX_NODE_RETRIES, MAX_NODE_MODIFICATIONS, MAX_CONSECUTIVE_FAILURES

logger = logging.getLogger(__name__)

class RecoveryAction:
    RETRY = "retry"
    MODIFY = "modify"
    SKIP = "skip"
    REPORT_TO_USER = "report_to_user"
    ABORT = "abort"

def handle_failure(ai, project, node, exec_output, verify_output):
    project.consecutive_failures += 1
    if project.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        logger.warning(f"Project consecutive failures {project.consecutive_failures}, escalating to user")
        return RecoveryAction.REPORT_TO_USER
    if node.retry_count >= MAX_NODE_RETRIES and node.modify_count >= MAX_NODE_MODIFICATIONS:
        logger.info(f"Node {node.id} retries and mods exhausted, auto-skipping")
        return RecoveryAction.SKIP

    env = {"os": f"{platform.system()} {platform.release()}", "arch": platform.machine()}
    packet = build_node_failed(project, node.id, exec_output, verify_output, node.retry_count + 1, env)
    messages = [{"role": "user", "content": json.dumps(packet, ensure_ascii=False)}]
    try:
        response = ai.ask(messages, system_prompt=RECOVERY_PROMPT)
        project.ai_call_count += 1
    except Exception as e:
        logger.error(f"AI recovery failed: {e}, auto-skipping")
        return RecoveryAction.SKIP

    cmd = response.get("cmd")
    if cmd == "retry":
        if node.retry_count >= MAX_NODE_RETRIES:
            return RecoveryAction.SKIP
        node.retry_count += 1
        logger.info(f"Node {node.id}: AI decided retry (#{node.retry_count})")
        return RecoveryAction.RETRY
    elif cmd == "modify":
        if node.modify_count >= MAX_NODE_MODIFICATIONS:
            return RecoveryAction.SKIP
        node.modify_count += 1
        if "execute" in response: node.execute = ExecuteSpec.from_dict(response["execute"])
        if "verify" in response: node.verify = VerifySpec.from_dict(response["verify"])
        logger.info(f"Node {node.id}: AI modified command (#{node.modify_count})")
        return RecoveryAction.MODIFY
    elif cmd == "skip":
        logger.info(f"Node {node.id}: AI decided skip")
        return RecoveryAction.SKIP
    elif cmd == "abort":
        return RecoveryAction.ABORT
    else:
        logger.warning(f"Node {node.id}: AI returned invalid recovery cmd '{cmd}', auto-skipping")
        return RecoveryAction.SKIP

def remove_node(project, node_id):
    node = project.nodes.get(node_id)
    if not node: return
    if node.parent_id:
        parent = project.nodes.get(node.parent_id)
        if parent and node_id in parent.children: parent.children.remove(node_id)
    elif node_id in project.root_children:
        project.root_children.remove(node_id)
    for other in project.nodes.values():
        if node_id in other.depends_on: other.depends_on.remove(node_id)
    del project.nodes[node_id]
    logger.info(f"Node {node_id} removed from project tree")

def find_resume_point(project):
    from littleant.core.verifier import run_verify
    for nid in project.get_execution_order():
        node = project.nodes.get(nid)
        if not node or not node.verify: continue
        result = run_verify(node.verify)
        if result["passed"]:
            node.status = NodeStatus.COMPLETED; node.verify_output = result
        else:
            node.status = NodeStatus.READY; return nid
    return None
