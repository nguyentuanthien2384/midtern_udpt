"""Docker healthcheck cho Flask Manager."""
from __future__ import annotations

import argparse
import sys
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    try:
        with urllib.request.urlopen(f"http://{args.host}:{args.port}/api/dashboard/stats", timeout=3) as resp:
            # Healthcheck phải fail nếu endpoint trả về 4xx/5xx.
            return 0 if 200 <= resp.status < 300 else 1
    except Exception as exc:
        print(f"Manager healthcheck failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
