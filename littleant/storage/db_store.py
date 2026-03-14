"""
LittleAnt V13 - Database Storage
Execution logs in DB (streaming data). Template library in DB (needs search).
"""
from __future__ import annotations
import os
import json
import time
import sqlite3
from littleant.config import DB_PATH, DATA_DIR


def _get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
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
    conn.commit()
    conn.close()


# ============================================================
# Execution Logs
# ============================================================

def log_execution(project_id: str, node_id: str, attempt: int,
                  action: str, exec_output: dict = None,
                  verify_output: dict = None, ai_decision: str = None):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO execution_logs (project_id, node_id, attempt, action, execute_output, verify_output, ai_decision) VALUES (?,?,?,?,?,?,?)",
        (project_id, node_id, attempt, action,
         json.dumps(exec_output, ensure_ascii=False) if exec_output else None,
         json.dumps(verify_output, ensure_ascii=False) if verify_output else None,
         ai_decision),
    )
    conn.commit()
    conn.close()


def get_node_logs(project_id: str, node_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM execution_logs WHERE project_id=? AND node_id=? ORDER BY created_at",
        (project_id, node_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# Template Library (Template Library)
# ============================================================

def save_template(template_id: str, name: str, keywords: list[str],
                  tree: dict, nodes: dict):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO templates (id, name, keywords, tree_json, nodes_json, node_count) VALUES (?,?,?,?,?,?)",
        (template_id, name, ",".join(keywords),
         json.dumps(tree, ensure_ascii=False),
         json.dumps(nodes, ensure_ascii=False),
         len(nodes)),
    )
    conn.commit()
    conn.close()


def search_templates(keywords: list[str], limit: int = 10) -> list[dict]:
    """V13 Step 1: Return snapshot list"""
    conn = _get_conn()
    conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
    params = [f"%{kw}%" for kw in keywords]
    rows = conn.execute(
        f"SELECT id, name, node_count, created_at FROM templates WHERE {conditions} ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "nodes": r["node_count"],
             "date": time.strftime("%Y-%m-%d", time.localtime(r["created_at"]))} for r in rows]


def get_template_tree(template_id: str) -> dict | None:
    """V13 Step 2: Return template tree"""
    conn = _get_conn()
    row = conn.execute("SELECT tree_json FROM templates WHERE id=?", (template_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row["tree_json"])


def get_template_nodes(template_id: str, scope: list[str] = None) -> dict | None:
    """V13 Step 3: Return branch commands"""
    conn = _get_conn()
    row = conn.execute("SELECT nodes_json FROM templates WHERE id=?", (template_id,)).fetchone()
    conn.close()
    if not row:
        return None
    all_nodes = json.loads(row["nodes_json"])
    if scope is None:
        return all_nodes
    # Only return branches in scope
    filtered = {}
    for nid, ndata in all_nodes.items():
        for prefix in scope:
            if nid.startswith(prefix):
                filtered[nid] = ndata
                break
    return filtered


# ============================================================
# Tool Library (Tool command library)
# ============================================================

def save_tool(tool_id: str, name: str, description: str, path: str,
              usage: str, keywords: list[str], project_id: str = None,
              tool_type: str = "script"):
    """Save a tool to command library"""
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO tools (id, name, description, tool_type, path, usage, keywords, project_id) VALUES (?,?,?,?,?,?,?,?)",
        (tool_id, name, description, tool_type, path, usage,
         ",".join(keywords), project_id),
    )
    conn.commit()
    conn.close()


def search_tools(keywords: list[str], limit: int = 5) -> list[dict]:
    """Search tool library"""
    conn = _get_conn()
    if not keywords:
        rows = conn.execute(
            "SELECT * FROM tools ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        rows = conn.execute(
            f"SELECT * FROM tools WHERE {conditions} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "description": r["description"],
             "path": r["path"], "usage": r["usage"], "type": r["tool_type"]} for r in rows]


def list_all_tools() -> list[dict]:
    """List all tools"""
    return search_tools([], limit=100)
