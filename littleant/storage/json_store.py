"""
LittleAnt V13 - JSON Storage
Project tree and execution chain stored as JSON files.
"""
from __future__ import annotations
import os
import json
from littleant.models.project import Project
from littleant.config import PROJECTS_DIR


def ensure_dirs():
    os.makedirs(PROJECTS_DIR, exist_ok=True)


def save_project(project: Project):
    """Save project to JSON file"""
    ensure_dirs()
    path = os.path.join(PROJECTS_DIR, f"{project.id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)


def load_project(project_id: str) -> Project | None:
    """Load project"""
    path = os.path.join(PROJECTS_DIR, f"{project_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Project.from_dict(data)


def list_projects() -> list[dict]:
    """List all projects (summary)"""
    ensure_dirs()
    projects = []
    for fname in os.listdir(PROJECTS_DIR):
        if fname.endswith(".json"):
            path = os.path.join(PROJECTS_DIR, fname)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            projects.append({
                "id": data["id"],
                "name": data["name"],
                "status": data["status"],
                "nodes": len(data.get("nodes", {})),
            })
    return projects


def delete_project(project_id: str):
    """Delete project"""
    path = os.path.join(PROJECTS_DIR, f"{project_id}.json")
    if os.path.exists(path):
        os.remove(path)
