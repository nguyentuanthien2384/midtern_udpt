"""
Test nhanh cluster local hoặc VM.
Yêu cầu: đã chạy các node trước.

Local:
  python test_cluster.py --config cluster_config.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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
        return json.load(f)["nodes"]


def connect(node: Dict[str, Any]) -> xmlrpc.client.ServerProxy:
    return xmlrpc.client.ServerProxy(
        f"http://{node['host']}:{int(node['port'])}",
        transport=TimeoutTransport(timeout=2.0),
        allow_none=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="cluster_config.json")
    args = parser.parse_args()

    nodes = load_nodes(args.config)
    proxies = {str(n["id"]): connect(n) for n in nodes}

    print("[1] Kiểm tra trạng thái node")
    for node in nodes:
        info = json.loads(proxies[str(node["id"])].get_node_info())
        print(f"  - {info['id']}: ONLINE, primary={info['primary_count']}, replica={info['replica_count']}")

    key = f"test_vm_{int(time.time())}"
    value = "hello_distributed_vm"
    entry = nodes[0]
    print(f"[2] PUT {key}={value} qua {entry['id']}")
    put_result = json.loads(proxies[str(entry["id"])].put(key, value))
    print("  ", put_result)
    assert put_result["status"] == "ok"

    time.sleep(1)
    print("[3] GET key từ tất cả node")
    found = 0
    for node in nodes:
        result = json.loads(proxies[str(node["id"])].get(key))
        print(f"  - {node['id']}: {result}")
        if result.get("status") == "ok" and result.get("value") == value:
            found += 1
    assert found >= 1, "Không node nào đọc được dữ liệu vừa PUT"

    print("[4] DELETE key")
    del_result = json.loads(proxies[str(entry["id"])].delete(key))
    print("  ", del_result)
    assert del_result["status"] in ("ok", "not_found")

    print("\n✅ Test cluster hoàn tất.")


if __name__ == "__main__":
    main()
