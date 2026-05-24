"""Run the FastAPI control plane with production defaults."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--reload", action="store_true", help="development only")
    args = parser.parse_args()
    uvicorn.run(
        "shorts_pipeline.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
