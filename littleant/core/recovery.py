"""
LittleAnt V14 - Recovery Utilities
Node removal and crash recovery helpers.
L1/L2/L3 recovery logic lives in orchestrator.py.
"""
from __future__ import annotations
import logging
from littleant.models.project import Project, Node, NodeStatus

logger = logging.getLogger(__name__)


def remove_node(project, node_id):
    """Remove a node and its children from the project tree."""
    node = project.nodes.get(node_id)
    if not node: return
    for cid in list(node.children):
        remove_node(project, cid)
    if node.parent_id:
        parent = project.nodes.get(node.parent_id)
        if parent and node_id in parent.children: parent.children.remove(node_id)
    elif node_id in project.root_children:
        project.root_children.remove(node_id)
    for other in project.nodes.values():
        if node_id in other.depends_on: other.depends_on.remove(node_id)
    del project.nodes[node_id]
    logger.info(f"Node {node_id} removed")


def find_resume_point(project):
    """Crash recovery: find first node that fails verify."""
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
