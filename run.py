#!/usr/bin/env python3
"""
LittleAnt V12.1 - Telegram Bot (Dual AI Architecture)
Improvements: type-driven decomposition, replan recovery, quote support, image/file support
"""
from __future__ import annotations
import json, logging, threading, time, sys, os, traceback, base64

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from littleant.i18n import load_language, t
from littleant.telegram_bot import TelegramBot
from littleant.ai.adapter import (
    OpenAICompatibleAdapter, CHAT_PROMPT, EXECUTOR_PROMPT, CLASSIFY_TASK_TYPE_PROMPT
)
from littleant.core.orchestrator import Orchestrator
from littleant.core.decomposer import DecompositionError
from littleant.core.readonly_executor import run_readonly, is_safe_readonly
from littleant.models.project import Project, ProjectStatus, NodeStatus
from littleant.storage.json_store import save_project, load_project, list_projects
from littleant.storage.db_store import init_db, search_tools, search_templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("littleant")

CHAT_HISTORY_LIMIT = 20

def load_config():
    cp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "littleant", "config.json")
    if not os.path.exists(cp):
        print("\n⚠️  No config found. Run: python3 setup.py\n"); sys.exit(1)
    with open(cp) as f: return json.load(f)


class UserSession:
    def __init__(self, cid):
        self.chat_id = cid
        self.current_project_id = None
        self.busy = False
        self.state = "idle"  # idle/confirming_task/confirming_plan/executing/waiting_user
        self.pending_task = None
        self.pending_task_types = []  # ["query","modify",...]
        self.chat_history = []

    def add_user(self, text):
        self.chat_history.append({"role": "user", "content": text})
        if len(self.chat_history) > CHAT_HISTORY_LIMIT * 2:
            self.chat_history = self.chat_history[-CHAT_HISTORY_LIMIT * 2:]

    def add_ai(self, text):
        self.chat_history.append({"role": "assistant", "content": text})

    def get_project(self):
        return load_project(self.current_project_id) if self.current_project_id else None

    def status_text(self):
        p = self.get_project()
        if not p: return None
        total = len([n for n in p.nodes.values() if n.is_leaf])
        done = len([n for n in p.nodes.values() if n.status == NodeStatus.COMPLETED])
        failed = len([n for n in p.nodes.values() if n.status == NodeStatus.FAILED])
        s = t("project_label", name=p.name, done=done, total=total)
        if failed: s += t("project_failed_suffix", count=failed)
        return s

sessions = {}
def get_session(cid):
    if cid not in sessions: sessions[cid] = UserSession(cid)
    return sessions[cid]


# ============================================================
# Intent classification
# ============================================================
CLASSIFY_PROMPT = """You are LittleAnt front-end AI. Classify user intent. Reply JSON only.

Current state: {state}

If state=confirming_task or confirming_plan:
  {{"intent":"confirm_yes"}} or {{"intent":"confirm_no"}}

Otherwise:
- {{"intent":"chat","reply":"answer"}} — chat, questions (DEFAULT)
- {{"intent":"task","task_description":"description"}} — explicit action (needs imperative words)
- {{"intent":"quick_query","command":"read-only cmd"}} — needs a command to answer
- {{"intent":"query_status"}} — current task progress
- {{"intent":"query_history","keywords":"kw"}} — past projects
- {{"intent":"skip_node"}} — "skip" "forget it"
- {{"intent":"replan_node"}} — "try another way" "change approach" "different method"
- {{"intent":"retry_project"}} — "restart" "retry"
- {{"intent":"cancel"}} — "cancel task"

Rules: when unsure choose chat. "skip"/"forget it" → skip_node. "try differently" → replan_node."""

def classify(ai, s, text):
    state_map = {"idle": "idle", "confirming_task": f"confirming task: {s.pending_task}",
                 "confirming_plan": "confirming plan", "executing": "task running",
                 "waiting_user": "step failed, waiting decision"}
    sd = state_map.get(s.state, "idle")
    if s.current_project_id:
        st = s.status_text()
        if st: sd += f" ({st})"
    msgs = s.chat_history[-10:] + [{"role": "user", "content": text}]
    try:
        return ai.ask(msgs, system_prompt=CLASSIFY_PROMPT.format(state=sd))
    except:
        return {"intent": "chat", "reply": t("sorry_error")}


def classify_task_type(ai, task_desc):
    """Ask AI to classify task types: query/create/modify"""
    try:
        prompt = CLASSIFY_TASK_TYPE_PROMPT.format(task=task_desc)
        result = ai.ask([{"role": "user", "content": prompt}])
        return result.get("types", ["modify"]), result.get("summary", "")
    except:
        return ["modify"], ""


# ============================================================
# Helpers
# ============================================================
def summarize_results(ai, project):
    results = []
    for nid in sorted(project.nodes.keys()):
        node = project.nodes[nid]
        if not node.is_leaf or node.status != NodeStatus.COMPLETED: continue
        if node.execute_output:
            stdout = node.execute_output.get("stdout", "")[:500]
            if stdout.strip(): results.append(f"[{node.name}]\n{stdout.strip()}")
    if not results: return f"Project \"{project.name}\" completed."
    req = f"Task \"{project.goal}\" done. Summarize output for user. No technical jargon. Match user's language.\n\n" + "\n\n".join(results[:10])
    try:
        return ai.ask_text([{"role": "user", "content": req}], system_prompt="Summarize concisely. Match user's language.")
    except:
        return "\n".join(results[:5])


def plan_project(ai, task, tool_ctx="", tpl_ctx=""):
    extra = ""
    if tool_ctx: extra += f"\n\nExisting tools (reuse, don't recreate):\n{tool_ctx}"
    if tpl_ctx: extra += f"\n\nPast projects:\n{tpl_ctx}"
    prompt = f'User request: {task}{extra}\n\nGenerate project as typed steps. Each child must have "type":"query" or "create" or "modify".\nPure JSON:\n{{"cmd":"create_project","name":"name","goal":"goal","children":[{{"id":"1","name":"step","type":"query","depends_on":[]}}]}}\n\nOnly do what was asked. JSON only.'
    return ai.ask([{"role": "user", "content": prompt}], system_prompt=EXECUTOR_PROMPT)


def read_file_content(file_path, max_chars=5000):
    """Read text file content, return empty string for binary."""
    text_exts = {".txt",".conf",".cfg",".ini",".json",".yaml",".yml",".xml",".sh",".py",".js",".php",".html",".css",".md",".log",".env",".toml"}
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in text_exts:
        return None  # Binary file
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    except:
        return None


def encode_image_base64(file_path):
    """Encode image to base64 for AI vision."""
    try:
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except:
        return None


# ============================================================
# Main
# ============================================================
def main():
    config = load_config()
    load_language(config.get("language", "en"))
    init_db()

    ai = OpenAICompatibleAdapter(
        api_key=config["ai_api_key"], base_url=config["ai_base_url"], model=config["ai_model"])
    bot = TelegramBot(config["telegram_token"])
    orch = Orchestrator(ai=ai)

    lang = config.get("language", "en")
    if lang == "zh":
        bot.set_menu_commands([("help","查看帮助"),("status","任务进度"),("cancel","取消任务")])
    else:
        bot.set_menu_commands([("help","Show help"),("status","Task progress"),("cancel","Cancel task")])

    # ======== Commands ========
    @bot.on_command("start")
    def cmd_start(msg):
        bot.send_message(msg["chat"]["id"], t("bot_welcome", name=msg["from"].get("first_name","")))
    @bot.on_command("help")
    def cmd_help(msg):
        bot.send_message(msg["chat"]["id"], t("bot_help"))
    @bot.on_command("status")
    def cmd_status(msg):
        s = get_session(msg["chat"]["id"])
        st = s.status_text()
        bot.send_message(msg["chat"]["id"], t("task_status", status=st) if st else t("no_task"))
    @bot.on_command("cancel")
    def cmd_cancel(msg):
        s = get_session(msg["chat"]["id"])
        if s.current_project_id:
            p = load_project(s.current_project_id)
            if p: p.status = ProjectStatus.ABORTED; save_project(p)
            s.current_project_id = None; s.busy = False; s.state = "idle"; s.pending_task = None
            bot.send_message(msg["chat"]["id"], t("task_cancelled"))
        else:
            bot.send_message(msg["chat"]["id"], t("no_task"))

    # ======== Message handling ========
    @bot.on_message
    def handle_message(msg):
        cid = msg["chat"]["id"]
        text = msg.get("text", "").strip()
        s = get_session(cid)
        bot.send_typing(cid)

        # Build context with reply/quote
        reply_ctx = msg.get("_reply_context", "")
        msg_type = msg.get("_type", "text")

        # Handle image messages
        if msg_type == "photo":
            file_path = msg.get("_file_path")
            caption = msg.get("_caption", "")
            if file_path:
                img_b64 = encode_image_base64(file_path)
                if img_b64:
                    # Send to AI with vision
                    user_text = caption or "What's in this image?"
                    if reply_ctx:
                        user_text = f"[Quoted: {reply_ctx[:200]}]\n{user_text}"
                    try:
                        # Use vision-capable message format
                        vision_msg = [{"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                            {"type": "text", "text": user_text}
                        ]}]
                        reply = ai.ask_text(vision_msg, system_prompt=CHAT_PROMPT)
                    except Exception as e:
                        reply = f"Cannot process image: {str(e)[:200]}"
                    s.add_user(f"[image] {caption}")
                    bot.send_message(cid, reply); s.add_ai(reply)
                    return

        # Handle file messages
        if msg_type == "document":
            file_path = msg.get("_file_path")
            file_name = msg.get("_file_name", "unknown")
            caption = msg.get("_caption", "")
            if file_path:
                content = read_file_content(file_path)
                if content:
                    user_text = caption or f"Here's the content of {file_name}:"
                    if reply_ctx:
                        user_text = f"[Quoted: {reply_ctx[:200]}]\n{user_text}"
                    ctx = f"{user_text}\n\n--- File: {file_name} ---\n{content[:4000]}"
                    s.add_user(ctx)
                    msgs = s.chat_history[-10:]
                    try:
                        reply = ai.ask_text(msgs, system_prompt=CHAT_PROMPT)
                    except:
                        reply = t("sorry_error")
                    bot.send_message(cid, reply); s.add_ai(reply)
                else:
                    bot.send_message(cid, f"File {file_name} saved to server at {file_path}")
                    s.add_user(f"[file: {file_name}]")
                return

        if not text:
            return

        # Prepend quote context to user message for AI
        full_text = text
        if reply_ctx:
            full_text = f"[Quoted: {reply_ctx[:300]}]\n{text}"
        s.add_user(full_text)

        result = classify(ai, s, full_text)
        intent = result.get("intent", "chat")
        logger.info(f"[{cid}] intent={intent} state={s.state}")

        # ---- Chat ----
        if intent == "chat":
            reply = result.get("reply") or t("sorry_error")
            bot.send_message(cid, reply); s.add_ai(reply)

        # ---- Quick query (front-end AI read-only) ----
        elif intent == "quick_query":
            cmd = result.get("command", "")
            safe, reason = is_safe_readonly(cmd) if cmd else (False, "empty")
            if not safe:
                bot.send_message(cid, t("readonly_denied", reason=reason)); return
            r = run_readonly(cmd)
            if r["success"]:
                try: reply = ai.ask_text([{"role":"user","content":f"Command `{cmd}` output:\n{r['output'][:3000]}\nSummarize. Match user's language."}], system_prompt=CHAT_PROMPT)
                except: reply = r["output"][:2000]
            else: reply = t("query_failed", error=r["error"])
            bot.send_message(cid, reply); s.add_ai(reply)

        # ---- Query history ----
        elif intent == "query_history":
            kw = result.get("keywords", "")
            projects = list_projects()
            if not projects: reply = t("no_projects")
            else:
                matched = [p for p in projects if any(k in p["name"] for k in kw.split())] if kw else projects[:5]
                if not matched: matched = projects[:5]
                info = "\n".join([f"- {p['name']} [{p['status']}]" for p in matched[:10]])
                try: reply = ai.ask_text([{"role":"user","content":f"Past projects:\n{info}\nSummarize. Match user's language."}], system_prompt=CHAT_PROMPT)
                except: reply = info
            bot.send_message(cid, reply); s.add_ai(reply)

        # ---- Query status ----
        elif intent == "query_status":
            st = s.status_text()
            reply = t("task_status", status=st) if st else t("no_task")
            bot.send_message(cid, reply); s.add_ai(reply)

        # ---- Task ----
        elif intent == "task":
            if s.busy:
                bot.send_message(cid, t("busy")); return
            task_desc = result.get("task_description", text)

            # Classify task types
            types, summary = classify_task_type(ai, task_desc)
            s.pending_task = task_desc
            s.pending_task_types = types

            # Pure query tasks skip confirmation
            if types == ["query"]:
                s.state = "idle"
                s.pending_task = None
                reply = t("planning", task=task_desc)
                bot.send_message(cid, reply); s.add_ai(reply)
                threading.Thread(target=_plan_and_execute_query, args=(bot,ai,orch,s,task_desc), daemon=True).start()
            else:
                # Tasks with create/modify need confirmation
                s.state = "confirming_task"
                reply = t("confirm_task", task=task_desc)
                bot.send_message(cid, reply, reply_markup={"inline_keyboard":[[
                    {"text": t("confirm_task_yes_btn"), "callback_data": "confirm_task_yes"},
                    {"text": t("confirm_task_no_btn"), "callback_data": "confirm_task_no"}]]})
                s.add_ai(reply)

        # ---- Confirm yes ----
        elif intent == "confirm_yes":
            if s.state == "confirming_task" and s.pending_task:
                s.state = "idle"; td = s.pending_task; s.pending_task = None
                bot.send_message(cid, t("planning", task=td)); s.add_ai(t("planning", task=td))
                threading.Thread(target=_plan_and_confirm, args=(bot,ai,orch,s,td), daemon=True).start()
            elif s.state == "confirming_plan" and s.current_project_id:
                s.state = "idle"; p = load_project(s.current_project_id)
                if p:
                    bot.send_message(cid, t("exec_start")); s.add_ai(t("exec_start"))
                    threading.Thread(target=_exec, args=(bot,ai,orch,s,p), daemon=True).start()
            else: bot.send_message(cid, t("no_confirm_pending"))

        # ---- Confirm no ----
        elif intent == "confirm_no":
            if s.state in ("confirming_task","confirming_plan"):
                s.state = "idle"; s.pending_task = None
            bot.send_message(cid, t("confirm_no_reply")); s.add_ai(t("confirm_no_reply"))

        # ---- Skip node ----
        elif intent == "skip_node":
            if s.current_project_id:
                from littleant.core.recovery import remove_node
                p = load_project(s.current_project_id)
                if p:
                    removed = [nid for nid in list(p.nodes.keys()) if p.nodes[nid].status == NodeStatus.FAILED]
                    for nid in removed: remove_node(p, nid)
                    save_project(p)
                    if removed:
                        bot.send_message(cid, t("skipped"))
                        if not s.busy: threading.Thread(target=_exec, args=(bot,ai,orch,s,p), daemon=True).start()
                    else: bot.send_message(cid, t("no_skip_needed"))
            else: bot.send_message(cid, t("no_skip_needed"))

        # ---- Replan node ----
        elif intent == "replan_node":
            if s.current_project_id:
                from littleant.core.recovery import replan_branch
                p = load_project(s.current_project_id)
                if p:
                    # Find failed nodes and replan their parent
                    replanned = False
                    for nid in list(p.nodes.keys()):
                        n = p.nodes.get(nid)
                        if n and n.status == NodeStatus.FAILED and n.parent_id:
                            if replan_branch(p, n.parent_id, ai, "user requested replan"):
                                replanned = True
                                break
                    if replanned:
                        save_project(p)
                        bot.send_message(cid, "OK, trying a different approach...")
                        if not s.busy:
                            threading.Thread(target=_replan_and_exec, args=(bot,ai,orch,s,p), daemon=True).start()
                    else:
                        bot.send_message(cid, "No failed steps to replan.")
            else:
                bot.send_message(cid, "No active project.")

        # ---- Retry project ----
        elif intent == "retry_project":
            if s.current_project_id and not s.busy:
                p = load_project(s.current_project_id)
                if p:
                    for n in p.nodes.values():
                        if n.status == NodeStatus.FAILED: n.status = NodeStatus.READY; n.retry_count=0; n.modify_count=0
                    p.consecutive_failures = 0; save_project(p)
                    bot.send_message(cid, t("retrying"))
                    threading.Thread(target=_exec, args=(bot,ai,orch,s,p), daemon=True).start()
                else: bot.send_message(cid, t("project_not_found"))
            else: bot.send_message(cid, t("no_retry"))

        elif intent == "cancel": cmd_cancel(msg)
        else:
            reply = result.get("reply") or t("unknown_msg")
            bot.send_message(cid, reply); s.add_ai(reply)

    # ======== Callbacks ========
    @bot.on_callback
    def handle_cb(cb):
        cid = cb["message"]["chat"]["id"]; data = cb.get("data",""); bot.answer_callback(cb["id"])
        s = get_session(cid)
        if data == "confirm_task_yes" and s.state == "confirming_task" and s.pending_task:
            s.state="idle"; td=s.pending_task; s.pending_task=None
            bot.send_message(cid, t("planning", task=td))
            threading.Thread(target=_plan_and_confirm, args=(bot,ai,orch,s,td), daemon=True).start()
        elif data == "confirm_task_no":
            s.state="idle"; s.pending_task=None; bot.send_message(cid, t("confirm_no_reply"))
        elif data == "approve_plan" and s.state == "confirming_plan" and s.current_project_id:
            s.state="idle"; p=load_project(s.current_project_id)
            if p: bot.send_message(cid, t("exec_start")); threading.Thread(target=_exec, args=(bot,ai,orch,s,p), daemon=True).start()
        elif data == "reject_plan":
            s.state="idle"; s.current_project_id=None; bot.send_message(cid, t("plan_rejected"))

    # ======== Background tasks ========

    def _plan_and_execute_query(bot, ai, orch, s, task_desc):
        """Fast track for pure query tasks — no user confirmation needed."""
        cid = s.chat_id; s.busy = True
        try:
            kws = [k for k in task_desc.split() if len(k) > 1][:5]
            tool_ctx = "\n".join([f"- {x['name']}: {x.get('description','')} (path:{x['path']})" for x in search_tools(kws,5)]) if kws else ""
            plan = plan_project(ai, task_desc, tool_ctx)
            project = orch.create_project(name=plan.get("name",task_desc), goal=plan.get("goal",task_desc), initial_children=plan.get("children",[]))
            s.current_project_id = project.id
            orch.decompose(project)
            # Execute directly (no confirmation for queries)
            result = orch.execute_with_replan(project)
            summary = summarize_results(ai, project)
            bot.send_message(cid, t("exec_done", summary=summary)); s.add_ai(summary)
        except Exception as e:
            logger.error(traceback.format_exc())
            bot.send_message(cid, t("planning_error", error=str(e)[:200]))
        finally: s.busy = False; s.state = "idle"

    def _plan_and_confirm(bot, ai, orch, s, task_desc):
        """Plan and show to user for confirmation."""
        cid = s.chat_id
        try:
            kws = [k for k in task_desc.split() if len(k) > 1][:5]
            tool_ctx = "\n".join([f"- {x['name']}: {x.get('description','')} (path:{x['path']}, usage:{x['usage']})" for x in search_tools(kws,5)]) if kws else ""
            tpl_ctx = "\n".join([f"- {x['name']} ({x['nodes']} nodes, {x['date']})" for x in search_templates(kws,3)]) if kws else ""
            plan = plan_project(ai, task_desc, tool_ctx, tpl_ctx)
            project = orch.create_project(name=plan.get("name",task_desc), goal=plan.get("goal",task_desc), initial_children=plan.get("children",[]))
            s.current_project_id = project.id
            orch.decompose(project)

            leaves = sorted([n for n in project.nodes.values() if n.is_leaf], key=lambda n: n.id)
            lines = [t("plan_header", name=project.name, count=len(leaves)), ""]
            for leaf in leaves[:12]:
                cs = ""
                if leaf.execute and leaf.execute.command:
                    c = leaf.execute.command; cs = f"\n  → {c[:50]}{'...' if len(c)>50 else ''}"
                lines.append(f"• {leaf.name}{cs}")
            if len(leaves) > 12: lines.append(t("plan_more", count=len(leaves)-12))
            lines.append(t("plan_confirm"))
            s.state = "confirming_plan"
            bot.send_message(cid, "\n".join(lines), reply_markup={"inline_keyboard":[[
                {"text": t("plan_approve_btn"), "callback_data": "approve_plan"},
                {"text": t("plan_reject_btn"), "callback_data": "reject_plan"}]]})
        except Exception as e:
            s.state="idle"; logger.error(traceback.format_exc())
            bot.send_message(cid, t("planning_error", error=str(e)[:200]))
            s.current_project_id = None

    def _exec(bot, ai, orch, s, project):
        """Execute project with automatic replan."""
        cid = s.chat_id; s.busy = True; s.state = "executing"
        try:
            result = orch.execute_with_replan(project)
            if result == "completed":
                summary = summarize_results(ai, project)
                bot.send_message(cid, t("exec_done", summary=summary)); s.add_ai(summary)
            elif result == "abort" or result == "failed":
                bot.send_message(cid, t("exec_failed"))
            elif result == "waiting_user":
                # Find the failed node
                for n in project.nodes.values():
                    if n.status == NodeStatus.FAILED:
                        det = n.verify_output.get("detail","") if n.verify_output else ""
                        bot.send_message(cid, t("node_problem", name=n.name, detail=det))
                        break
                s.state = "waiting_user"
        except Exception as e:
            logger.error(traceback.format_exc())
            bot.send_message(cid, t("exec_error", error=str(e)[:200]))
        finally:
            if s.state != "waiting_user":
                s.busy = False; s.state = "idle"
            else:
                s.busy = False

    def _replan_and_exec(bot, ai, orch, s, project):
        """Re-decompose after replan, then execute."""
        cid = s.chat_id; s.busy = True; s.state = "executing"
        try:
            orch.decompose(project)
            result = orch.execute_with_replan(project)
            if result == "completed":
                summary = summarize_results(ai, project)
                bot.send_message(cid, t("exec_done", summary=summary)); s.add_ai(summary)
            elif result == "waiting_user":
                for n in project.nodes.values():
                    if n.status == NodeStatus.FAILED:
                        det = n.verify_output.get("detail","") if n.verify_output else ""
                        bot.send_message(cid, t("node_problem", name=n.name, detail=det))
                        break
                s.state = "waiting_user"
            else:
                bot.send_message(cid, t("exec_failed"))
        except Exception as e:
            logger.error(traceback.format_exc())
            bot.send_message(cid, t("exec_error", error=str(e)[:200]))
        finally:
            if s.state != "waiting_user": s.busy = False; s.state = "idle"
            else: s.busy = False

    # ======== Start ========
    logger.info("=" * 50)
    logger.info("LittleAnt V12.1 - Dual AI Architecture")
    logger.info(f"Language: {config.get('language','en')} | AI: {config.get('ai_provider','?')}")
    logger.info("=" * 50)
    bot.start_polling()


if __name__ == "__main__":
    main()
