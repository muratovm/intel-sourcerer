"""Private authentication, article storage, and import API service."""

import argparse
import os

from .web import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/intel_sourcerer.sqlite3")
    parser.add_argument("--config", default="config/sources.toml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--secure-cookies",
        action="store_true",
        help="Set when the public viewer uses HTTPS",
    )
    args = parser.parse_args()
    key = os.environ.get("INTEL_BACKEND_KEY", "")
    if not key:
        parser.error(
            "Set INTEL_BACKEND_KEY to the same secret on the viewer and backend."
        )
    server = create_server(
        args.database,
        args.port,
        args.config,
        api_only=True,
        service_key=key,
        secure_cookies=args.secure_cookies,
        host=args.host,
    )
    print(
        f"Intel Sourcerer backend: http://{args.host}:{server.server_port}", flush=True
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
