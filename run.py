#!/usr/bin/env python3
"""
LittleAnt V12.1 - Telegram Bot (Dual AI Architecture)
Front-end AI: Chat with user, read-only access, can initiate tasks
Back-end AI: Execute commands, read-write access, JSON only
"""
from __future__ import annotations
import json, logging, threading, time, sys, os, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from littleant.i18n import load_language, t
from littleant.telegram_bot import TelegramBot
from littleant.ai.adapter import OpenAICompatibleAdapter, CHAT_PROMPT, EXECUTOR_PROMPT
from littleant.core.orchestrator import Orchestrator
from littleant.core.protocol import build_project_status
from littleant.core.decomposer import DecompositionError
from littleant.core.readonly_executor import run_readonly, is_safe_readonly
from littleant.models.project import Project, ProjectStatus, NodeStatus
from littleant.storage.json_store import save_project, load_project, list_projects
from littleant.storage.db_store import init_db, search_tools, search_templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("littleant")

CHAT_HISTORY_LIMIT = 20

def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "littleant", "config.json")
    if not os.path.exists(config_path):
        print("\n⚠️  No config found. Run setup first:\n   python3 setup.py\n")
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


class UserSession:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.current_project_id = None
        self.busy = False
        self.state = "idle"
        self.pending_task = None
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


CLASSIFY_PROMPT = """You are LittleAnt front-end AI. Classify user intent based on conversation context. Reply JSON only.

Current state: {state}

Intent types (check in priority order):

1. If state=confirming_task:
   {{"intent":"confirm_yes"}} or {{"intent":"confirm_no"}}

2. If state=confirming_plan:
   {{"intent":"confirm_yes"}} or {{"intent":"confirm_no"}}

3. Otherwise:
   - {{"intent":"chat","reply":"natural language answer"}} — chat, questions (DEFAULT)
   - {{"intent":"task","task_description":"one-line description"}} — explicit action request (must have imperative words like "help me" "please" "install" "deploy" "create" "setup" "write" or equivalent in any language)
   - {{"intent":"quick_query","command":"read-only command"}} — needs a command to answer (e.g. "what's in crontab" → crontab -l)
   - {{"intent":"query_status"}} — asking about current task progress
   - {{"intent":"query_history","keywords":"keywords"}} — asking about past projects
   - {{"intent":"skip_node"}} — "skip" "forget it" "never mind"
   - {{"intent":"retry_project"}} — "restart" "retry" "continue"
   - {{"intent":"cancel"}} — "cancel task"

Rules:
- When unsure, choose chat
- "how to do X" "what is X" → chat, NOT task
- "skip" "forget it" → skip_node or confirm_no based on state
- "restart" "retry" → retry_project, NOT new task"""


def classify(ai, s, text):
    state_map = {"idle": "idle", "confirming_task": f"confirming task: {s.pending_task}",
                 "confirming_plan": "confirming execution plan", "executing": "task running in background",
                 "waiting_user": "step failed, waiting for user decision"}
    sd = state_map.get(s.state, "idle")
    if s.current_project_id:
        st = s.status_text()
        if st: sd += f" ({st})"
    msgs = s.chat_history[-10:] + [{"role": "user", "content": text}]
    try:
        return ai.ask(msgs, system_prompt=CLASSIFY_PROMPT.format(state=sd))
    except:
        return {"intent": "chat", "reply": t("sorry_error")}


def summarize_results(ai, project):
    results = []
    for nid in sorted(project.nodes.keys()):
        node = project.nodes[nid]
        if not node.is_leaf or node.status != NodeStatus.COMPLETED: continue
        if node.execute_output:
            stdout = node.execute_output.get("stdout", "")[:500]
            if stdout.strip(): results.append(f"[{node.name}]\n{stdout.strip()}")
    if not results: return f"Project \"{project.name}\" completed."
    req = f"Task \"{project.goal}\" is done. Raw output below. Summarize in plain language for the user. Don't mention nodes or commands.\n\n" + "\n\n".join(results[:10])
    try:
        return ai.ask_text([{"role": "user", "content": req}], system_prompt="Summarize technical output into plain language. Be concise. Match the user's language.")
    except:
        return "\n".join(results[:5])


def query_project_results(ai, project):
    results = []
    for nid in sorted(project.nodes.keys()):
        node = project.nodes[nid]
        if not node.is_leaf: continue
        st = "✅" if node.status == NodeStatus.COMPLETED else "❌" if node.status == NodeStatus.FAILED else "⏭️"
        line = f"{st} {node.name}"
        if node.execute_output and node.execute_output.get("stdout", "").strip():
            line += f"\n  {node.execute_output['stdout'][:200]}"
        results.append(line)
    return "\n".join(results) if results else "No results"


def plan_project(ai, task, tool_ctx="", tpl_ctx=""):
    extra = ""
    if tool_ctx: extra += f"\n\nExisting tools (use directly, don't recreate):\n{tool_ctx}"
    if tpl_ctx: extra += f"\n\nPast projects for reference:\n{tpl_ctx}"
    prompt = f'User request: {task}{extra}\n\nGenerate project plan as pure JSON:\n{{"cmd":"create_project","name":"project name","goal":"one-line goal","children":[{{"id":"1","name":"phase 1","depends_on":[]}},{{"id":"2","name":"phase 2","depends_on":["1"]}}]}}\n\n2-4 phases. Only do what was asked. JSON only.'
    return ai.ask([{"role": "user", "content": prompt}], system_prompt=EXECUTOR_PROMPT)


def main():
    config = load_config()
    load_language(config.get("language", "en"))
    init_db()

    ai = OpenAICompatibleAdapter(
        api_key=config["ai_api_key"],
        base_url=config["ai_base_url"],
        model=config["ai_model"],
    )
    bot = TelegramBot(config["telegram_token"])
    orch = Orchestrator(ai=ai)

    # Set bot menu (replaces any old V8 commands)
    lang = config.get("language", "en")
    if lang == "zh":
        bot.set_menu_commands([
            ("help", "查看帮助"),
            ("status", "查看任务进度"),
            ("cancel", "取消当前任务"),
        ])
    else:
        bot.set_menu_commands([
            ("help", "Show help"),
            ("status", "Task progress"),
            ("cancel", "Cancel current task"),
        ])

    @bot.on_command("start")
    def cmd_start(msg):
        bot.send_message(msg["chat"]["id"], t("bot_welcome", name=msg["from"].get("first_name", "")))

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

    @bot.on_message
    def handle_message(msg):
        cid = msg["chat"]["id"]
        text = msg["text"].strip()
        s = get_session(cid)
        s.add_user(text)
        bot.send_typing(cid)

        result = classify(ai, s, text)
        intent = result.get("intent", "chat")
        logger.info(f"[{cid}] intent={intent} state={s.state}")

        if intent == "chat":
            reply = result.get("reply") or t("sorry_error")
            bot.send_message(cid, reply); s.add_ai(reply)

        elif intent == "quick_query":
            cmd = result.get("command", "")
            safe, reason = is_safe_readonly(cmd) if cmd else (False, "empty")
            if not safe:
                reply = t("readonly_denied", reason=reason)
                bot.send_message(cid, reply); s.add_ai(reply); return
            r = run_readonly(cmd)
            if r["success"]:
                try: reply = ai.ask_text([{"role":"user","content":f"User asked a system question. Output of `{cmd}`:\n\n{r['output'][:3000]}\n\nSummarize concisely. Match user's language."}], system_prompt=CHAT_PROMPT)
                except: reply = r["output"][:2000]
            else: reply = t("query_failed", error=r["error"])
            bot.send_message(cid, reply); s.add_ai(reply)

        elif intent == "query_history":
            kw = result.get("keywords", "")
            projects = list_projects()
            if not projects: reply = t("no_projects")
            else:
                matched = [p for p in projects if any(k in p["name"] for k in kw.split())] if kw else projects[:5]
                if not matched: matched = projects[:5]
                info = "\n".join([f"- {p['name']} [{p['status']}]" for p in matched[:10]])
                try: reply = ai.ask_text([{"role":"user","content":f"User asks about past projects:\n{info}\nSummarize. Match user's language."}], system_prompt=CHAT_PROMPT)
                except: reply = info
            bot.send_message(cid, reply); s.add_ai(reply)

        elif intent == "query_status":
            st = s.status_text()
            if st:
                p = s.get_project()
                detail = query_project_results(ai, p) if p else ""
                try: reply = ai.ask_text([{"role":"user","content":f"User asks task progress. Status: {st}\nDetails:\n{detail}\nSummarize. Match user's language."}], system_prompt=CHAT_PROMPT)
                except: reply = t("task_status", status=st)
            else: reply = t("no_task")
            bot.send_message(cid, reply); s.add_ai(reply)

        elif intent == "task":
            if s.busy:
                bot.send_message(cid, t("busy")); return
            task_desc = result.get("task_description", text)
            s.pending_task = task_desc; s.state = "confirming_task"
            reply = t("confirm_task", task=task_desc)
            bot.send_message(cid, reply, reply_markup={"inline_keyboard":[[
                {"text": t("confirm_task_yes_btn"), "callback_data": "confirm_task_yes"},
                {"text": t("confirm_task_no_btn"), "callback_data": "confirm_task_no"}]]})
            s.add_ai(reply)

        elif intent == "confirm_yes":
            if s.state == "confirming_task" and s.pending_task:
                s.state = "idle"; td = s.pending_task; s.pending_task = None
                bot.send_message(cid, t("planning", task=td)); s.add_ai(t("planning", task=td))
                threading.Thread(target=_plan, args=(bot,ai,orch,s,td), daemon=True).start()
            elif s.state == "confirming_plan" and s.current_project_id:
                s.state = "idle"; p = load_project(s.current_project_id)
                if p:
                    bot.send_message(cid, t("exec_start")); s.add_ai(t("exec_start"))
                    threading.Thread(target=_exec, args=(bot,ai,orch,s,p), daemon=True).start()
            else: bot.send_message(cid, t("no_confirm_pending"))

        elif intent == "confirm_no":
            if s.state in ("confirming_task","confirming_plan"):
                s.state = "idle"; s.pending_task = None
                if s.state == "confirming_plan": s.current_project_id = None
            bot.send_message(cid, t("confirm_no_reply")); s.add_ai(t("confirm_no_reply"))

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

    @bot.on_callback
    def handle_cb(cb):
        cid = cb["message"]["chat"]["id"]; data = cb.get("data",""); bot.answer_callback(cb["id"])
        s = get_session(cid)
        if data == "confirm_task_yes" and s.state == "confirming_task" and s.pending_task:
            s.state="idle"; td=s.pending_task; s.pending_task=None
            bot.send_message(cid, t("planning", task=td))
            threading.Thread(target=_plan, args=(bot,ai,orch,s,td), daemon=True).start()
        elif data == "confirm_task_no":
            s.state="idle"; s.pending_task=None; bot.send_message(cid, t("confirm_no_reply"))
        elif data == "approve_plan" and s.state == "confirming_plan" and s.current_project_id:
            s.state="idle"; p=load_project(s.current_project_id)
            if p: bot.send_message(cid, t("exec_start")); threading.Thread(target=_exec, args=(bot,ai,orch,s,p), daemon=True).start()
        elif data == "reject_plan":
            s.state="idle"; s.current_project_id=None; bot.send_message(cid, t("plan_rejected"))

    def _plan(bot, ai, orch, s, task_desc):
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
        cid = s.chat_id; s.busy=True; s.state="executing"
        try:
            project.status = ProjectStatus.EXECUTING
            for nid in project.get_execution_order():
                node = project.nodes.get(nid)
                if not node or node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED): continue
                if not all(not project.nodes.get(d) or project.nodes[d].status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED) for d in node.depends_on): continue
                result = orch._execute_node(project, node); save_project(project)
                if result == "abort":
                    project.status=ProjectStatus.ABORTED; save_project(project)
                    bot.send_message(cid, t("exec_failed")); s.busy=False; s.state="idle"; return
                elif result == "waiting_user":
                    project.status=ProjectStatus.WAITING_USER; save_project(project)
                    det = node.verify_output.get("detail","") if node.verify_output else ""
                    bot.send_message(cid, t("node_problem", name=node.name, detail=det))
                    s.busy=False; s.state="waiting_user"; return
                time.sleep(0.3)
            project.status=ProjectStatus.COMPLETED; save_project(project)
            summary = summarize_results(ai, project)
            bot.send_message(cid, t("exec_done", summary=summary)); s.add_ai(summary)
        except Exception as e:
            logger.error(traceback.format_exc())
            bot.send_message(cid, t("exec_error", error=str(e)[:200]))
        finally: s.busy=False; s.state="idle"

    logger.info("=" * 50)
    logger.info("LittleAnt V12.1 - Dual AI Architecture")
    logger.info(f"Language: {config.get('language','en')} | AI: {config.get('ai_provider','?')}")
    logger.info("=" * 50)
    bot.start_polling()


if __name__ == "__main__":
    main()
