from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import sys
import threading
import time
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
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
    - Consistent hashing với VIRTUAL NODES để xác định primary node.
    - Mỗi node vật lý được rải thành nhiều virtual node trên hash ring nên
      khoảng (partition) giữa các node đều hơn -> tải phân bố cân bằng hơn.
    - Replication sang các node vật lý kế tiếp (khác nhau) trên hash ring.
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
        data_dir: Optional[str] = None,
        vnodes_per_node: int = 256,
        fresh: bool = False,
    ) -> None:
        self.node_id = node_id
        self.nodes = sorted(nodes, key=lambda n: self._hash_int(n.id))
        self.nodes_by_id = {n.id: n for n in self.nodes}
        if node_id not in self.nodes_by_id:
            raise ValueError(f"Node id '{node_id}' không tồn tại trong cluster config")

        self.endpoint = self.nodes_by_id[node_id]
        self.replication_factor = max(1, min(replication_factor, len(self.nodes)))
        # Số virtual node trên hash ring cho mỗi node vật lý (cải tiến phân bố).
        self.vnodes_per_node = max(1, int(vnodes_per_node))
        self._build_ring()
        self.heartbeat_interval = heartbeat_interval
        self.failure_timeout = failure_timeout
        self.rpc_timeout = rpc_timeout
        self.server: Optional[ThreadedServer] = None

        self.data_store: Dict[str, str] = {}      # dữ liệu primary
        self.replica_store: Dict[str, str] = {}   # dữ liệu replica
        self.lock = threading.RLock()
        self._rebalance_lock = threading.Lock()   # chống chạy nhiều rebalance song song

        # Lưu trữ bền vững xuống đĩa để dữ liệu sống sót khi node tắt/khởi động lại.
        base_dir = Path(data_dir) if data_dir else Path(__file__).with_name("node_data")
        base_dir.mkdir(parents=True, exist_ok=True)
        self.data_file = base_dir / f"{self.node_id}.json"

        # Khôi phục node: bỏ dữ liệu cũ trên đĩa (có thể đã lỗi thời) và lấy lại
        # dữ liệu HIỆN HÀNH từ cụm -> tránh ghi đè dữ liệu mới bằng bản cũ.
        self._fresh_start = bool(fresh)
        if self._fresh_start:
            try:
                if self.data_file.exists():
                    self.data_file.unlink()
            except Exception:
                pass

        self.neighbor_ids = [n.id for n in self.nodes if n.id != self.node_id]
        self.last_heartbeat: Dict[str, Optional[float]] = {nid: None for nid in self.neighbor_ids}
        self.node_status: Dict[str, str] = {nid: "UNKNOWN" for nid in self.neighbor_ids}

        print(f"=== {self.endpoint.label} ĐANG KHỞI ĐỘNG ===")
        print("Cluster:")
        for n in self.nodes:
            role = " (this)" if n.id == self.node_id else ""
            print(f"  - {n.label}{role}")
        print(f"Replication factor: {self.replication_factor}")
        print(
            f"Hash ring: {self.vnodes_per_node} virtual node/node vật lý "
            f"-> tổng {len(self._ring)} điểm trên ring"
        )

        if not self._fresh_start:
            self._load_from_disk()
        self._sync_data_on_startup()
        threading.Thread(target=self._send_heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._detect_failures_loop, daemon=True).start()

        print(f"=== {self.endpoint.label} SẴN SÀNG ===")
        print(f"Primary keys: {list(self.data_store.keys())}")
        print(f"Replica keys: {list(self.replica_store.keys())}")

    def attach_server(self, server: ThreadedServer) -> None:
        self.server = server

    # --- Hash ring (virtual nodes) / routing ---

    @staticmethod
    def _hash_int(value: str) -> int:
        return int(hashlib.md5(value.encode("utf-8")).hexdigest(), 16)

    @staticmethod
    def _vnode_key(node_id: str, replica_index: int) -> str:
        """Tên định danh của một virtual node trên ring, ví dụ 'node1#7'."""
        return f"{node_id}#{replica_index}"

    def _build_ring(self) -> None:
        """Dựng hash ring với virtual node.

        Mỗi node vật lý được rải thành `vnodes_per_node` điểm (virtual node) trên
        vòng băm. Nhờ vậy mỗi node chiếm nhiều cung nhỏ rải đều thay vì một cung
        lớn duy nhất, giúp dữ liệu phân bố cân bằng hơn (đúng theo lý thuyết
        consistent hashing + virtual nodes).
        """
        ring: List[tuple[int, str]] = []
        for node in self.nodes:
            for i in range(self.vnodes_per_node):
                ring.append((self._hash_int(self._vnode_key(node.id, i)), node.id))
        ring.sort(key=lambda item: item[0])
        self._ring = ring
        self._ring_hashes = [h for h, _ in ring]

    def _ring_index_for(self, key: str) -> int:
        """Vị trí trên ring (theo chiều kim đồng hồ) của virtual node phụ trách key."""
        key_hash = self._hash_int(key)
        idx = bisect.bisect_left(self._ring_hashes, key_hash)
        if idx == len(self._ring_hashes):
            idx = 0  # vượt quá điểm cuối -> quay vòng về đầu ring
        return idx

    def _get_primary_node(self, key: str) -> NodeEndpoint:
        if not self._ring:
            return self.nodes[0]
        node_id = self._ring[self._ring_index_for(key)][1]
        return self.nodes_by_id[node_id]

    def _get_replica_nodes(self, key: str) -> List[NodeEndpoint]:
        if self.replication_factor <= 1 or len(self.nodes) <= 1:
            return []
        needed = self.replication_factor - 1
        start = self._ring_index_for(key)
        primary_id = self._ring[start][1]
        seen = {primary_id}
        replicas: List[NodeEndpoint] = []
        ring_len = len(self._ring)
        # Đi tiếp theo chiều kim đồng hồ, gom các NODE VẬT LÝ khác nhau làm replica.
        for step in range(1, ring_len):
            node_id = self._ring[(start + step) % ring_len][1]
            if node_id in seen:
                continue
            seen.add(node_id)
            replicas.append(self.nodes_by_id[node_id])
            if len(replicas) >= needed:
                break
        return replicas

    def _live_owners(self, key: str, count: int) -> List[NodeEndpoint]:
        """Đi theo chiều kim đồng hồ, gom `count` NODE VẬT LÝ còn SỐNG phụ trách key.

        Bỏ qua các node đang bị đánh dấu DEAD (luôn coi chính node này là sống).
        Dùng để nhân bản BÙ khi một chủ của key bị chết, nhằm khôi phục đủ số bản.
        """
        if not self._ring:
            return [self.nodes[0]]
        start = self._ring_index_for(key)
        seen = set()
        owners: List[NodeEndpoint] = []
        ring_len = len(self._ring)
        for step in range(ring_len):
            nid = self._ring[(start + step) % ring_len][1]
            if nid in seen:
                continue
            seen.add(nid)
            if nid != self.node_id and self.node_status.get(nid) == "DEAD":
                continue  # bỏ node chết
            owners.append(self.nodes_by_id[nid])
            if len(owners) >= count:
                break
        return owners

    def _build_ring_for(self, node_ids: List[str]):
        """Dựng ring cho một danh sách node bất kỳ (dùng cho so sánh scale up/down)."""
        ring = []
        for nid in node_ids:
            for i in range(self.vnodes_per_node):
                ring.append((self._hash_int(self._vnode_key(nid, i)), nid))
        ring.sort(key=lambda x: x[0])
        return [h for h, _ in ring], [o for _, o in ring]

    def _primary_on(self, key: str, hashes: List[int], owners: List[str]) -> str:
        idx = bisect.bisect_left(hashes, self._hash_int(key))
        if idx == len(hashes):
            idx = 0
        return owners[idx]

    def _remap_comparison(self, sample: int = 1500) -> Dict[str, Any]:
        """So sánh % key phải PHÂN BỐ LẠI khi thêm/bớt 1 node:
        consistent hashing (cách của hệ thống) vs hash % N (cách ngây thơ).

        Đây chính là luận điểm trung tâm của bài viết tham khảo. Kết quả được
        cache theo danh sách node (chỉ đổi khi config đổi) nên không tốn CPU.
        """
        base = [n.id for n in self.nodes]
        cache_key = tuple(base)
        cached = getattr(self, "_remap_cache", None)
        if cached and cached.get("_key") == cache_key:
            return {k: v for k, v in cached.items() if k != "_key"}

        n = len(base)
        keys = [f"__cmp__{i}" for i in range(sample)]
        hb, ob = self._build_ring_for(base)
        base_ch = [self._primary_on(k, hb, ob) for k in keys]
        base_mod = [base[self._hash_int(k) % n] for k in keys] if n else []

        # --- Bớt 1 node (trung bình trên mọi node bị bớt) ---
        ch_rm = mod_rm = 0
        if n > 1:
            for d in base:
                after = [x for x in base if x != d]
                ha, oa = self._build_ring_for(after)
                m = len(after)
                for j, k in enumerate(keys):
                    if self._primary_on(k, ha, oa) != base_ch[j]:
                        ch_rm += 1
                    if after[self._hash_int(k) % m] != base_mod[j]:
                        mod_rm += 1
            ch_rm /= (n * sample)
            mod_rm /= (n * sample)

        # --- Thêm 1 node ---
        after = base + ["__new_node__"]
        ha, oa = self._build_ring_for(after)
        m = len(after)
        ch_add = sum(1 for j, k in enumerate(keys) if self._primary_on(k, ha, oa) != base_ch[j]) / sample
        mod_add = sum(1 for j, k in enumerate(keys) if after[self._hash_int(k) % m] != base_mod[j]) / sample

        result = {
            "sample": sample,
            "nodes": n,
            "remove": {"consistent": round(ch_rm * 100, 1), "modulo": round(mod_rm * 100, 1)},
            "add": {"consistent": round(ch_add * 100, 1), "modulo": round(mod_add * 100, 1)},
        }
        self._remap_cache = {**result, "_key": cache_key}
        return result

    def _alive_node_ids(self) -> set:
        """Tập node đang được coi là SỐNG theo heartbeat.

        Node hiện tại luôn sống. Hàng xóm chỉ bị coi là chết khi heartbeat đã
        xác nhận DEAD; trạng thái UNKNOWN (mới khởi động, chưa nhận heartbeat)
        vẫn coi là sống để tránh báo chết nhầm.
        """
        alive = {self.node_id}
        for nid in self.neighbor_ids:
            if self.node_status.get(nid) != "DEAD":
                alive.add(nid)
        return alive

    def _ring_distribution(self) -> Dict[str, Any]:
        """Thống kê phần keyspace mỗi node phụ trách trên ring (để demo độ đều).

        Có kèm trạng thái sống/chết của từng node và phần keyspace "hiệu dụng"
        sau khi đã loại các node chết (keyspace của node chết được dồn cho node
        sống kế tiếp theo chiều kim đồng hồ — đúng kịch bản 'xoá server' của
        consistent hashing). Nhờ đó biểu đồ trên Web UI phản ánh được sự cố
        thật, không còn là hình tĩnh.
        """
        total_space = 1 << 128  # md5 -> 128 bit
        share: Dict[str, int] = {n.id: 0 for n in self.nodes}
        ring_len = len(self._ring)
        for i in range(ring_len):
            cur_hash, node_id = self._ring[i]
            prev_hash = self._ring[i - 1][0]  # i=0 -> phần tử cuối (quay vòng)
            arc = (cur_hash - prev_hash) % total_space
            share[node_id] += arc

        alive_ids = self._alive_node_ids()

        # Phân bố HIỆU DỤNG: dồn cung của node chết sang node sống kế tiếp.
        eff_share: Dict[str, int] = {nid: 0 for nid in alive_ids}
        if alive_ids and ring_len:
            for i in range(ring_len):
                cur_hash, node_id = self._ring[i]
                prev_hash = self._ring[i - 1][0]
                arc = (cur_hash - prev_hash) % total_space
                owner = node_id
                if owner not in alive_ids:
                    for step in range(1, ring_len + 1):
                        cand = self._ring[(i + step) % ring_len][1]
                        if cand in alive_ids:
                            owner = cand
                            break
                eff_share[owner] = eff_share.get(owner, 0) + arc

        def _status(nid: str) -> str:
            if nid == self.node_id:
                return "ALIVE"
            return "DEAD" if self.node_status.get(nid) == "DEAD" else "ALIVE"

        any_dead = any(_status(n.id) == "DEAD" for n in self.nodes)

        return {
            "vnodes_per_node": self.vnodes_per_node,
            "ring_points": ring_len,
            "any_dead": any_dead,
            "alive_count": len(alive_ids),
            "nodes": [
                {
                    "id": n.id,
                    "vnodes": self.vnodes_per_node,
                    "status": _status(n.id),
                    "keyspace_percent": round(share[n.id] / total_space * 100, 2),
                    # Phần keyspace thực sự gánh sau khi loại node chết.
                    "effective_percent": round(eff_share.get(n.id, 0) / total_space * 100, 2),
                }
                for n in self.nodes
            ],
        }

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

    def _store_degraded_local(self, key: str, value: str) -> str:
        # Chế độ suy giảm: khi toàn bộ tuyến primary/replica không phản hồi,
        # vẫn nhận ghi cục bộ để node còn sống tiếp tục phục vụ.
        with self.lock:
            self.replica_store[key] = value
            self._persist()
        print(f"[{self.node_id}] DEGRADED LOCAL PUT: {key} = {value}")
        return self._ok_response(
            node=self.node_id,
            role="degraded_local",
            primary=self._get_primary_node(key).id,
            replicas=[r.id for r in self._get_replica_nodes(key)],
            degraded=True,
        )

    # --- PUT ---

    def put(self, key: str, value: str, source: str = "client") -> str:
        key = str(key).strip()
        value = str(value)
        if not key:
            return json.dumps({"status": "error", "message": "Key không được để trống"}, ensure_ascii=False)

        primary = self._get_primary_node(key)
        replicas = self._get_replica_nodes(key)

        if source == "primary":
            with self.lock:
                self.replica_store[key] = value
                self.data_store.pop(key, None)
                self._persist()
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
                self._persist()
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
                self._persist()
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
            # Trả kết quả nhanh trong chế độ suy giảm, không chờ retry mạng kéo dài.
            return self._store_degraded_local(key, value)

        # Nếu primary down, ưu tiên ghi cục bộ ngay để giảm timeout phía client.
        if self.node_id in {r.id for r in replicas}:
            return self._store_degraded_local(key, value)

        # Nếu primary down, thử ghi vào replica đầu tiên còn phản hồi.
        for replica in replicas:
            if replica.id == self.node_id:
                return self._store_degraded_local(key, value)
            try:
                return self._connect(replica).put(key, value, "replica_forward")
            except Exception as exc:
                print(f"[{self.node_id}] Replica {replica.label} không phản hồi: {exc}")

        # Không còn node nào phản hồi -> vẫn ghi local để đảm bảo availability.
        return self._store_degraded_local(key, value)

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

    def _last_resort_scan(self, key: str) -> Optional[str]:
        """Quét mọi node khác (source=internal) để tìm bản orphan/degraded khi
        toàn bộ định tuyến primary/replica đã trượt — ví dụ ngay sau khi thêm
        node, key chưa kịp di trú về chủ mới. An toàn vì DELETE đã quét toàn cụm
        nên key đã xóa sẽ không xuất hiện ở đâu để 'sống lại'.
        """
        for nid in self.neighbor_ids:
            try:
                res = self._connect(self.nodes_by_id[nid]).get(key, "internal")
                if json.loads(res).get("status") == "ok":
                    print(f"[{self.node_id}] GET last-resort scan tìm thấy '{key}' ở {nid}")
                    return res
            except Exception:
                continue
        return None

    def _client_not_found(self, key: str) -> str:
        found = self._last_resort_scan(key)
        return found if found is not None else self._not_found_response(key)

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

        # Ưu tiên đọc local nếu node này đang giữ replica/degraded copy.
        with self.lock:
            local = self._local_get_unlocked(key)
        if local is not None:
            print(f"[{self.node_id}] GET local-first {local['role']}: {key} -> {local['value']}")
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
                    return self._client_not_found(key)
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

            return self._client_not_found(key)

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

        return self._client_not_found(key)

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
                self._persist()
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
                self._persist()
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

        with self.lock:
            local_removed_primary = self.data_store.pop(key, None)
            local_removed_replica = self.replica_store.pop(key, None)
            local_deleted = local_removed_primary is not None or local_removed_replica is not None
            if local_deleted:
                self._persist()
            primary_marked_dead = self.node_status.get(primary.id) == "DEAD"

        # Chỉ trả nhanh khi heartbeat đã xác nhận primary chết.
        # Nếu primary còn sống thì PHẢI forward để xóa bản chính.
        if local_deleted and primary_marked_dead:
            return json.dumps(
                {
                    "status": "ok",
                    "message": "Deleted local copy (degraded mode)",
                    "node": self.node_id,
                    "role": "degraded_local",
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
                    if stale_replica is not None:
                        self._persist()
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
                if parsed.get("status") == "not_found" and local_deleted:
                    parsed.update(
                        {
                            "status": "ok",
                            "message": "Deleted local degraded copy",
                            "node": self.node_id,
                            "role": "degraded_local",
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
                    self._persist()
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
                "status": "ok" if (deleted_any or local_deleted) else "error",
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

    # --- Lưu trữ bền vững (đĩa) ---

    def _persist(self) -> None:
        """Ghi toàn bộ data_store + replica_store xuống đĩa (atomic)."""
        try:
            with self.lock:
                payload = {
                    "primary": dict(self.data_store),
                    "replica": dict(self.replica_store),
                }
            tmp = self.data_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.data_file)
        except Exception as exc:
            print(f"[{self.node_id}] WARN: khong ghi duoc file luu tru: {exc}")

    def _load_from_disk(self) -> None:
        """Nạp lại dữ liệu từ đĩa lúc khởi động, đặt vào đúng store theo hash ring."""
        if not self.data_file.exists():
            print(f"[{self.node_id}] Khong co file luu tru -> bat dau rong")
            return
        try:
            raw = json.loads(self.data_file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[{self.node_id}] WARN: file luu tru hong, bo qua: {exc}")
            return

        combined: Dict[str, str] = {}
        for key, value in raw.get("primary", {}).items():
            combined[str(key)] = str(value)
        for key, value in raw.get("replica", {}).items():
            combined.setdefault(str(key), str(value))

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
                else:
                    # Giữ lại bản local đã ghi trong lúc cluster suy giảm để tránh mất dữ liệu.
                    self.replica_store[key] = value
                    self.data_store.pop(key, None)
        print(
            f"[{self.node_id}] Da nap tu dia: "
            f"primary={len(self.data_store)}, replica={len(self.replica_store)}"
        )

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
                    "vnodes_per_node": self.vnodes_per_node,
                    "ring_points": len(self._ring),
                },
                ensure_ascii=False,
            )

    def get_ring_info(self) -> str:
        """Trả về cấu trúc hash ring (virtual nodes) + phân bố keyspace.

        Dùng cho Web UI minh họa cách consistent hashing rải node trên vòng băm
        và mức độ cân bằng tải giữa các node.
        """
        total_space = 1 << 128
        alive_ids = self._alive_node_ids()
        points = [
            {
                "angle": round(h / total_space * 360, 4),
                "node": node_id,
                "alive": node_id in alive_ids,
            }
            for h, node_id in self._ring
        ]
        return json.dumps(
            {
                "status": "ok",
                "node": self.node_id,
                "distribution": self._ring_distribution(),
                "remap": self._remap_comparison(),
                "points": points,
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
        recovered = False
        with self.lock:
            self.last_heartbeat[from_node_id] = time.time()
            old = self.node_status.get(from_node_id)
            self.node_status[from_node_id] = "ALIVE"
            if old == "DEAD":
                recovered = True
                print(f"  [{self.node_id}] {from_node_id} ĐÃ HỒI PHỤC")
            elif old != "ALIVE":
                print(f"  [{self.node_id}] {from_node_id} ONLINE")
        if recovered:
            # Node vừa sống lại -> tái phân phối dữ liệu về đúng chủ sở hữu.
            threading.Thread(target=self._rebalance_after_recovery, args=(from_node_id,), daemon=True).start()
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
            newly_dead = []
            with self.lock:
                for nid in self.neighbor_ids:
                    last = self.last_heartbeat.get(nid)
                    if last is None:
                        continue
                    if now - last > self.failure_timeout and self.node_status.get(nid) == "ALIVE":
                        self.node_status[nid] = "DEAD"
                        newly_dead.append(nid)
                        print(f"  [{self.node_id}] {nid} BỊ MẤT KẾT NỐI")
            # Node vừa chết -> nhân bản bù để khôi phục đủ số bản trên các node sống.
            for nid in newly_dead:
                threading.Thread(target=self._replicate_after_failure, args=(nid,), daemon=True).start()
            # Khi cụm khỏe mạnh, dọn các bản sao thừa (bản bù tạm còn sót sau hồi phục).
            if not newly_dead:
                threading.Thread(target=self._reconcile_strays, daemon=True).start()
            time.sleep(2)

    def _reconcile_strays(self) -> None:
        """Dọn bản sao thừa khi CỤM KHỎE MẠNH (không có node nào DEAD).

        Key mà node này không còn là primary/replica theo ring (vd bản nhân-bù tạm
        tạo ra lúc một node chết) sẽ được đẩy về chủ thật rồi xóa, đưa mỗi key về
        đúng replication_factor bản. Trong lúc có node chết thì KHÔNG dọn (giữ dự
        phòng). Nhờ vậy hệ thống tự hội tụ về trạng thái đúng sau mỗi sự cố.
        """
        with self.lock:
            if any(s == "DEAD" for s in self.node_status.values()):
                return
            snapshot = {**self.replica_store, **self.data_store}
        if not snapshot:
            return
        to_clean = []
        for key, value in snapshot.items():
            primary = self._get_primary_node(key)
            replicas = self._get_replica_nodes(key)
            owner_ids = {primary.id} | {r.id for r in replicas}
            if self.node_id not in owner_ids:
                to_clean.append((key, value, primary))
        if not to_clean:
            return
        if not self._rebalance_lock.acquire(blocking=False):
            return
        try:
            cleaned = 0
            for key, value, primary in to_clean:
                try:
                    chk = json.loads(self._connect(primary).get(key, "internal"))
                    if chk.get("status") != "ok":
                        self._connect(primary).put(key, value, "client")
                    with self.lock:
                        self.data_store.pop(key, None)
                        self.replica_store.pop(key, None)
                    cleaned += 1
                except Exception:
                    continue  # chủ chưa sẵn sàng -> để lần sau dọn
            if cleaned:
                self._persist()
                print(f"[{self.node_id}] DỌN {cleaned} bản sao thừa (cụm khỏe mạnh)")
        finally:
            self._rebalance_lock.release()

    def _rebalance_after_recovery(self, recovered_node_id: str) -> None:
        """Tái phân phối dữ liệu khi một node sống lại.

        - Key mà node này là primary: re-replicate sang các replica (node hồi phục
          có thể là replica đang thiếu dữ liệu).
        - Key mà node này giữ tạm (degraded copy, không phải primary/replica đúng):
          đẩy về primary rồi xóa bản tạm.
        - Key mà node này là replica đúng: đảm bảo primary có dữ liệu (chỉ đẩy khi
          primary chưa có để không ghi đè giá trị mới hơn).
        """
        if not self._rebalance_lock.acquire(blocking=False):
            return
        try:
            # Chờ node hồi phục ổn định (xong sync khởi động của chính nó).
            time.sleep(1.0)
            with self.lock:
                snapshot = {**self.replica_store, **self.data_store}
            if not snapshot:
                return

            pushed = 0
            cleaned = 0
            for key, value in snapshot.items():
                primary = self._get_primary_node(key)
                replicas = self._get_replica_nodes(key)
                replica_ids = {r.id for r in replicas}

                if primary.id == self.node_id:
                    # Mình là primary -> bơm lại bản sao cho replica vừa hồi phục.
                    if recovered_node_id in replica_ids:
                        self._replicate_put(key, value, replicas)
                        pushed += 1
                    continue

                is_proper_replica = self.node_id in replica_ids
                try:
                    check = json.loads(self._connect(primary).get(key, "internal"))
                    if check.get("status") != "ok":
                        self._connect(primary).put(key, value, "client")
                        pushed += 1
                except Exception:
                    # Primary chưa sẵn sàng -> giữ bản local, lần hồi phục sau xử lý tiếp.
                    continue

                if not is_proper_replica:
                    with self.lock:
                        self.data_store.pop(key, None)
                        self.replica_store.pop(key, None)
                    cleaned += 1

            if pushed or cleaned:
                self._persist()
                print(
                    f"[{self.node_id}] REBALANCE sau khi {recovered_node_id} hồi phục: "
                    f"đẩy {pushed} key, dọn {cleaned} bản tạm"
                )
        finally:
            self._rebalance_lock.release()

    def _replicate_after_failure(self, dead_node_id: str) -> None:
        """Nhân bản BÙ khi một node bị chết (ALIVE -> DEAD).

        Mỗi key vốn có 1 bản trên node vừa chết sẽ chỉ còn (replication_factor - 1)
        bản sống -> mất dự phòng. Node nào đang giữ bản sống của key đó sẽ đẩy thêm
        một bản sang node SỐNG kế tiếp trên ring, khôi phục đủ replication_factor bản.

        Bản bù này là tạm: khi node chết hồi phục, `_rebalance_after_recovery` sẽ
        dọn lại cho đúng chủ theo ring. Nhờ vậy dữ liệu không bị mất nếu thêm một
        node nữa chết trong lúc node kia chưa kịp sống lại.
        """
        if self.replication_factor <= 1:
            return
        if not self._rebalance_lock.acquire(blocking=False):
            return
        try:
            time.sleep(0.5)
            with self.lock:
                snapshot = {**self.replica_store, **self.data_store}
            if not snapshot:
                return

            pushed = 0
            for key, value in snapshot.items():
                primary = self._get_primary_node(key)
                replicas = self._get_replica_nodes(key)
                ring_owner_ids = {primary.id} | {r.id for r in replicas}
                if dead_node_id not in ring_owner_ids:
                    continue  # key này không mất bản nào trên node chết

                live = self._live_owners(key, self.replication_factor)
                live_ids = {o.id for o in live}
                if self.node_id not in live_ids:
                    continue  # node khác chịu trách nhiệm key này

                # Bảo đảm mọi chủ-sống đều có bản; chỉ đẩy cho node nào còn thiếu.
                for o in live:
                    if o.id == self.node_id:
                        continue
                    try:
                        chk = json.loads(self._connect(o).get(key, "internal"))
                        if chk.get("status") != "ok":
                            self._connect(o).put(key, value, "primary")
                            pushed += 1
                    except Exception:
                        continue

            if pushed:
                self._persist()
                print(f"[{self.node_id}] NHÂN BẢN BÙ sau khi {dead_node_id} chết: thêm {pushed} bản sao sống")
        finally:
            self._rebalance_lock.release()

    def _sync_data_on_startup(self) -> None:
        print("Dang dong bo du lieu tu cluster...")
        primary_candidates: Dict[str, str] = {}
        replica_candidates: Dict[str, str] = {}
        synced_from: List[str] = []

        # Chỉ hỏi các hàng xóm KHÔNG bị đánh dấu DEAD -> không phải chờ timeout
        # vì một node đã chết (nguyên nhân chính gây "Lỗi đồng bộ: timed out").
        targets = [nid for nid in self.neighbor_ids if self.node_status.get(nid) != "DEAD"]
        skipped = [nid for nid in self.neighbor_ids if nid not in targets]
        for nid in skipped:
            print(f"  -> bo qua {nid} (DEAD)")

        def _pull(nid: str):
            node = self.nodes_by_id[nid]
            try:
                raw = self._connect(node).get_all_data()
                return nid, json.loads(raw)
            except Exception as exc:
                print(f"  -> {node.label} not available: {exc}")
                return nid, None

        # Hỏi song song để tổng thời gian không cộng dồn theo số node.
        results: List[tuple] = []
        if targets:
            with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
                futures = [ex.submit(_pull, nid) for nid in targets]
                for fut in futures:
                    try:
                        results.append(fut.result(timeout=self.rpc_timeout + 1.0))
                    except Exception:
                        continue

        for nid, data in results:
            if data is None:
                continue
            synced_from.append(nid)
            for key, value in data.get("replica", {}).items():
                replica_candidates.setdefault(str(key), str(value))
            for key, value in data.get("primary", {}).items():
                primary_candidates[str(key)] = str(value)

        if not synced_from:
            print("  -> No online node found. Starting with empty data.")
            return

        # Tập key cần xét = dữ liệu từ hàng xóm + dữ liệu đang giữ cục bộ
        # (gồm cả bản nạp từ đĩa) -> nhờ vậy mới DỌN được các bản sao thừa.
        combined: Dict[str, str] = dict(replica_candidates)
        combined.update(primary_candidates)
        with self.lock:
            for key, value in self.replica_store.items():
                combined.setdefault(str(key), str(value))
            for key, value in self.data_store.items():
                combined[str(key)] = str(value)  # bản primary cục bộ ưu tiên

        stray: List[tuple] = []
        with self.lock:
            self.data_store.clear()
            self.replica_store.clear()
            for key, value in combined.items():
                primary = self._get_primary_node(key)
                replicas = self._get_replica_nodes(key)
                if primary.id == self.node_id:
                    self.data_store[key] = value
                elif any(r.id == self.node_id for r in replicas):
                    self.replica_store[key] = value
                else:
                    # Node này KHÔNG phải chủ của key theo ring -> không được giữ.
                    # Đẩy về chủ thật rồi bỏ bản cục bộ, đúng nguyên tắc consistent
                    # hashing: mỗi key chỉ nằm trên primary + replica do ring chỉ
                    # định (đúng replication_factor), không có bản thừa.
                    stray.append((key, value, primary))

            primary_items = list(self.data_store.items())

        # Đẩy bản thừa về primary thật (best-effort). Nếu primary không liên lạc
        # được (đang chết) thì giữ tạm để tránh mất dữ liệu (degraded persistence).
        for key, value, primary in stray:
            try:
                self._connect(primary).put(key, value, "client")
            except Exception:
                with self.lock:
                    self.replica_store[key] = value

        # Bảo đảm key mình làm primary được nhân bản đủ cho replica do ring chỉ định.
        for key, value in primary_items:
            self._replicate_put(key, value, self._get_replica_nodes(key))

        self._persist()
        print(
            f"  -> Synced from {', '.join(synced_from)}: "
            f"primary={len(self.data_store)}, replica={len(self.replica_store)}"
        )

    def force_sync(self) -> str:
        # Nạp lại từ đĩa trước rồi mới đồng bộ mạng -> không bao giờ xóa sạch
        # dữ liệu cục bộ ngay cả khi các node khác đang offline.
        with self.lock:
            self.data_store.clear()
            self.replica_store.clear()
        self._load_from_disk()
        self._sync_data_on_startup()
        self._persist()
        return self._ok_response(primary=len(self.data_store), replica=len(self.replica_store), node=self.node_id)

    # --- Membership động (thêm/xóa node lúc đang chạy) ---

    def apply_membership(self, nodes_json: Any) -> str:
        """Cập nhật DANH SÁCH NODE của cluster khi đang chạy (thêm/xóa node).

        Dựng lại hash ring theo membership mới rồi DI TRÚ dữ liệu cục bộ cho
        đúng chủ sở hữu mới. Nhờ tính chất consistent hashing, chỉ khoảng k/n
        key phải di chuyển (đúng luận điểm của bài viết tham khảo).

        - Key node này không còn là primary/replica -> đẩy sang chủ mới rồi xóa.
        - Key node này vẫn giữ -> đảm bảo nằm đúng store (primary/replica).
        - Nếu chính node này bị loại khỏi membership -> đẩy hết dữ liệu đi (drain).
        """
        try:
            specs = json.loads(nodes_json) if isinstance(nodes_json, str) else nodes_json
            new_nodes = [
                NodeEndpoint(id=str(s["id"]), host=str(s["host"]), port=int(s["port"]))
                for s in specs
            ]
        except Exception as exc:
            return json.dumps(
                {"status": "error", "message": f"membership không hợp lệ: {exc}"},
                ensure_ascii=False,
            )
        if not new_nodes:
            return json.dumps(
                {"status": "error", "message": "membership rỗng"}, ensure_ascii=False
            )

        # Một thao tác membership tại một thời điểm (tránh đua với rebalance hồi phục).
        with self._rebalance_lock:
            with self.lock:
                old_ids = [n.id for n in self.nodes]
                held = {**self.replica_store, **self.data_store}

            # Dựng lại ring theo membership mới.
            self.nodes = sorted(new_nodes, key=lambda n: self._hash_int(n.id))
            self.nodes_by_id = {n.id: n for n in self.nodes}
            in_cluster = self.node_id in self.nodes_by_id
            if in_cluster:
                self.endpoint = self.nodes_by_id[self.node_id]
            self.replication_factor = max(1, min(self.replication_factor, len(self.nodes)))
            self._build_ring()
            self._remap_cache = None  # buộc tính lại biểu đồ so sánh remap

            self.neighbor_ids = [n.id for n in self.nodes if n.id != self.node_id]
            self.last_heartbeat = {
                nid: self.last_heartbeat.get(nid) for nid in self.neighbor_ids
            }
            self.node_status = {
                nid: self.node_status.get(nid, "UNKNOWN") for nid in self.neighbor_ids
            }

            migrated = self._migrate_local_keys(held)
            self._persist()

        new_ids = [n.id for n in self.nodes]
        print(f"[{self.node_id}] MEMBERSHIP {old_ids} -> {new_ids} | di trú: {migrated}")
        return self._ok_response(
            node=self.node_id,
            in_cluster=in_cluster,
            nodes=new_ids,
            ring_points=len(self._ring),
            migrated=migrated,
        )

    def _migrate_local_keys(self, held: Dict[str, str]) -> Dict[str, int]:
        """Đưa từng key cục bộ về đúng chủ sở hữu theo ring MỚI."""
        pushed = cleaned = kept = 0
        for key, value in held.items():
            primary = self._get_primary_node(key)
            replicas = self._get_replica_nodes(key)
            owner_ids = {primary.id} | {r.id for r in replicas}

            if primary.id == self.node_id:
                # Mình là primary mới -> giữ ở data_store và nhân bản cho replica.
                with self.lock:
                    self.data_store[key] = value
                    self.replica_store.pop(key, None)
                self._replicate_put(key, value, replicas)
                kept += 1
                continue

            # Đẩy sang primary mới; primary sẽ tự nhân bản cho các replica của nó.
            try:
                self._connect(primary).put(key, value, "client")
                pushed += 1
            except Exception:
                # Chủ mới chưa sẵn sàng -> giữ tạm, lần đồng bộ sau xử lý tiếp.
                with self.lock:
                    self.replica_store[key] = value
                    self.data_store.pop(key, None)
                continue

            if self.node_id in owner_ids:
                # Vẫn là replica đúng -> chuyển sang replica_store.
                with self.lock:
                    self.replica_store[key] = value
                    self.data_store.pop(key, None)
                kept += 1
            else:
                # Không còn liên quan -> xóa bản cục bộ.
                with self.lock:
                    self.data_store.pop(key, None)
                    self.replica_store.pop(key, None)
                cleaned += 1
        return {"pushed": pushed, "kept": kept, "cleaned": cleaned}

    def shutdown(self, delay: float = 0.1) -> str:
        # Shutdown bất đồng bộ để trả response XML-RPC trước khi server dừng hẳn.
        if self.server is None:
            return json.dumps({"status": "error", "message": "Server chưa được attach"}, ensure_ascii=False)

        delay = max(0.0, float(delay))

        def _do_shutdown() -> None:
            time.sleep(delay)
            try:
                self.server.shutdown()
            finally:
                self.server.server_close()

        threading.Thread(target=_do_shutdown, daemon=True).start()
        return self._ok_response(node=self.node_id, message="Node đang dừng")


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
        "virtual_nodes_per_node": 256,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chạy một node Key-Value phân tán")
    parser.add_argument("legacy_ports", nargs="*", help="Cách cũ: python node.py 8000 8001 8002")
    parser.add_argument("--node", "--node-id", dest="node_id", default=os.environ.get("NODE_ID"), help="ID node trong cluster_config.json, ví dụ: node1")
    parser.add_argument("--config", default=os.environ.get("CLUSTER_CONFIG", "cluster_config.json"), help="Đường dẫn file cấu hình cluster")
    parser.add_argument("--bind-host", default=os.environ.get("BIND_HOST", "127.0.0.1"), help="IP bind server. VM/Docker nên dùng 0.0.0.0")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR"), help="Thư mục lưu dữ liệu bền vững. Mặc định: ./node_data")
    parser.add_argument("--self-host", dest="self_host", default=os.environ.get("SELF_HOST"),
                        help="Host quảng bá của chính node này khi nó CHƯA có trong config (dùng cho thêm node động, vd Docker: tên service)")
    parser.add_argument("--self-port", dest="self_port", type=int, default=int(os.environ.get("SELF_PORT", 0) or 0),
                        help="Port quảng bá của chính node này khi nó chưa có trong config")
    parser.add_argument("--fresh", dest="fresh", action="store_true",
                        default=(os.environ.get("START_FRESH", "") not in ("", "0", "false", "False")),
                        help="Khởi động sạch: bỏ dữ liệu cũ trên đĩa, lấy lại dữ liệu hiện hành từ cụm (dùng cho khôi phục node)")
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

    endpoint_map = {n.id: n for n in nodes}
    if node_id not in endpoint_map:
        # Node CHƯA có trong config (trường hợp thêm node động, vd container Docker
        # mới đọc config baked cũ). Tự bootstrap bằng --self-host/--self-port rồi
        # manager sẽ gọi apply_membership để hoàn thiện ring ngay sau đó.
        self_host = getattr(args, "self_host", None) or bind_host
        self_port = int(getattr(args, "self_port", 0) or 0)
        if self_port <= 0:
            parser.error(
                f"Node '{node_id}' không có trong config. Hãy cung cấp --self-host/--self-port "
                f"(hoặc biến môi trường SELF_HOST/SELF_PORT) để node tự bootstrap."
            )
        self_ep = NodeEndpoint(id=node_id, host=self_host, port=self_port)
        nodes = nodes + [self_ep]
        endpoint_map[node_id] = self_ep
        print(f"[{node_id}] tự bootstrap (chưa có trong config): {self_ep.host}:{self_ep.port}")

    endpoint = endpoint_map[node_id]
    server = ThreadedServer((bind_host, endpoint.port), requestHandler=QuietHandler, allow_none=True)
    node = KeyValueNode(
        node_id=node_id,
        nodes=nodes,
        replication_factor=int(config.get("replication_factor", 2)),
        heartbeat_interval=float(config.get("heartbeat_interval", 3)),
        failure_timeout=float(config.get("failure_timeout", 10)),
        rpc_timeout=float(config.get("rpc_timeout", 1.5)),
        data_dir=getattr(args, "data_dir", None),
        vnodes_per_node=int(config.get("virtual_nodes_per_node", 256)),
        fresh=getattr(args, "fresh", False),
    )
    node.attach_server(server)
    server.register_instance(node)

    print(f"{node_id} đang lắng nghe tại {bind_host}:{endpoint.port}")
    print(f"   Địa chỉ quảng bá trong cluster: {endpoint.url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
