#!/usr/bin/env python3
"""
LittleAnt V13 - Telegram Bot (Cycle Execution Architecture)
Flow: query → judge → act → query → ... → goal met
Three-level recovery: L1 command → L2 diagnose → L3 redesign
"""
from __future__ import annotations
import json, logging, threading, time, sys, os, traceback, base64

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from littleant.i18n import load_language, t
from littleant.telegram_bot import TelegramBot
from littleant.ai.adapter import OpenAICompatibleAdapter, CHAT_PROMPT
from littleant.core.orchestrator import Orchestrator
from littleant.core.readonly_executor import run_readonly, is_safe_readonly
from littleant.models.project import Project, ProjectStatus, NodeStatus
from littleant.storage.json_store import save_project, load_project, list_projects
from littleant.storage.db_store import init_db, search_tools, search_templates, update_template_feedback
from setup import PROVIDERS_BY_ID, test_api_key, save_config as save_setup_config

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
        self.state = "idle"
        self.pending_task = None
        self.pending_provider = None
        self.pending_template_id = None  # for feedback
        self.confirm_event = threading.Event()
        self.confirm_result = None
        self.confirm_commands = None
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

If state is confirming_task or confirming_plan:
  {{"intent":"confirm_yes"}} or {{"intent":"confirm_no"}}

Otherwise:
- {{"intent":"chat","reply":"answer"}} — chat, questions (DEFAULT)
- {{"intent":"task","task_description":"description"}} — explicit action request with imperative words
- {{"intent":"quick_query","command":"read-only cmd"}} — needs a specific shell command to answer
- {{"intent":"query_status"}} — current task progress
- {{"intent":"query_history","keywords":"kw"}} — past projects
- {{"intent":"skip_node"}} — "skip" "forget it"
- {{"intent":"retry_project"}} — "restart" "retry"
- {{"intent":"cancel"}} — "cancel task"

Rules: when unsure choose chat. "skip"/"forget it" → skip_node."""

def classify(ai, s, text):
    sd = {"idle":"idle","confirming_task":f"confirming task: {s.pending_task}",
          "confirming_plan":"confirming execution plan","executing":"task running",
          "waiting_user":"step failed, waiting","waiting_api_key":"waiting for API key input",
          "waiting_feedback":"waiting for task feedback"
          }.get(s.state, "idle")
    if s.current_project_id:
        st = s.status_text()
        if st: sd += f" ({st})"
    msgs = s.chat_history[-10:] + [{"role":"user","content":text}]
    try: return ai.ask(msgs, system_prompt=CLASSIFY_PROMPT.format(state=sd))
    except: return {"intent":"chat","reply":t("sorry_error")}


def summarize_results(ai, project):
    results = []
    for nid in sorted(project.nodes.keys()):
        node = project.nodes[nid]
        if not node.is_leaf or node.status != NodeStatus.COMPLETED: continue
        if node.execute_output:
            stdout = node.execute_output.get("stdout","")[:500]
            if stdout.strip(): results.append(f"[{node.name}]\n{stdout.strip()}")
    if not results: return f"Project completed."
    req = f"Task \"{project.goal}\" done. Summarize for user. Match their language.\n\n"+"\n\n".join(results[:10])
    try: return ai.ask_text([{"role":"user","content":req}], system_prompt="Summarize concisely. Match user's language.")
    except: return "\n".join(results[:5])


def read_file_content(path, max_chars=5000):
    text_exts = {".txt",".conf",".cfg",".ini",".json",".yaml",".yml",".xml",".sh",".py",".js",".php",".html",".css",".md",".log",".env",".toml"}
    if os.path.splitext(path)[1].lower() not in text_exts: return None
    try:
        with open(path,"r",encoding="utf-8",errors="replace") as f: return f.read(max_chars)
    except: return None


def encode_image_base64(path):
    try:
        with open(path,"rb") as f: return base64.b64encode(f.read()).decode("utf-8")
    except: return None


# ============================================================
# Main
# ============================================================
def main():
    config = load_config()
    load_language(config.get("language","en"))
    init_db()

    ai = OpenAICompatibleAdapter(
        api_key=config["ai_api_key"], base_url=config["ai_base_url"], model=config["ai_model"])
    bot = TelegramBot(config["telegram_token"])
    orch = Orchestrator(ai=ai)

    lang = config.get("language","en")
    if lang == "zh":
        bot.set_menu_commands([("help","查看帮助"),("status","任务进度"),("model","切换AI模型"),("cancel","取消任务")])
    else:
        bot.set_menu_commands([("help","Show help"),("status","Task progress"),("model","Switch AI model"),("cancel","Cancel task")])

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
        bot.send_message(msg["chat"]["id"], t("task_status",status=st) if st else t("no_task"))
    @bot.on_command("cancel")
    def cmd_cancel(msg):
        s = get_session(msg["chat"]["id"])
        if s.current_project_id:
            p = load_project(s.current_project_id)
            if p: p.status=ProjectStatus.ABORTED; save_project(p)
            s.current_project_id=None; s.busy=False; s.state="idle"; s.pending_task=None
            s.confirm_event.set()  # unblock any waiting confirm
            bot.send_message(msg["chat"]["id"], t("task_cancelled"))
        else:
            bot.send_message(msg["chat"]["id"], t("no_task"))
    @bot.on_command("model")
    def cmd_model(msg):
        cid = msg["chat"]["id"]
        current = config.get("ai_provider","?")
        buttons = []
        for pid, info in PROVIDERS_BY_ID.items():
            marker = " ✅" if pid == current else ""
            has_key = bool(config.get("providers",{}).get(pid,{}).get("api_key"))
            ks = " (key saved)" if has_key else ""
            buttons.append([{"text":f"{info['name']}{marker}{ks}","callback_data":f"switch_{pid}"}])
        bot.send_message(cid, f"Current AI: {current}\nSwitch to:", reply_markup={"inline_keyboard":buttons})

    # ======== Message handling ========
    @bot.on_message
    def handle_message(msg):
        cid = msg["chat"]["id"]
        text = msg.get("text","").strip()
        s = get_session(cid)
        bot.send_typing(cid)

        # API key input for model switching
        if s.state == "waiting_api_key" and s.pending_provider and text:
            prov = PROVIDERS_BY_ID.get(s.pending_provider)
            if not prov: bot.send_message(cid,"Invalid provider."); s.state="idle"; return
            if len(text) < 10: bot.send_message(cid,"API key too short. Try again or /cancel."); return
            bot.send_message(cid, f"Testing {prov['name']} API key...")
            ok, err = test_api_key(prov["base_url"], prov["model"], text)
            if ok:
                if "providers" not in config: config["providers"] = {}
                config["providers"][s.pending_provider] = {"api_key":text,"base_url":prov["base_url"],"model":prov["model"]}
                config["ai_provider"]=s.pending_provider; config["ai_api_key"]=text
                config["ai_base_url"]=prov["base_url"]; config["ai_model"]=prov["model"]
                save_setup_config(config)
                ai.api_key=text; ai.base_url=prov["base_url"]; ai.model=prov["model"]
                bot.send_message(cid, f"✅ Switched to {prov['name']}!")
            else:
                bot.send_message(cid, f"❌ API key invalid: {err}\nTry again or /cancel.")
                return
            s.state="idle"; s.pending_provider=None; return

        # Handle user feedback text (unsatisfied reason)
        if s.state == "waiting_feedback" and s.pending_template_id and text:
            update_template_feedback(s.pending_template_id, 1, text)
            bot.send_message(cid, t("feedback_saved"))
            s.state = "idle"; s.pending_template_id = None; return

        # Reply/quote context
        reply_ctx = msg.get("_reply_context","")
        msg_type = msg.get("_type","text")

        # Image
        if msg_type == "photo":
            fp = msg.get("_file_path"); cap = msg.get("_caption","")
            if fp:
                b64 = encode_image_base64(fp)
                if b64:
                    ut = cap or "What's in this image?"
                    if reply_ctx: ut = f"[Quoted: {reply_ctx[:200]}]\n{ut}"
                    try:
                        r = ai.ask_text([{"role":"user","content":[
                            {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}},
                            {"type":"text","text":ut}]}], system_prompt=CHAT_PROMPT)
                    except Exception as e: r = f"Cannot process image: {str(e)[:200]}"
                    s.add_user(f"[image] {cap}"); bot.send_message(cid, r); s.add_ai(r); return

        # File
        if msg_type == "document":
            fp = msg.get("_file_path"); fn = msg.get("_file_name","?"); cap = msg.get("_caption","")
            if fp:
                content = read_file_content(fp)
                if content:
                    ctx = f"{cap or f'File {fn}:'}\n\n--- {fn} ---\n{content[:4000]}"
                    s.add_user(ctx)
                    try: r = ai.ask_text(s.chat_history[-10:], system_prompt=CHAT_PROMPT)
                    except: r = t("sorry_error")
                    bot.send_message(cid, r); s.add_ai(r)
                else:
                    bot.send_message(cid, f"File {fn} saved to {fp}")
                    s.add_user(f"[file: {fn}]")
                return

        if not text: return
        full_text = f"[Quoted: {reply_ctx[:300]}]\n{text}" if reply_ctx else text
        s.add_user(full_text)

        result = classify(ai, s, full_text)
        intent = result.get("intent","chat")
        logger.info(f"[{cid}] intent={intent} state={s.state}")

        # Chat
        if intent == "chat":
            r = result.get("reply") or t("sorry_error")
            bot.send_message(cid, r); s.add_ai(r)

        # Quick query
        elif intent == "quick_query":
            cmd = result.get("command","")
            safe, reason = is_safe_readonly(cmd) if cmd else (False,"empty")
            if not safe:
                try: r = ai.ask_text([{"role":"user","content":f"User asked: {full_text}\nCan't run as quick query. Suggest creating a task. Be natural. Match language."}], system_prompt=CHAT_PROMPT)
                except: r = "This needs a task to execute. Say 'execute task: ...' to start."
                bot.send_message(cid, r); s.add_ai(r); return
            ro = run_readonly(cmd)
            if ro["success"]:
                try: r = ai.ask_text([{"role":"user","content":f"`{cmd}` output:\n{ro['output'][:3000]}\nSummarize. Match language."}], system_prompt=CHAT_PROMPT)
                except: r = ro["output"][:2000]
            else: r = t("query_failed", error=ro["error"])
            bot.send_message(cid, r); s.add_ai(r)

        # Query history
        elif intent == "query_history":
            kw = result.get("keywords","")
            projects = list_projects()
            if not projects: r = t("no_projects")
            else:
                matched = [p for p in projects if any(k in p["name"] for k in kw.split())] if kw else projects[:5]
                if not matched: matched = projects[:5]
                info = "\n".join([f"- {p['name']} [{p['status']}]" for p in matched[:10]])
                try: r = ai.ask_text([{"role":"user","content":f"Past projects:\n{info}\nSummarize. Match language."}], system_prompt=CHAT_PROMPT)
                except: r = info
            bot.send_message(cid, r); s.add_ai(r)

        # Query status
        elif intent == "query_status":
            st = s.status_text()
            bot.send_message(cid, t("task_status",status=st) if st else t("no_task"))

        # Task
        elif intent == "task":
            if s.busy: bot.send_message(cid, t("busy")); return
            td = result.get("task_description", text)

            # Phase 1: classify
            types, summary = orch.classify_task(td)
            s.pending_task = td

            if types == ["query"]:
                # Pure query → no confirmation needed
                r = t("planning", task=td)
                bot.send_message(cid, r); s.add_ai(r)
                threading.Thread(target=_run_task, args=(bot,ai,orch,s,td,types,False), daemon=True).start()
            else:
                # Has create/modify → confirm first
                s.state = "confirming_task"
                r = t("confirm_task", task=td)
                bot.send_message(cid, r, reply_markup={"inline_keyboard":[[
                    {"text":t("confirm_task_yes_btn"),"callback_data":"confirm_task_yes"},
                    {"text":t("confirm_task_no_btn"),"callback_data":"confirm_task_no"}]]})
                s.add_ai(r)

        # Confirm yes
        elif intent == "confirm_yes":
            if s.state == "confirming_task" and s.pending_task:
                s.state = "idle"; td = s.pending_task; s.pending_task = None
                types, _ = orch.classify_task(td)
                r = t("planning", task=td)
                bot.send_message(cid, r); s.add_ai(r)
                threading.Thread(target=_run_task, args=(bot,ai,orch,s,td,types,True), daemon=True).start()
            elif s.state == "confirming_plan":
                s.confirm_result = True; s.confirm_event.set()
            else: bot.send_message(cid, t("no_confirm_pending"))

        # Confirm no
        elif intent == "confirm_no":
            if s.state == "confirming_task":
                s.state="idle"; s.pending_task=None
            elif s.state == "confirming_plan":
                s.confirm_result = False; s.confirm_event.set()
            bot.send_message(cid, t("confirm_no_reply")); s.add_ai(t("confirm_no_reply"))

        # Skip
        elif intent == "skip_node":
            if s.current_project_id:
                from littleant.core.recovery import remove_node
                p = load_project(s.current_project_id)
                if p:
                    removed = [nid for nid in list(p.nodes.keys()) if p.nodes[nid].status == NodeStatus.FAILED]
                    for nid in removed: remove_node(p, nid)
                    save_project(p)
                    bot.send_message(cid, t("skipped") if removed else t("no_skip_needed"))
            else: bot.send_message(cid, t("no_skip_needed"))

        # Retry
        elif intent == "retry_project":
            if s.current_project_id and not s.busy:
                p = load_project(s.current_project_id)
                if p:
                    for n in p.nodes.values():
                        if n.status == NodeStatus.FAILED: n.status=NodeStatus.READY; n.retry_count=0; n.modify_count=0
                    p.consecutive_failures=0; save_project(p)
                    bot.send_message(cid, t("retrying"))
                    # Re-run phased execution
                    threading.Thread(target=_resume_task, args=(bot,ai,orch,s,p), daemon=True).start()
                else: bot.send_message(cid, t("project_not_found"))
            else: bot.send_message(cid, t("no_retry"))

        elif intent == "cancel": cmd_cancel(msg)
        else:
            r = result.get("reply") or t("unknown_msg")
            bot.send_message(cid, r); s.add_ai(r)

    # ======== Callbacks ========
    @bot.on_callback
    def handle_cb(cb):
        cid = cb["message"]["chat"]["id"]; data = cb.get("data",""); bot.answer_callback(cb["id"])
        s = get_session(cid)
        if data == "confirm_task_yes" and s.state == "confirming_task" and s.pending_task:
            s.state="idle"; td=s.pending_task; s.pending_task=None
            types, _ = orch.classify_task(td)
            bot.send_message(cid, t("planning", task=td))
            threading.Thread(target=_run_task, args=(bot,ai,orch,s,td,types,True), daemon=True).start()
        elif data == "confirm_task_no":
            s.state="idle"; s.pending_task=None; bot.send_message(cid, t("confirm_no_reply"))
        elif data == "approve_plan":
            s.confirm_result = True; s.confirm_event.set()
        elif data == "reject_plan":
            s.confirm_result = False; s.confirm_event.set()
        elif data.startswith("switch_"):
            pid = data.replace("switch_","")
            info = PROVIDERS_BY_ID.get(pid)
            if not info: return
            saved = config.get("providers",{}).get(pid,{}).get("api_key")
            if saved:
                config["ai_provider"]=pid; config["ai_api_key"]=saved
                config["ai_base_url"]=info["base_url"]; config["ai_model"]=info["model"]
                save_setup_config(config)
                ai.api_key=saved; ai.base_url=info["base_url"]; ai.model=info["model"]
                bot.send_message(cid, f"✅ Switched to {info['name']}!")
            else:
                s.state="waiting_api_key"; s.pending_provider=pid
                bot.send_message(cid, f"Please send me your {info['name']} API key:")
        elif data == "feedback_yes":
            if s.pending_template_id:
                update_template_feedback(s.pending_template_id, 5, "")
                bot.send_message(cid, t("feedback_thanks"))
                s.pending_template_id = None; s.state = "idle"
        elif data == "feedback_no":
            if s.pending_template_id:
                bot.send_message(cid, t("feedback_ask_reason"))
                s.state = "waiting_feedback"

    # ======== Background task runner ========

    def _run_task(bot, ai, orch, s, task_desc, types, needs_confirm):
        """Route: pure query → fast path, mixed → cycle model."""
        cid = s.chat_id
        s.busy = True; s.state = "executing"
        try:
            # Pure query: fast path (no cycle, no confirmation)
            if types == ["query"]:
                task_brief = {
                    "user_request": task_desc,
                    "command_types": types,
                    "ai_model": getattr(ai, "model", "unknown"),
                    "planned_steps": [],
                }
                project = orch.create_project(task_brief)
                s.current_project_id = project.id

                def on_status(msg):
                    bot.send_message(cid, f"⏳ {msg}")

                result = orch.run_query_fast(project, on_status=on_status)

                if result == "completed":
                    summary = summarize_results(ai, project)
                    bot.send_message(cid, t("exec_done", summary=summary))
                    s.add_ai(summary)
                    _send_feedback_buttons(bot, cid, s, project)
                else:
                    bot.send_message(cid, t("exec_failed"))
                return

            # Mixed tasks: design steps → cycle model
            task_brief = orch.design_steps(task_desc, types)
            task_brief = orch.do_think_steps(task_brief)

            # Show plan
            steps = task_brief.get("planned_steps", [])
            step_lines = []
            for step in steps:
                dep = f" (after {step['depends_on']})" if step.get("depends_on") else ""
                done = f" → {step['conclusion'][:80]}" if step.get("conclusion") else ""
                step_lines.append(f"  {step['step']}. [{step['type']}] {step['name']}{dep}{done}")
            bot.send_message(cid, f"📋 Plan ({len(steps)} steps):\n" + "\n".join(step_lines))

            project = orch.create_project(task_brief)
            s.current_project_id = project.id

            def on_confirm(plan_text):
                s.state = "confirming_plan"
                s.confirm_event.clear(); s.confirm_result = None
                bot.send_message(cid, f"📋 Ready to execute:\n\n{plan_text}\n\n{t('plan_confirm')}",
                    reply_markup={"inline_keyboard":[[
                        {"text":t("plan_approve_btn"),"callback_data":"approve_plan"},
                        {"text":t("plan_reject_btn"),"callback_data":"reject_plan"}]]})
                s.confirm_event.wait(timeout=300)
                s.state = "executing"
                return s.confirm_result == True

            def on_status(msg):
                bot.send_message(cid, f"⏳ {msg}")

            result = orch.run_cycle(
                project,
                on_confirm=on_confirm if needs_confirm else None,
                on_status=on_status,
            )

            if result == "completed":
                summary = summarize_results(ai, project)
                bot.send_message(cid, t("exec_done", summary=summary))
                s.add_ai(summary)
                _send_feedback_buttons(bot, cid, s, project)
            elif result == "aborted":
                bot.send_message(cid, t("plan_rejected"))
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
            bot.send_message(cid, t("planning_error", error=str(e)[:200]))
        finally:
            if s.state not in ("waiting_user", "waiting_feedback"):
                s.busy = False; s.state = "idle"
            else:
                s.busy = False

    def _send_feedback_buttons(bot, cid, s, project):
        """Send feedback buttons after task completion."""
        tpl_id = f"tpl_{project.id}"
        s.pending_template_id = tpl_id
        bot.send_message(cid, t("feedback_ask"), reply_markup={"inline_keyboard":[[
            {"text": t("feedback_yes_btn"), "callback_data": "feedback_yes"},
            {"text": t("feedback_no_btn"), "callback_data": "feedback_no"}]]})

    def _resume_task(bot, ai, orch, s, project):
        """Resume: re-enter the cycle."""
        cid = s.chat_id
        s.busy = True; s.state = "executing"
        try:
            def on_confirm(plan_text):
                s.state = "confirming_plan"
                s.confirm_event.clear(); s.confirm_result = None
                bot.send_message(cid, f"📋 Resume:\n\n{plan_text}\n\n{t('plan_confirm')}",
                    reply_markup={"inline_keyboard":[[
                        {"text":t("plan_approve_btn"),"callback_data":"approve_plan"},
                        {"text":t("plan_reject_btn"),"callback_data":"reject_plan"}]]})
                s.confirm_event.wait(timeout=300)
                s.state = "executing"
                return s.confirm_result == True

            def on_status(msg):
                bot.send_message(cid, f"⏳ {msg}")

            result = orch.run_cycle(project, on_confirm=on_confirm, on_status=on_status)

            if result == "completed":
                summary = summarize_results(ai, project)
                bot.send_message(cid, t("exec_done", summary=summary)); s.add_ai(summary)
                _send_feedback_buttons(bot, cid, s, project)
            elif result == "waiting_user":
                for n in project.nodes.values():
                    if n.status == NodeStatus.FAILED:
                        det = n.verify_output.get("detail","") if n.verify_output else ""
                        bot.send_message(cid, t("node_problem", name=n.name, detail=det)); break
                s.state = "waiting_user"
            else:
                bot.send_message(cid, t("exec_failed"))
        except Exception as e:
            logger.error(traceback.format_exc())
            bot.send_message(cid, t("exec_error", error=str(e)[:200]))
        finally:
            if s.state not in ("waiting_user", "waiting_feedback"): s.busy=False; s.state="idle"
            else: s.busy=False

    # ======== Start ========
    logger.info("=" * 50)
    logger.info("LittleAnt V13 - Cycle Execution Architecture")
    logger.info(f"Language: {config.get('language','en')} | AI: {config.get('ai_provider','?')}")
    logger.info("=" * 50)
    bot.start_polling()


if __name__ == "__main__":
    main()
