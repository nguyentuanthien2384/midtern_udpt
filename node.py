
from __future__ import annotations

import xmlrpc.client
from dataclasses import dataclass
from typing import Any
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer
from socketserver import ThreadingMixIn


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


def main() -> None:
    # Smoke-run cho Day 2 (sẽ thay bằng main thật ở các ngày sau)
    ep = NodeEndpoint(id="node1", host="127.0.0.1", port=8000)
    print(" core classes loaded.")
    print(f"Endpoint: {ep.label} -> {ep.url}")


if __name__ == "__main__":
    main()
