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


def main() -> None:
    nodes = [
        NodeEndpoint(id="node1", host="127.0.0.1", port=8000),
        NodeEndpoint(id="node2", host="127.0.0.1", port=8001),
        NodeEndpoint(id="node3", host="127.0.0.1", port=8002),
    ]
    node = KeyValueNode(node_id="node1", nodes=nodes, replication_factor=2)

    key = "day4_key"
    primary = node._get_primary_node(key)
    replicas = node._get_replica_nodes(key)

    result = json.loads(node.put("k1", "v1"))
    print("PUT flow with forwarding and replication helper added.")
    print(f"PUT status: {result.get('status')}")
    print(f"PUT role: {result.get('role')}")


    print(" routing/hash helper methods added.")
    print(f"Primary for '{key}': {primary.label}")
    print(f"Replicas: {[r.label for r in replicas]}")

if __name__ == "__main__":
    main()
