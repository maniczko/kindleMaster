from __future__ import annotations

import os

import app as app_module
from durable_runtime_integration import install_durable_runtime


app = install_durable_runtime(app_module)


def main() -> None:
    from waitress import serve

    host = os.environ.get("KINDLEMASTER_BIND_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT") or os.environ.get("KINDLEMASTER_PORT") or "5001")
    threads = max(2, int(os.environ.get("KINDLEMASTER_API_THREADS", "4")))
    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
