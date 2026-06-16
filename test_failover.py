#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_failover.py — Kịch bản kiểm thử TỰ ĐỘNG Failover & Recovery
==================================================================

Dùng để demo lúc bảo vệ: script tự GIẾT một node, kiểm tra cluster vẫn
ĐỌC/GHI được (failover), rồi BẬT LẠI node và kiểm tra dữ liệu được
ĐỒNG BỘ trở lại đúng chủ sở hữu (recovery). Mỗi bước in PASS/FAIL rõ ràng.

Hai chế độ điều khiển node:

  --mode local   : script tự bật/tắt node bằng `python node.py ...`
                   (mặc định; hợp khi chạy 3 node bằng Python trên 1 máy)

  --mode docker  : script bật/tắt CONTAINER bằng `docker stop/start`
                   (hợp khi cluster chạy bằng docker compose)

Ví dụ chạy:

  # Local (config 127.0.0.1, port 8000/8001/8002)
  python3 test_failover.py --config cluster_config.json --mode local

  # Docker (gọi từ máy host; config trỏ tới 127.0.0.1:8001/8002/8003)
  python3 test_failover.py --config cluster_config.docker.host.json \
          --mode docker --container-prefix udpt-

  # Chỉ định node muốn giết
  python3 test_failover.py --victim node2
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import xmlrpc.client
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Tiện ích in màu + theo dõi PASS/FAIL
# --------------------------------------------------------------------------- #
class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


_results: List[bool] = []


def banner(text: str) -> None:
    print(f"\n{C.BOLD}{C.CYAN}{'=' * 64}{C.END}")
    print(f"{C.BOLD}{C.CYAN}{text}{C.END}")
    print(f"{C.BOLD}{C.CYAN}{'=' * 64}{C.END}")


def info(text: str) -> None:
    print(f"   {text}")


def check(label: str, ok: bool, detail: str = "") -> bool:
    _results.append(ok)
    tag = f"{C.GREEN}PASS{C.END}" if ok else f"{C.RED}FAIL{C.END}"
    line = f"   [{tag}] {label}"
    if detail:
        line += f"  →  {detail}"
    print(line)
    return ok


# --------------------------------------------------------------------------- #
# RPC helpers
# --------------------------------------------------------------------------- #
class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = 3.0):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


def rpc(url: str, timeout: float = 3.0) -> xmlrpc.client.ServerProxy:
    return xmlrpc.client.ServerProxy(
        url, transport=TimeoutTransport(timeout), allow_none=True
    )


def is_up(url: str) -> bool:
    try:
        rpc(url, timeout=1.5).get_node_info()
        return True
    except Exception:
        return False


def wait_until(predicate, timeout: float, interval: float = 1.0, desc: str = "") -> bool:
    """Chờ tới khi predicate() trả True hoặc hết timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


# --------------------------------------------------------------------------- #
# Điều khiển vòng đời node (local / docker)
# --------------------------------------------------------------------------- #
class NodeController:
    def __init__(self, mode: str, config_path: str, prefix: str = "udpt-"):
        self.mode = mode
        self.config_path = config_path
        self.prefix = prefix
        self.bind_host = "127.0.0.1"

    # ---- local: bật/tắt bằng python node.py ----
    def _local_pids(self, node_id: str) -> List[int]:
        out = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True
        ).stdout.splitlines()
        pids = []
        needle = f"--node {node_id}"
        for line in out:
            if "node.py" in line and needle in line and "test_failover" not in line:
                try:
                    pids.append(int(line.split()[1]))
                except (IndexError, ValueError):
                    pass
        return pids

    def kill(self, node_id: str) -> None:
        if self.mode == "docker":
            subprocess.run(
                ["docker", "stop", f"{self.prefix}{node_id}"],
                capture_output=True, text=True,
            )
        else:
            for pid in self._local_pids(node_id):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def start(self, node_id: str) -> None:
        if self.mode == "docker":
            subprocess.run(
                ["docker", "start", f"{self.prefix}{node_id}"],
                capture_output=True, text=True,
            )
        else:
            logs = Path("logs")
            logs.mkdir(exist_ok=True)
            log = open(logs / f"{node_id}.failovertest.log", "w")
            subprocess.Popen(
                [
                    sys.executable, "node.py",
                    "--node", node_id,
                    "--config", self.config_path,
                    "--bind-host", self.bind_host,
                ],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True,  # tách session để sống sau khi script thoát
            )


# --------------------------------------------------------------------------- #
# Đọc config
# --------------------------------------------------------------------------- #
def load_cfg(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def node_urls(cfg: Dict[str, Any]) -> Dict[str, str]:
    return {
        n["id"]: f"http://{n['host']}:{n['port']}"
        for n in cfg["nodes"]
    }


# --------------------------------------------------------------------------- #
# Tìm key có primary là node nạn nhân (để test có ý nghĩa)
# --------------------------------------------------------------------------- #
def find_key_for_primary(any_url: str, victim: str, taken: set) -> Optional[str]:
    for i in range(5000):
        key = f"fo_{victim}_{i}"
        if key in taken:
            continue
        try:
            info_ = json.loads(rpc(any_url).get_routing_info(key))
        except Exception:
            return None
        if info_["primary"]["id"] == victim and info_["replicas"]:
            return key
    return None


# --------------------------------------------------------------------------- #
# Kịch bản chính
# --------------------------------------------------------------------------- #
def run(args) -> int:
    cfg = load_cfg(args.config)
    urls = node_urls(cfg)
    failure_timeout = float(cfg.get("failure_timeout", 10))
    ctl = NodeController(args.mode, args.config, args.container_prefix)

    node_ids = list(urls.keys())
    victim = args.victim or node_ids[1]  # mặc định node thứ 2
    if victim not in urls:
        print(f"{C.RED}Node nạn nhân '{victim}' không có trong config{C.END}")
        return 2
    survivors = [n for n in node_ids if n != victim]

    banner("PHA 0 — CHUẨN BỊ CLUSTER")
    info(f"Config        : {args.config}  (mode={args.mode})")
    info(f"Các node       : {', '.join(node_ids)}")
    info(f"Node nạn nhân  : {C.YELLOW}{victim}{C.END}")
    info(f"failure_timeout: {failure_timeout}s")

    # Bật những node chưa chạy (chỉ local; docker giả định đã up hoặc start lại)
    for nid in node_ids:
        if not is_up(urls[nid]):
            info(f"{nid} chưa chạy → đang khởi động...")
            ctl.start(nid)
    all_up = wait_until(
        lambda: all(is_up(u) for u in urls.values()),
        timeout=25, desc="tất cả node online",
    )
    check("Tất cả node online", all_up,
          f"{sum(is_up(u) for u in urls.values())}/{len(urls)} node")
    if not all_up:
        print(f"{C.RED}Không đủ node để chạy test. Dừng.{C.END}")
        return 2

    survivor_url = urls[survivors[0]]

    # Chờ heartbeat thiết lập: node sống phải thấy victim = ALIVE trước khi giết.
    # Nếu giết quá sớm (chưa nhận heartbeat đầu), victim ở trạng thái UNKNOWN và
    # sẽ không bao giờ chuyển sang DEAD -> tránh flaky cho bài test.
    info("Chờ heartbeat thiết lập (node sống thấy victim = ALIVE)...")
    wait_until(
        lambda: json.loads(rpc(survivor_url).get_node_info())["node_status"].get(victim) == "ALIVE",
        timeout=15, desc="victim ALIVE",
    )
    # Chọn 2 key có primary = victim: 1 để test đọc-từ-replica, 1 để test ghi-degraded
    taken: set = set()
    key_read = find_key_for_primary(survivor_url, victim, taken)
    taken.add(key_read)
    key_write = find_key_for_primary(survivor_url, victim, taken)
    if not key_read or not key_write:
        print(f"{C.RED}Không tìm được key có primary={victim}. Dừng.{C.END}")
        return 2
    val_read = "GIA_TRI_GOC_123"
    info(f"Key test đọc   : '{key_read}'  (primary={victim})")
    info(f"Key test ghi   : '{key_write}' (primary={victim})")

    # Ghi sẵn key_read và xác nhận đã replicate
    rpc(survivor_url).put(key_read, val_read, "client")
    time.sleep(0.6)
    routing = json.loads(rpc(survivor_url).get_routing_info(key_read))
    replica_ids = [r["id"] for r in routing["replicas"]]
    holders = []
    for nid in node_ids:
        d = json.loads(rpc(urls[nid]).get_all_data())
        if key_read in d["primary"] or key_read in d["replica"]:
            holders.append(nid)
    check("Ghi & nhân bản key test thành công",
          victim in holders and any(r in holders for r in replica_ids),
          f"đang giữ ở: {holders}; replica dự kiến: {replica_ids}")

    # --------------------------------------------------------------------- #
    banner("PHA 1 — FAILOVER (giết node primary)")
    info(f"Đang giết {victim} ...")
    ctl.kill(victim)
    down = wait_until(lambda: not is_up(urls[victim]), timeout=15, desc="victim down")
    check(f"{victim} đã ngừng phản hồi", down)

    # T1: ĐỌC vẫn được nhờ replica
    r = json.loads(rpc(survivor_url).get(key_read, "client"))
    check("ĐỌC vẫn hoạt động khi primary chết (đọc từ replica)",
          r.get("status") == "ok" and r.get("value") == val_read,
          f"value={r.get('value')} served_by={r.get('node')} role={r.get('role')}")

    # T2: GHI vẫn được ở chế độ suy giảm (degraded)
    val_write = "GHI_LUC_PRIMARY_CHET"
    w = json.loads(rpc(survivor_url).put(key_write, val_write, "client"))
    check("GHI vẫn hoạt động khi primary chết (degraded write)",
          w.get("status") == "ok",
          f"role={w.get('role')} served_by={w.get('node')}")

    # T3: đọc lại key vừa ghi degraded
    r = json.loads(rpc(survivor_url).get(key_write, "client"))
    check("Đọc lại được dữ liệu vừa ghi trong lúc suy giảm",
          r.get("status") == "ok" and r.get("value") == val_write,
          f"value={r.get('value')} served_by={r.get('node')}")

    # T4: heartbeat đánh dấu victim DEAD
    info(f"Chờ heartbeat đánh dấu {victim} = DEAD (tối đa {failure_timeout + 6:.0f}s)...")
    dead = wait_until(
        lambda: json.loads(rpc(survivor_url).get_node_info())
        ["node_status"].get(victim) == "DEAD",
        timeout=failure_timeout + 6,
    )
    status_map = json.loads(rpc(survivor_url).get_node_info())["node_status"]
    check("Heartbeat phát hiện node chết (DEAD)", dead,
          f"node_status = {status_map}")

    # T5: keyspace của node chết được dồn sang node sống (kịch bản 'xóa server')
    dist = json.loads(rpc(survivor_url).get_ring_info())["distribution"]
    by_id = {n["id"]: n for n in dist["nodes"]}
    victim_eff0 = by_id[victim]["effective_percent"]
    survivors_grew = all(
        by_id[s]["effective_percent"] >= by_id[s]["keyspace_percent"] - 0.01
        for s in survivors
    )
    check("Keyspace node chết được dồn sang node sống (effective%)",
          dist["any_dead"] and victim_eff0 == 0.0 and survivors_grew,
          f"{victim}→{victim_eff0}% ; " +
          " ; ".join(f"{s}:{by_id[s]['keyspace_percent']}→{by_id[s]['effective_percent']}%"
                     for s in survivors))

    # --------------------------------------------------------------------- #
    banner("PHA 2 — RECOVERY (bật lại node)")
    info(f"Đang bật lại {victim} ...")
    ctl.start(victim)
    up = wait_until(lambda: is_up(urls[victim]), timeout=25, desc="victim up")
    check(f"{victim} đã online trở lại", up)

    # T6: heartbeat thấy victim ALIVE lại
    alive = wait_until(
        lambda: json.loads(rpc(survivor_url).get_node_info())
        ["node_status"].get(victim) == "ALIVE",
        timeout=20,
    )
    check("Heartbeat phát hiện node hồi phục (ALIVE)", alive)

    # T7: node vừa hồi phục TỰ ĐỒNG BỘ lại key mà nó là primary
    synced = wait_until(
        lambda: key_read in json.loads(rpc(urls[victim]).get_all_data())["primary"],
        timeout=15,
    )
    dv = json.loads(rpc(urls[victim]).get_all_data())
    check("Node hồi phục tự đồng bộ lại dữ liệu primary của nó",
          synced and dv["primary"].get(key_read) == val_read,
          f"{victim}.primary có '{key_read}'={dv['primary'].get(key_read)}")

    # T8: bản degraded được REBALANCE về đúng primary, dọn khỏi node giữ tạm.
    # Hệ thống hội tụ kiểu eventual-consistency (dọn định kỳ ~2s) nên ta CHỜ.
    info("Chờ rebalance + dọn bản tạm về đúng chủ (eventual, tối đa 20s)...")
    rebalanced = wait_until(
        lambda: key_write in json.loads(rpc(urls[victim]).get_all_data())["primary"],
        timeout=15,
    )
    routing_w = json.loads(rpc(survivor_url).get_routing_info(key_write))
    legit = {routing_w["primary"]["id"]} | {r["id"] for r in routing_w["replicas"]}

    def _no_stray():
        for nid in node_ids:
            d = json.loads(rpc(urls[nid]).get_all_data())
            if (key_write in d["primary"] or key_write in d["replica"]) and nid not in legit:
                return False
        return True

    cleaned = wait_until(_no_stray, timeout=20)
    stray = []
    for nid in node_ids:
        d = json.loads(rpc(urls[nid]).get_all_data())
        if (key_write in d["primary"] or key_write in d["replica"]) and nid not in legit:
            stray.append(nid)
    check("Bản degraded rebalance về primary & dọn bản tạm (hội tụ)",
          rebalanced and cleaned,
          f"primary mới giữ '{key_write}'; node giữ tạm còn sót: {stray or 'không'}")

    # T9: force_sync thủ công chạy được
    fs = json.loads(rpc(urls[victim]).force_sync())
    check("force_sync thủ công hoạt động", fs.get("status") == "ok",
          f"primary={fs.get('primary')} replica={fs.get('replica')}")

    # T10: keyspace cân bằng trở lại, không còn node chết
    dist2 = json.loads(rpc(survivor_url).get_ring_info())["distribution"]
    check("Cluster cân bằng trở lại (không còn node chết)",
          not dist2["any_dead"] and dist2["alive_count"] == len(node_ids),
          f"alive={dist2['alive_count']}/{len(node_ids)}")

    # T11: đọc lại cả 2 key đều đúng giá trị
    ok_read = json.loads(rpc(survivor_url).get(key_read, "client"))
    ok_write = json.loads(rpc(survivor_url).get(key_write, "client"))
    check("Đọc cuối cùng: cả 2 key đều đúng giá trị",
          ok_read.get("value") == val_read and ok_write.get("value") == val_write,
          f"'{key_read}'={ok_read.get('value')} ; '{key_write}'={ok_write.get('value')}")

    # Dọn dữ liệu test
    if not args.keep_data:
        for k in (key_read, key_write):
            try:
                rpc(survivor_url).delete(k, "client")
            except Exception:
                pass

    # --------------------------------------------------------------------- #
    banner("KẾT QUẢ")
    passed = sum(1 for r in _results if r)
    total = len(_results)
    color = C.GREEN if passed == total else C.RED
    print(f"{C.BOLD}{color}   {passed}/{total} kiểm tra PASS{C.END}")
    if passed == total:
        print(f"{C.GREEN}   ✅ Failover & Recovery hoạt động đúng.{C.END}")
        return 0
    print(f"{C.RED}   ❌ Có kiểm tra thất bại — xem log node trong ./logs.{C.END}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Kiểm thử tự động Failover & Recovery cho KV store phân tán"
    )
    p.add_argument("--config", default="cluster_config.json",
                   help="File cấu hình cluster (mặc định: cluster_config.json)")
    p.add_argument("--mode", choices=["local", "docker"], default="local",
                   help="Cách bật/tắt node: local (python) hoặc docker (container)")
    p.add_argument("--victim", default=None,
                   help="ID node sẽ bị giết (mặc định: node thứ 2 trong config)")
    p.add_argument("--container-prefix", default="udpt-",
                   help="Tiền tố tên container khi --mode docker (mặc định: udpt-)")
    p.add_argument("--keep-data", action="store_true",
                   help="Không xóa key test sau khi chạy xong")
    return p


if __name__ == "__main__":
    try:
        sys.exit(run(build_parser().parse_args()))
    except KeyboardInterrupt:
        print("\nĐã hủy.")
        sys.exit(130)
