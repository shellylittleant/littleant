"""
LittleAnt V12.1 - Recursive Decomposition Engine
Depth-first, no depth limit, safety valves as backstop.
"""
from __future__ import annotations
import json, logging
from littleant.models.project import Project, Node, NodeStatus, ExecuteSpec, VerifySpec
from littleant.core.protocol import validate_ai_command, build_decompose, build_format_error
from littleant.ai.adapter import AIAdapter
from littleant.config import MAX_PATH_DEPTH, MAX_PROJECT_AI_CALLS, MAX_LEAF_NODES

logger = logging.getLogger(__name__)

class DecompositionError(Exception): pass

class Decomposer:
    def __init__(self, ai: AIAdapter, project: Project):
        self.ai = ai
        self.project = project

    def decompose_all(self):
        for root_id in self.project.root_children:
            node = self.project.nodes.get(root_id)
            if node and node.status == NodeStatus.PENDING:
                self._decompose(node, depth=0)

    def _decompose(self, node: Node, depth: int):
        if depth > MAX_PATH_DEPTH:
            raise DecompositionError(f"Node {node.id} depth exceeds {MAX_PATH_DEPTH}, pausing")
        if self.project.ai_call_count >= MAX_PROJECT_AI_CALLS:
            raise DecompositionError(f"Project AI call count exceeds {MAX_PROJECT_AI_CALLS}, pausing")
        leaf_count = sum(1 for n in self.project.nodes.values() if n.is_leaf)
        if leaf_count >= MAX_LEAF_NODES:
            raise DecompositionError(f"Leaf node count exceeds {MAX_LEAF_NODES}, pausing")
        if node.is_leaf:
            node.status = NodeStatus.READY
            return

        node.status = NodeStatus.DECOMPOSING
        feedback = build_decompose(node_id=node.id, node_name=node.name, context=self._build_context(node, depth))

        if depth >= 3:
            feedback["force_executable"] = True
            feedback["instruction"] = (
                f"Current depth is {depth}. You MUST return cmd:executable. "
                f"Do NOT return subtasks. Give a single shell command for this task.")

        response = self._ask_with_retry(feedback, max_retries=3)
        self.project.ai_call_count += 1
        cmd = response.get("cmd")

        if depth >= 3 and cmd == "subtasks":
            logger.warning(f"Node {node.id}: depth {depth}, AI still returned subtasks, forcing executable")
            force = {"feedback": "format_error",
                     "error": f"Depth {depth}: subtasks forbidden. Return cmd:executable with a single shell command for '{node.name}'."}
            try:
                response = self.ai.ask([{"role": "user", "content": json.dumps(force, ensure_ascii=False)}])
                self.project.ai_call_count += 1
                cmd = response.get("cmd")
            except Exception:
                pass
            if cmd != "executable":
                logger.error(f"Node {node.id}: cannot get executable, marking FAILED")
                node.status = NodeStatus.FAILED
                return

        if cmd == "executable":
            self._apply_executable(node, response)
            node.status = NodeStatus.READY
            logger.info(f"Node {node.id} got executable command")
        elif cmd == "subtasks":
            children = response.get("children", [])
            for cd in children:
                cn = Node(id=cd["id"], name=cd["name"], parent_id=node.id, depends_on=cd.get("depends_on", []))
                cn.node_type = cd.get("type", getattr(node, "node_type", "modify"))
                self.project.add_node(cn)
            for cd in children:
                child = self.project.nodes[cd["id"]]
                self._decompose(child, depth + 1)
        else:
            raise DecompositionError(f"Node {node.id}: AI returned invalid cmd={cmd}")

    def _ask_with_retry(self, feedback, max_retries=3):
        messages = [{"role": "user", "content": json.dumps(feedback, ensure_ascii=False)}]
        for attempt in range(max_retries):
            try:
                response = self.ai.ask(messages)
            except ValueError as e:
                logger.warning(f"AI returned invalid format (attempt {attempt+1}): {e}")
                messages.append({"role": "assistant", "content": str(e)})
                messages.append({"role": "user", "content": json.dumps(build_format_error({}, str(e)))})
                continue
            valid, error = validate_ai_command(response)
            if valid: return response
            logger.warning(f"AI command validation failed (attempt {attempt+1}): {error}")
            messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
            messages.append({"role": "user", "content": json.dumps(build_format_error(response, error))})
        raise DecompositionError(f"AI returned invalid format {max_retries} times, pausing")

    def _apply_executable(self, node, response):
        node.execute = ExecuteSpec.from_dict(response["execute"])
        node.verify = VerifySpec.from_dict(response["verify"])
        node.on_fail = response.get("on_fail", "report")

    def _build_context(self, node, depth):
        ctx = {"project_name": self.project.name, "project_goal": self.project.goal, "current_depth": depth}
        if depth == 0:
            ctx["hint"] = "Project phases. Split into 2-5 sub-steps."
        elif depth == 1:
            ctx["hint"] = "Return executable directly. Only split if truly multi-step."
        else:
            ctx["hint"] = f"Layer {depth}: must return executable."
        existing = {}
        for nid, n in self.project.nodes.items():
            if n.is_leaf and n.execute:
                existing[nid] = f"{n.name}: {n.execute.command or n.execute.type}"
            elif nid != node.id:
                existing[nid] = n.name
        if existing: ctx["existing_nodes"] = existing
        if node.parent_id:
            parent = self.project.nodes.get(node.parent_id)
            if parent:
                ctx["parent"] = {"id": parent.id, "name": parent.name}
                sibs = [{"id": s.id, "name": s.name, "status": s.status.value}
                        for sid in parent.children for s in [self.project.nodes.get(sid)]
                        if s and s.id != node.id]
                if sibs: ctx["siblings"] = sibs
        return ctx
