"""Entry point: python -m display [options]"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .server import create_app

DEFAULT_ROOT = Path(
    os.environ.get("HALLOWEEN_HOME", Path(__file__).resolve().parent.parent)
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="display",
        description="Halloween window display controller",
    )
    parser.add_argument("--config", type=Path,
                        default=DEFAULT_ROOT / "config.json",
                        help="path to the JSON config file")
    parser.add_argument("--sounds", type=Path,
                        default=DEFAULT_ROOT / "sounds",
                        help="directory holding the sound files")
    parser.add_argument("--data", type=Path,
                        default=DEFAULT_ROOT / "data",
                        help="directory for runtime state such as round history")
    parser.add_argument("--host", default=None, help="override the web host")
    parser.add_argument("--port", type=int, default=None,
                        help="override the web port")
    parser.add_argument("--no-audio", action="store_true",
                        help="start with audio disabled")
    parser.add_argument("--no-output", action="store_true",
                        help="run the game but send no DDP packets (dry run)")
    args = parser.parse_args()

    args.sounds.mkdir(parents=True, exist_ok=True)
    args.data.mkdir(parents=True, exist_ok=True)

    app = create_app(args.config, args.sounds, args.data)
    store = app.state.store
    if args.no_audio:
        store.update({"audio": {"enabled": False}})
    if args.no_output:
        store.update({"render": {"enabled": False}})

    host = args.host or store.data["web"]["host"]
    port = args.port or store.data["web"]["port"]
    print(f"[display] web interface on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
