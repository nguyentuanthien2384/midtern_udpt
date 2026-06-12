"""Docker healthcheck cho XML-RPC node."""
from __future__ import annotations

import argparse
import json
import sys
import xmlrpc.client


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = 2.0) -> None:
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host: str):  # type: ignore[override]
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    try:
        proxy = xmlrpc.client.ServerProxy(
            f"http://{args.host}:{args.port}",
            transport=TimeoutTransport(timeout=2.0),
            allow_none=True,
        )
        info = json.loads(proxy.get_node_info())
        return 0 if info.get("id") else 1
    except Exception as exc:
        print(f"Node healthcheck failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
