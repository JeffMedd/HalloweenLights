"""Test mode: latching control of individual strips, and wiring test sequences.

While test mode is active the game is suspended and the physical button only
reports presses. Anything can be lit: a single strip, a sash, a window, a bay
or the whole house, each with its own colour, brightness and pattern, and it
stays lit until switched off. On top of that there are automated sequences for
wiring checks, and the A/B comparison used to decide whether the strips need
aluminium channel and diffusers.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from .colour import hex_to_rgb, normalise_hex, rgb_to_hex

PATTERNS = {
    "solid": "Solid",
    "alternate": "Every other pixel",
    "markers": "First pixel green, last red",
    "ramp": "Brightness ramp along the strip",
    "chase": "Single pixel chase",
    "blink": "Blink once a second",
}

SEQUENCES = {
    "walk": "Pixel walk",
    "strips": "Strips one at a time",
    "sections": "Sashes one at a time",
    "rgbw": "Colour order check",
    "ramp": "Brightness ramp",
    "sync": "Floor sync flash",
    "load": "Full white load test",
}

RGBW_STEPS = [("RED", (1.0, 0.0, 0.0)), ("GREEN", (0.0, 1.0, 0.0)),
              ("BLUE", (0.0, 0.0, 1.0)), ("WHITE", (1.0, 1.0, 1.0))]


class TestMode:
    def __init__(self, geo, cfg: Dict[str, Any]):
        self.geo = geo
        self.cfg = cfg["test"]
        self.active = False
        self.entered_at = 0.0
        self.last_activity = 0.0
        self.lit: Dict[str, Dict[str, Any]] = {}
        self.sequence: Optional[Dict[str, Any]] = None
        self.lamp_override: Optional[float] = None
        self.info = ""
        self.exit_reason = ""

    # -- lifecycle --------------------------------------------------------

    def rebind(self, geo, cfg: Dict[str, Any]) -> None:
        """Geometry or settings changed: drop anything that no longer exists."""
        self.geo = geo
        self.cfg = cfg["test"]
        self.lit = {k: v for k, v in self.lit.items() if k in geo.strip_by_key}
        if self.sequence:
            self.sequence["scope"] = [k for k in self.sequence["scope"]
                                      if k in geo.strip_by_key] or [s["key"] for s in geo.strips]

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def enter(self) -> None:
        if not self.active:
            self.active = True
            self.entered_at = time.monotonic()
            self.exit_reason = ""
        self.touch()

    def exit(self, reason: str = "") -> None:
        self.active = False
        self.lit.clear()
        self.sequence = None
        self.lamp_override = None
        self.info = ""
        self.exit_reason = reason

    def check_timeout(self, now: float) -> None:
        limit = float(self.cfg.get("timeout_minutes", 30)) * 60.0
        if self.active and now - self.last_activity > limit:
            self.exit("timed out after no activity")

    # -- manual control ---------------------------------------------------

    def set(self, targets: Iterable[str], on: str = "on", colour: Optional[str] = None,
            level: Optional[float] = None, pattern: Optional[str] = None) -> List[str]:
        self.enter()
        self.sequence = None
        keys = self.geo.resolve(targets)
        spec = {
            "colour": normalise_hex(colour, self.cfg.get("colour", "#FFFFFF")),
            "level": max(0.0, min(1.0, float(self.cfg.get("level", 1.0) if level is None else level))),
            "pattern": pattern if pattern in PATTERNS else self.cfg.get("pattern", "solid"),
        }
        if on == "toggle":
            # A group toggles as one: if everything in it is lit, switch it
            # off, otherwise light all of it.
            on = "off" if keys and all(k in self.lit for k in keys) else "on"
        for key in keys:
            if on == "off":
                self.lit.pop(key, None)
            else:
                self.lit[key] = dict(spec)
        return keys

    def restyle(self, colour: Optional[str], level: Optional[float],
                pattern: Optional[str]) -> None:
        """Apply new settings to everything already lit."""
        self.touch()
        for spec in self.lit.values():
            if colour:
                spec["colour"] = normalise_hex(colour, spec["colour"])
            if level is not None:
                spec["level"] = max(0.0, min(1.0, float(level)))
            if pattern in PATTERNS:
                spec["pattern"] = pattern

    def clear(self) -> None:
        self.touch()
        self.lit.clear()
        self.sequence = None

    def compare(self, a: Dict[str, Any], b: Dict[str, Any], keep_others: bool = False) -> None:
        """Light two targets side by side with independent settings."""
        self.enter()
        self.sequence = None
        if not keep_others:
            self.lit.clear()
        for side in (a, b):
            self.set([side.get("target", "")], "on", side.get("colour"),
                     side.get("level"), side.get("pattern"))

    # -- sequences --------------------------------------------------------

    def start_sequence(self, kind: str, scope: Optional[Iterable[str]] = None,
                       colour: Optional[str] = None, dwell: Optional[float] = None,
                       speed: Optional[float] = None) -> None:
        if kind not in SEQUENCES:
            raise ValueError(f"unknown sequence {kind}")
        self.enter()
        keys = self.geo.resolve(scope or []) if scope else []
        if not keys:
            keys = [s["key"] for s in self.geo.strips]
        self.sequence = {
            "kind": kind,
            "scope": keys,
            "started": time.monotonic(),
            "colour": normalise_hex(colour, "#FFFFFF"),
            "dwell": float(dwell or self.cfg.get("dwell_seconds", 3.0)),
            "speed": float(speed or self.cfg.get("walk_speed", 6.0)),
        }

    def stop_sequence(self) -> None:
        self.touch()
        self.sequence = None
        self.info = ""

    # -- rendering --------------------------------------------------------

    def render(self, now: float) -> np.ndarray:
        geo = self.geo
        frame = geo.blank()
        if self.sequence:
            return self._render_sequence(now, frame)
        self.info = ""
        for key, spec in self.lit.items():
            strip = geo.strip_by_key.get(key)
            if strip is None:
                continue
            n = strip["pixels"]
            base = np.array(hex_to_rgb(spec["colour"])) * spec["level"]
            px = np.tile(base, (n, 1))
            pattern = spec["pattern"]
            idx = np.arange(n)
            if pattern == "alternate":
                px[idx % 2 == 1] = 0.0
            elif pattern == "markers":
                px *= 0.15
                px[0] = (0.0, spec["level"], 0.0)
                px[-1] = (spec["level"], 0.0, 0.0)
            elif pattern == "ramp":
                px *= ((idx + 1) / n)[:, None]
            elif pattern == "chase":
                head = int(now * 6.0) % n
                dist = (head - idx) % n
                px *= np.where(dist < 3, 1.0 - dist / 3.0, 0.0)[:, None]
            elif pattern == "blink":
                if (now % 1.0) >= 0.5:
                    px[:] = 0.0
            frame[strip["start"]:strip["end"]] = px
        return frame

    def _render_sequence(self, now: float, frame: np.ndarray) -> np.ndarray:
        geo = self.geo
        seq = self.sequence
        age = now - seq["started"]
        scope = seq["scope"]
        col = np.array(hex_to_rgb(seq["colour"]))
        kind = seq["kind"]
        mask = geo.mask(scope)

        if kind == "walk":
            pixels = np.flatnonzero(mask)
            if len(pixels):
                step = int(age * seq["speed"]) % len(pixels)
                for back, level in ((0, 1.0), (1, 0.35), (2, 0.12)):
                    if step - back >= 0:
                        frame[pixels[step - back]] = col * level
                p = int(pixels[step])
                strip = geo.strips[geo.strip[p]]
                self.info = (f"{strip['section']} {strip['side']} strip, pixel "
                             f"{geo.pos[p] + 1} of {strip['pixels']} "
                             f"(overall {step + 1} of {len(pixels)})")
        elif kind in ("strips", "sections"):
            if kind == "strips":
                groups = [[k] for k in scope]
                names = [f"{geo.strip_by_key[k]['section']} {geo.strip_by_key[k]['side']}"
                         for k in scope]
            else:
                order = []
                for k in scope:
                    sid = geo.strip_by_key[k]["section"]
                    if sid not in order:
                        order.append(sid)
                groups = [[k for k in scope if geo.strip_by_key[k]["section"] == sid]
                          for sid in order]
                names = order
            if groups:
                i = int(age / seq["dwell"]) % len(groups)
                frame[geo.mask(groups[i])] = col
                self.info = f"{names[i]} ({i + 1} of {len(groups)})"
        elif kind == "rgbw":
            i = int(age / max(1.0, seq["dwell"] * 0.7)) % len(RGBW_STEPS)
            name, rgb = RGBW_STEPS[i]
            frame[mask] = rgb
            self.info = f"Every lit strip should be {name}"
        elif kind == "ramp":
            period = max(2.0, seq["dwell"] * 4)
            level = (age % period) / period
            frame[mask] = col * level
            self.info = f"Brightness {int(level * 100)}%"
        elif kind == "sync":
            on = (age % 1.0) < 0.5
            if on:
                frame[mask] = col
            self.info = "Both floors should flash together, once a second"
        elif kind == "load":
            limit = float(self.cfg.get("load_seconds", 60))
            if age >= limit:
                self.sequence = None
                self.info = "Load test finished"
                return frame
            frame[mask] = (1.0, 1.0, 1.0)
            self.info = f"Full white, {int(limit - age)}s left"
        return frame

    # -- reporting --------------------------------------------------------

    def state(self, now: float) -> Dict[str, Any]:
        limit = float(self.cfg.get("timeout_minutes", 30)) * 60.0
        seq = None
        if self.sequence:
            seq = {"kind": self.sequence["kind"],
                   "label": SEQUENCES[self.sequence["kind"]],
                   "scope": len(self.sequence["scope"])}
        return {
            "active": self.active,
            "lit": {k: {"colour": v["colour"], "level": round(v["level"], 3),
                        "pattern": v["pattern"]} for k, v in self.lit.items()},
            "sequence": seq,
            "info": self.info,
            "lamp_override": self.lamp_override,
            "timeout_in": round(max(0.0, limit - (now - self.last_activity))) if self.active else None,
            "exit_reason": self.exit_reason,
        }


def describe() -> Dict[str, Any]:
    return {"patterns": PATTERNS, "sequences": SEQUENCES}


__all__ = ["TestMode", "PATTERNS", "SEQUENCES", "describe", "rgb_to_hex"]
