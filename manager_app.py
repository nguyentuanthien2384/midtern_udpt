"""
Management Web App cho hệ thống Key-Value phân tán.

Local:
  python manager_app.py

VM/multi-machine:
  python manager_app.py --config cluster_config.json --host 0.0.0.0 --port 5000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = 1.0, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.timeout = timeout

    def make_connection(self, host: str):  # type: ignore[override]
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


app = Flask(__name__)

CLUSTER_CONFIG_PATH = os.environ.get("CLUSTER_CONFIG", "cluster_config.json")
CLUSTER_NODES: List[Dict[str, Any]] = []
NODES_BY_ID: Dict[str, Dict[str, Any]] = {}
RPC_TIMEOUT = 1.0


def load_cluster_config(path: str) -> None:
    global CLUSTER_CONFIG_PATH, CLUSTER_NODES, NODES_BY_ID, RPC_TIMEOUT
    CLUSTER_CONFIG_PATH = path
    config_path = Path(path)
    if not config_path.exists():
        # Fallback để chạy ngay khi thiếu config.
        CLUSTER_NODES = [
            {"id": "node1", "host": "127.0.0.1", "port": 8000},
            {"id": "node2", "host": "127.0.0.1", "port": 8001},
            {"id": "node3", "host": "127.0.0.1", "port": 8002},
        ]
        NODES_BY_ID = {str(n["id"]): n for n in CLUSTER_NODES}
        return

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    CLUSTER_NODES = config.get("nodes", [])
    RPC_TIMEOUT = float(config.get("rpc_timeout", 1.0))
    NODES_BY_ID = {str(n["id"]): n for n in CLUSTER_NODES}


def node_url(node: Dict[str, Any]) -> str:
    return f"http://{node['host']}:{int(node['port'])}"


def resolve_node(node_ref: Optional[Any]) -> Dict[str, Any]:
    if node_ref is None or node_ref == "":
        if not CLUSTER_NODES:
            raise ValueError("Cluster chưa có node nào")
        return CLUSTER_NODES[0]

    ref = str(node_ref)
    if ref in NODES_BY_ID:
        return NODES_BY_ID[ref]

    # Tương thích code cũ: frontend/client cũ gửi port.
    for node in CLUSTER_NODES:
        if str(node.get("port")) == ref:
            return node

    raise ValueError(f"Không tìm thấy node '{ref}' trong cluster config")


def connect_node(node_ref: Any) -> xmlrpc.client.ServerProxy:
    node = resolve_node(node_ref)
    return xmlrpc.client.ServerProxy(
        node_url(node),
        transport=TimeoutTransport(timeout=RPC_TIMEOUT),
        allow_none=True,
    )


def safe_node_public(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": node.get("id"),
        "node_id": node.get("id"),
        "host": node.get("host"),
        "port": int(node.get("port")),
        "url": node_url(node),
        "label": f"{node.get('id')} — {node.get('host')}:{int(node.get('port'))}",
    }


# ─── Web Pages ────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─── API: Cluster nodes ───────────────────────────────

@app.route("/api/cluster/nodes")
def cluster_nodes():
    return jsonify([safe_node_public(n) for n in CLUSTER_NODES])


# ─── API: Cluster Status ─────────────────────────────

@app.route("/api/cluster/status")
def cluster_status():
    nodes = []
    for node_cfg in CLUSTER_NODES:
        node_id = str(node_cfg.get("id"))
        try:
            proxy = connect_node(node_id)
            info = json.loads(proxy.get_node_info())
            info["status"] = "ONLINE"
            info["id"] = info.get("id") or node_id
            info["node_id"] = info.get("node_id") or node_id
            info["host"] = info.get("host") or node_cfg.get("host")
            info["port"] = int(info.get("port") or node_cfg.get("port"))
            info["url"] = node_url(node_cfg)
            nodes.append(info)
        except Exception:
            nodes.append(
                {
                    "id": node_id,
                    "node_id": node_id,
                    "host": node_cfg.get("host"),
                    "port": int(node_cfg.get("port")),
                    "url": node_url(node_cfg),
                    "status": "OFFLINE",
                    "primary_count": 0,
                    "replica_count": 0,
                    "primary_keys": [],
                    "replica_keys": [],
                    "node_status": {},
                }
            )
    return jsonify(nodes)


# ─── API: PUT ─────────────────────────────────────────

@app.route("/api/put", methods=["POST"])
def api_put():
    data = request.json or {}
    key = data.get("key", "").strip()
    value = data.get("value", "")
    target = data.get("node_id") or data.get("node") or data.get("port")

    if not key:
        return jsonify({"status": "error", "message": "Key không được để trống"}), 400

    try:
        proxy = connect_node(target)
        result = json.loads(proxy.put(key, value))
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Node không phản hồi: {e}"}), 500


# ─── API: GET ─────────────────────────────────────────

@app.route("/api/get", methods=["POST"])
def api_get():
    data = request.json or {}
    key = data.get("key", "").strip()
    target = data.get("node_id") or data.get("node") or data.get("port")

    if not key:
        return jsonify({"status": "error", "message": "Key không được để trống"}), 400

    try:
        proxy = connect_node(target)
        result = json.loads(proxy.get(key))
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Node không phản hồi: {e}"}), 500


# ─── API: DELETE ──────────────────────────────────────

@app.route("/api/delete", methods=["POST"])
def api_delete():
    data = request.json or {}
    key = data.get("key", "").strip()
    target = data.get("node_id") or data.get("node") or data.get("port")

    if not key:
        return jsonify({"status": "error", "message": "Key không được để trống"}), 400

    try:
        proxy = connect_node(target)
        result = json.loads(proxy.delete(key))
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Node không phản hồi: {e}"}), 500
