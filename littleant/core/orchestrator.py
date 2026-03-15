"""
LittleAnt V14 - Orchestrator (Cycle Execution + Black Box Logging)

Every AI call, every command, every decision is recorded to experiment_log.
"""
from __future__ import annotations
import uuid, json, logging, time, os, subprocess
from littleant.models.project import (
    Project, Node, NodeStatus, ProjectStatus, ExecuteSpec, VerifySpec
)
from littleant.core.executor import run_execute
from littleant.core.verifier import run_verify
from littleant.storage.json_store import save_project, load_project
from littleant.storage.db_store import (
    log_execution, save_template, save_tool,
    search_history_for_context, search_tools, log_event,
)
from littleant.ai.adapter import (
    AIAdapter, PROMPT_CLASSIFY, PROMPT_DESIGN, PROMPT_THINK,
    PROMPT_WRITE_QUERY, PROMPT_JUDGE, PROMPT_WRITE_ACTION, PROMPT_REVIEW,
    PROMPT_L1_RECOVERY, PROMPT_L2_DIAGNOSE, PROMPT_L2_FIX, PROMPT_L3_REDESIGN,
    PROMPT_QUERY_FAST, PROMPT_QUERY_ENOUGH, PROMPT_HISTORY_CONTEXT,
    PROMPT_WRITE_FILE_CONTENT, PROMPT_MODIFY_FILE_CONTENT,
    PROMPT_LINEAR_PLAN, PROMPT_BATCH_FILES,
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

    def _ai_model(self):
        return getattr(self.ai, 'model', 'unknown')

    def _ask_logged(self, messages, system_prompt, project_id, task_name, task_types,
                    event_type, direction="program→ai", actor="backend_ai",
                    cycle_number=0, recovery_level="none", node_id=None):
        """Call AI and log prompt+response+timing to experiment_log."""
        prompt_text = system_prompt + "\n\n" + json.dumps(messages, ensure_ascii=False)[:15000] if system_prompt else json.dumps(messages, ensure_ascii=False)[:15000]
        t0 = time.time()
        try:
            result = self.ai.ask(messages, system_prompt=system_prompt)
            duration = int((time.time() - t0) * 1000)
            response_text = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
            log_event(project_id=project_id, task_name=task_name, task_types=task_types,
                      event_type=event_type, direction=direction, actor=actor,
                      ai_prompt=prompt_text, ai_response=response_text,
                      ai_model=self._ai_model(), duration_ms=duration,
                      cycle_number=cycle_number, recovery_level=recovery_level, node_id=node_id)
            return result
        except Exception as e:
            duration = int((time.time() - t0) * 1000)
            log_event(project_id=project_id, task_name=task_name, task_types=task_types,
                      event_type=event_type, direction=direction, actor=actor,
                      ai_prompt=prompt_text, ai_model=self._ai_model(),
                      duration_ms=duration, error_message=str(e),
                      cycle_number=cycle_number, recovery_level=recovery_level, node_id=node_id)
            raise

    # ==========================================================
    # Phase 1: Classify
    # ==========================================================
    def classify_task(self, user_request):
        prompt = PROMPT_CLASSIFY.format(task=user_request)
        try:
            r = self._ask_logged([{"role":"user","content":prompt}], None,
                project_id=None, task_name=user_request, task_types=None,
                event_type="ai_classify")
            return r.get("types", ["modify"]), r.get("summary", "")
        except:
            return ["modify"], ""

    # ==========================================================
    # Phase 2: Design steps
    # ==========================================================
    def design_steps(self, user_request, command_types):
        history_ctx = self.build_history_context(user_request)
        prompt = PROMPT_DESIGN.format(
            user_request=user_request, command_types=", ".join(command_types))
        if history_ctx: prompt += f"\n\n{history_ctx}"
        r = self._ask_logged([{"role":"user","content":prompt}], None,
            project_id=None, task_name=user_request, task_types=",".join(command_types),
            event_type="ai_design_steps")
        brief = {
            "user_request": user_request, "command_types": command_types,
            "ai_model": self._ai_model(), "planned_steps": r.get("steps", []),
            "history_context": history_ctx,
        }
        return brief

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
                    r = self._ask_logged([{"role":"user","content":prompt}], None,
                        project_id=None, task_name=brief["user_request"],
                        task_types=",".join(brief["command_types"]),
                        event_type="ai_think", node_id=f"think_{step['step']}")
                    step["conclusion"] = r.get("conclusion", "")
                except Exception as e:
                    step["conclusion"] = f"Error: {e}"
                step["status"] = "done"
                logger.info(f"Think step {step['step']}: {step['conclusion'][:80]}")
        return brief

    # ==========================================================
    # History context
    # ==========================================================
    def build_history_context(self, user_request):
        keywords = [k.strip() for k in user_request.split() if len(k.strip()) > 1][:5]
        if not keywords: return ""
        success_cases, failure_cases = search_history_for_context(keywords)
        if not success_cases and not failure_cases: return ""
        ss = "\n".join([f"- \"{c['name']}\" (rated {c['rating']}/5)" for c in success_cases[:3]]) if success_cases else "None"
        fs = "\n".join([f"- \"{c['name']}\" — feedback: \"{c['feedback']}\"" for c in failure_cases[:3]]) if failure_cases else "None"
        return PROMPT_HISTORY_CONTEXT.format(success_section=ss, failure_section=fs)

    # ==========================================================
    # Create project
    # ==========================================================
    def create_project(self, brief):
        p = Project(id=f"proj_{uuid.uuid4().hex[:8]}", name=brief["user_request"][:60],
                    goal=brief["user_request"], task_brief=brief)
        save_project(p)
        log_event(project_id=p.id, task_name=brief["user_request"],
                  task_types=",".join(brief.get("command_types",[])),
                  event_type="task_start", actor="program",
                  output_text=json.dumps(brief, ensure_ascii=False)[:5000])
        logger.info(f"Project created: {p.id}")
        return p

    # ==========================================================
    # QUERY FAST PATH
    # ==========================================================
    def run_query_fast(self, project, on_status=None):
        pid = project.id
        brief = project.task_brief
        tn = brief["user_request"]
        tt = ",".join(brief.get("command_types",[]))
        history_ctx = self.build_history_context(tn)

        if on_status: on_status("Querying system...")

        # Write queries
        prompt = PROMPT_QUERY_FAST.format(user_request=tn,
            history_context=history_ctx or "No history.")
        try:
            r = self._ask_logged([{"role":"user","content":prompt}], None,
                project_id=pid, task_name=tn, task_types=tt,
                event_type="ai_write_query")
            queries = r.get("commands", [])
        except Exception as e:
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="task_fail", error_message=str(e))
            project.status = ProjectStatus.FAILED; save_project(project)
            return "failed"

        if not queries:
            project.status = ProjectStatus.FAILED; save_project(project)
            return "failed"

        # Execute queries
        results = self._run_queries_logged(project, queries, cycle=1)
        results_text = self._format_results(results)

        # Check if enough
        prompt2 = PROMPT_QUERY_ENOUGH.format(user_request=tn, results=results_text)
        try:
            r2 = self._ask_logged([{"role":"user","content":prompt2}], None,
                project_id=pid, task_name=tn, task_types=tt,
                event_type="ai_judge_enough")
        except:
            project.status = ProjectStatus.COMPLETED; save_project(project)
            self._save_to_library(project); return "completed"

        if r2.get("enough", True):
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="task_complete", actor="program",
                      api_calls_total=project.ai_call_count)
            project.status = ProjectStatus.COMPLETED; save_project(project)
            self._save_to_library(project); return "completed"

        # Supplement round
        extra_cmds = r2.get("extra_commands", [])
        if extra_cmds:
            if on_status: on_status("Additional checks...")
            self._run_queries_logged(project, extra_cmds, cycle=2)

        log_event(project_id=pid, task_name=tn, task_types=tt,
                  event_type="task_complete", actor="program",
                  api_calls_total=project.ai_call_count)
        project.status = ProjectStatus.COMPLETED; save_project(project)
        self._save_to_library(project); return "completed"

    # ==========================================================
    # V14 LINEAR MODE: query snapshot → one-shot plan → batch files → execute
    # For create/modify tasks with clear goals
    # ==========================================================
    def run_linear(self, project, on_confirm=None, on_status=None):
        pid = project.id
        brief = project.task_brief
        tn = brief["user_request"]
        tt = ",".join(brief.get("command_types",[]))
        brief_json = json.dumps(brief, ensure_ascii=False, indent=2)

        # --- PHASE 1: V14 query scan ---
        if on_status: on_status("Scanning system...")
        log_event(project_id=pid, task_name=tn, task_types=tt,
                  event_type="linear_scan_start", actor="program")

        scan_queries = self._write_queries_logged(pid, tn, tt, brief_json, {}, 0)
        if scan_queries:
            scan_results = self._run_queries_logged(project, scan_queries, 0)
        else:
            scan_results = {}
        snapshot_text = self._format_results(scan_results)
        save_project(project)

        log_event(project_id=pid, task_name=tn, task_types=tt,
                  event_type="linear_scan_done", actor="program",
                  output_text=f"Scanned {len(scan_results)} items")

        # --- PHASE 2: One-shot plan ---
        if on_status: on_status("Planning...")
        prompt = PROMPT_LINEAR_PLAN.format(user_request=tn, snapshot=snapshot_text)
        try:
            plan = self._ask_logged([{"role":"user","content":prompt}], None,
                project_id=pid, task_name=tn, task_types=tt,
                event_type="ai_linear_plan")
        except Exception as e:
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="task_fail", error_message=str(e))
            project.status = ProjectStatus.FAILED; save_project(project)
            return "failed"

        files_create = plan.get("files_to_create", [])
        files_modify = plan.get("files_to_modify", [])
        cmds_before = plan.get("commands_before", [])
        cmds_after = plan.get("commands_after", [])

        # Show plan to user
        plan_lines = []
        for c in cmds_before: plan_lines.append(f"  ▸ {c.get('name','')}")
        for f in files_create: plan_lines.append(f"  📄 Create {f['path']}")
        for f in files_modify: plan_lines.append(f"  ✏️ Modify {f['path']}")
        for c in cmds_after: plan_lines.append(f"  ▸ {c.get('name','')}")
        plan_text = "\n".join(plan_lines)

        log_event(project_id=pid, task_name=tn, task_types=tt,
                  event_type="linear_plan_show", output_text=plan_text)

        # Confirm (if needed)
        if on_confirm:
            if not on_confirm(plan_text):
                project.status = ProjectStatus.ABORTED; save_project(project)
                return "aborted"

        # --- PHASE 3: Execute pre-commands ---
        if cmds_before:
            if on_status: on_status("Preparing...")
            for cmd in cmds_before:
                self._run_shell_logged(project, cmd, 0)

        # --- PHASE 4: Batch generate all files (1 API call) ---
        all_files = files_create + files_modify
        if all_files:
            if on_status: on_status("Generating files...")

            # Read current content for files_to_modify
            for f in files_modify:
                try:
                    r = subprocess.run(f"cat {f['path']} 2>/dev/null", shell=True,
                                      capture_output=True, text=True, timeout=10)
                    if r.returncode == 0: f["current_content"] = r.stdout[:5000]
                except: pass

            file_list = "\n".join([
                f"- {f['path']}: {f.get('description','')}" +
                (f"\n  Current content:\n{f['current_content'][:500]}" if f.get('current_content') else "")
                for f in all_files
            ])

            prompt = PROMPT_BATCH_FILES.format(
                user_request=tn, snapshot=snapshot_text[:3000], file_list=file_list)

            try:
                t0 = time.time()
                raw = self.ai.ask_text([{"role":"user","content":prompt}])
                dur = int((time.time() - t0) * 1000)

                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="ai_batch_files", actor="backend_ai",
                          ai_prompt=f"Batch generate {len(all_files)} files",
                          ai_response=raw[:500] + "..." if len(raw) > 500 else raw,
                          ai_model=self._ai_model(), duration_ms=dur)

                # Parse ===FILE: path=== ... ===END_FILE=== blocks
                written = self._parse_and_write_files(project, raw, pid, tn, tt)

                if written == 0:
                    logger.warning("Batch file parse returned 0 files, trying single-file fallback")
                    # Fallback: generate files one by one
                    for f in all_files:
                        self._handle_write_file_single(project, f, brief_json, 0)

            except Exception as e:
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="ai_batch_files", error_message=str(e))
                # Fallback: generate files one by one
                for f in all_files:
                    self._handle_write_file_single(project, f, brief_json, 0)

        # --- PHASE 5: Execute post-commands with recovery ---
        if cmds_after:
            if on_status: on_status("Configuring...")
            for cmd in cmds_after:
                result = self._run_shell_with_recovery(project, cmd, brief_json, snapshot_text, 0)
                if result == "waiting_user": return "waiting_user"
                elif result == "abort":
                    project.status = ProjectStatus.FAILED; save_project(project)
                    return "failed"

        # --- PHASE 6: Quick verify ---
        if on_status: on_status("Verifying...")
        # Run a quick scan to confirm
        verify_queries = self._write_queries_logged(pid, tn, tt, brief_json, scan_results, 1,
            next_step_info="Verify that the task is complete. Check that services are running, files exist, and the site is accessible.")
        if verify_queries:
            self._run_queries_logged(project, verify_queries, 1)

        save_project(project)

        log_event(project_id=pid, task_name=tn, task_types=tt,
                  event_type="task_complete", actor="program",
                  api_calls_total=project.ai_call_count)
        project.status = ProjectStatus.COMPLETED; save_project(project)
        self._save_to_library(project)
        return "completed"

    # --- Linear mode helpers ---

    def _parse_and_write_files(self, project, raw, pid, tn, tt):
        """Parse ===FILE: path=== ... ===END_FILE=== blocks and write to disk."""
        import re
        pattern = r'===FILE:\s*(.+?)===\n(.*?)===END_FILE==='
        matches = re.findall(pattern, raw, re.DOTALL)
        written = 0
        for path, content in matches:
            path = path.strip()
            content = content.strip()
            if not path or not content: continue
            try:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                written += 1
                size = os.path.getsize(path)

                node = Node(id=f"file_{uuid.uuid4().hex[:6]}", name=f"Write {os.path.basename(path)}")
                node.execute = ExecuteSpec(type="write_file", path=path)
                node.execute_output = {"stdout": f"Written {size} bytes to {path}", "return_code": 0}
                node.verify_output = {"passed": True, "detail": f"{size} bytes"}
                node.status = NodeStatus.COMPLETED
                project.nodes[node.id] = node

                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="file_write", direction="program→server",
                          command=f"write_file {path}", stdout=f"{size} bytes",
                          verify_passed=1, node_id=node.id)
                logger.info(f"Written: {path} ({size} bytes)")
            except Exception as e:
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="file_write", error_message=str(e),
                          command=f"write_file {path}", verify_passed=0)
                logger.error(f"Failed to write {path}: {e}")
        return written

    def _handle_write_file_single(self, project, file_info, brief_json, cycle):
        """Fallback: generate one file at a time."""
        path = file_info.get("path","")
        desc = file_info.get("description","")
        current = file_info.get("current_content")
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))

        if current:
            prompt = PROMPT_MODIFY_FILE_CONTENT.format(
                path=path, description=desc, current_content=current[:8000],
                task_brief=brief_json[:3000])
        else:
            prompt = PROMPT_WRITE_FILE_CONTENT.format(
                path=path, description=desc, task_brief=brief_json[:3000])
        try:
            content = self.ai.ask_text([{"role":"user","content":prompt}])
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[-1].strip() == "```": lines = lines[1:-1]
                else: lines = lines[1:]
                content = "\n".join(lines)
            parent = os.path.dirname(path)
            if parent: os.makedirs(parent, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="file_write", command=f"write_file {path}",
                      stdout=f"{os.path.getsize(path)} bytes", verify_passed=1)
        except Exception as e:
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="file_write", error_message=str(e),
                      command=f"write_file {path}", verify_passed=0)

    def _run_shell_logged(self, project, cmd_info, cycle):
        """Execute a single shell command with logging."""
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))
        cmd = cmd_info.get("command","")
        name = cmd_info.get("name","")
        if not cmd: return
        t0 = time.time()
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            dur = int((time.time()-t0)*1000)
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="node_execute", direction="program→server",
                      command=cmd, stdout=r.stdout[:3000], stderr=r.stderr[:1000],
                      return_code=r.returncode, duration_ms=dur, cycle_number=cycle,
                      node_id=cmd_info.get("id",""))

            node = Node(id=cmd_info.get("id", f"sh_{uuid.uuid4().hex[:6]}"), name=name)
            node.execute = ExecuteSpec(type="run_shell", command=cmd)
            node.execute_output = {"stdout": r.stdout[:3000], "return_code": r.returncode}

            # Verify if specified
            verify_cmd = cmd_info.get("verify","")
            if verify_cmd:
                vr = subprocess.run(verify_cmd, shell=True, capture_output=True, text=True, timeout=30)
                passed = vr.returncode == 0
                node.verify_output = {"passed": passed, "detail": f"rc={vr.returncode}"}
            else:
                passed = r.returncode == 0
                node.verify_output = {"passed": passed, "detail": f"rc={r.returncode}"}

            node.status = NodeStatus.COMPLETED if passed else NodeStatus.FAILED
            project.nodes[node.id] = node
            log_execution(project_id=pid, node_id=node.id, attempt=1,
                          action="execute", exec_output=node.execute_output, verify_output=node.verify_output)
        except Exception as e:
            dur = int((time.time()-t0)*1000)
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="node_execute", error_message=str(e),
                      command=cmd, duration_ms=dur, cycle_number=cycle)

    def _run_shell_with_recovery(self, project, cmd_info, brief_json, snapshot_text, cycle):
        """Execute shell command with 3-level recovery."""
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))

        # Convert to node and use existing recovery
        nid = cmd_info.get("id", f"sh_{uuid.uuid4().hex[:6]}")
        node = Node(id=nid, name=cmd_info.get("name",""))
        node.execute = ExecuteSpec(type="run_shell", command=cmd_info.get("command",""))
        verify_cmd = cmd_info.get("verify","")
        if verify_cmd:
            node.verify = VerifySpec(type="return_code_eq", command=verify_cmd, expected_code=0)
        else:
            node.verify = VerifySpec(type="return_code_eq", command=cmd_info.get("command",""), expected_code=0)
        node.status = NodeStatus.READY
        project.nodes[nid] = node

        result = self._execute_node_l1_logged(project, node, cycle)
        if result == "success": return "ok"
        elif result == "l1_exhausted":
            l2 = self._recover_l2_logged(project, node, brief_json, cycle)
            if l2 == "recovered": return "ok"
            elif l2 == "l2_exhausted":
                l3 = self._recover_l3_logged(project, node, brief_json, snapshot_text, cycle)
                if l3 == "recovered": return "ok"
                else:
                    if project.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        return "waiting_user"
                    _remove_safe(project, node.id)
                    return "ok"
        return "ok"

    # ==========================================================
    # MAIN CYCLE (V14 - kept for complex diagnostic tasks)
    # ==========================================================
    def run_cycle(self, project, on_confirm=None, on_status=None):
        pid = project.id
        brief = project.task_brief
        tn = brief["user_request"]
        tt = ",".join(brief.get("command_types",[]))
        brief_json = json.dumps(brief, ensure_ascii=False, indent=2)
        all_results = {}
        cycle = 0

        while cycle < MAX_CYCLES:
            cycle += 1
            logger.info(f"=== Cycle {cycle} ===")
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="cycle_start", cycle_number=cycle, actor="program")

            if on_status: on_status(f"Cycle {cycle}: scanning...")

            # STEP A: Query
            queries = self._write_queries_logged(pid, tn, tt, brief_json, all_results, cycle)
            if queries:
                snapshot = self._run_queries_logged(project, queries, cycle)
                all_results.update(snapshot)
            snapshot_text = self._format_results(all_results)
            save_project(project)

            # STEP B: Judge
            judgment = self._judge_logged(pid, tn, tt, brief_json, snapshot_text, brief["user_request"], cycle)
            if judgment.get("goal_met"):
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="task_complete", actor="program",
                          api_calls_total=project.ai_call_count, cycle_number=cycle)
                project.status = ProjectStatus.COMPLETED; save_project(project)
                self._save_to_library(project); return "completed"

            gap = judgment.get("gap", "")
            next_action = judgment.get("next_action", "")
            action_type = judgment.get("action_type", "modify")

            # STEP C: Supplement queries
            supp = self._write_queries_logged(pid, tn, tt, brief_json, all_results, cycle,
                        next_step_info=f"Next: {next_action}. Gap: {gap}")
            if supp:
                sr = self._run_queries_logged(project, supp, cycle)
                all_results.update(sr)
                snapshot_text = self._format_results(all_results)

            # STEP D: Write actions
            commands = self._write_actions_logged(pid, tn, tt, brief_json, snapshot_text, next_action, cycle)
            if not commands: continue

            # STEP E: Review
            commands = self._review_logged(pid, tn, tt, brief_json, commands, cycle)

            # STEP F: Confirm
            if on_confirm and action_type in ("create", "modify"):
                plan_text = self._format_plan(commands)
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="user_confirm_ask", direction="ai→user",
                          output_text=plan_text, cycle_number=cycle)
                approved = on_confirm(plan_text)
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="user_confirm_reply", direction="user→program",
                          input_text="approved" if approved else "rejected", cycle_number=cycle)
                if not approved:
                    project.status = ProjectStatus.ABORTED; save_project(project)
                    return "aborted"

            # STEP G: Execute with recovery
            if on_status: on_status("Executing...")
            exec_result = self._execute_with_recovery(project, commands, brief_json, snapshot_text, cycle)
            save_project(project)
            if exec_result == "waiting_user": return "waiting_user"
            elif exec_result == "abort":
                project.status = ProjectStatus.ABORTED; save_project(project); return "failed"

        project.status = ProjectStatus.FAILED; save_project(project)
        return "failed"

    # ==========================================================
    # Logged wrappers for each phase
    # ==========================================================

    def _write_queries_logged(self, pid, tn, tt, brief_json, prev_results, cycle, next_step_info=""):
        prompt = PROMPT_WRITE_QUERY.format(
            task_brief=brief_json, next_step_info=next_step_info or "System scan",
            previous_results=self._format_results(prev_results) if prev_results else "None")
        try:
            r = self._ask_logged([{"role":"user","content":prompt}], None,
                project_id=pid, task_name=tn, task_types=tt,
                event_type="ai_write_query", cycle_number=cycle)
            return r.get("commands", [])
        except: return []

    def _run_queries_logged(self, project, queries, cycle):
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))
        results = {}
        for q in queries:
            qid = q.get("id", "q")
            cmd = q.get("command", "")
            name = q.get("name", cmd[:40])
            if not cmd: continue
            t0 = time.time()
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                dur = int((time.time() - t0) * 1000)
                out = r.stdout[:5000]
                err = r.stderr[:2000] if r.returncode != 0 else ""
                results[qid] = {"name": name, "command": cmd, "stdout": out, "stderr": err}
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="query_execute", direction="program→server", actor="program",
                          command=cmd, stdout=out, stderr=err, return_code=r.returncode,
                          duration_ms=dur, node_id=qid, cycle_number=cycle)
                node = Node(id=f"q_{pid}_{qid}_{cycle}", name=name, node_type="query")
                node.execute = ExecuteSpec(type="run_shell", command=cmd)
                node.execute_output = {"stdout": out, "return_code": r.returncode}
                node.status = NodeStatus.COMPLETED
                project.nodes[node.id] = node
            except Exception as e:
                dur = int((time.time() - t0) * 1000)
                results[qid] = {"name": name, "command": cmd, "stdout": "", "stderr": str(e)}
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="query_execute", direction="program→server",
                          command=cmd, error_message=str(e), duration_ms=dur,
                          node_id=qid, cycle_number=cycle)
        return results

    def _judge_logged(self, pid, tn, tt, brief_json, snapshot_text, goal, cycle):
        prompt = PROMPT_JUDGE.format(task_brief=brief_json, snapshot=snapshot_text, goal=goal)
        try:
            return self._ask_logged([{"role":"user","content":prompt}], None,
                project_id=pid, task_name=tn, task_types=tt,
                event_type="ai_judge", cycle_number=cycle)
        except:
            return {"goal_met": False, "gap": "judge failed", "next_action": "retry"}

    def _write_actions_logged(self, pid, tn, tt, brief_json, snapshot_text, action_desc, cycle):
        prompt = PROMPT_WRITE_ACTION.format(
            task_brief=brief_json, snapshot=snapshot_text, action_description=action_desc)
        try:
            r = self._ask_logged([{"role":"user","content":prompt}], None,
                project_id=pid, task_name=tn, task_types=tt,
                event_type="ai_write_action", cycle_number=cycle)
            return r.get("commands", [])
        except: return []

    def _review_logged(self, pid, tn, tt, brief_json, commands, cycle):
        for rnd in range(MAX_REVIEW_ROUNDS):
            prompt = PROMPT_REVIEW.format(
                task_brief=brief_json,
                commands=json.dumps(commands, ensure_ascii=False, indent=2))
            try:
                r = self._ask_logged([{"role":"user","content":prompt}], None,
                    project_id=pid, task_name=tn, task_types=tt,
                    event_type="ai_review", cycle_number=cycle)
                if r.get("approved"):
                    log_event(project_id=pid, task_name=tn, task_types=tt,
                              event_type="review_pass", cycle_number=cycle,
                              output_text=f"round {rnd+1}")
                    return commands
                fixed = r.get("fixed_commands")
                if fixed and isinstance(fixed, list): commands = fixed
                else: return commands
            except: return commands
        return commands

    # ==========================================================
    # Execute with 3-level recovery (all logged)
    # ==========================================================
    def _execute_with_recovery(self, project, commands, brief_json, snapshot_text, cycle):
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))
        nodes = self._commands_to_nodes(project, commands)

        for node in nodes:
            # Handle write_file: generate content via separate AI call, then write
            if node.execute and node.execute.type == "write_file" and node.execute.path:
                result = self._handle_write_file(project, node, brief_json, cycle)
                if result == "success": continue
                elif result == "failed":
                    if project.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        return "waiting_user"
                    _remove_safe(project, node.id); continue
                continue

            # Normal shell command execution with recovery
            result = self._execute_node_l1_logged(project, node, cycle)
            if result == "success": continue
            elif result == "l1_exhausted":
                l2 = self._recover_l2_logged(project, node, brief_json, cycle)
                if l2 == "recovered": continue
                elif l2 == "l2_exhausted":
                    l3 = self._recover_l3_logged(project, node, brief_json, snapshot_text, cycle)
                    if l3 == "recovered": continue
                    else:
                        if project.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            return "waiting_user"
                        log_event(project_id=pid, task_name=tn, task_types=tt,
                                  event_type="node_skip", node_id=node.id, cycle_number=cycle,
                                  output_text="all recovery exhausted")
                        _remove_safe(project, node.id); continue
            elif result == "abort": return "abort"
        return "ok"

    def _handle_write_file(self, project, node, brief_json, cycle):
        """Generate file content via AI (plain text) and write to disk."""
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))
        path = node.execute.path
        desc = node.execute.description or node.name or "file content"

        # Check if file exists (modify vs create)
        current_content = None
        try:
            r = subprocess.run(f"cat {path} 2>/dev/null", shell=True,
                              capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                current_content = r.stdout
        except: pass

        # Generate content via AI (plain text, no JSON)
        if current_content:
            prompt = PROMPT_MODIFY_FILE_CONTENT.format(
                path=path, description=desc,
                current_content=current_content[:8000],
                task_brief=brief_json[:3000])
        else:
            prompt = PROMPT_WRITE_FILE_CONTENT.format(
                path=path, description=desc,
                task_brief=brief_json[:3000])

        try:
            t0 = time.time()
            content = self.ai.ask_text([{"role":"user","content":prompt}])
            dur = int((time.time() - t0) * 1000)

            # Strip markdown code fences if AI added them
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                # Remove first line (```html or ```) and last line (```)
                if lines[-1].strip() == "```":
                    lines = lines[1:-1]
                else:
                    lines = lines[1:]
                content = "\n".join(lines)

            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="ai_write_file_content", direction="program→ai",
                      actor="backend_ai", node_id=node.id,
                      ai_prompt=f"Generate content for {path}: {desc[:100]}",
                      ai_response=content[:500] + "..." if len(content) > 500 else content,
                      ai_model=self._ai_model(), duration_ms=dur, cycle_number=cycle)

        except Exception as e:
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="ai_write_file_content", error_message=str(e),
                      node_id=node.id, cycle_number=cycle)
            project.consecutive_failures += 1
            return "failed"

        # Ensure parent directory exists
        parent_dir = os.path.dirname(path)
        if parent_dir:
            subprocess.run(f"mkdir -p {parent_dir}", shell=True, timeout=10)

        # Write file content directly via Python
        try:
            t0 = time.time()
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            dur = int((time.time() - t0) * 1000)

            # Verify file was written
            if os.path.exists(path) and os.path.getsize(path) > 0:
                node.execute_output = {"stdout": f"File written: {path} ({os.path.getsize(path)} bytes)", "return_code": 0}
                node.verify_output = {"passed": True, "detail": f"File exists: {os.path.getsize(path)} bytes"}
                node.status = NodeStatus.COMPLETED
                project.consecutive_failures = 0

                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="file_write", direction="program→server",
                          actor="program", node_id=node.id, command=f"write_file {path}",
                          stdout=f"{os.path.getsize(path)} bytes written",
                          verify_passed=1, duration_ms=dur, cycle_number=cycle)

                log_execution(project_id=pid, node_id=node.id, attempt=1,
                              action="write_file", exec_output=node.execute_output,
                              verify_output=node.verify_output)
                return "success"
            else:
                node.status = NodeStatus.FAILED
                project.consecutive_failures += 1
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="file_write", error_message="File empty or not found after write",
                          node_id=node.id, cycle_number=cycle, verify_passed=0)
                return "failed"

        except Exception as e:
            node.status = NodeStatus.FAILED
            project.consecutive_failures += 1
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="file_write", error_message=str(e),
                      node_id=node.id, cycle_number=cycle, verify_passed=0)
            return "failed"

    def _execute_node_l1_logged(self, project, node, cycle):
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))

        for attempt in range(MAX_NODE_RETRIES + MAX_NODE_MODIFICATIONS + 1):
            node.status = NodeStatus.EXECUTING
            t0 = time.time()
            exec_out = run_execute(node.execute)
            node.execute_output = exec_out
            verify_out = run_verify(node.verify)
            node.verify_output = verify_out
            dur = int((time.time() - t0) * 1000)

            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="node_execute", direction="program→server", actor="program",
                      node_id=node.id, command=node.execute.command if node.execute else "",
                      stdout=exec_out.get("stdout","")[:5000],
                      stderr=exec_out.get("stderr","")[:2000],
                      return_code=exec_out.get("return_code"),
                      verify_type=node.verify.type if node.verify else "",
                      verify_passed=1 if verify_out["passed"] else 0,
                      verify_detail=verify_out.get("detail",""),
                      duration_ms=dur, cycle_number=cycle,
                      recovery_level="L1" if attempt > 0 else "none")

            log_execution(project_id=pid, node_id=node.id, attempt=attempt+1,
                          action="execute", exec_output=exec_out, verify_output=verify_out)

            if verify_out["passed"]:
                node.status = NodeStatus.COMPLETED
                project.consecutive_failures = 0
                return "success"

            node.status = NodeStatus.FAILED
            project.consecutive_failures += 1
            error = verify_out.get("detail", "")
            output = exec_out.get("stdout","")[:300] + exec_out.get("stderr","")[:300]

            prompt = PROMPT_L1_RECOVERY.format(node_name=node.name, error=error, output=output)
            try:
                r = self._ask_logged([{"role":"user","content":prompt}], None,
                    project_id=pid, task_name=tn, task_types=tt,
                    event_type="ai_recovery_l1", recovery_level="L1",
                    node_id=node.id, cycle_number=cycle)
                cmd = r.get("cmd", "give_up")
            except:
                cmd = "give_up"

            if cmd == "retry" and node.retry_count < MAX_NODE_RETRIES:
                node.retry_count += 1; continue
            elif cmd == "modify" and node.modify_count < MAX_NODE_MODIFICATIONS:
                node.modify_count += 1
                if "execute" in r: node.execute = ExecuteSpec.from_dict(r["execute"])
                if "verify" in r: node.verify = VerifySpec.from_dict(r["verify"])
                continue
            else:
                return "l1_exhausted"
        return "l1_exhausted"

    def _recover_l2_logged(self, project, node, brief_json, cycle):
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))
        failed_info = f"Node: {node.name}\nCmd: {node.execute.command if node.execute else '?'}\nError: {node.verify_output.get('detail','') if node.verify_output else '?'}\nOutput: {node.execute_output.get('stdout','')[:500] if node.execute_output else ''}"

        for attempt in range(MAX_L2_ATTEMPTS):
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="recovery_l2_start", recovery_level="L2",
                      node_id=node.id, cycle_number=cycle,
                      input_text=f"attempt {attempt+1}")

            prompt = PROMPT_L2_DIAGNOSE.format(task_brief=brief_json, failed_info=failed_info)
            try:
                r = self._ask_logged([{"role":"user","content":prompt}], None,
                    project_id=pid, task_name=tn, task_types=tt,
                    event_type="ai_l2_diagnose", recovery_level="L2",
                    node_id=node.id, cycle_number=cycle)
                diag_queries = r.get("diagnostic_queries", [])
            except: diag_queries = []

            diag_results = {}
            for dq in diag_queries:
                cmd = dq.get("command","")
                if not cmd: continue
                t0 = time.time()
                try:
                    dr = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                    dur = int((time.time()-t0)*1000)
                    diag_results[dq.get("id","d")] = {"name": dq.get("name",""), "stdout": dr.stdout[:2000]}
                    log_event(project_id=pid, task_name=tn, task_types=tt,
                              event_type="l2_diag_execute", direction="program→server",
                              command=cmd, stdout=dr.stdout[:2000], return_code=dr.returncode,
                              duration_ms=dur, recovery_level="L2", node_id=node.id, cycle_number=cycle)
                except Exception as e:
                    diag_results[dq.get("id","d")] = {"name": dq.get("name",""), "stdout": str(e)}

            prompt2 = PROMPT_L2_FIX.format(task_brief=brief_json, failed_info=failed_info,
                diagnostic_results=json.dumps(diag_results, ensure_ascii=False))
            try:
                r2 = self._ask_logged([{"role":"user","content":prompt2}], None,
                    project_id=pid, task_name=tn, task_types=tt,
                    event_type="ai_l2_fix", recovery_level="L2",
                    node_id=node.id, cycle_number=cycle)
            except: continue

            if r2.get("no_alternative"): continue
            alt_cmds = r2.get("alternative_commands", [])
            if not alt_cmds: continue

            alt_nodes = self._commands_to_nodes(project, alt_cmds)
            all_ok = True
            for an in alt_nodes:
                res = self._execute_node_l1_logged(project, an, cycle)
                if res != "success": all_ok = False; break
            if all_ok:
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="recovery_l2_success", recovery_level="L2",
                          node_id=node.id, cycle_number=cycle)
                return "recovered"
        return "l2_exhausted"

    def _recover_l3_logged(self, project, node, brief_json, snapshot_text, cycle):
        pid = project.id
        tn = project.task_brief.get("user_request","")
        tt = ",".join(project.task_brief.get("command_types",[]))
        failed_info = f"Node: {node.name}\nError: {node.verify_output.get('detail','') if node.verify_output else '?'}"

        expand_cmds = ["uname -a","cat /etc/os-release","dpkg --print-architecture","free -h","df -h","whoami"]
        expanded = {}
        for cmd in expand_cmds:
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                expanded[cmd] = r.stdout[:500]
            except: pass

        all_failures = failed_info
        for nid, n in project.nodes.items():
            if n.status == NodeStatus.FAILED and n.verify_output:
                all_failures += f"\n{n.name}: {n.verify_output.get('detail','')}"

        for attempt in range(MAX_L3_ATTEMPTS):
            log_event(project_id=pid, task_name=tn, task_types=tt,
                      event_type="recovery_l3_start", recovery_level="L3",
                      node_id=node.id, cycle_number=cycle,
                      input_text=f"attempt {attempt+1}")

            prompt = PROMPT_L3_REDESIGN.format(task_brief=brief_json,
                all_failures=all_failures, expanded_info=json.dumps(expanded, ensure_ascii=False))
            try:
                r = self._ask_logged([{"role":"user","content":prompt}], None,
                    project_id=pid, task_name=tn, task_types=tt,
                    event_type="ai_l3_redesign", recovery_level="L3",
                    node_id=node.id, cycle_number=cycle)
            except: continue

            if r.get("impossible"):
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="recovery_l3_impossible", recovery_level="L3",
                          output_text=r.get("reason",""), node_id=node.id)
                return "impossible"

            redesigned = r.get("redesigned_commands", [])
            if not redesigned: continue
            re_nodes = self._commands_to_nodes(project, redesigned)
            all_ok = True
            for rn in re_nodes:
                res = self._execute_node_l1_logged(project, rn, cycle)
                if res != "success": all_ok = False; break
            if all_ok:
                log_event(project_id=pid, task_name=tn, task_types=tt,
                          event_type="recovery_l3_success", recovery_level="L3",
                          node_id=node.id, cycle_number=cycle)
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
            if "execute" in cmd: node.execute = ExecuteSpec.from_dict(cmd["execute"])
            if "verify" in cmd: node.verify = VerifySpec.from_dict(cmd["verify"])
            node.status = NodeStatus.READY
            project.nodes[nid] = node
            if nid not in project.root_children: project.root_children.append(nid)
            nodes.append(node)
        return nodes

    def _format_results(self, results):
        if not results: return "No results yet."
        lines = []
        for rid, r in results.items():
            lines.append(f"[{r.get('name',rid)}] ({r.get('command','')})\n{r.get('stdout','')[:500]}")
        return "\n\n".join(lines)

    def _format_plan(self, commands):
        lines = []
        for cmd in commands:
            c = cmd.get("execute",{}).get("command","")
            if len(c)>60: c=c[:60]+"..."
            lines.append(f"• {cmd.get('name','')}\n  → {c}" if c else f"• {cmd.get('name','')}")
        return "\n".join(lines)

    def _save_to_library(self, project):
        try:
            kw = [k.strip() for k in project.name.split() if len(k.strip())>1][:5]
            tree = project.get_tree_summary()
            nd = {nid: n.to_dict() for nid, n in project.nodes.items() if n.is_leaf}
            tt = project.task_brief.get("command_types",[])
            save_template(f"tpl_{project.id}", project.name, kw, tree, nd, task_types=tt)
            for nid, n in project.nodes.items():
                if not n.is_leaf or not n.execute: continue
                if n.execute.type=="write_file" and n.execute.path:
                    p=n.execute.path
                    if any(p.endswith(e) for e in (".py",".sh",".js")):
                        nm=os.path.basename(p)
                        save_tool(f"tool_{project.id}_{nid}",nm,f"From '{project.name}'",
                                  p,f"python3 {p}" if p.endswith(".py") else f"bash {p}",
                                  kw,project.id,"script")
        except Exception as e:
            logger.warning(f"Library save failed: {e}")


def _remove_safe(project, node_id):
    node = project.nodes.get(node_id)
    if not node: return
    for cid in list(node.children): _remove_safe(project, cid)
    if node.parent_id:
        parent = project.nodes.get(node.parent_id)
        if parent and node_id in parent.children: parent.children.remove(node_id)
    elif node_id in project.root_children: project.root_children.remove(node_id)
    for other in project.nodes.values():
        if node_id in other.depends_on: other.depends_on.remove(node_id)
    del project.nodes[node_id]
