"""
LittleAnt V13 - Project & Node Models
Project tree and node state machine.
"""
from __future__ import annotations
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class NodeStatus(str, Enum):
    """Node states: pending→decomposing→ready→executing→verifying→completed/failed"""
    PENDING = "pending"
    DECOMPOSING = "decomposing"
    READY = "ready"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProjectStatus(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    PAUSED = "paused"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class ExecuteSpec:
    """Executable command specification"""
    type: str           # run_shell, write_file, etc.
    command: Optional[str] = None
    path: Optional[str] = None
    content: Optional[str] = None
    url: Optional[str] = None
    method: Optional[str] = None
    body: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {"type": self.type}
        for k in ("command", "path", "content", "url", "method", "body"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ExecuteSpec:
        return cls(**{k: d.get(k) for k in ("type", "command", "path", "content", "url", "method", "body")})


@dataclass
class VerifySpec:
    """Verification specification"""
    type: str           # return_code_eq, file_exists, etc.
    command: Optional[str] = None
    expected_code: Optional[int] = None
    path: Optional[str] = None
    keyword: Optional[str] = None
    service_name: Optional[str] = None
    url: Optional[str] = None
    expected_status: Optional[int] = None
    source: Optional[str] = None
    field_path: Optional[str] = None
    expected: Optional[str] = None
    domain: Optional[str] = None
    expected_ip: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"type": self.type}
        for k, v in self.__dict__.items():
            if k != "type" and v is not None:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> VerifySpec:
        fields = cls.__dataclass_fields__
        return cls(**{k: d.get(k) for k in fields})


@dataclass
class Node:
    """A node in the project tree"""
    id: str
    name: str
    parent_id: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    children: list[str] = field(default_factory=list)  # child node ids

    # Leaf nodes only
    execute: Optional[ExecuteSpec] = None
    verify: Optional[VerifySpec] = None
    on_fail: str = "report"
    node_type: str = "modify"  # query / create / modify

    # Execution records
    execute_output: Optional[dict] = None
    verify_output: Optional[dict] = None
    retry_count: int = 0
    modify_count: int = 0
    updated_at: float = field(default_factory=time.time)

    @property
    def is_leaf(self) -> bool:
        return self.execute is not None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "parent_id": self.parent_id,
            "depends_on": self.depends_on,
            "children": self.children,
            "on_fail": self.on_fail,
            "node_type": self.node_type,
            "retry_count": self.retry_count,
            "modify_count": self.modify_count,
        }
        if self.execute:
            d["execute"] = self.execute.to_dict()
        if self.verify:
            d["verify"] = self.verify.to_dict()
        if self.execute_output:
            d["execute_output"] = self.execute_output
        if self.verify_output:
            d["verify_output"] = self.verify_output
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Node:
        n = cls(
            id=d["id"],
            name=d["name"],
            parent_id=d.get("parent_id"),
            depends_on=d.get("depends_on", []),
            status=NodeStatus(d.get("status", "pending")),
            children=d.get("children", []),
            on_fail=d.get("on_fail", "report"),
            node_type=d.get("node_type", "modify"),
            retry_count=d.get("retry_count", 0),
            modify_count=d.get("modify_count", 0),
        )
        if "execute" in d and d["execute"]:
            n.execute = ExecuteSpec.from_dict(d["execute"])
        if "verify" in d and d["verify"]:
            n.verify = VerifySpec.from_dict(d["verify"])
        n.execute_output = d.get("execute_output")
        n.verify_output = d.get("verify_output")
        return n


@dataclass
class Project:
    """Project"""
    id: str
    name: str
    goal: str
    status: ProjectStatus = ProjectStatus.PLANNING
    nodes: dict[str, Node] = field(default_factory=dict)  # node_id -> Node
    root_children: list[str] = field(default_factory=list)  # top-level node ids
    ai_call_count: int = 0
    consecutive_failures: int = 0
    task_brief: dict = field(default_factory=dict)  # Task specification document
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_node(self, node: Node):
        self.nodes[node.id] = node
        if node.parent_id is None:
            if node.id not in self.root_children:
                self.root_children.append(node.id)
        else:
            parent = self.nodes.get(node.parent_id)
            if parent and node.id not in parent.children:
                parent.children.append(node.id)

    def get_tree_summary(self) -> dict:
        """Generate project tree summary (for node_failed feedback)"""
        summary = {}
        for nid, node in self.nodes.items():
            status_mark = ""
            if node.status == NodeStatus.COMPLETED:
                status_mark = " done"
            elif node.status == NodeStatus.FAILED:
                status_mark = " FAILED"
            elif node.status == NodeStatus.SKIPPED:
                status_mark = " skipped"
            summary[nid] = f"{node.name}{status_mark}"
        return summary

    def get_execution_order(self) -> list[str]:
        """Return leaf nodes in dependency order (topological sort)"""
        leaves = [nid for nid, n in self.nodes.items() if n.is_leaf]
        visited = set()
        order = []

        def visit(nid: str):
            if nid in visited or nid not in self.nodes:
                return
            node = self.nodes[nid]
            for dep in node.depends_on:
                visit(dep)
            visited.add(nid)
            if node.is_leaf:
                order.append(nid)

        for nid in leaves:
            visit(nid)
        return order

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "status": self.status.value,
            "root_children": self.root_children,
            "ai_call_count": self.ai_call_count,
            "consecutive_failures": self.consecutive_failures,
            "task_brief": self.task_brief,
            "created_at": self.created_at,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> Project:
        p = cls(
            id=d["id"],
            name=d["name"],
            goal=d["goal"],
            status=ProjectStatus(d.get("status", "planning")),
            root_children=d.get("root_children", []),
            ai_call_count=d.get("ai_call_count", 0),
            consecutive_failures=d.get("consecutive_failures", 0),
            task_brief=d.get("task_brief", {}),
            created_at=d.get("created_at", time.time()),
        )
        for nid, nd in d.get("nodes", {}).items():
            p.nodes[nid] = Node.from_dict(nd)
        return p
