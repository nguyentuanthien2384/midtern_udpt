from __future__ import annotations

import argparse
import json
import logging
import os
import socket as pysocket
import subprocess
import sys
import threading
import time
import xmlrpc.client
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# ─── Hệ thống Log dùng chung (Docker stdout + Web UI) ────────────────
# Mọi thao tác qua manager được ghi đồng thời:
#   1) ra stdout -> hiển thị bằng `docker logs udpt-manager`
#   2) vào ring buffer trong RAM -> Web UI lấy qua GET /api/logs
LOG_BUFFER_SIZE = 500
_LOG_BUFFER: Deque[Dict[str, Any]] = deque(maxlen=LOG_BUFFER_SIZE)
_LOG_LOCK = threading.Lock()
_LOG_SEQ = 0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MANAGER] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
# Tắt log truy cập HTTP của Werkzeug (polling /api/logs, /api/dashboard mỗi vài giây)
# để Docker logs chỉ còn các thao tác có ý nghĩa.
logging.getLogger("werkzeug").setLevel(logging.WARNING)
_logger = logging.getLogger("manager")

_LEVEL_TO_PY = {
    "ok": logging.INFO,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "err": logging.ERROR,
}


def record_log(msg: str, level: str = "ok") -> Dict[str, Any]:
    """Ghi một dòng log vừa ra Docker stdout, vừa vào buffer cho Web UI."""
    global _LOG_SEQ
    level = level if level in _LEVEL_TO_PY else "ok"
    now = datetime.now()
    with _LOG_LOCK:
        _LOG_SEQ += 1
        entry = {
            "id": _LOG_SEQ,
            "time": now.strftime("%H:%M:%S"),
            "ts": now.isoformat(timespec="seconds"),
            "level": level,
            "msg": msg,
        }
        _LOG_BUFFER.append(entry)
    _logger.log(_LEVEL_TO_PY[level], msg)
    return entry


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
# Timeout ngắn riêng cho kiểm tra trạng thái: node chết sẽ fail nhanh thay vì kéo chậm UI.
STATUS_RPC_TIMEOUT = 0.8
MANAGED_NODE_PROCS: Dict[str, subprocess.Popen[Any]] = {}
# Các node đã bị XÓA khỏi cluster (để có thể KHÔI PHỤC lại sau).
REMOVED_NODES: List[Dict[str, Any]] = []
PROJECT_DIR = Path(__file__).resolve().parent
DOCKER_NODE_CONTAINER_PREFIX = os.environ.get("DOCKER_NODE_CONTAINER_PREFIX", "udpt-")
DOCKER_COMPOSE_FILE = os.environ.get("DOCKER_COMPOSE_FILE", "docker-compose.yml")
_DOCKER_AVAILABLE: Optional[bool] = None


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

    for node in CLUSTER_NODES:
        if str(node.get("port")) == ref:
            return node

    raise ValueError(f"Không tìm thấy node '{ref}' trong cluster config")


def connect_node(node_ref: Any, timeout: Optional[float] = None) -> xmlrpc.client.ServerProxy:
    node = resolve_node(node_ref)
    return xmlrpc.client.ServerProxy(
        node_url(node),
        transport=TimeoutTransport(timeout=(RPC_TIMEOUT if timeout is None else timeout)),
        allow_none=True,
    )


def connect_entry_node(node_ref: Any, timeout: Optional[float] = None) -> xmlrpc.client.ServerProxy:
    """Chọn node làm điểm vào cho PUT/GET/DELETE.

    Nếu người dùng chỉ định node cụ thể -> dùng đúng node đó.
    Nếu không chỉ định, KHÔNG mặc định CLUSTER_NODES[0] (có thể đang chết) mà
    thử lần lượt để lấy node còn sống đầu tiên. Node đó sẽ tự định tuyến nội bộ
    tới primary/replica thật của key.
    """
    eff_timeout = STATUS_RPC_TIMEOUT if timeout is None else timeout
    if node_ref not in (None, ""):
        return connect_node(node_ref, timeout=timeout)

    last_err: Optional[Exception] = None
    for node_cfg in CLUSTER_NODES:
        node_id = str(node_cfg.get("id"))
        try:
            proxy = connect_node(node_id, timeout=eff_timeout)
            proxy.get_node_info()
            return connect_node(node_id, timeout=timeout)
        except Exception as exc:
            last_err = exc
            continue
    raise ConnectionError(f"Không có node nào online để xử lý yêu cầu ({last_err})")


def can_control_node(node: Dict[str, Any]) -> bool:
    return detect_control_mode(node) != "none"


def docker_cli_available() -> bool:
    global _DOCKER_AVAILABLE
    if _DOCKER_AVAILABLE is not None:
        return _DOCKER_AVAILABLE
    try:
        subprocess.check_output(["docker", "version"], text=True, encoding="utf-8", errors="ignore")
        _DOCKER_AVAILABLE = True
    except Exception:
        _DOCKER_AVAILABLE = False
    return _DOCKER_AVAILABLE


# ─── Docker Engine API qua unix socket ───────────────
# Cho phép manager chạy TRONG container vẫn bật/tắt được các container node,
# bằng cách mount /var/run/docker.sock (xem docker-compose.yml).

DOCKER_SOCKET_PATH = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")


def docker_socket_available() -> bool:
    return hasattr(pysocket, "AF_UNIX") and os.path.exists(DOCKER_SOCKET_PATH)


def _docker_api_request(method: str, path: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """Gọi Docker Engine API qua unix socket bằng HTTP/1.0 thuần (không cần thư viện ngoài)."""
    if not docker_socket_available():
        return None
    sock = pysocket.socket(pysocket.AF_UNIX, pysocket.SOCK_STREAM)  # type: ignore[attr-defined]
    sock.settimeout(timeout)
    try:
        sock.connect(DOCKER_SOCKET_PATH)
        request = f"{method} {path} HTTP/1.0\r\nHost: docker\r\n\r\n"
        sock.sendall(request.encode("ascii"))
        raw = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    except Exception:
        return None
    finally:
        sock.close()

    header, _, body = raw.partition(b"\r\n\r\n")
    try:
        status_code = int(header.split(b"\r\n")[0].split()[1])
    except Exception:
        return None
    return {"status": status_code, "body": body}


def _docker_api_json(path: str) -> Optional[Any]:
    resp = _docker_api_request("GET", path)
    if resp is None or resp["status"] >= 400:
        return None
    try:
        return json.loads(resp["body"].decode("utf-8"))
    except Exception:
        return None


def docker_container_candidates(node_id: str) -> List[str]:
    candidates: List[str] = []
    prefix = DOCKER_NODE_CONTAINER_PREFIX.strip()
    if prefix:
        candidates.append(f"{prefix}{node_id}")
        if prefix.endswith("-"):
            candidates.append(f"{prefix[:-1]}-{node_id}")
    candidates.append(node_id)
    seen: Dict[str, bool] = {}
    dedup: List[str] = []
    for c in candidates:
        if c and c not in seen:
            seen[c] = True
            dedup.append(c)
    return dedup


def list_docker_container_names() -> List[str]:
    # Ưu tiên Docker API socket (hoạt động cả khi manager nằm trong container).
    containers = _docker_api_json("/containers/json?all=1")
    if containers is not None:
        names: List[str] = []
        for c in containers:
            for raw_name in c.get("Names", []):
                names.append(str(raw_name).lstrip("/"))
        return names

    if not docker_cli_available():
        return []
    try:
        out = subprocess.check_output(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        return [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        return []


def find_existing_docker_container(node_id: str) -> Optional[str]:
    candidates = set(docker_container_candidates(node_id))
    for name in list_docker_container_names():
        if name in candidates:
            return name
    return None


def is_docker_container_running(container_name: str) -> bool:
    if not container_name:
        return False
    info = _docker_api_json(f"/containers/{container_name}/json")
    if info is not None:
        return bool((info.get("State") or {}).get("Running"))
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        return out.strip().lower() == "true"
    except Exception:
        return False


def _docker_container_action(container_name: str, action: str) -> bool:
    """start/stop container, ưu tiên Docker API socket rồi mới tới CLI."""
    path = f"/containers/{container_name}/{action}"
    if action == "stop":
        path += "?t=3"
    resp = _docker_api_request("POST", path, timeout=15.0)
    if resp is not None:
        # 204 = thành công, 304 = đã ở trạng thái mong muốn.
        return resp["status"] in (204, 304)
    if not docker_cli_available():
        return False
    try:
        subprocess.check_call(["docker", action, container_name])
        return True
    except Exception:
        return False


# ─── Docker: tạo / xóa container node mới (membership động) ───

def _docker_api_post_json(path: str, payload: Dict[str, Any], timeout: float = 25.0) -> Optional[Dict[str, Any]]:
    """POST JSON tới Docker Engine API qua unix socket (tự gắn Content-Length)."""
    if not docker_socket_available():
        return None
    body = json.dumps(payload).encode("utf-8")
    sock = pysocket.socket(pysocket.AF_UNIX, pysocket.SOCK_STREAM)  # type: ignore[attr-defined]
    sock.settimeout(timeout)
    try:
        sock.connect(DOCKER_SOCKET_PATH)
        head = (
            f"POST {path} HTTP/1.0\r\n"
            f"Host: docker\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode("ascii")
        sock.sendall(head + body)
        raw = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    except Exception:
        return None
    finally:
        sock.close()
    header, _, rbody = raw.partition(b"\r\n\r\n")
    try:
        status_code = int(header.split(b"\r\n")[0].split()[1])
    except Exception:
        return None
    return {"status": status_code, "body": rbody}


def docker_reference_container() -> Optional[str]:
    """Tìm một container node đang tồn tại để sao chép image/network/command."""
    for n in CLUSTER_NODES:
        name = find_existing_docker_container(str(n.get("id")))
        if name:
            return name
    manager_name = f"{DOCKER_NODE_CONTAINER_PREFIX}manager"
    for name in list_docker_container_names():
        if name.startswith(DOCKER_NODE_CONTAINER_PREFIX) and name != manager_name:
            return name
    return None


def docker_node_template() -> Optional[Dict[str, Any]]:
    """Lấy image, network, file config từ một container node mẫu."""
    ref = docker_reference_container()
    if not ref:
        return None
    info = _docker_api_json(f"/containers/{ref}/json")
    if not info:
        return None
    cfg = info.get("Config") or {}
    image = cfg.get("Image") or "udpt-kv-store:latest"
    networks = list(((info.get("NetworkSettings") or {}).get("Networks") or {}).keys())
    network = networks[0] if networks else None
    cmd = cfg.get("Cmd") or []
    config_arg = "cluster_config.docker.json"
    if "--config" in cmd:
        try:
            config_arg = cmd[cmd.index("--config") + 1]
        except Exception:
            pass
    return {"image": image, "network": network, "config_arg": config_arg, "reference": ref}


def docker_create_node(node_id: str, host_port: Optional[int] = None,
                       internal_port: int = 8000, fresh: bool = False) -> Dict[str, Any]:
    """Tạo MỚI và bật một container node, gắn vào đúng network của cluster.

    Container được đặt network-alias = node_id để các node khác gọi RPC tới
    nó bằng tên (vd node4:8000). Vì config baked trong image chưa có node này,
    ta truyền SELF_HOST/SELF_PORT để node tự bootstrap; manager sẽ gọi
    apply_membership ngay sau đó để hoàn thiện ring.
    """
    tmpl = docker_node_template()
    if not tmpl:
        return {"status": "error", "message": "Không tìm thấy container node mẫu để sao chép cấu hình"}
    if not tmpl["network"]:
        return {"status": "error", "message": "Không xác định được Docker network của cluster"}

    name = f"{DOCKER_NODE_CONTAINER_PREFIX}{node_id}"
    network = tmpl["network"]
    port_key = f"{internal_port}/tcp"

    # Dọn container cũ trùng tên (nếu có) để tạo lại sạch.
    existing = find_existing_docker_container(node_id)
    if existing:
        _docker_api_request("DELETE", f"/containers/{existing}?force=1", timeout=15.0)

    payload: Dict[str, Any] = {
        "Image": tmpl["image"],
        "Hostname": node_id,
        "Cmd": ["python", "-u", "node.py", "--node", node_id,
                "--config", tmpl["config_arg"], "--bind-host", "0.0.0.0"],
        "Env": [
            f"NODE_ID={node_id}",
            f"SELF_HOST={node_id}",
            f"SELF_PORT={internal_port}",
            f"CLUSTER_CONFIG={tmpl['config_arg']}",
            f"START_FRESH={'1' if fresh else '0'}",
        ],
        "ExposedPorts": {port_key: {}},
        "HostConfig": {
            "NetworkMode": network,
            "RestartPolicy": {"Name": "no"},
            # Volume bền: dữ liệu node sống sót khi container bị xóa/tạo lại.
            "Binds": [f"udpt-{node_id}-data:/app/node_data"],
        },
        "NetworkingConfig": {
            "EndpointsConfig": {network: {"Aliases": [node_id]}}
        },
    }
    if host_port:
        payload["HostConfig"]["PortBindings"] = {port_key: [{"HostPort": str(host_port)}]}

    create = _docker_api_post_json(f"/containers/create?name={name}", payload)
    if create is None:
        return _docker_create_node_cli(node_id, tmpl, host_port, internal_port, fresh=fresh)
    if create["status"] >= 400:
        detail = create["body"][:200].decode("utf-8", "ignore")
        return {"status": "error", "message": f"Docker create lỗi {create['status']}: {detail}"}
    if not _docker_container_action(name, "start"):
        return {"status": "error", "message": "Đã tạo container nhưng không start được"}
    return {"status": "ok", "container": name, "network": network,
            "image": tmpl["image"], "control_mode": "docker"}


def _docker_create_node_cli(node_id: str, tmpl: Dict[str, Any],
                            host_port: Optional[int], internal_port: int,
                            fresh: bool = False) -> Dict[str, Any]:
    """Dự phòng tạo container bằng `docker run` khi không dùng được socket API."""
    if not docker_cli_available():
        return {"status": "error", "message": "Không có quyền tạo container (thiếu cả socket lẫn CLI)"}
    name = f"{DOCKER_NODE_CONTAINER_PREFIX}{node_id}"
    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
    except Exception:
        pass
    cmd = [
        "docker", "run", "-d", "--name", name,
        "--hostname", node_id,
        "--network", tmpl["network"], "--network-alias", node_id,
        "-v", f"udpt-{node_id}-data:/app/node_data",
        "-e", f"NODE_ID={node_id}", "-e", f"SELF_HOST={node_id}",
        "-e", f"SELF_PORT={internal_port}", "-e", f"CLUSTER_CONFIG={tmpl['config_arg']}",
        "-e", f"START_FRESH={'1' if fresh else '0'}",
    ]
    if host_port:
        cmd += ["-p", f"{host_port}:{internal_port}"]
    cmd += [tmpl["image"], "python", "-u", "node.py", "--node", node_id,
            "--config", tmpl["config_arg"], "--bind-host", "0.0.0.0"]
    try:
        subprocess.check_call(cmd)
        return {"status": "ok", "container": name, "network": tmpl["network"],
                "image": tmpl["image"], "control_mode": "docker"}
    except Exception as e:
        return {"status": "error", "message": f"docker run thất bại: {e}"}


def docker_remove_node(node_id: str) -> Dict[str, Any]:
    """Dừng và XÓA HẲN container của node (dùng khi xóa node khỏi cluster)."""
    name = find_existing_docker_container(node_id)
    if not name:
        return {"status": "ok", "message": "Không có container để xóa"}
    _docker_container_action(name, "stop")
    resp = _docker_api_request("DELETE", f"/containers/{name}?force=1", timeout=15.0)
    if resp is not None and resp["status"] in (200, 204, 404):
        return {"status": "ok", "container": name, "message": "Đã xóa container"}
    if docker_cli_available():
        try:
            subprocess.check_call(["docker", "rm", "-f", name])
            return {"status": "ok", "container": name, "message": "Đã xóa container (CLI)"}
        except Exception as e:
            return {"status": "error", "message": f"docker rm thất bại: {e}"}
    return {"status": "error", "message": "Xóa container thất bại"}


def cluster_runtime_mode() -> str:
    """Phán đoán cluster đang chạy bằng Docker hay tiến trình local."""
    if docker_socket_available() and docker_reference_container():
        return "docker"
    if docker_socket_available():
        for n in CLUSTER_NODES:
            host = str(n.get("host", "")).lower()
            if host not in ("127.0.0.1", "localhost", ""):
                return "docker"
    return "local"


def docker_start_node(node_id: str) -> Dict[str, Any]:
    if not docker_socket_available() and not docker_cli_available():
        return {"status": "error", "node_id": node_id, "message": "Không có quyền điều khiển Docker"}

    container_name = find_existing_docker_container(node_id)
    if container_name:
        if is_docker_container_running(container_name):
            return {
                "status": "ok",
                "node_id": node_id,
                "message": "Node Docker đã chạy",
                "control_mode": "docker",
                "container": container_name,
                "managed_process": False,
            }
        if _docker_container_action(container_name, "start"):
            return {
                "status": "ok",
                "node_id": node_id,
                "message": "Đã bật node Docker",
                "control_mode": "docker",
                "container": container_name,
                "managed_process": False,
            }
        return {"status": "error", "node_id": node_id, "message": "Docker start thất bại"}

    compose_file = PROJECT_DIR / DOCKER_COMPOSE_FILE
    if compose_file.exists():
        try:
            subprocess.check_call(
                ["docker", "compose", "-f", str(compose_file), "up", "-d", node_id],
                cwd=str(PROJECT_DIR),
            )
            container_name = find_existing_docker_container(node_id) or node_id
            return {
                "status": "ok",
                "node_id": node_id,
                "message": "Đã bật node Docker bằng compose service",
                "control_mode": "docker",
                "container": container_name,
                "managed_process": False,
            }
        except Exception as e:
            return {"status": "error", "node_id": node_id, "message": f"Docker compose up thất bại: {e}"}

    return {
        "status": "error",
        "node_id": node_id,
        "message": "Không tìm thấy container/service Docker tương ứng",
    }


def docker_stop_node(node_id: str) -> Dict[str, Any]:
    if not docker_socket_available() and not docker_cli_available():
        return {"status": "error", "node_id": node_id, "message": "Không có quyền điều khiển Docker"}

    container_name = find_existing_docker_container(node_id)
    if not container_name:
        return {"status": "error", "node_id": node_id, "message": "Không tìm thấy container Docker của node"}

    if not is_docker_container_running(container_name):
        return {
            "status": "ok",
            "node_id": node_id,
            "message": "Node Docker đã dừng",
            "control_mode": "docker",
            "container": container_name,
            "managed_process": False,
        }
    if _docker_container_action(container_name, "stop"):
        return {
            "status": "ok",
            "node_id": node_id,
            "message": "Đã tắt node Docker",
            "control_mode": "docker",
            "container": container_name,
            "managed_process": False,
        }
    return {"status": "error", "node_id": node_id, "message": "Docker stop thất bại"}


def detect_control_mode(node: Dict[str, Any]) -> str:
    host = str(node.get("host", "")).strip().lower()
    if host in {"127.0.0.1", "localhost"}:
        return "local_process"
    node_id = str(node.get("id") or "")
    if node_id and find_existing_docker_container(node_id):
        return "docker"
    return "none"


def is_managed_node_alive(node_id: str) -> bool:
    proc = MANAGED_NODE_PROCS.get(node_id)
    if proc is None:
        return False
    if proc.poll() is None:
        return True
    MANAGED_NODE_PROCS.pop(node_id, None)
    return False


def build_node_command(node_id: str, fresh: bool = False) -> List[str]:
    node_py = PROJECT_DIR / "node.py"
    cmd = [
        sys.executable,
        "-u",
        str(node_py),
        "--node",
        node_id,
        "--config",
        CLUSTER_CONFIG_PATH,
        "--bind-host",
        "127.0.0.1",
    ]
    if fresh:
        cmd.append("--fresh")  # khôi phục: bỏ dữ liệu cũ, lấy lại từ cụm
    return cmd


def find_listener_pid_by_port(port: int) -> Optional[int]:
    try:
        if os.name == "nt":
            out = subprocess.check_output(["netstat", "-ano"], text=True, encoding="utf-8", errors="ignore")
            needle = f":{port}"
            for line in out.splitlines():
                line = line.strip()
                if "LISTENING" not in line or needle not in line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                local_addr = parts[1]
                state = parts[3]
                pid_str = parts[4]
                if local_addr.endswith(needle) and state == "LISTENING" and pid_str.isdigit():
                    return int(pid_str)
            return None

        out = subprocess.check_output(["lsof", "-i", f"TCP:{port}", "-sTCP:LISTEN", "-t"], text=True)
        pid_line = out.strip().splitlines()[0] if out.strip() else ""
        return int(pid_line) if pid_line.isdigit() else None
    except Exception:
        return None


def force_stop_node_by_port(port: int) -> bool:
    pid = find_listener_pid_by_port(port)
    if not pid:
        return False
    try:
        if os.name == "nt":
            subprocess.check_call(["taskkill", "/PID", str(pid), "/F"])
        else:
            subprocess.check_call(["kill", "-9", str(pid)])
        return True
    except Exception:
        return False


def start_managed_node(node_id: str, fresh: bool = False) -> Dict[str, Any]:
    node = resolve_node(node_id)
    control_mode = detect_control_mode(node)
    if control_mode == "docker":
        return docker_start_node(node_id)
    if control_mode != "local_process":
        return {"status": "error", "node_id": node_id, "message": "Node này không hỗ trợ điều khiển tự động"}

    if is_managed_node_alive(node_id):
        return {"status": "ok", "node_id": node_id, "message": "Node đã chạy", "managed_process": True}

    cmd = build_node_command(node_id, fresh=fresh)
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=str(PROJECT_DIR),
    )
    MANAGED_NODE_PROCS[node_id] = proc
    time.sleep(0.25)
    return {
        "status": "ok",
        "node_id": node_id,
        "message": "Đã bật node",
        "pid": proc.pid,
        "control_mode": "local_process",
        "managed_process": is_managed_node_alive(node_id),
    }


def stop_managed_node(node_id: str) -> Dict[str, Any]:
    node = resolve_node(node_id)
    # Ưu tiên tắt mềm qua RPC để dùng được cho cả local và Docker.
    try:
        proxy = connect_node(node_id, timeout=max(RPC_TIMEOUT, 2.0))
        result = json.loads(proxy.shutdown(0.1))
        if result.get("status") == "ok":
            MANAGED_NODE_PROCS.pop(node_id, None)
            return {
                "status": "ok",
                "node_id": node_id,
                "message": "Đã gửi lệnh tắt node qua RPC",
                "control_mode": "rpc",
                "managed_process": False,
            }
    except Exception:
        pass

    control_mode = detect_control_mode(node)
    if control_mode == "docker":
        return docker_stop_node(node_id)
    if control_mode != "local_process":
        return {"status": "error", "node_id": node_id, "message": "Node này không hỗ trợ điều khiển tự động"}

    proc = MANAGED_NODE_PROCS.get(node_id)
    if proc is None or proc.poll() is not None:
        MANAGED_NODE_PROCS.pop(node_id, None)
        port = int(node.get("port", 0))
        if port > 0 and force_stop_node_by_port(port):
            return {
                "status": "ok",
                "node_id": node_id,
                "message": "Đã tắt node (force theo port)",
                "managed_process": False,
                "forced": True,
                "control_mode": "local_process",
            }
        return {
            "status": "error",
            "node_id": node_id,
            "message": "Node không do Web Manager khởi chạy hoặc đã dừng trước đó",
        }

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
    MANAGED_NODE_PROCS.pop(node_id, None)
    return {
        "status": "ok",
        "node_id": node_id,
        "message": "Đã tắt node",
        "managed_process": False,
        "control_mode": "local_process",
    }


def collect_online_node_ids() -> List[str]:
    """Hỏi tất cả node SONG SONG (deadline cứng) để node chết không kéo chậm."""
    if not CLUSTER_NODES:
        return []

    def _probe(cfg: Dict[str, Any]) -> Optional[str]:
        try:
            connect_node(str(cfg.get("id")), timeout=STATUS_RPC_TIMEOUT).get_node_info()
            return str(cfg.get("id"))
        except Exception:
            return None

    pool = ThreadPoolExecutor(max_workers=len(CLUSTER_NODES))
    futures = [pool.submit(_probe, cfg) for cfg in CLUSTER_NODES]
    end = time.time() + 1.0
    online: List[str] = []
    for fut in futures:
        try:
            r = fut.result(timeout=max(0.05, end - time.time()))
            if r:
                online.append(r)
        except Exception:
            continue
    pool.shutdown(wait=False)
    return online


def safe_node_public(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": node.get("id"),
        "node_id": node.get("id"),
        "host": node.get("host"),
        "port": int(node.get("port")),
        "url": node_url(node),
        "label": f"{node.get('id')} — {node.get('host')}:{int(node.get('port'))}",
    }


# ─── Membership động: thêm/xóa node lúc đang chạy ─────

def _node_min(n: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": str(n["id"]), "host": str(n["host"]), "port": int(n["port"])}


def membership_payload(nodes: List[Dict[str, Any]]) -> str:
    """Chuỗi JSON membership gửi cho RPC apply_membership của node."""
    return json.dumps([_node_min(n) for n in nodes], ensure_ascii=False)


def persist_membership() -> None:
    """Ghi membership hiện tại xuống file config để node restart vẫn đúng ring."""
    try:
        path = Path(CLUSTER_CONFIG_PATH)
        config: Dict[str, Any] = {}
        if path.exists():
            config = json.loads(path.read_text(encoding="utf-8"))
        config["nodes"] = [_node_min(n) for n in CLUSTER_NODES]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        record_log(f"Không ghi được membership xuống {CLUSTER_CONFIG_PATH}: {exc}", "warn")


def wait_node_online(node_id: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            connect_node(node_id, timeout=1.5).get_node_info()
            return True
        except Exception:
            time.sleep(0.7)
    return False


def broadcast_membership(targets: List[Dict[str, Any]], payload: str,
                         timeout: float = 25.0) -> Dict[str, Any]:
    """Gửi membership mới tới từng node trong `targets` (tuần tự, an toàn)."""
    results: Dict[str, Any] = {}
    for cfg in targets:
        nid = str(cfg["id"])
        try:
            proxy = xmlrpc.client.ServerProxy(
                node_url(cfg),
                transport=TimeoutTransport(timeout=timeout),
                allow_none=True,
            )
            results[nid] = json.loads(proxy.apply_membership(payload))
        except Exception as exc:
            results[nid] = {"status": "error", "message": str(exc)}
    return results


# ─── Web Pages

@app.route("/")
def index():
    return render_template("index.html")


#  API: Cluster nodes

@app.route("/api/cluster/nodes")
def cluster_nodes():
    return jsonify([safe_node_public(n) for n in CLUSTER_NODES])


#  API: Cluster Status

def _offline_node_status(node_cfg: Dict[str, Any], control_mode: str = "none") -> Dict[str, Any]:
    node_id = str(node_cfg.get("id"))
    return {
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
        "control_capable": control_mode != "none",
        "control_mode": control_mode,
        "managed_process": is_managed_node_alive(node_id),
    }


def _collect_with_deadline(fn, fallback_fn, deadline_sec: float = 1.2) -> List[Dict[str, Any]]:
    """Hỏi tất cả node song song với deadline cứng.

    Khi container bị tắt, DNS resolution có thể treo lâu hơn nhiều so với
    socket timeout, nên không thể chỉ dựa vào timeout của XML-RPC.
    Quá deadline thì coi node là OFFLINE và trả kết quả ngay.
    """
    if not CLUSTER_NODES:
        return []
    pool = ThreadPoolExecutor(max_workers=len(CLUSTER_NODES))
    futures = [(cfg, pool.submit(fn, cfg)) for cfg in CLUSTER_NODES]
    end_time = time.time() + deadline_sec
    results: List[Dict[str, Any]] = []
    for cfg, fut in futures:
        remaining = max(0.05, end_time - time.time())
        try:
            results.append(fut.result(timeout=remaining))
        except Exception:
            results.append(fallback_fn(cfg))
    # Không chờ các thread còn treo (DNS chậm); chúng tự kết thúc sau đó.
    pool.shutdown(wait=False)
    return results


def _fetch_node_status(node_cfg: Dict[str, Any]) -> Dict[str, Any]:
    node_id = str(node_cfg.get("id"))
    control_mode = detect_control_mode(node_cfg)
    try:
        proxy = connect_node(node_id, timeout=STATUS_RPC_TIMEOUT)
        info = json.loads(proxy.get_node_info())
        info["status"] = "ONLINE"
        info["id"] = info.get("id") or node_id
        info["node_id"] = info.get("node_id") or node_id
        info["host"] = info.get("host") or node_cfg.get("host")
        info["port"] = int(info.get("port") or node_cfg.get("port"))
        info["url"] = node_url(node_cfg)
        # Node đang online thì luôn có thể gửi lệnh tắt mềm qua RPC.
        info["control_capable"] = True
        info["control_mode"] = "rpc" if control_mode == "none" else control_mode
        info["managed_process"] = is_managed_node_alive(node_id)
        return info
    except Exception:
        return _offline_node_status(node_cfg, control_mode)


@app.route("/api/cluster/status")
def cluster_status():
    # Hỏi tất cả node song song để node chết không kéo chậm cả trang.
    # Fallback vẫn phải tính control_mode để UI hiện đúng nút "Bật node".
    nodes = _collect_with_deadline(
        _fetch_node_status,
        lambda cfg: _offline_node_status(cfg, detect_control_mode(cfg)),
    )
    return jsonify(nodes)


# ─── API: Node Control (local only) ───────────────────

@app.route("/api/node/<path:node_ref>/start", methods=["POST"])
def api_node_start(node_ref: str):
    try:
        data = request.json or {}
        fresh = bool(data.get("fresh"))
        node = resolve_node(node_ref)
        node_id = str(node.get("id"))
        result = start_managed_node(node_id, fresh=fresh)
        ok = result.get("status") == "ok"
        action = "KHÔI PHỤC (sạch)" if fresh else "BẬT"
        record_log(
            f"{action} node {node_id} -> {'OK' if ok else 'THẤT BẠI'} ({result.get('message', result.get('mode', ''))})",
            "ok" if ok else "err",
        )
        return jsonify(result), (200 if ok else 400)
    except Exception as e:
        record_log(f"BẬT node {node_ref} THẤT BẠI: {e}", "err")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/node/<path:node_ref>/stop", methods=["POST"])
def api_node_stop(node_ref: str):
    try:
        node = resolve_node(node_ref)
        node_id = str(node.get("id"))
        result = stop_managed_node(node_id)
        ok = result.get("status") == "ok"
        record_log(
            f"TẮT node {node_id} -> {'OK' if ok else 'THẤT BẠI'} ({result.get('message', result.get('mode', ''))})",
            "warn" if ok else "err",
        )
        return jsonify(result), (200 if ok else 400)
    except Exception as e:
        record_log(f"TẮT node {node_ref} THẤT BẠI: {e}", "err")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/demo/failover", methods=["POST"])
def api_demo_failover():
    data = request.json or {}
    key = str(data.get("key") or f"demo_failover_{int(time.time())}")
    value = str(data.get("value") or "demo_value")

    if not CLUSTER_NODES:
        return jsonify({"status": "error", "message": "Cluster chưa được cấu hình"}), 500

    online_before = collect_online_node_ids()
    if not online_before:
        return jsonify({"status": "error", "message": "Không có node nào online để chạy demo"}), 500

    entry_node_id = online_before[0]
    try:
        route = json.loads(connect_node(entry_node_id, timeout=max(RPC_TIMEOUT, 4.0)).get_routing_info(key))
    except Exception as e:
        return jsonify({"status": "error", "message": f"Không lấy được routing info: {e}"}), 500

    primary = route.get("primary") or {}
    primary_id = str(primary.get("id") or "")
    if not primary_id:
        return jsonify({"status": "error", "message": "Routing info thiếu primary node"}), 500

    stop_result = stop_managed_node(primary_id)
    if stop_result.get("status") != "ok":
        return jsonify(
            {
                "status": "error",
                "message": "Không thể tắt primary tự động. Hãy bật node bằng Web Manager trước khi chạy demo.",
                "route": route,
                "stop_result": stop_result,
            }
        ), 400

    time.sleep(0.25)
    online_after_stop = collect_online_node_ids()
    operator_node = next((nid for nid in online_after_stop if nid != primary_id), None)
    if operator_node is None:
        start_managed_node(primary_id)
        return jsonify({"status": "error", "message": "Không còn node online sau khi tắt primary"}), 500

    try:
        proxy = connect_node(operator_node, timeout=max(RPC_TIMEOUT, 4.0))
        put_result = json.loads(proxy.put(key, value))
        get_result = json.loads(proxy.get(key))
        delete_result = json.loads(proxy.delete(key))
    except Exception as e:
        start_managed_node(primary_id)
        return jsonify({"status": "error", "message": f"Lỗi khi chạy PUT/GET/DELETE demo: {e}"}), 500

    start_result = start_managed_node(primary_id)

    record_log(
        f"DEMO FAILOVER key='{key}' | tắt primary={primary_id} | "
        f"thao tác qua {operator_node}: put={put_result.get('status')}, "
        f"get={get_result.get('status')}, delete={delete_result.get('status')} | bật lại {primary_id}",
        "info",
    )

    return jsonify(
        {
            "status": "ok",
            "key": key,
            "value": value,
            "route": route,
            "entry_node": entry_node_id,
            "operator_node": operator_node,
            "stopped_primary": primary_id,
            "put_result": put_result,
            "get_result": get_result,
            "delete_result": delete_result,
            "restart_result": start_result,
            "online_before": online_before,
            "online_after_stop": online_after_stop,
        }
    )


#  API: PUT

@app.route("/api/put", methods=["POST"])
def api_put():
    data = request.json or {}
    key = data.get("key", "").strip()
    value = data.get("value", "")
    target = data.get("node_id") or data.get("node") or data.get("port")

    if not key:
        return jsonify({"status": "error", "message": "Key không được để trống"}), 400

    try:
        proxy = connect_entry_node(target, timeout=max(RPC_TIMEOUT, 4.0))
        result = json.loads(proxy.put(key, value))
        record_log(
            f"PUT '{key}' = '{value}' -> node={result.get('node', '?')} role={result.get('role', '?')}",
            "ok" if result.get("status") == "ok" else "err",
        )
        return jsonify(result)
    except Exception as e:
        record_log(f"PUT '{key}' THẤT BẠI: {e}", "err")
        return jsonify({"status": "error", "message": f"Node không phản hồi: {e}"}), 500


#  API: GET

@app.route("/api/get", methods=["POST"])
def api_get():
    data = request.json or {}
    key = data.get("key", "").strip()
    target = data.get("node_id") or data.get("node") or data.get("port")

    if not key:
        return jsonify({"status": "error", "message": "Key không được để trống"}), 400

    try:
        proxy = connect_entry_node(target, timeout=max(RPC_TIMEOUT, 4.0))
        result = json.loads(proxy.get(key))
        if result.get("status") == "ok":
            record_log(f"GET '{key}' -> '{result.get('value')}' từ node={result.get('node', '?')}", "ok")
        else:
            record_log(f"GET '{key}' -> NOT FOUND", "warn")
        return jsonify(result)
    except Exception as e:
        record_log(f"GET '{key}' THẤT BẠI: {e}", "err")
        return jsonify({"status": "error", "message": f"Node không phản hồi: {e}"}), 500


#  API: DELETE

@app.route("/api/delete", methods=["POST"])
def api_delete():
    data = request.json or {}
    key = data.get("key", "").strip()

    if not key:
        return jsonify({"status": "error", "message": "Key không được để trống"}), 400

    # XÓA QUÉT TOÀN CỤM: purge bản local trên MỌI node online (primary + replica +
    # cả bản "mồ côi"/degraded ghi lúc node chết, vốn nằm ngoài nhóm primary/replica).
    # delete(key, "primary") xóa cả data_store lẫn replica_store cục bộ, không forward,
    # nên quét qua tất cả node là đảm bảo key biến mất khỏi mọi nơi.
    def _purge(node_cfg: Dict[str, Any]) -> Dict[str, Any]:
        node_id = str(node_cfg.get("id"))
        try:
            proxy = connect_node(node_id, timeout=max(RPC_TIMEOUT, 2.0))
            res = json.loads(proxy.delete(key, "primary"))
            return {"id": node_id, "online": True, "status": res.get("status")}
        except Exception as exc:
            return {"id": node_id, "online": False, "status": "offline", "error": str(exc)}

    results = _collect_with_deadline(
        _purge,
        lambda cfg: {"id": str(cfg.get("id")), "online": False, "status": "offline"},
        deadline_sec=3.0,
    )

    deleted_on = [r["id"] for r in results if r.get("status") == "ok"]
    offline = [r["id"] for r in results if not r.get("online")]
    any_deleted = bool(deleted_on)

    if any_deleted:
        msg = f"DELETE '{key}' -> đã xóa trên {', '.join(deleted_on)}"
        if offline:
            msg += f" (node offline bỏ qua: {', '.join(offline)})"
        record_log(msg, "ok")
        return jsonify(
            {
                "status": "ok",
                "node": deleted_on[0],
                "deleted_on": deleted_on,
                "offline_nodes": offline,
                "message": "Đã xóa khỏi toàn bộ node online",
            }
        )

    record_log(f"DELETE '{key}' -> không tồn tại (không node online nào giữ key)", "warn")
    return jsonify(
        {
            "status": "not_found",
            "key": key,
            "offline_nodes": offline,
            "message": "Key không tồn tại trên các node online",
        }
    )


# ─── API: Nhật ký hoạt động ─────────────────────────

@app.route("/api/logs")
def api_logs():
    """Trả về log gần đây. Hỗ trợ ?after=<id> để chỉ lấy log mới (long-poll nhẹ)."""
    after = request.args.get("after", type=int)
    with _LOG_LOCK:
        entries = list(_LOG_BUFFER)
    if after is not None:
        entries = [e for e in entries if e["id"] > after]
    last_id = entries[-1]["id"] if entries else (after or 0)
    return jsonify({"logs": entries, "last_id": last_id})


@app.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    with _LOG_LOCK:
        _LOG_BUFFER.clear()
    record_log("Đã xóa nhật ký hoạt động", "info")
    return jsonify({"status": "ok"})


# ─── API: Tất cả dữ liệu ────────────────────────────

def _fetch_node_all_data(node_cfg: Dict[str, Any]) -> Dict[str, Any]:
    node_id = str(node_cfg.get("id"))
    try:
        proxy = connect_node(node_id, timeout=STATUS_RPC_TIMEOUT)
        raw = json.loads(proxy.get_all_data())
        return {"id": node_id, "online": True, "data": raw}
    except Exception:
        return {"id": node_id, "online": False, "data": None}


def _offline_node_all_data(node_cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": str(node_cfg.get("id")), "online": False, "data": None}


@app.route("/api/all-data")
def api_all_data():
    # Hỏi tất cả node song song với deadline để node chết không kéo chậm trang.
    results = _collect_with_deadline(_fetch_node_all_data, _offline_node_all_data, deadline_sec=1.5)

    all_entries: Dict[str, Dict[str, Any]] = {}
    online_node_ids: List[str] = []
    for item in results:
        if not item["online"]:
            continue
        node_id = item["id"]
        online_node_ids.append(node_id)
        raw = item["data"] or {}
        for k, v in raw.get("primary", {}).items():
            if k not in all_entries:
                all_entries[k] = {"key": k, "value": v, "primary_node": node_id, "replica_nodes": []}
            else:
                all_entries[k]["primary_node"] = node_id
        for k, v in raw.get("replica", {}).items():
            if k not in all_entries:
                all_entries[k] = {"key": k, "value": v, "primary_node": None, "replica_nodes": [node_id]}
            else:
                all_entries[k]["replica_nodes"].append(node_id)

    # Nếu key chỉ còn ở replica (primary đang down), tra hash ring qua MỘT NODE
    # CÒN SỐNG để hiển thị đúng "node gốc". Dùng 1 lời gọi hàng loạt thay vì lặp.
    if online_node_ids:
        missing = [k for k, e in all_entries.items() if not e.get("primary_node")]
        if missing:
            try:
                route_proxy = connect_node(online_node_ids[0], timeout=max(RPC_TIMEOUT, 2.0))
                routed = json.loads(route_proxy.get_routing_bulk(json.dumps(missing)))
                for key, primary_id in routed.items():
                    if primary_id and key in all_entries:
                        all_entries[key]["primary_node"] = primary_id
                        all_entries[key]["primary_inferred"] = True
            except Exception:
                pass

    # Đánh dấu key có node gốc đang offline -> UI hiển thị "đang phục vụ từ bản sao".
    for entry in all_entries.values():
        primary = entry.get("primary_node")
        entry["primary_offline"] = bool(primary) and primary not in online_node_ids

    return jsonify(list(all_entries.values()))


# ─── API: Routing Info ───────────────────────────────

@app.route("/api/routing/<path:key>")
def api_routing(key):
    for node_cfg in CLUSTER_NODES:
        try:
            proxy = connect_node(node_cfg.get("id"))
            result = json.loads(proxy.get_routing_info(key))
            return jsonify(result)
        except Exception:
            continue
    return jsonify({"status": "error", "message": "Không có node nào online"}), 500


# ─── API: Hash Ring (virtual nodes) ──────────────────

@app.route("/api/ring")
def api_ring():
    """Lấy cấu trúc hash ring + phân bố keyspace từ một node còn sống.

    Mọi node dùng chung config nên ring giống nhau; chỉ cần hỏi một node online.
    Sau đó bổ sung trạng thái online tức thời + số key thật mỗi node đang giữ,
    để biểu đồ phản ánh đúng thực tế (node chết -> xám, kèm tải thực tế).
    """
    last_err: Optional[str] = None
    ring_payload: Optional[Dict[str, Any]] = None
    for node_cfg in CLUSTER_NODES:
        node_id = str(node_cfg.get("id"))
        try:
            proxy = connect_node(node_id, timeout=max(RPC_TIMEOUT, 2.0))
            ring_payload = json.loads(proxy.get_ring_info())
            ring_payload["source_node"] = node_id
            break
        except Exception as exc:
            last_err = str(exc)
            continue

    if ring_payload is None:
        return jsonify({"status": "error", "message": f"Không có node nào online ({last_err})"}), 500

    # Hỏi song song trạng thái thật + số key của từng node (node chết không kéo chậm).
    live = _collect_with_deadline(
        _fetch_node_status,
        lambda cfg: _offline_node_status(cfg, detect_control_mode(cfg)),
    )
    live_by_id = {str(n.get("id") or n.get("node_id")): n for n in live}

    dist = ring_payload.get("distribution", {})
    any_dead = False
    for node in dist.get("nodes", []):
        info = live_by_id.get(node["id"], {})
        online = str(info.get("status", "")).upper() == "ONLINE"
        node["status"] = "ALIVE" if online else "DEAD"
        node["actual_keys"] = int(info.get("primary_count", 0) or 0)
        node["replica_keys"] = int(info.get("replica_count", 0) or 0)
        if not online:
            any_dead = True
    dist["any_dead"] = any_dead
    dist["alive_count"] = sum(1 for n in dist.get("nodes", []) if n["status"] == "ALIVE")

    # Tính lại phân bố HIỆU DỤNG theo trạng thái online tức thời của manager
    # (đáng tin hơn heartbeat). Dồn keyspace node chết sang node sống kế tiếp.
    points = ring_payload.get("points", [])
    if any_dead and points:
        alive_ids = {n["id"] for n in dist.get("nodes", []) if n["status"] == "ALIVE"}
        # points đã sắp theo góc tăng dần; tính cung giữa 2 điểm liên tiếp.
        n_pts = len(points)
        eff = {nid: 0.0 for nid in alive_ids}
        for i in range(n_pts):
            cur = points[i]["angle"]
            prev = points[i - 1]["angle"]
            arc = (cur - prev) % 360.0
            owner = points[i]["node"]
            if owner not in alive_ids:
                for step in range(1, n_pts + 1):
                    cand = points[(i + step) % n_pts]["node"]
                    if cand in alive_ids:
                        owner = cand
                        break
            if owner in eff:
                eff[owner] += arc
        for node in dist.get("nodes", []):
            node["effective_percent"] = (
                round(eff.get(node["id"], 0.0) / 360.0 * 100, 2) if node["status"] == "ALIVE" else 0.0
            )
        # đồng thời cập nhật cờ alive cho từng điểm để JS tô xám
        for p in points:
            p["alive"] = p["node"] in alive_ids
    ring_payload["distribution"] = dist
    return jsonify(ring_payload)


# ─── API: Force Sync ─────────────────────────────────

@app.route("/api/sync/<path:node_ref>", methods=["POST"])
def api_sync(node_ref: str):
    try:
        # force_sync có thể mất vài giây (nạp đĩa + hỏi hàng xóm) nên cho hạn chờ rộng.
        proxy = connect_node(node_ref, timeout=max(RPC_TIMEOUT, 15.0))
        result = json.loads(proxy.force_sync())
        record_log(
            f"ĐỒNG BỘ node {node_ref} -> primary={result.get('primary')}, replica={result.get('replica')}",
            "info",
        )
        return jsonify(result)
    except Exception as e:
        record_log(f"ĐỒNG BỘ node {node_ref} THẤT BẠI: {e}", "err")
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── API: Thêm / Xóa / Khôi phục node động ───────────

def _join_cluster(nid: str, host: str, port: Any, host_port: Any,
                  fresh: bool, op_label: str):
    """Lõi dùng chung cho THÊM và KHÔI PHỤC node. Trả (dict, http_status)."""
    if not nid:
        return {"status": "error", "message": "Thiếu id node"}, 400
    if nid in NODES_BY_ID:
        return {"status": "error", "message": f"Node '{nid}' đã tồn tại"}, 400

    mode = cluster_runtime_mode()
    old_nodes = list(CLUSTER_NODES)
    create_res: Dict[str, Any] = {}

    if mode == "docker":
        internal_port = 8000
        try:
            hp = int(host_port) if host_port not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            hp = None
        new_node = {"id": nid, "host": nid, "port": internal_port}
        CLUSTER_NODES.append(new_node)
        NODES_BY_ID[nid] = new_node
        persist_membership()
        create_res = docker_create_node(nid, host_port=hp, internal_port=internal_port, fresh=fresh)
        if create_res.get("status") != "ok" or not wait_node_online(nid, timeout=30):
            docker_remove_node(nid)
            CLUSTER_NODES[:] = old_nodes
            NODES_BY_ID.pop(nid, None)
            persist_membership()
            return {"status": "error",
                    "message": f"Không tạo/bật được container: {create_res.get('message', 'timeout')}",
                    "create": create_res}, 400
    else:
        host = str(host or "127.0.0.1").strip()
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"status": "error", "message": "port không hợp lệ"}, 400
        for n in CLUSTER_NODES:
            if str(n.get("host")) == host and int(n.get("port")) == port:
                return {"status": "error", "message": f"Đã có node ở {host}:{port}"}, 400
        new_node = {"id": nid, "host": host, "port": port}
        CLUSTER_NODES.append(new_node)
        NODES_BY_ID[nid] = new_node
        persist_membership()
        create_res = start_managed_node(nid, fresh=fresh)
        if create_res.get("status") != "ok" or not wait_node_online(nid, timeout=20):
            CLUSTER_NODES[:] = old_nodes
            NODES_BY_ID.pop(nid, None)
            persist_membership()
            return {"status": "error",
                    "message": f"Không bật được node: {create_res.get('message', 'timeout')}",
                    "start": create_res}, 400

    # Báo membership mới cho mọi node -> dữ liệu của node này tự chảy về từ cụm.
    payload = membership_payload(CLUSTER_NODES)
    apply_res = broadcast_membership(CLUSTER_NODES, payload)
    moved = sum(int((r.get("migrated") or {}).get("pushed", 0))
                for r in apply_res.values() if isinstance(r, dict))

    # Nếu node này từng nằm trong danh sách "đã xóa" thì gỡ ra.
    global REMOVED_NODES
    REMOVED_NODES = [n for n in REMOVED_NODES if str(n["id"]) != nid]

    record_log(f"{op_label} node {nid} [{mode}] — di trú {moved} key", "ok")
    return {"status": "ok", "mode": mode, "node": new_node,
            "membership": [str(n["id"]) for n in CLUSTER_NODES],
            "keys_migrated": moved, "apply": apply_res, "create": create_res}, 200


@app.route("/api/cluster/add-node", methods=["POST"])
def api_add_node():
    """Thêm một node MỚI vào cluster lúc đang chạy (local hoặc Docker)."""
    data = request.json or {}
    nid = str(data.get("id") or data.get("node_id") or "").strip()
    body, code = _join_cluster(
        nid, data.get("host"), data.get("port"),
        data.get("host_port", data.get("port")), fresh=False, op_label="THÊM",
    )
    return jsonify(body), code


@app.route("/api/cluster/removed-nodes")
def api_removed_nodes():
    """Danh sách các node đã bị xóa (có thể khôi phục)."""
    return jsonify({"removed": [{"id": n["id"], "host": n["host"], "port": n["port"]}
                                for n in REMOVED_NODES]})


@app.route("/api/cluster/restore-node", methods=["POST"])
def api_restore_node():
    """KHÔI PHỤC một node đã bị xóa: đưa node trở lại cụm với đúng id, và lấy lại
    dữ liệu HIỆN HÀNH từ các node còn sống (nguồn chuẩn). Node khởi động 'sạch'
    (bỏ dữ liệu cũ có thể đã lỗi thời trên đĩa) để không ghi đè dữ liệu mới."""
    data = request.json or {}
    nid = str(data.get("id") or data.get("node_id") or "").strip()
    if not nid:
        return jsonify({"status": "error", "message": "Thiếu id node"}), 400

    # Lấy host/port đã ghi nhớ khi xóa (cho phép ghi đè từ request nếu cần).
    remembered = next((n for n in REMOVED_NODES if str(n["id"]) == nid), None)
    host = data.get("host") or (remembered or {}).get("host") or "127.0.0.1"
    port = data.get("port") or (remembered or {}).get("port")
    host_port = data.get("host_port", port)

    body, code = _join_cluster(nid, host, port, host_port, fresh=True, op_label="KHÔI PHỤC")
    return jsonify(body), code


@app.route("/api/cluster/remove-node", methods=["POST"])
def api_remove_node():
    """Xóa một node khỏi cluster lúc đang chạy (không mất dữ liệu).

    Luồng: báo membership mới cho các node ở lại -> node bị xóa drain dữ liệu
    sang chủ mới -> tắt (local) hoặc XÓA HẲN container (Docker) -> persist.
    """
    data = request.json or {}
    nid = str(data.get("id") or data.get("node_id") or "").strip()

    if nid not in NODES_BY_ID:
        return jsonify({"status": "error", "message": f"Node '{nid}' không tồn tại"}), 400
    if len(CLUSTER_NODES) <= 1:
        return jsonify({"status": "error", "message": "Không thể xóa node cuối cùng"}), 400

    removed_cfg = NODES_BY_ID[nid]
    survivors = [n for n in CLUSTER_NODES if str(n["id"]) != nid]
    payload = membership_payload(survivors)

    # 1) Các node ở lại dựng ring mới trước (sẵn sàng nhận dữ liệu).
    survivor_res = broadcast_membership(survivors, payload)

    # 2) Node bị xóa (nếu còn online) drain dữ liệu sang survivors rồi tự loại.
    try:
        proxy = xmlrpc.client.ServerProxy(
            node_url(removed_cfg),
            transport=TimeoutTransport(timeout=25.0),
            allow_none=True,
        )
        drained = json.loads(proxy.apply_membership(payload))
    except Exception as exc:
        drained = {"status": "skip",
                   "message": f"Node bị xóa offline — dữ liệu được khôi phục từ replica ({exc})"}

    # 3) Dừng node bị xóa: Docker thì xóa hẳn container, local thì tắt tiến trình.
    if docker_socket_available() and find_existing_docker_container(nid):
        stop_res = docker_remove_node(nid)
    else:
        stop_res = stop_managed_node(nid)

    # 4) Cập nhật membership chính thức + persist.
    CLUSTER_NODES[:] = survivors
    NODES_BY_ID.pop(nid, None)
    persist_membership()

    # Ghi nhớ node vừa xóa để có thể KHÔI PHỤC lại sau (cùng id/host/port).
    global REMOVED_NODES
    REMOVED_NODES = [n for n in REMOVED_NODES if str(n["id"]) != nid]
    REMOVED_NODES.append({"id": nid, "host": str(removed_cfg["host"]),
                          "port": int(removed_cfg["port"]), "removed_at": time.time()})

    record_log(f"XÓA node {nid} — drain: {(drained.get('migrated') or drained.get('message'))}", "warn")
    return jsonify({
        "status": "ok",
        "removed": nid,
        "membership": [str(n["id"]) for n in CLUSTER_NODES],
        "drained": drained,
        "survivors": survivor_res,
        "stop": stop_res,
    })


def _offline_node_stats(node_cfg: Dict[str, Any]) -> Dict[str, Any]:
    node_id = str(node_cfg.get("id"))
    return {
        "id": node_id,
        "node_id": node_id,
        "host": node_cfg.get("host"),
        "port": int(node_cfg.get("port")),
        "status": "OFFLINE",
        "primary_count": 0,
        "replica_count": 0,
    }


def _fetch_node_stats(node_cfg: Dict[str, Any]) -> Dict[str, Any]:
    node_id = str(node_cfg.get("id"))
    try:
        proxy = connect_node(node_id, timeout=STATUS_RPC_TIMEOUT)
        info = json.loads(proxy.get_node_info())
        return {
            "id": node_id,
            "node_id": node_id,
            "host": node_cfg.get("host"),
            "port": int(node_cfg.get("port")),
            "status": "ONLINE",
            "primary_count": info.get("primary_count", 0),
            "replica_count": info.get("replica_count", 0),
        }
    except Exception:
        return _offline_node_stats(node_cfg)


@app.route("/api/cluster/summary")
@app.route("/api/dashboard/stats")
def dashboard_stats():
    nodes_info = _collect_with_deadline(_fetch_node_stats, _offline_node_stats)

    online_count = sum(1 for n in nodes_info if n["status"] == "ONLINE")
    total_primary = sum(n["primary_count"] for n in nodes_info)
    total_replica = sum(n["replica_count"] for n in nodes_info)

    total_nodes = len(CLUSTER_NODES)
    health = round(online_count / total_nodes * 100) if total_nodes > 0 else 0

    return jsonify(
        {
            "total_keys": total_primary,
            "online_nodes": online_count,
            "total_nodes": total_nodes,
            "total_replicas": total_replica,
            "cluster_health": health,
            "nodes": nodes_info,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Web UI quản lý KV Store phân tán")
    parser.add_argument("--config", default=os.environ.get("CLUSTER_CONFIG", "cluster_config.json"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    load_cluster_config(args.config)
    print("=" * 60)
    print("  KV Store Management App")
    print(f"  Config: {CLUSTER_CONFIG_PATH}")
    print(f"  Web UI: http://{args.host}:{args.port}")
    print("  Cluster nodes:")
    for n in CLUSTER_NODES:
        print(f"   - {n.get('id')}: {node_url(n)}")
    print("=" * 60)
    record_log(f"Manager khởi động, quản lý {len(CLUSTER_NODES)} node", "info")
    app.run(host=args.host, port=args.port, debug=False)
else:
    load_cluster_config(CLUSTER_CONFIG_PATH)
