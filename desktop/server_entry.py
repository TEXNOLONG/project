"""Explicit PyInstaller entry point for the bundled CRM service."""

import argparse

import uvicorn

# Keep the application import static so PyInstaller discovers FastAPI and the
# complete backend dependency graph instead of only bundling run.py.
from backend.main import app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
