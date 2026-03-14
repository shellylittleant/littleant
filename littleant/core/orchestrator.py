"""
LittleAnt V13 - Orchestrator (Cycle Execution Model)

Upper layer (task chain): query → judge → act → query → ... → goal met
Lower layer (exec chain): command → verify → retry/modify → next command

Three-level recovery:
  L1: command retry/modify (lower layer handles)
  L2: diagnostic query + alternative approach (upper layer)
  L3: full redesign with expanded info (upper layer)
"""
from __future__ import annotations
import uuid, json, logging, time, os, subprocess
from littleant.models.project import (
    Project, Node, NodeStatus, ProjectStatus, ExecuteSpec, VerifySpec
)
from littleant.core.executor import run_execute
from littleant.core.verifier import run_verify
from littleant.storage.json_store import save_project, load_project
from littleant.storage.db_store import log_execution, save_template, save_tool
from littleant.ai.adapter import (
    AIAdapter, PROMPT_CLASSIFY, PROMPT_DESIGN, PROMPT_THINK,
    PROMPT_WRITE_QUERY, PROMPT_JUDGE, PROMPT_WRITE_ACTION, PROMPT_REVIEW,
    PROMPT_L1_RECOVERY, PROMPT_L2_DIAGNOSE, PROMPT_L2_FIX, PROMPT_L3_REDESIGN,
)
from littleant.config import (
    MAX_NODE_RETRIES, MAX_NODE_MODIFICATIONS,
    MAX_L2_ATTEMPTS, MAX_L3_ATTEMPTS, MAX_CONSECUTIVE_FAILURES,
)

logger = logging.getLogger(__name__)
MAX_CYCLES = 10
MAX_REVIEW_ROUNDS = 3


class Orchestrator:
    def __init__(self, ai: AIAdapter):
        self.ai = ai

    # ==========================================================
    # Phase 1: Classify task types
    # ==========================================================
    def classify_task(self, user_request):
        prompt = PROMPT_CLASSIFY.format(task=user_request)
        try:
            r = self.ai.ask([{"role": "user", "content": prompt}])
            return r.get("types", ["modify"]), r.get("summary", "")
        except:
            return ["modify"], ""

    # ==========================================================
    # Phase 2: Design steps → task_brief
    # ==========================================================
    def design_steps(self, user_request, command_types):
        prompt = PROMPT_DESIGN.format(
            user_request=user_request,
            command_types=", ".join(command_types),
        )
        r = self.ai.ask([{"role": "user", "content": prompt}])
        return {
            "user_request": user_request,
            "command_types": command_types,
            "ai_model": getattr(self.ai, "model", "unknown"),
            "planned_steps": r.get("steps", []),
        }

    # ==========================================================
    # Phase 2b: Think steps
    # ==========================================================
    def do_think_steps(self, brief):
        for step in brief.get("planned_steps", []):
            if step["type"] == "think" and step.get("status") != "done":
                prompt = PROMPT_THINK.format(
                    task_brief=json.dumps(brief, ensure_ascii=False),
                    step_number=step["step"], step_name=step["name"])
                try:
                    r = self.ai.ask([{"role": "user", "content": prompt}])
                    step["conclusion"] = r.get("conclusion", "")
                except Exception as e:
                    step["conclusion"] = f"Analysis error: {e}"
                step["status"] = "done"
                logger.info(f"Think step {step['step']}: {step['conclusion'][:80]}")
        return brief

    # ==========================================================
    # Create project
    # ==========================================================
    def create_project(self, brief):
        p = Project(
            id=f"proj_{uuid.uuid4().hex[:8]}",
            name=brief["user_request"][:60],
            goal=brief["user_request"],
            task_brief=brief,
        )
        save_project(p)
        logger.info(f"Project created: {p.id}")
        return p

    # ==========================================================
    # MAIN CYCLE: query → judge → act → query → ... → goal met
    # ==========================================================
    def run_cycle(self, project, on_confirm=None, on_status=None):
        """
        Main execution loop.
        on_confirm(plan_text) -> bool
        on_status(message) -> None
        Returns: "completed" / "aborted" / "waiting_user" / "failed"
        """
        brief = project.task_brief
        brief_json = json.dumps(brief, ensure_ascii=False, indent=2)
        all_results = {}      # accumulated query results
        cycle_count = 0

        while cycle_count < MAX_CYCLES:
            cycle_count += 1
            logger.info(f"=== Cycle {cycle_count} ===")

            # --- STEP A: Write & execute query commands ---
            if on_status:
                on_status(f"Cycle {cycle_count}: scanning system state...")

            queries = self._write_queries(brief_json, all_results)
            if queries:
                snapshot = self._run_queries(project, queries)
                all_results.update(snapshot)
            else:
                snapshot = all_results

            snapshot_text = self._format_results(all_results)
            save_project(project)

            # --- STEP B: Judge - goal met? ---
            judgment = self._judge(brief_json, snapshot_text, brief["user_request"])

            if judgment.get("goal_met"):
                project.status = ProjectStatus.COMPLETED
                save_project(project)
                self._save_to_library(project)
                logger.info("Goal met!")
                return "completed"

            gap = judgment.get("gap", "")
            next_action = judgment.get("next_action", "")
            action_type = judgment.get("action_type", "modify")
            logger.info(f"Gap: {gap} | Next: {next_action}")

            # --- STEP C: Supplement queries if needed ---
            # AI may need more info before acting
            supp_queries = self._write_queries(
                brief_json, all_results,
                next_step_info=f"Next action: {next_action}. Gap: {gap}"
            )
            if supp_queries:
                supp_snapshot = self._run_queries(project, supp_queries)
                all_results.update(supp_snapshot)
                snapshot_text = self._format_results(all_results)
                save_project(project)

            # --- STEP D: Write action commands ---
            commands = self._write_actions(brief_json, snapshot_text, next_action)
            if not commands:
                logger.warning("AI produced no commands, skipping cycle")
                continue

            # --- STEP E: Review ---
            commands = self._review(brief_json, commands)

            # --- STEP F: Confirm (create/modify only) ---
            if on_confirm and action_type in ("create", "modify"):
                plan_text = self._format_plan(commands)
                approved = on_confirm(plan_text)
                if not approved:
                    project.status = ProjectStatus.ABORTED
                    save_project(project)
                    return "aborted"

            # --- STEP G: Execute with 3-level recovery ---
            if on_status:
                on_status("Executing...")

            exec_result = self._execute_with_recovery(
                project, commands, brief_json, snapshot_text
            )
            save_project(project)

            if exec_result == "waiting_user":
                return "waiting_user"
            elif exec_result == "abort":
                project.status = ProjectStatus.ABORTED
                save_project(project)
                return "failed"
            # else: "ok" or "recovered" → next cycle

        logger.warning(f"Max cycles ({MAX_CYCLES}) reached")
        project.status = ProjectStatus.FAILED
        save_project(project)
        return "failed"

    # ==========================================================
    # Write query commands (dynamic per cycle)
    # ==========================================================
    def _write_queries(self, brief_json, prev_results, next_step_info=""):
        prompt = PROMPT_WRITE_QUERY.format(
            task_brief=brief_json,
            next_step_info=next_step_info or "Initial system scan",
            previous_results=self._format_results(prev_results) if prev_results else "None yet",
        )
        try:
            r = self.ai.ask([{"role": "user", "content": prompt}])
            return r.get("commands", [])
        except Exception as e:
            logger.error(f"Write queries failed: {e}")
            return []

    # ==========================================================
    # Run query commands (read-only, no recovery needed)
    # ==========================================================
    def _run_queries(self, project, queries):
        results = {}
        for q in queries:
            qid = q.get("id", "q")
            cmd = q.get("command", "")
            name = q.get("name", cmd[:40])
            if not cmd:
                continue
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True,
                                   text=True, timeout=30)
                output = r.stdout[:3000]
                results[qid] = {"name": name, "command": cmd, "stdout": output,
                                "stderr": r.stderr[:500] if r.returncode != 0 else ""}
                logger.info(f"Query {qid}: {name} → {len(output)} chars")

                # Also store as a completed node for history
                node = Node(id=f"q_{project.id}_{qid}", name=name, node_type="query")
                node.execute = ExecuteSpec(type="run_shell", command=cmd)
                node.execute_output = {"stdout": output, "return_code": r.returncode}
                node.status = NodeStatus.COMPLETED
                project.nodes[node.id] = node

            except Exception as e:
                results[qid] = {"name": name, "command": cmd, "stdout": "",
                                "stderr": str(e)}
                logger.warning(f"Query {qid} failed: {e}")
        return results

    # ==========================================================
    # Judge: compare snapshot with goal
    # ==========================================================
    def _judge(self, brief_json, snapshot_text, goal):
        prompt = PROMPT_JUDGE.format(
            task_brief=brief_json, snapshot=snapshot_text, goal=goal)
        try:
            return self.ai.ask([{"role": "user", "content": prompt}])
        except Exception as e:
            logger.error(f"Judge failed: {e}")
            return {"goal_met": False, "gap": "judgment failed", "next_action": "retry"}

    # ==========================================================
    # Write action commands
    # ==========================================================
    def _write_actions(self, brief_json, snapshot_text, action_desc):
        prompt = PROMPT_WRITE_ACTION.format(
            task_brief=brief_json, snapshot=snapshot_text,
            action_description=action_desc)
        try:
            r = self.ai.ask([{"role": "user", "content": prompt}])
            return r.get("commands", [])
        except Exception as e:
            logger.error(f"Write actions failed: {e}")
            return []

    # ==========================================================
    # Review commands
    # ==========================================================
    def _review(self, brief_json, commands):
        for round_n in range(MAX_REVIEW_ROUNDS):
            prompt = PROMPT_REVIEW.format(
                task_brief=brief_json,
                commands=json.dumps(commands, ensure_ascii=False, indent=2))
            try:
                r = self.ai.ask([{"role": "user", "content": prompt}])
                if r.get("approved"):
                    logger.info(f"Review passed (round {round_n+1})")
                    return commands
                fixed = r.get("fixed_commands")
                if fixed and isinstance(fixed, list):
                    commands = fixed
                else:
                    return commands
            except:
                return commands
        return commands

    # ==========================================================
    # Execute with 3-level recovery
    # ==========================================================
    def _execute_with_recovery(self, project, commands, brief_json, snapshot_text):
        """Execute commands. Returns: 'ok' / 'recovered' / 'waiting_user' / 'abort'"""
        nodes = self._commands_to_nodes(project, commands)

        for node in nodes:
            result = self._execute_node_l1(project, node)

            if result == "success":
                continue
            elif result == "l1_exhausted":
                # L2: diagnostic query + alternative
                l2_result = self._recover_l2(project, node, brief_json)
                if l2_result == "recovered":
                    continue
                elif l2_result == "l2_exhausted":
                    # L3: full redesign
                    l3_result = self._recover_l3(project, node, brief_json, snapshot_text)
                    if l3_result == "recovered":
                        continue
                    else:
                        # All levels exhausted
                        if project.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            return "waiting_user"
                        # Skip this node and continue
                        logger.warning(f"Node {node.id}: all recovery exhausted, skipping")
                        remove_node_safe(project, node.id)
                        continue
            elif result == "abort":
                return "abort"

        return "ok"

    # ==========================================================
    # L1: Command-level retry/modify
    # ==========================================================
    def _execute_node_l1(self, project, node):
        for attempt in range(MAX_NODE_RETRIES + MAX_NODE_MODIFICATIONS + 1):
            node.status = NodeStatus.EXECUTING
            logger.info(f"L1 exec {node.id}: {node.name} (attempt {attempt+1})")

            exec_out = run_execute(node.execute)
            node.execute_output = exec_out
            node.status = NodeStatus.VERIFYING
            verify_out = run_verify(node.verify)
            node.verify_output = verify_out

            log_execution(project_id=project.id, node_id=node.id,
                          attempt=attempt+1, action="execute",
                          exec_output=exec_out, verify_output=verify_out)

            if verify_out["passed"]:
                node.status = NodeStatus.COMPLETED
                project.consecutive_failures = 0
                return "success"

            node.status = NodeStatus.FAILED
            project.consecutive_failures += 1
            error = verify_out.get("detail", "")
            output = exec_out.get("stdout", "")[:300] + exec_out.get("stderr", "")[:300]

            # Ask AI for L1 decision
            prompt = PROMPT_L1_RECOVERY.format(
                node_name=node.name, error=error, output=output)
            try:
                r = self.ai.ask([{"role": "user", "content": prompt}])
                cmd = r.get("cmd", "give_up")
            except:
                cmd = "give_up"

            if cmd == "retry" and node.retry_count < MAX_NODE_RETRIES:
                node.retry_count += 1
                continue
            elif cmd == "modify" and node.modify_count < MAX_NODE_MODIFICATIONS:
                node.modify_count += 1
                if "execute" in r: node.execute = ExecuteSpec.from_dict(r["execute"])
                if "verify" in r: node.verify = VerifySpec.from_dict(r["verify"])
                continue
            else:
                return "l1_exhausted"

        return "l1_exhausted"

    # ==========================================================
    # L2: Diagnostic query + alternative approach
    # ==========================================================
    def _recover_l2(self, project, node, brief_json):
        failed_info = f"Node: {node.name}\nCommand: {node.execute.command if node.execute else '?'}\nError: {node.verify_output.get('detail','') if node.verify_output else '?'}\nOutput: {node.execute_output.get('stdout','')[:500] if node.execute_output else ''}"

        for attempt in range(MAX_L2_ATTEMPTS):
            logger.info(f"L2 recovery attempt {attempt+1} for {node.id}")

            # Step 1: diagnostic queries
            prompt = PROMPT_L2_DIAGNOSE.format(
                task_brief=brief_json, failed_info=failed_info)
            try:
                r = self.ai.ask([{"role": "user", "content": prompt}])
                diag_queries = r.get("diagnostic_queries", [])
            except:
                diag_queries = []

            # Run diagnostics
            diag_results = {}
            for dq in diag_queries:
                cmd = dq.get("command", "")
                if not cmd: continue
                try:
                    dr = subprocess.run(cmd, shell=True, capture_output=True,
                                        text=True, timeout=30)
                    diag_results[dq.get("id", "d")] = {
                        "name": dq.get("name", ""), "stdout": dr.stdout[:2000]}
                except Exception as e:
                    diag_results[dq.get("id", "d")] = {"name": dq.get("name", ""), "stdout": str(e)}

            # Step 2: ask for alternative based on diagnostics
            prompt2 = PROMPT_L2_FIX.format(
                task_brief=brief_json, failed_info=failed_info,
                diagnostic_results=json.dumps(diag_results, ensure_ascii=False))
            try:
                r2 = self.ai.ask([{"role": "user", "content": prompt2}])
            except:
                continue

            if r2.get("no_alternative"):
                continue

            alt_cmds = r2.get("alternative_commands", [])
            if not alt_cmds:
                continue

            # Execute alternative
            alt_nodes = self._commands_to_nodes(project, alt_cmds)
            all_ok = True
            for an in alt_nodes:
                result = self._execute_node_l1(project, an)
                if result != "success":
                    all_ok = False
                    break

            if all_ok:
                logger.info(f"L2 recovered {node.id} on attempt {attempt+1}")
                return "recovered"

        return "l2_exhausted"

    # ==========================================================
    # L3: Full redesign with expanded system info
    # ==========================================================
    def _recover_l3(self, project, node, brief_json, snapshot_text):
        failed_info = f"Node: {node.name}\nError: {node.verify_output.get('detail','') if node.verify_output else '?'}"

        # Gather expanded system info
        expand_cmds = ["uname -a", "cat /etc/os-release", "dpkg --print-architecture",
                       "free -h", "df -h", "whoami"]
        expanded = {}
        for cmd in expand_cmds:
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True,
                                   text=True, timeout=10)
                expanded[cmd] = r.stdout[:500]
            except:
                pass

        # Collect all previous failures for context
        all_failures = failed_info
        for nid, n in project.nodes.items():
            if n.status == NodeStatus.FAILED and n.verify_output:
                all_failures += f"\n{n.name}: {n.verify_output.get('detail','')}"

        for attempt in range(MAX_L3_ATTEMPTS):
            logger.info(f"L3 redesign attempt {attempt+1} for {node.id}")

            prompt = PROMPT_L3_REDESIGN.format(
                task_brief=brief_json, all_failures=all_failures,
                expanded_info=json.dumps(expanded, ensure_ascii=False))
            try:
                r = self.ai.ask([{"role": "user", "content": prompt}])
            except:
                continue

            if r.get("impossible"):
                logger.warning(f"L3: AI says impossible: {r.get('reason')}")
                return "impossible"

            redesigned = r.get("redesigned_commands", [])
            if not redesigned:
                continue

            re_nodes = self._commands_to_nodes(project, redesigned)
            all_ok = True
            for rn in re_nodes:
                result = self._execute_node_l1(project, rn)
                if result != "success":
                    all_ok = False
                    break

            if all_ok:
                logger.info(f"L3 recovered on attempt {attempt+1}")
                return "recovered"

        return "l3_exhausted"

    # ==========================================================
    # Helpers
    # ==========================================================
    def _commands_to_nodes(self, project, commands):
        nodes = []
        for cmd in commands:
            nid = cmd.get("id", f"n_{uuid.uuid4().hex[:6]}")
            node = Node(id=nid, name=cmd.get("name", ""))
            if "execute" in cmd:
                node.execute = ExecuteSpec.from_dict(cmd["execute"])
            if "verify" in cmd:
                node.verify = VerifySpec.from_dict(cmd["verify"])
            node.status = NodeStatus.READY
            project.nodes[nid] = node
            if nid not in project.root_children:
                project.root_children.append(nid)
            nodes.append(node)
        return nodes

    def _format_results(self, results):
        if not results: return "No results yet."
        lines = []
        for rid, r in results.items():
            stdout = r.get("stdout", "")[:500]
            lines.append(f"[{r.get('name', rid)}] ({r.get('command','')})\n{stdout}")
        return "\n\n".join(lines)

    def _format_plan(self, commands):
        lines = []
        for cmd in commands:
            c = cmd.get("execute", {}).get("command", "")
            if len(c) > 60: c = c[:60] + "..."
            lines.append(f"• {cmd.get('name','')}\n  → {c}" if c else f"• {cmd.get('name','')}")
        return "\n".join(lines)

    def _save_to_library(self, project):
        try:
            kw = [k.strip() for k in project.name.split() if len(k.strip()) > 1][:5]
            tree = project.get_tree_summary()
            nd = {nid: n.to_dict() for nid, n in project.nodes.items() if n.is_leaf}
            save_template(f"tpl_{project.id}", project.name, kw, tree, nd)
            for nid, n in project.nodes.items():
                if not n.is_leaf or not n.execute: continue
                if n.execute.type == "write_file" and n.execute.path:
                    p = n.execute.path
                    if any(p.endswith(e) for e in (".py",".sh",".js")):
                        nm = os.path.basename(p)
                        save_tool(f"tool_{project.id}_{nid}", nm,
                                  f"From '{project.name}'", p,
                                  f"python3 {p}" if p.endswith(".py") else f"bash {p}",
                                  kw, project.id, "script")
        except Exception as e:
            logger.warning(f"Library save failed: {e}")


def remove_node_safe(project, node_id):
    """Remove node without crashing if already gone."""
    node = project.nodes.get(node_id)
    if not node: return
    for cid in list(node.children):
        remove_node_safe(project, cid)
    if node.parent_id:
        parent = project.nodes.get(node.parent_id)
        if parent and node_id in parent.children: parent.children.remove(node_id)
    elif node_id in project.root_children:
        project.root_children.remove(node_id)
    for other in project.nodes.values():
        if node_id in other.depends_on: other.depends_on.remove(node_id)
    del project.nodes[node_id]
