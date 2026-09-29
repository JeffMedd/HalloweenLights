#!/usr/bin/env python3
"""Point the config at the placeholder sounds produced by make_sounds.py.

    python3 tools/apply_default_sounds.py --config config.json --sounds sounds

Only fills in slots that are currently empty unless --force is given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from display.config import SECTION_ORDER, ConfigStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--sounds", type=Path, default=Path("sounds"))
    parser.add_argument("--force", action="store_true",
                        help="overwrite sounds that are already assigned")
    args = parser.parse_args()

    store = ConfigStore(args.config)
    store.load()
    cfg = store.data

    def available(name: str) -> str:
        return name if (args.sounds / name).is_file() else ""

    for key, filename in (("ambient", "ambient.wav"), ("button", "button.wav"),
                          ("tick", "tick.wav"), ("landing", "landing.wav")):
        if args.force or not cfg["audio"].get(key):
            found = available(filename)
            if found:
                cfg["audio"][key] = found
                print(f"audio.{key} -> {found}")

    for sid in SECTION_ORDER:
        if sid not in cfg["sections"]:
            continue
        if args.force or not cfg["sections"][sid].get("sound"):
            found = available(f"finale_{sid}.wav")
            if found:
                cfg["sections"][sid]["sound"] = found
                print(f"{sid} -> {found}")

    store.save(cfg)
    print(f"Saved {args.config}")


if __name__ == "__main__":
    main()
