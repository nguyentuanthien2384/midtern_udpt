"""
Client CLI cho hệ thống Key-Value phân tán.

Ví dụ chạy local:
  python client.py 8000
  python client.py --node node1 --config cluster_config.json

Ví dụ chạy qua máy ảo:
  python client.py --node node2 --config cluster_config.json
"""
from __future__ import annotations

import argparse
import json
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any, Dict, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = 2.0, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.timeout = timeout

    def make_connection(self, host: str):  # type: ignore[override]
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


def load_nodes(config_path: str) -> List[Dict[str, Any]]:
    with Path(config_path).open("r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get("nodes", [])


def resolve_endpoint(args: argparse.Namespace) -> Dict[str, Any]:
    # Cách cũ: python client.py 8000
    if args.legacy_port:
        return {"id": f"node:{args.legacy_port}", "host": args.host or "127.0.0.1", "port": int(args.legacy_port)}

    nodes = load_nodes(args.config)
    if not nodes:
        raise SystemExit("Không tìm thấy nodes trong config")

    node_id = args.node or nodes[0]["id"]
    for node in nodes:
        if str(node["id"]) == str(node_id):
            return node
    raise SystemExit(f"Không tìm thấy node '{node_id}' trong {args.config}")


def connect_node(endpoint: Dict[str, Any]) -> xmlrpc.client.ServerProxy:
    url = f"http://{endpoint['host']}:{int(endpoint['port'])}"
    return xmlrpc.client.ServerProxy(url, transport=TimeoutTransport(timeout=2.0), allow_none=True)


def print_json_result(result: Dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))
