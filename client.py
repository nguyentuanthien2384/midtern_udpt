
from __future__ import annotations

import argparse
import json
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def run_client(endpoint: Dict[str, Any]) -> None:
    url = f"http://{endpoint['host']}:{int(endpoint['port'])}"
    try:
        node = connect_node(endpoint)
        info = json.loads(node.get_node_info())
        print(f"[OK] Đã kết nối tới {info.get('id', endpoint.get('id'))} tại {url}")
    except Exception as e:
        print(f"[ERROR] Không kết nối được node tại {url}: {e}")
        return

    while True:
        print("\n===============================")
        print("     ARS STORE CLIENT CLI      ")
        print("===============================")
        print("  1. PUT    - Lưu dữ liệu")
        print("  2. GET    - Đọc dữ liệu")
        print("  3. DELETE - Xóa dữ liệu")
        print("  4. INFO   - Thông tin node")
        print("  5. ROUTE  - Xem key thuộc node nào")
        print("  6. Thoát")
        print("===============================")
        choice = input("Chọn lệnh (1-6): ").strip()

        try:
            if choice == "1":
                k = input("  Key: ").strip()
                v = input("  Value: ").strip()
                result = json.loads(node.put(k, v))
                if result.get("status") == "ok":
                    print(f"  [OK] Đã lưu! Node xử lý: {result.get('node')} ({result.get('role')})")
                    print(f"       Primary: {result.get('primary')} | Replica(s): {result.get('replicas')}")
                else:
                    print(f"  [ERROR] {result.get('message', 'Unknown')}")

            elif choice == "2":
                k = input("  Key: ").strip()
                result = json.loads(node.get(k))
                if result.get("status") == "ok":
                    print(f"  [OK] Value: {result.get('value')}")
                    print(f"       Node: {result.get('node')} ({result.get('role')})")
                else:
                    print(f"  [ERROR] Không tìm thấy key '{k}'")

            elif choice == "3":
                k = input("  Key cần xóa: ").strip()
                result = json.loads(node.delete(k))
                if result.get("status") == "ok":
                    print("  [OK] Đã xóa thành công!")
                else:
                    print(f"  [ERROR] {result.get('message', 'Key không tồn tại hoặc lỗi')}")

            elif choice == "4":
                print_json_result(json.loads(node.get_node_info()))

            elif choice == "5":
                k = input("  Key cần kiểm tra routing: ").strip()
                print_json_result(json.loads(node.get_routing_info(k)))

            elif choice == "6":
                print("Tạm biệt!")
                break
            else:
                print("  [WARN] Vui lòng chọn từ 1 đến 6")

        except KeyboardInterrupt:
            print("\nTạm biệt!")
            break
        except Exception as e:
            print(f"  [ERROR] Lỗi: {e}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Client CLI cho KV Store phân tán")
    parser.add_argument("legacy_port", nargs="?", help="Cách cũ: python client.py 8000")
    parser.add_argument("--node", help="Node ID trong config, ví dụ node1")
    parser.add_argument("--config", default="cluster_config.json", help="File cấu hình cluster")
    parser.add_argument("--host", help="Host tùy chọn nếu dùng legacy_port")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    endpoint = resolve_endpoint(args)
    run_client(endpoint)
