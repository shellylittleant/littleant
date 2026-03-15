"""LittleAnt V14 - System Configuration"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
DB_PATH = os.path.join(DATA_DIR, "littleant.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

MAX_PATH_DEPTH = 5
MAX_PROJECT_AI_CALLS = 1000
MAX_NODE_RETRIES = 2
MAX_NODE_MODIFICATIONS = 2
MAX_CONSECUTIVE_FAILURES = 5
MAX_LEAF_NODES = 50
MAX_REPLAN_PER_PARENT = 3
MAX_L2_ATTEMPTS = 3
MAX_L3_ATTEMPTS = 2

ALLOWED_EXECUTE_TYPES = ["run_shell", "write_file", "make_dir", "read_file", "http_request"]
ALLOWED_VERIFY_TYPES = [
    "return_code_eq", "file_exists", "content_contains", "service_active",
    "http_status_eq", "json_field_eq", "dns_resolves_to", "port_open",
]
