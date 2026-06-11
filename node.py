from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import xmlrpc.client
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer
from socketserver import ThreadingMixIn

# Fix Windows terminal encoding for Vietnamese characters.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class NodeEndpoint:
    id: str
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def label(self) -> str:
        return f"{self.id}@{self.host}:{self.port}"


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = 1.5, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.timeout = timeout

    def make_connection(self, host: str):  # type: ignore[override]
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


class QuietHandler(SimpleXMLRPCRequestHandler):
    rpc_paths = ("/RPC2", "/")

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


class ThreadedServer(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True
    allow_reuse_address = True


class KeyValueNode:
    """
    Node phân tán lưu trữ key-value.

    Cơ chế chính:
    - Consistent hashing để xác định primary node.
    - Replication sang node kế tiếp trên hash ring.
    - Forward request nếu client gửi vào node không phải primary.
    - Heartbeat phát hiện node sống/chết.
    - Recovery/force-sync khi node khởi động lại.
    """

    def __init__(
        self,
        node_id: str,
        nodes: List[NodeEndpoint],
        replication_factor: int = 2,
        heartbeat_interval: float = 3.0,
        failure_timeout: float = 10.0,
        rpc_timeout: float = 1.5,
    ) -> None:
        self.node_id = node_id
        self.nodes = sorted(nodes, key=lambda n: self._hash_int(n.id))
        self.nodes_by_id = {n.id: n for n in self.nodes}
        if node_id not in self.nodes_by_id:
            raise ValueError(f"Node id '{node_id}' không tồn tại trong cluster config")

        self.endpoint = self.nodes_by_id[node_id]
        self.replication_factor = max(1, min(replication_factor, len(self.nodes)))
        self.heartbeat_interval = heartbeat_interval
        self.failure_timeout = failure_timeout
        self.rpc_timeout = rpc_timeout

        self.data_store: Dict[str, str] = {}      # dữ liệu primary
        self.replica_store: Dict[str, str] = {}   # dữ liệu replica
        self.lock = threading.RLock()

        self.neighbor_ids = [n.id for n in self.nodes if n.id != self.node_id]
        self.last_heartbeat: Dict[str, Optional[float]] = {nid: None for nid in self.neighbor_ids}
        self.node_status: Dict[str, str] = {nid: "UNKNOWN" for nid in self.neighbor_ids}

    @staticmethod
    def _hash_int(value: str) -> int:
        return int(hashlib.md5(value.encode("utf-8")).hexdigest(), 16)

    def _get_primary_node(self, key: str) -> NodeEndpoint:
        key_hash = self._hash_int(key)
        for node in self.nodes:
            if self._hash_int(node.id) >= key_hash:
                return node
        return self.nodes[0]

    def _get_replica_nodes(self, key: str) -> List[NodeEndpoint]:
        if self.replication_factor <= 1:
            return []
        primary = self._get_primary_node(key)
        idx = self.nodes.index(primary)
        replicas: List[NodeEndpoint] = []
        for step in range(1, self.replication_factor):
            replicas.append(self.nodes[(idx + step) % len(self.nodes)])
        return replicas

    def _connect(self, node: NodeEndpoint) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(
            node.url,
            transport=TimeoutTransport(timeout=self.rpc_timeout),
            allow_none=True,
        )

    def _ok_response(self, **extra: Any) -> str:
        payload = {"status": "ok", **extra}
        return json.dumps(payload, ensure_ascii=False)

    def _node_payload(self, node: NodeEndpoint) -> Dict[str, Any]:
        return {"id": node.id, "host": node.host, "port": node.port, "url": node.url}

    def _not_found_response(self, key: str) -> str:
        return json.dumps({"status": "not_found", "key": key, "node": self.node_id}, ensure_ascii=False)

    def _local_get_unlocked(self, key: str) -> Optional[Dict[str, Any]]:
        if key in self.data_store:
            return {"value": self.data_store[key], "role": "primary"}
        if key in self.replica_store:
            return {"value": self.replica_store[key], "role": "replica"}
        return None

    def put(self, key: str, value: str, source: str = "client") -> str:
        key = str(key).strip()
        value = str(value)
        if not key:
            return json.dumps({"status": "error", "message": "Key không được để trống"}, ensure_ascii=False)

        primary = self._get_primary_node(key)
        replicas = self._get_replica_nodes(key)

        # Primary yêu cầu node này lưu bản sao.
        if source == "primary":
            with self.lock:
                self.replica_store[key] = value
                self.data_store.pop(key, None)
            print(f"[{self.node_id}] REPLICA PUT: {key} = {value}")
            return self._ok_response(
                node=self.node_id,
                role="replica",
                primary=primary.id,
                replicas=[r.id for r in replicas],
            )

        # Khi primary down, request có thể được ghi tạm lên replica.
        if source == "replica_forward":
            with self.lock:
                self.replica_store[key] = value
            print(f"[{self.node_id}] FALLBACK REPLICA PUT: {key} = {value}")
            return self._ok_response(
                node=self.node_id,
                role="replica_fallback",
                primary=primary.id,
                replicas=[r.id for r in replicas],
            )

        # Node hiện tại là primary.
        if primary.id == self.node_id:
            with self.lock:
                self.data_store[key] = value
                self.replica_store.pop(key, None)
            print(f"[{self.node_id}] PRIMARY PUT: {key} = {value}")
            self._replicate_put(key, value, replicas)
            return self._ok_response(
                node=self.node_id,
                role="primary",
                primary=primary.id,
                primary_port=primary.port,
                replicas=[r.id for r in replicas],
                replica_ports=[r.port for r in replicas],
            )

        # Node hiện tại không phải primary -> forward sang primary.
        print(f"[{self.node_id}] FORWARD PUT '{key}' -> {primary.label}")
        try:
            return self._connect(primary).put(key, value, "client")
        except Exception as exc:
            print(f"[{self.node_id}] Primary {primary.label} DOWN: {exc}")

        # Nếu primary down, thử ghi vào replica đầu tiên còn phản hồi.
        for replica in replicas:
            if replica.id == self.node_id:
                with self.lock:
                    self.replica_store[key] = value
                return self._ok_response(
                    node=self.node_id,
                    role="replica_fallback",
                    primary=primary.id,
                    replicas=[r.id for r in replicas],
                )
            try:
                return self._connect(replica).put(key, value, "replica_forward")
            except Exception as exc:
                print(f"[{self.node_id}] Replica {replica.label} không phản hồi: {exc}")

        return json.dumps({"status": "error", "message": "Primary và replica đều không phản hồi"}, ensure_ascii=False)

    def _replicate_put(self, key: str, value: str, replicas: List[NodeEndpoint]) -> None:
        def _do(replica: NodeEndpoint) -> None:
            if replica.id == self.node_id:
                return
            try:
                self._connect(replica).put(key, value, "primary")
                print(f"  -> Đã sao lưu '{key}' sang {replica.label}")
            except Exception as exc:
                print(f"  -> {replica.label} DOWN, bỏ qua sao lưu: {exc}")

        for replica in replicas:
            threading.Thread(target=_do, args=(replica,), daemon=True).start()

    # --- GET ---

    def get(self, key: str, source: str = "client") -> str:
        key = str(key).strip()
        if not key:
            return self._not_found_response(key)

        primary = self._get_primary_node(key)
        replicas = self._get_replica_nodes(key)

        if source == "internal":
            with self.lock:
                local = self._local_get_unlocked(key)
            if local is None:
                return self._not_found_response(key)
            print(f"[{self.node_id}] GET {local['role']}: {key} -> {local['value']}")
            return self._ok_response(
                value=local["value"],
                node=self.node_id,
                role=local["role"],
                primary=primary.id,
                replicas=[r.id for r in replicas],
            )

        if primary.id != self.node_id:
            print(f"[{self.node_id}] FORWARD GET '{key}' -> {primary.label}")
            try:
                primary_result = self._connect(primary).get(key, "internal")
                parsed = json.loads(primary_result)
                if parsed.get("status") == "ok":
                    return primary_result
                if parsed.get("status") == "not_found":
                    with self.lock:
                        self.replica_store.pop(key, None)
                    return self._not_found_response(key)
            except Exception as exc:
                print(f"[{self.node_id}] Primary get failed, trying replica fallback: {exc}")

            if any(r.id == self.node_id for r in replicas):
                with self.lock:
                    local = self._local_get_unlocked(key)
                if local is not None:
                    print(f"[{self.node_id}] GET fallback {local['role']}: {key} -> {local['value']}")
                    return self._ok_response(
                        value=local["value"],
                        node=self.node_id,
                        role=local["role"],
                        primary=primary.id,
                        replicas=[r.id for r in replicas],
                    )

            for replica in replicas:
                if replica.id == self.node_id:
                    continue
                try:
                    result = self._connect(replica).get(key, "internal")
                    parsed = json.loads(result)
                    if parsed.get("status") == "ok":
                        return result
                except Exception:
                    continue

            return self._not_found_response(key)

        with self.lock:
            local = self._local_get_unlocked(key)
        if local is not None:
            print(f"[{self.node_id}] GET {local['role']}: {key} -> {local['value']}")
            return self._ok_response(
                value=local["value"],
                node=self.node_id,
                role=local["role"],
                primary=primary.id,
                replicas=[r.id for r in replicas],
            )

        return self._not_found_response(key)

    def delete(self, key: str, source: str = "client") -> str:
        key = str(key).strip()
        if not key:
            return self._not_found_response(key)

        primary = self._get_primary_node(key)
        replicas = self._get_replica_nodes(key)

        if source == "primary":
            with self.lock:
                removed_replica = self.replica_store.pop(key, None)
                removed_primary = self.data_store.pop(key, None)
            found = removed_replica is not None or removed_primary is not None
            print(f"[{self.node_id}] REPLICA DELETE: {key} ({'found' if found else 'not found'})")
            return json.dumps(
                {"status": "ok" if found else "not_found", "node": self.node_id, "role": "replica"},
                ensure_ascii=False,
            )

        if primary.id == self.node_id:
            with self.lock:
                removed_primary = self.data_store.pop(key, None)
                removed_replica = self.replica_store.pop(key, None)
            print(f"[{self.node_id}] PRIMARY DELETE: {key}")
            deleted_on_replica = self._delete_from_replicas(key, replicas)
            found = removed_primary is not None or removed_replica is not None or deleted_on_replica
            return json.dumps(
                {
                    "status": "ok" if found else "not_found",
                    "node": self.node_id,
                    "role": "primary",
                    "primary": primary.id,
                    "replicas": [r.id for r in replicas],
                },
                ensure_ascii=False,
            )

        print(f"[{self.node_id}] FORWARD DELETE '{key}' -> {primary.label}")
        try:
            result = self._connect(primary).delete(key, "client")
            parsed = json.loads(result)
            if parsed.get("status") in {"ok", "not_found"}:
                with self.lock:
                    stale_replica = self.replica_store.pop(key, None)
                if parsed.get("status") == "not_found" and stale_replica is not None:
                    parsed.update(
                        {
                            "status": "ok",
                            "message": "Deleted stale local replica",
                            "node": self.node_id,
                            "role": "replica",
                        }
                    )
                    return json.dumps(parsed, ensure_ascii=False)
            return result
        except Exception as exc:
            print(f"[{self.node_id}] Primary delete failed, trying replica fallback: {exc}")

        deleted_any = False
        for replica in replicas:
            if replica.id == self.node_id:
                with self.lock:
                    removed_replica = self.replica_store.pop(key, None)
                    removed_primary = self.data_store.pop(key, None)
                deleted_any = deleted_any or removed_replica is not None or removed_primary is not None
                continue
            try:
                result = json.loads(self._connect(replica).delete(key, "primary"))
                if result.get("status") == "ok":
                    deleted_any = True
            except Exception:
                continue

        return json.dumps(
            {
                "status": "ok" if deleted_any else "error",
                "message": "Primary down, attempted delete on replicas",
                "node": self.node_id,
                "primary": primary.id,
                "replicas": [r.id for r in replicas],
            },
            ensure_ascii=False,
        )

    def _delete_from_replicas(self, key: str, replicas: List[NodeEndpoint]) -> bool:
        deleted_any = False
        for replica in replicas:
            if replica.id == self.node_id:
                continue
            try:
                result = json.loads(self._connect(replica).delete(key, "primary"))
                if result.get("status") == "ok":
                    deleted_any = True
                print(f"  -> Delete replica '{key}' on {replica.label}: {result.get('status')}")
            except Exception as exc:
                print(f"  -> Cannot delete replica '{key}' on {replica.label}: {exc}")
        return deleted_any

    def get_all_data(self) -> str:
        with self.lock:
            return json.dumps({"primary": dict(self.data_store), "replica": dict(self.replica_store)}, ensure_ascii=False)

    def get_node_info(self) -> str:
        with self.lock:
            return json.dumps(
                {
                    "id": self.node_id,
                    "node_id": self.node_id,
                    "host": self.endpoint.host,
                    "port": self.endpoint.port,
                    "url": self.endpoint.url,
                    "all_nodes": [self._node_payload(n) for n in self.nodes],
                    "all_ports": [n.port for n in self.nodes],
                    "primary_count": len(self.data_store),
                    "replica_count": len(self.replica_store),
                    "primary_keys": list(self.data_store.keys()),
                    "replica_keys": list(self.replica_store.keys()),
                    "neighbors": self.neighbor_ids,
                    "node_status": dict(self.node_status),
                    "replication_factor": self.replication_factor,
                },
                ensure_ascii=False,
            )

    def get_routing_info(self, key: str) -> str:
        primary = self._get_primary_node(key)
        replicas = self._get_replica_nodes(key)
        return json.dumps(
            {
                "key": key,
                "primary": self._node_payload(primary),
                "primary_port": primary.port,
                "replicas": [self._node_payload(r) for r in replicas],
                "replica_port": replicas[0].port if replicas else None,
            },
            ensure_ascii=False,
        )

    # --- Heartbeat ---

    def heartbeat(self, from_node_id: str) -> bool:
        with self.lock:
            self.last_heartbeat[from_node_id] = time.time()
            old = self.node_status.get(from_node_id)
            self.node_status[from_node_id] = "ALIVE"
            if old == "DEAD":
                print(f"  [{self.node_id}] {from_node_id} ĐÃ HỒI PHỤC")
            elif old != "ALIVE":
                print(f"  [{self.node_id}] {from_node_id} ONLINE")
        return True

    def _send_heartbeat_loop(self) -> None:
        while True:
            for nid in self.neighbor_ids:
                node = self.nodes_by_id[nid]
                try:
                    self._connect(node).heartbeat(self.node_id)
                except Exception:
                    pass
            time.sleep(self.heartbeat_interval)

    def _detect_failures_loop(self) -> None:
        while True:
            now = time.time()
            with self.lock:
                for nid in self.neighbor_ids:
                    last = self.last_heartbeat.get(nid)
                    if last is None:
                        continue
                    if now - last > self.failure_timeout and self.node_status.get(nid) == "ALIVE":
                        self.node_status[nid] = "DEAD"
                        print(f"  [{self.node_id}] {nid} BỊ MẤT KẾT NỐI")
            time.sleep(2)

    # --- Recovery ---

    def _sync_data_on_startup(self) -> None:
        print("Dang dong bo du lieu tu cluster...")
        primary_candidates: Dict[str, str] = {}
        replica_candidates: Dict[str, str] = {}
        synced_from: List[str] = []

        for nid in self.neighbor_ids:
            node = self.nodes_by_id[nid]
            try:
                raw = self._connect(node).get_all_data()
                data = json.loads(raw)
            except Exception as exc:
                print(f"  -> {node.label} not available: {exc}")
                continue

            synced_from.append(node.id)
            for key, value in data.get("replica", {}).items():
                replica_candidates.setdefault(str(key), str(value))
            for key, value in data.get("primary", {}).items():
                primary_candidates[str(key)] = str(value)

        if not synced_from:
            print("  -> No online node found. Starting with empty data.")
            return

        combined: Dict[str, str] = dict(replica_candidates)
        combined.update(primary_candidates)

        with self.lock:
            for key, value in combined.items():
                primary = self._get_primary_node(key)
                replicas = self._get_replica_nodes(key)
                if primary.id == self.node_id:
                    self.data_store[key] = value
                    self.replica_store.pop(key, None)
                elif any(r.id == self.node_id for r in replicas):
                    self.replica_store[key] = value
                    self.data_store.pop(key, None)

            primary_items = list(self.data_store.items())

        for key, value in primary_items:
            self._replicate_put(key, value, self._get_replica_nodes(key))

        print(
            f"  -> Synced from {', '.join(synced_from)}: "
            f"primary={len(self.data_store)}, replica={len(self.replica_store)}"
        )

    def force_sync(self) -> str:
        with self.lock:
            self.data_store.clear()
            self.replica_store.clear()
        self._sync_data_on_startup()
        return self._ok_response(primary=len(self.data_store), replica=len(self.replica_store), node=self.node_id)


def load_config(path: str) -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file config: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_nodes_from_config(config: Dict[str, Any]) -> List[NodeEndpoint]:
    nodes = []
    for item in config.get("nodes", []):
        nodes.append(NodeEndpoint(id=str(item["id"]), host=str(item["host"]), port=int(item["port"])))
    if not nodes:
        raise ValueError("cluster_config.json phải có danh sách nodes")
    return nodes


def legacy_config_from_ports(ports: List[int]) -> Dict[str, Any]:
    return {
        "nodes": [{"id": f"node{i + 1}", "host": "127.0.0.1", "port": port} for i, port in enumerate(ports)],
        "replication_factor": 2,
        "heartbeat_interval": 3,
        "failure_timeout": 10,
        "rpc_timeout": 1.5,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chạy một node Key-Value phân tán")
    parser.add_argument("legacy_ports", nargs="*", help="Cách cũ: python node.py 8000 8001 8002")
    parser.add_argument("--node", "--node-id", dest="node_id", default=os.environ.get("NODE_ID"), help="ID node trong cluster_config.json, ví dụ: node1")
    parser.add_argument("--config", default=os.environ.get("CLUSTER_CONFIG", "cluster_config.json"), help="Đường dẫn file cấu hình cluster")
    parser.add_argument("--bind-host", default=os.environ.get("BIND_HOST", "127.0.0.1"), help="IP bind server. VM/Docker nên dùng 0.0.0.0")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    # Legacy mode để không phá cách chạy cũ.
    if args.legacy_ports and not args.node_id:
        ports = [int(p) for p in args.legacy_ports]
        my_port = ports[0]
        config = legacy_config_from_ports(ports)
        nodes = parse_nodes_from_config(config)
        node_id = next(n.id for n in nodes if n.port == my_port)
        bind_host = "127.0.0.1"
    else:
        if not args.node_id:
            parser.error("Cần truyền --node node1 hoặc dùng cách cũ: python node.py 8000 8001 8002")
        config = load_config(args.config)
        nodes = parse_nodes_from_config(config)
        node_id = args.node_id
        bind_host = args.bind_host

    endpoint = {n.id: n for n in nodes}[node_id]
    server = ThreadedServer((bind_host, endpoint.port), requestHandler=QuietHandler, allow_none=True)
    node = KeyValueNode(
        node_id=node_id,
        nodes=nodes,
        replication_factor=int(config.get("replication_factor", 2)),
        heartbeat_interval=float(config.get("heartbeat_interval", 3)),
        failure_timeout=float(config.get("failure_timeout", 10)),
        rpc_timeout=float(config.get("rpc_timeout", 1.5)),
    )
    server.register_instance(node)

    print(f"{node_id} đang lắng nghe tại {bind_host}:{endpoint.port}")
    print(f"   Địa chỉ quảng bá trong cluster: {endpoint.url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
