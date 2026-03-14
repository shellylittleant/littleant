"""
LittleAnt V13 - Database Storage
Execution logs in DB. Template library with user feedback. Tool library.
"""
from __future__ import annotations
import json, os, sqlite3, time, logging

logger = logging.getLogger(__name__)

def _get_conn():
    from littleant.config import DB_PATH
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS execution_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            attempt INTEGER DEFAULT 1,
            action TEXT,
            execute_output TEXT,
            verify_output TEXT,
            ai_decision TEXT,
            created_at REAL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            keywords TEXT,
            tree_json TEXT NOT NULL,
            nodes_json TEXT NOT NULL,
            node_count INTEGER DEFAULT 0,
            task_types TEXT DEFAULT '',
            user_rating INTEGER DEFAULT 0,
            user_feedback TEXT DEFAULT '',
            created_at REAL DEFAULT (strftime('%s','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_logs_project ON execution_logs(project_id);
        CREATE INDEX IF NOT EXISTS idx_logs_node ON execution_logs(project_id, node_id);
        CREATE INDEX IF NOT EXISTS idx_templates_keywords ON templates(keywords);

        CREATE TABLE IF NOT EXISTS tools (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            tool_type TEXT DEFAULT 'script',
            path TEXT,
            usage TEXT,
            keywords TEXT,
            project_id TEXT,
            created_at REAL DEFAULT (strftime('%s','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_tools_keywords ON tools(keywords);
    """)
    # Migration: add columns if missing (for existing DBs)
    try: conn.execute("ALTER TABLE templates ADD COLUMN user_rating INTEGER DEFAULT 0")
    except: pass
    try: conn.execute("ALTER TABLE templates ADD COLUMN user_feedback TEXT DEFAULT ''")
    except: pass
    try: conn.execute("ALTER TABLE templates ADD COLUMN task_types TEXT DEFAULT ''")
    except: pass
    conn.commit()
    conn.close()


# ============================================================
# Execution Logs
# ============================================================

def log_execution(project_id, node_id, attempt, action, exec_output=None,
                  verify_output=None, ai_decision=None):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO execution_logs (project_id, node_id, attempt, action, execute_output, verify_output, ai_decision) VALUES (?,?,?,?,?,?,?)",
        (project_id, node_id, attempt, action,
         json.dumps(exec_output, ensure_ascii=False) if exec_output else None,
         json.dumps(verify_output, ensure_ascii=False) if verify_output else None,
         ai_decision))
    conn.commit(); conn.close()


def get_node_logs(project_id, node_id):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM execution_logs WHERE project_id=? AND node_id=? ORDER BY created_at",
        (project_id, node_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# Template Library (with user feedback)
# ============================================================

def save_template(template_id, name, keywords, tree, nodes, task_types=None):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO templates (id, name, keywords, tree_json, nodes_json, node_count, task_types) VALUES (?,?,?,?,?,?,?)",
        (template_id, name, ",".join(keywords),
         json.dumps(tree, ensure_ascii=False),
         json.dumps(nodes, ensure_ascii=False),
         len(nodes), ",".join(task_types or [])))
    conn.commit(); conn.close()


def update_template_feedback(template_id, rating, feedback=""):
    """Save user rating (5=satisfied, 1=unsatisfied) and feedback text."""
    conn = _get_conn()
    conn.execute(
        "UPDATE templates SET user_rating=?, user_feedback=? WHERE id=?",
        (rating, feedback, template_id))
    conn.commit(); conn.close()
    logger.info(f"Template {template_id} feedback: rating={rating}")


def search_templates(keywords, limit=10):
    conn = _get_conn()
    if not keywords:
        rows = conn.execute(
            "SELECT id, name, node_count, task_types, user_rating, user_feedback, created_at FROM templates ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
    else:
        conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        rows = conn.execute(
            f"SELECT id, name, node_count, task_types, user_rating, user_feedback, created_at FROM templates WHERE {conditions} ORDER BY created_at DESC LIMIT ?",
            params + [limit]).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "nodes": r["node_count"],
             "types": r["task_types"] or "", "rating": r["user_rating"] or 0,
             "feedback": r["user_feedback"] or "",
             "date": time.strftime("%Y-%m-%d", time.localtime(r["created_at"]))} for r in rows]


def search_history_for_context(keywords, limit=5):
    """Search history and return structured context for AI.
    Returns successful cases (rating=5) and failed cases (rating=1) separately."""
    all_results = search_templates(keywords, limit=limit*2)
    success_cases = []
    failure_cases = []
    for r in all_results:
        if r["rating"] >= 4:
            success_cases.append(r)
        elif r["rating"] == 1 and r["feedback"]:
            failure_cases.append(r)
    return success_cases[:limit], failure_cases[:limit]


def get_template_tree(template_id):
    conn = _get_conn()
    row = conn.execute("SELECT tree_json FROM templates WHERE id=?", (template_id,)).fetchone()
    conn.close()
    return json.loads(row["tree_json"]) if row else None


def get_template_nodes(template_id, scope=None):
    conn = _get_conn()
    row = conn.execute("SELECT nodes_json FROM templates WHERE id=?", (template_id,)).fetchone()
    conn.close()
    if not row: return None
    all_nodes = json.loads(row["nodes_json"])
    if scope is None: return all_nodes
    filtered = {}
    for nid, ndata in all_nodes.items():
        for prefix in scope:
            if nid.startswith(prefix): filtered[nid] = ndata; break
    return filtered


# ============================================================
# Tool Library
# ============================================================

def save_tool(tool_id, name, description, path, usage, keywords,
              project_id=None, tool_type="script"):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO tools (id, name, description, tool_type, path, usage, keywords, project_id) VALUES (?,?,?,?,?,?,?,?)",
        (tool_id, name, description, tool_type, path, usage,
         ",".join(keywords), project_id))
    conn.commit(); conn.close()


def search_tools(keywords, limit=5):
    conn = _get_conn()
    if not keywords:
        rows = conn.execute("SELECT * FROM tools ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    else:
        conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        rows = conn.execute(
            f"SELECT * FROM tools WHERE {conditions} ORDER BY created_at DESC LIMIT ?",
            params + [limit]).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "description": r["description"],
             "path": r["path"], "usage": r["usage"], "type": r["tool_type"]} for r in rows]


def list_all_tools():
    return search_tools([], limit=100)
