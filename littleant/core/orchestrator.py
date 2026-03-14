"""LittleAnt V12.1 - Orchestrator: type-driven decompose -> execute -> verify -> recover"""
from __future__ import annotations
import uuid, logging, time, os
from littleant.models.project import Project, Node, NodeStatus, ProjectStatus
from littleant.core.decomposer import Decomposer, DecompositionError
from littleant.core.executor import run_execute
from littleant.core.verifier import run_verify
from littleant.core.recovery import handle_failure, find_resume_point, RecoveryAction, remove_node, replan_branch
from littleant.core.protocol import build_project_status
from littleant.storage.json_store import save_project, load_project
from littleant.storage.db_store import init_db, log_execution, save_template, save_tool
from littleant.ai.adapter import AIAdapter

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, ai: AIAdapter):
        self.ai = ai

    def create_project(self, name, goal, initial_children=None):
        p = Project(id=f"proj_{uuid.uuid4().hex[:8]}", name=name, goal=goal)
        if initial_children:
            for ch in initial_children:
                n = Node(id=ch["id"], name=ch["name"], depends_on=ch.get("depends_on", []))
                n.node_type = ch.get("type", "modify")  # query/create/modify
                p.add_node(n)
        save_project(p)
        logger.info(f"Project created: {p.id} - {name}")
        return p

    def decompose(self, p):
        d = Decomposer(self.ai, p)
        d.decompose_all()
        save_project(p)
        leaves = sum(1 for n in p.nodes.values() if n.is_leaf)
        logger.info(f"Project {p.id} decomposed: {len(p.nodes)} nodes ({leaves} executable)")

    def execute(self, p):
        p.status = ProjectStatus.EXECUTING
        for nid in p.get_execution_order():
            node = p.nodes.get(nid)
            if not node or node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED): continue
            if not self._deps_met(p, node): continue
            result = self._execute_node(p, node)
            save_project(p)
            if result == "abort":
                p.status = ProjectStatus.ABORTED; save_project(p); return "abort"
            elif result == "waiting_user":
                p.status = ProjectStatus.WAITING_USER; save_project(p); return "waiting_user"
            elif result == "replan":
                # After replan, need to re-decompose and re-execute
                save_project(p); return "replan"
        p.status = ProjectStatus.COMPLETED; save_project(p)
        logger.info(f"Project {p.id} completed")
        self._save_to_library(p)
        return "completed"

    def execute_with_replan(self, p):
        """Execute with automatic replan loop."""
        max_replans = 5
        for _ in range(max_replans):
            result = self.execute(p)
            if result == "replan":
                # Re-decompose pending nodes
                try:
                    self.decompose(p)
                except DecompositionError as e:
                    logger.error(f"Replan decompose failed: {e}")
                    break
                continue
            return result
        logger.warning("Max replan cycles reached")
        p.status = ProjectStatus.FAILED; save_project(p)
        return "failed"

    def _execute_node(self, p, node):
        node.status = NodeStatus.EXECUTING
        logger.info(f"Executing node {node.id}: {node.name}")
        exec_out = run_execute(node.execute); node.execute_output = exec_out
        node.status = NodeStatus.VERIFYING
        verify_out = run_verify(node.verify); node.verify_output = verify_out
        log_execution(project_id=p.id, node_id=node.id, attempt=node.retry_count+1,
                      action="execute", exec_output=exec_out, verify_output=verify_out)
        if verify_out["passed"]:
            node.status = NodeStatus.COMPLETED; p.consecutive_failures = 0
            logger.info(f"Node {node.id} succeeded"); return "success"
        else:
            node.status = NodeStatus.FAILED
            logger.warning(f"Node {node.id} verify failed: {verify_out.get('detail')}")
            decision, extra = handle_failure(self.ai, p, node, exec_out, verify_out)
            log_execution(project_id=p.id, node_id=node.id, attempt=node.retry_count, action="recovery", ai_decision=decision)

            if decision == RecoveryAction.RETRY:
                return self._execute_node(p, node)
            elif decision == RecoveryAction.MODIFY:
                return self._execute_node(p, node)
            elif decision == RecoveryAction.REPLAN:
                target = extra.get("target_parent", node.parent_id)
                reason = extra.get("reason", "approach failed")
                if target and replan_branch(p, target, self.ai, reason):
                    logger.info(f"Replanning branch {target}")
                    return "replan"
                else:
                    remove_node(p, node.id); return "skip"
            elif decision == RecoveryAction.SKIP:
                remove_node(p, node.id); return "skip"
            elif decision == RecoveryAction.ABORT:
                return "abort"
            else:
                return "waiting_user"

    def _deps_met(self, p, node):
        for did in node.depends_on:
            dn = p.nodes.get(did)
            if not dn: continue
            if dn.status not in (NodeStatus.COMPLETED, NodeStatus.SKIPPED): return False
        return True

    def resume_from_crash(self, p):
        resume = find_resume_point(p)
        if not resume:
            p.status = ProjectStatus.COMPLETED; save_project(p); return
        save_project(p); self.execute(p)

    def _save_to_library(self, p):
        try:
            kw = [k.strip() for k in p.name.replace("/",",").replace("-",",").split(",") if k.strip()]
            tree = p.get_tree_summary()
            nodes_data = {nid: n.to_dict() for nid, n in p.nodes.items() if n.is_leaf}
            save_template(f"tpl_{p.id}", p.name, kw, tree, nodes_data)
            for nid, node in p.nodes.items():
                if not node.is_leaf or not node.execute: continue
                if node.execute.type == "write_file" and node.execute.path:
                    path = node.execute.path
                    if any(path.endswith(e) for e in (".py",".sh",".js",".rb",".pl")):
                        name = os.path.basename(path)
                        usage = f"python3 {path}" if path.endswith(".py") else f"bash {path}"
                        save_tool(f"tool_{p.id}_{nid}", name, f"Created by '{p.name}'",
                                  path, usage, kw + [name.split(".")[0]], p.id, "script")
                if node.execute.type == "run_shell" and node.execute.command:
                    cmd = node.execute.command
                    if "git clone" in cmd or "pip install" in cmd:
                        save_tool(f"tool_{p.id}_{nid}", node.name,
                                  f"Installed by '{p.name}': {cmd[:100]}",
                                  cmd, cmd, kw + [node.name], p.id, "installed")
        except Exception as e:
            logger.warning(f"Failed to save to library: {e}")
