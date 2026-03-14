"""
LittleAnt V12.1 - Error Recovery
Priority: retry -> modify -> replan -> skip -> abort
AI handles failures autonomously. Only escalates at safety valve limits.
"""
from __future__ import annotations
import json, logging, platform
from littleant.models.project import Project, Node, NodeStatus, ExecuteSpec, VerifySpec
from littleant.core.protocol import build_node_failed
from littleant.ai.adapter import AIAdapter, RECOVERY_PROMPT
from littleant.config import (
    MAX_NODE_RETRIES, MAX_NODE_MODIFICATIONS, MAX_CONSECUTIVE_FAILURES, MAX_REPLAN_PER_PARENT
)

logger = logging.getLogger(__name__)

class RecoveryAction:
    RETRY = "retry"
    MODIFY = "modify"
    REPLAN = "replan"
    SKIP = "skip"
    REPORT_TO_USER = "report_to_user"
    ABORT = "abort"

def handle_failure(ai, project, node, exec_output, verify_output):
    project.consecutive_failures += 1

    if project.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        logger.warning(f"Project consecutive failures {project.consecutive_failures}, escalating to user")
        return RecoveryAction.REPORT_TO_USER, {}

    if node.retry_count >= MAX_NODE_RETRIES and node.modify_count >= MAX_NODE_MODIFICATIONS:
        logger.info(f"Node {node.id} retries and mods exhausted, trying replan")
        # Try replan before giving up
        parent_id = node.parent_id
        if parent_id:
            replan_count = getattr(project, '_replan_counts', {}).get(parent_id, 0)
            if replan_count < MAX_REPLAN_PER_PARENT:
                return RecoveryAction.REPLAN, {"target_parent": parent_id}
        return RecoveryAction.SKIP, {}

    env = {"os": f"{platform.system()} {platform.release()}", "arch": platform.machine()}
    packet = build_node_failed(project, node.id, exec_output, verify_output, node.retry_count + 1, env)
    messages = [{"role": "user", "content": json.dumps(packet, ensure_ascii=False)}]
    try:
        response = ai.ask(messages, system_prompt=RECOVERY_PROMPT)
        project.ai_call_count += 1
    except Exception as e:
        logger.error(f"AI recovery failed: {e}, trying replan")
        if node.parent_id:
            return RecoveryAction.REPLAN, {"target_parent": node.parent_id}
        return RecoveryAction.SKIP, {}

    cmd = response.get("cmd")

    if cmd == "retry":
        if node.retry_count >= MAX_NODE_RETRIES:
            return RecoveryAction.REPLAN, {"target_parent": node.parent_id} if node.parent_id else (RecoveryAction.SKIP, {})
        node.retry_count += 1
        logger.info(f"Node {node.id}: AI decided retry (#{node.retry_count})")
        return RecoveryAction.RETRY, {}

    elif cmd == "modify":
        if node.modify_count >= MAX_NODE_MODIFICATIONS:
            return RecoveryAction.REPLAN, {"target_parent": node.parent_id} if node.parent_id else (RecoveryAction.SKIP, {})
        node.modify_count += 1
        if "execute" in response: node.execute = ExecuteSpec.from_dict(response["execute"])
        if "verify" in response: node.verify = VerifySpec.from_dict(response["verify"])
        logger.info(f"Node {node.id}: AI modified command (#{node.modify_count})")
        return RecoveryAction.MODIFY, {}

    elif cmd == "replan":
        target = response.get("target_parent", node.parent_id)
        reason = response.get("reason", "approach failed")
        logger.info(f"Node {node.id}: AI decided replan to parent {target} ({reason})")
        return RecoveryAction.REPLAN, {"target_parent": target, "reason": reason}

    elif cmd == "skip":
        logger.info(f"Node {node.id}: AI decided skip")
        return RecoveryAction.SKIP, {}

    elif cmd == "abort":
        return RecoveryAction.ABORT, {}

    else:
        logger.warning(f"Node {node.id}: AI returned invalid cmd '{cmd}', defaulting to replan")
        if node.parent_id:
            return RecoveryAction.REPLAN, {"target_parent": node.parent_id}
        return RecoveryAction.SKIP, {}


def remove_node(project, node_id):
    node = project.nodes.get(node_id)
    if not node: return
    # Remove children recursively first
    for child_id in list(node.children):
        remove_node(project, child_id)
    if node.parent_id:
        parent = project.nodes.get(node.parent_id)
        if parent and node_id in parent.children: parent.children.remove(node_id)
    elif node_id in project.root_children:
        project.root_children.remove(node_id)
    for other in project.nodes.values():
        if node_id in other.depends_on: other.depends_on.remove(node_id)
    del project.nodes[node_id]
    logger.info(f"Node {node_id} removed from project tree")


def replan_branch(project, parent_id, ai, reason=""):
    """Delete all children of parent_id, reset parent to PENDING for re-decomposition."""
    parent = project.nodes.get(parent_id)
    if not parent:
        logger.warning(f"Replan: parent {parent_id} not found")
        return False

    # Track replan count
    if not hasattr(project, '_replan_counts'):
        project._replan_counts = {}
    project._replan_counts[parent_id] = project._replan_counts.get(parent_id, 0) + 1

    if project._replan_counts[parent_id] > MAX_REPLAN_PER_PARENT:
        logger.warning(f"Replan: parent {parent_id} exceeded max replans ({MAX_REPLAN_PER_PARENT})")
        return False

    # Remove all children
    for child_id in list(parent.children):
        remove_node(project, child_id)
    parent.children.clear()

    # Store failure context so AI knows what didn't work
    if not hasattr(parent, '_failed_approaches'):
        parent._failed_approaches = []
    parent._failed_approaches = getattr(parent, '_failed_approaches', [])
    parent._failed_approaches.append(reason or "previous approach failed")

    # Reset parent for re-decomposition
    parent.status = NodeStatus.PENDING
    parent.execute = None
    parent.verify = None
    logger.info(f"Replan: branch {parent_id} cleared for re-decomposition (attempt #{project._replan_counts[parent_id]})")
    return True


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
