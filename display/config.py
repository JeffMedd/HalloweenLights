"""Configuration handling for the Halloween window display.

The whole application is driven by a single JSON config file which the web
interface edits live.  Anything absent from the file falls back to the
defaults below, so a partially written config is never fatal, and configs
written by earlier versions load without any migration step.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

from .colour import PALETTE, hex_to_rgb, normalise_hex, rgb_to_hex  # noqa: F401 (re-exported)

# ---------------------------------------------------------------------------
# Section layout
# ---------------------------------------------------------------------------

# Canonical order: upstairs first, then ground. In each window "1" is the
# upper sash and "2" the lower sash; L and R are the left and right windows of
# the bay as seen from the street.
SECTION_ORDER = ["UL1", "UL2", "UR1", "UR2", "DL1", "DL2", "DR1", "DR2"]

SIDES = ("top", "right", "bottom", "left")

# id: (label, floor, row, col, colour, width_mm, height_mm)
# Sizes are the measured visible glass; they drive the on-screen drawing and
# the spatial effects, not the pixel counts.
_SECTION_META = {
    "UL1": ("Upstairs left, upper sash", "upstairs", 0, 0, "#FF6A00", 750, 690),
    "UL2": ("Upstairs left, lower sash", "upstairs", 1, 0, "#8A2BE2", 750, 700),
    "UR1": ("Upstairs right, upper sash", "upstairs", 0, 1, "#39FF14", 750, 690),
    "UR2": ("Upstairs right, lower sash", "upstairs", 1, 1, "#C81010", 750, 700),
    "DL1": ("Ground left, upper sash", "ground", 0, 0, "#F2F0E6", 750, 750),
    "DL2": ("Ground left, lower sash", "ground", 1, 0, "#FF6A00", 750, 750),
    "DR1": ("Ground right, upper sash", "ground", 0, 1, "#8A2BE2", 750, 750),
    "DR2": ("Ground right, lower sash", "ground", 1, 1, "#39FF14", 750, 750),
}

DEFAULT_STRIP_PIXELS = 13   # 722mm cut at 55.56mm pitch


def default_strips(total: int = 4 * DEFAULT_STRIP_PIXELS) -> List[Dict[str, Any]]:
    """Four strips, clockwise from the top left corner, total split evenly."""
    base = max(1, total // 4)
    counts = [base, base, base, max(1, total - base * 3)]
    return [{"side": side, "pixels": count, "reverse": False}
            for side, count in zip(SIDES, counts)]


def _default_sections() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for sid in SECTION_ORDER:
        label, floor, row, col, colour, width, height = _SECTION_META[sid]
        out[sid] = {
            "label": label,
            "enabled": True,
            "weight": 1.0,
            "colour": colour,
            "flash_colour": None,   # None means "use colour"
            "sound": "",            # filename inside the sounds directory
            "floor": floor,
            "row": row,
            "col": col,
            "width_mm": width,
            "height_mm": height,
            "strips": default_strips(),
            "pixels": 4 * DEFAULT_STRIP_PIXELS,   # derived from strips
        }
    return out


def _item(effect: str, duration: float, params: Dict[str, Any] | None = None,
          fade: float = 3.0) -> Dict[str, Any]:
    return {"id": uuid.uuid4().hex[:8], "effect": effect, "params": params or {},
            "duration": duration, "fade": fade, "enabled": True}


def _default_playlists() -> List[Dict[str, Any]]:
    return [
        {
            "id": "halloween",
            "name": "Halloween night",
            "shuffle": False,
            "items": [
                _item("breathe", 90, {"colour": "#8A2BE2"}),
                _item("lightning", 90),
                _item("ghosts", 90),
                _item("hellfire", 60),
                _item("eyes", 90),
                _item("cauldron", 60),
                _item("marquee", 45),
                _item("wave", 60),
                _item("poltergeist", 45),
                _item("drip", 60),
                _item("bats", 60),
                _item("searchlight", 45),
            ],
        },
        {
            "id": "quiet",
            "name": "Quiet glow",
            "shuffle": False,
            "items": [
                _item("breathe", 120, {"colour": "#8A2BE2"}, fade=6),
                _item("candle", 120, fade=6),
                _item("fireflies", 120, fade=6),
                _item("wave", 120, {"speed": 0.03}, fade=6),
            ],
        },
    ]


DEFAULTS: Dict[str, Any] = {
    "controllers": [
        {
            "name": "ground",
            "host": "192.168.1.51",
            "port": 4048,
            "enabled": True,
            # Order must match the WLED LED output order (GPIO 16, 12, 4, 2).
            "sections": ["DL1", "DL2", "DR1", "DR2"],
            "colour_order": "RGB",
            "white_mode": "none",   # none | auto
            "psu_watts": 240,
        },
        {
            "name": "upstairs",
            "host": "192.168.1.52",
            "port": 4048,
            "enabled": True,
            "sections": ["UL1", "UL2", "UR1", "UR2"],
            "colour_order": "RGB",
            "white_mode": "none",
            "psu_watts": 240,
        },
    ],
    "sections": _default_sections(),
    "layout": {
        "window_gap_mm": 230,   # the stone pier between the two windows of a bay
        "sash_gap_mm": 50,      # the meeting rail between upper and lower sash
        "floor_gap_mm": 700,    # between the bays; compressed from real life
    },
    "timing": {
        "cycle_seconds": 5.0,
        "min_step_ms": 70.0,
        "max_step_ms": 700.0,
        "steps": 0,               # 0 = auto fit to cycle_seconds
        "easing": 2.0,            # exponent of the ease-out curve
        "order": "random",        # random | sequential
        "trail": 0.35,            # how much the previous section lingers, 0-1
        "flash_seconds": 2.0,
        "flash_hz": 6.0,
        "flash_duty": 0.55,
        "hold_seconds": 1.0,      # winner stays solid after the flashing
        "fade_seconds": 1.5,      # fade from winner back to idle
        "cooldown_seconds": 2.0,  # presses ignored after a round
    },
    "idle": {
        "playlist": "halloween",  # id of the playlist that runs between rounds
        "master": 1.0,            # overall brightness of the idle effects
        "cycle_background": 0.30, # how lit the losing sections stay mid-round
    },
    "playlists": _default_playlists(),
    "test": {
        "timeout_minutes": 30,
        "colour": "#FFFFFF",
        "level": 1.0,
        "pattern": "solid",
        "dwell_seconds": 3.0,
        "walk_speed": 6.0,
        "load_seconds": 60.0,
    },
    "audio": {
        "enabled": True,
        "driver": "",             # blank = SDL default; e.g. "pulseaudio", "alsa"
        "device": "",             # blank = system default output
        "buffer": 512,
        "master_volume": 0.9,
        "offset_ms": 0.0,         # output latency compensation, e.g. 180 for Bluetooth
        "ambient": "",
        "ambient_volume": 0.3,
        "button": "",
        "button_volume": 0.9,
        "tick": "",
        "tick_volume": 0.6,
        "tick_enabled": True,
        "landing": "",
        "landing_volume": 1.0,
        "finale_delay_ms": 0.0,
        "finale_volume": 1.0,
    },
    "button": {
        "enabled": True,
        "switch_gpio": 17,
        "pull_up": True,
        "active_low": True,
        "bounce_ms": 60,
        "lamp_enabled": True,
        "lamp_gpio": 18,
        "lamp_pwm": True,
        "lamp_frequency": 200,
        "idle_brightness": 0.35,
        "idle_pulse": True,
        "idle_pulse_speed": 0.25,
        "cycle_flash_hz": 8.0,
        "landing_flash_hz": 3.0,
        "cooldown_brightness": 0.08,
    },
    "render": {
        "fps": 50,
        "master_brightness": 1.0,
        "gamma": 2.2,
        "enabled": True,
        "watts_per_pixel": 0.78,  # at full white: 14W/m over 18 pixels/m
        "supply_voltage": 24.0,
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8080,
        "preview_fps": 20,
    },
}


# ---------------------------------------------------------------------------
# Merge / validation helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = deepcopy(base)
        for key, value in override.items():
            out[key] = _deep_merge(base[key], value) if key in base else deepcopy(value)
        return out
    return deepcopy(override)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _num(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _validate_strips(raw: Any, fallback_total: int) -> List[Dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        return default_strips(max(4, fallback_total))
    strips = []
    for index, entry in enumerate(raw[:12]):
        if not isinstance(entry, dict):
            continue
        side = str(entry.get("side") or SIDES[index % 4]).lower()
        if side not in SIDES:
            side = SIDES[index % 4]
        strips.append({
            "side": side,
            "pixels": int(_clamp(_num(entry.get("pixels"), DEFAULT_STRIP_PIXELS), 1, 600)),
            "reverse": bool(entry.get("reverse", False)),
        })
    return strips or default_strips(max(4, fallback_total))


def _validate_section(sid: str, value: Dict[str, Any]) -> Dict[str, Any]:
    template = DEFAULTS["sections"].get(sid) or DEFAULTS["sections"]["UL1"]
    base = deepcopy(template)
    base.update(value or {})
    # Older configs had only a pixel count: split it into four strips.
    if "strips" not in (value or {}):
        base["strips"] = default_strips(max(4, int(_num(base.get("pixels"), 52))))
    base["strips"] = _validate_strips(base.get("strips"), int(_num(base.get("pixels"), 52)))
    base["pixels"] = sum(s["pixels"] for s in base["strips"])
    base["weight"] = max(0.0, _num(base.get("weight"), 1.0))
    base["enabled"] = bool(base.get("enabled", True))
    base["colour"] = rgb_to_hex(hex_to_rgb(base.get("colour", "#FFFFFF")))
    base["flash_colour"] = (rgb_to_hex(hex_to_rgb(base["flash_colour"]))
                            if base.get("flash_colour") else None)
    base["sound"] = str(base.get("sound") or "")
    base["label"] = str(base.get("label") or sid)
    base["floor"] = "ground" if str(base.get("floor")) == "ground" else "upstairs"
    base["row"] = int(_clamp(_num(base.get("row"), 0), 0, 5))
    base["col"] = int(_clamp(_num(base.get("col"), 0), 0, 5))
    base["width_mm"] = _clamp(_num(base.get("width_mm"), 750), 100, 5000)
    base["height_mm"] = _clamp(_num(base.get("height_mm"), 750), 100, 5000)
    return base


def _validate_playlists(raw: Any) -> List[Dict[str, Any]]:
    from . import effects  # local import: effects has no dependency on config

    if not isinstance(raw, list):
        return deepcopy(DEFAULTS["playlists"])
    out: List[Dict[str, Any]] = []
    seen = set()
    for entry in raw[:50]:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id") or uuid.uuid4().hex[:8])[:40]
        if pid in seen:
            pid = f"{pid}-{uuid.uuid4().hex[:4]}"
        seen.add(pid)
        items = []
        for item in (entry.get("items") or [])[:200]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("effect") or "")
            if key not in effects.REGISTRY:
                continue
            items.append({
                "id": str(item.get("id") or uuid.uuid4().hex[:8])[:40],
                "effect": key,
                "params": effects.validate_params(key, item.get("params") or {}),
                "duration": _clamp(_num(item.get("duration"), 60), 5, 7200),
                "fade": _clamp(_num(item.get("fade"), 3), 0, 60),
                "enabled": bool(item.get("enabled", True)),
            })
        out.append({
            "id": pid,
            "name": str(entry.get("name") or "Playlist")[:80],
            "shuffle": bool(entry.get("shuffle", False)),
            "items": items,
        })
    return out


def validate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Fill gaps, coerce types and clamp anything that could break the loop."""
    raw = cfg or {}
    cfg = _deep_merge(DEFAULTS, raw)

    # Lists replace rather than merge, so take playlists from the raw input.
    cfg["playlists"] = _validate_playlists(
        raw["playlists"] if "playlists" in raw else DEFAULTS["playlists"])

    # Sections ------------------------------------------------------------
    sections = raw.get("sections") if isinstance(raw.get("sections"), dict) else {}
    fixed: Dict[str, Any] = {}
    for sid in SECTION_ORDER:
        fixed[sid] = _validate_section(sid, sections.get(sid) or {})
    for sid, value in sections.items():
        if sid not in fixed and isinstance(value, dict):
            fixed[sid] = _validate_section(sid, value)
    cfg["sections"] = fixed

    # Layout --------------------------------------------------------------
    lay = cfg["layout"]
    lay["window_gap_mm"] = _clamp(_num(lay.get("window_gap_mm"), 230), 0, 5000)
    lay["sash_gap_mm"] = _clamp(_num(lay.get("sash_gap_mm"), 50), 0, 2000)
    lay["floor_gap_mm"] = _clamp(_num(lay.get("floor_gap_mm"), 700), 0, 10000)

    # Controllers ---------------------------------------------------------
    controllers = []
    for entry in cfg.get("controllers") or []:
        if not isinstance(entry, dict):
            continue
        ctrl = {
            "name": str(entry.get("name") or f"controller{len(controllers) + 1}"),
            "host": str(entry.get("host") or "").strip(),
            "port": int(_num(entry.get("port"), 4048) or 4048),
            "enabled": bool(entry.get("enabled", True)),
            "sections": [s for s in (entry.get("sections") or []) if s in cfg["sections"]],
            "colour_order": str(entry.get("colour_order") or "RGB").upper(),
            "white_mode": str(entry.get("white_mode") or "none").lower(),
            "psu_watts": _clamp(_num(entry.get("psu_watts"), 240), 1, 5000),
        }
        if ctrl["colour_order"] not in {"RGB", "GRB", "BRG", "RBG", "GBR", "BGR"}:
            ctrl["colour_order"] = "RGB"
        if ctrl["white_mode"] not in {"none", "auto"}:
            ctrl["white_mode"] = "none"
        controllers.append(ctrl)
    cfg["controllers"] = controllers

    # Timing --------------------------------------------------------------
    t = cfg["timing"]
    t["cycle_seconds"] = _clamp(_num(t["cycle_seconds"], 5), 0.5, 60.0)
    t["min_step_ms"] = _clamp(_num(t["min_step_ms"], 70), 20.0, 2000.0)
    t["max_step_ms"] = _clamp(_num(t["max_step_ms"], 700), t["min_step_ms"], 5000.0)
    t["steps"] = max(0, int(_num(t["steps"], 0)))
    t["easing"] = _clamp(_num(t["easing"], 2), 0.5, 6.0)
    t["order"] = "sequential" if str(t["order"]).lower() == "sequential" else "random"
    t["trail"] = _clamp(_num(t["trail"], 0.35), 0.0, 1.0)
    t["flash_seconds"] = _clamp(_num(t["flash_seconds"], 2), 0.0, 30.0)
    t["flash_hz"] = _clamp(_num(t["flash_hz"], 6), 0.5, 25.0)
    t["flash_duty"] = _clamp(_num(t["flash_duty"], 0.55), 0.05, 0.95)
    t["hold_seconds"] = _clamp(_num(t["hold_seconds"], 1), 0.0, 60.0)
    t["fade_seconds"] = _clamp(_num(t["fade_seconds"], 1.5), 0.0, 30.0)
    t["cooldown_seconds"] = _clamp(_num(t["cooldown_seconds"], 2), 0.0, 60.0)

    # Idle ----------------------------------------------------------------
    i = cfg["idle"]
    ids = [p["id"] for p in cfg["playlists"]]
    if str(i.get("playlist")) not in ids:
        i["playlist"] = ids[0] if ids else ""
    i["master"] = _clamp(_num(i.get("master"), 1.0), 0.0, 1.0)
    i["cycle_background"] = _clamp(_num(i.get("cycle_background"), 0.3), 0.0, 1.0)

    # Test ----------------------------------------------------------------
    tst = cfg["test"]
    tst["timeout_minutes"] = _clamp(_num(tst.get("timeout_minutes"), 30), 1, 240)
    tst["colour"] = normalise_hex(tst.get("colour"), "#FFFFFF")
    tst["level"] = _clamp(_num(tst.get("level"), 1.0), 0.0, 1.0)
    tst["dwell_seconds"] = _clamp(_num(tst.get("dwell_seconds"), 3), 0.3, 60)
    tst["walk_speed"] = _clamp(_num(tst.get("walk_speed"), 6), 0.5, 200)
    tst["load_seconds"] = _clamp(_num(tst.get("load_seconds"), 60), 5, 600)
    tst["pattern"] = str(tst.get("pattern") or "solid")

    # Audio ---------------------------------------------------------------
    a = cfg["audio"]
    a["enabled"] = bool(a["enabled"])
    a["tick_enabled"] = bool(a["tick_enabled"])
    a["buffer"] = int(_num(a.get("buffer"), 512)) if _num(a.get("buffer"), 512) > 0 else 512
    for key in ("master_volume", "ambient_volume", "button_volume",
                "tick_volume", "landing_volume", "finale_volume"):
        a[key] = _clamp(_num(a[key], 1.0), 0.0, 1.0)
    a["offset_ms"] = _clamp(_num(a["offset_ms"], 0), 0.0, 1000.0)
    a["finale_delay_ms"] = _clamp(_num(a["finale_delay_ms"], 0), 0.0, 5000.0)

    # Button --------------------------------------------------------------
    b = cfg["button"]
    b["enabled"] = bool(b["enabled"])
    b["switch_gpio"] = int(_num(b["switch_gpio"], 17))
    b["lamp_gpio"] = int(_num(b["lamp_gpio"], 18))
    b["bounce_ms"] = int(_clamp(_num(b["bounce_ms"], 60), 0.0, 2000.0))
    b["lamp_enabled"] = bool(b["lamp_enabled"])
    b["lamp_pwm"] = bool(b["lamp_pwm"])
    b["lamp_frequency"] = int(_clamp(_num(b["lamp_frequency"], 200), 30.0, 8000.0))
    b["idle_brightness"] = _clamp(_num(b["idle_brightness"], 0.35), 0.0, 1.0)
    b["cooldown_brightness"] = _clamp(_num(b["cooldown_brightness"], 0.08), 0.0, 1.0)
    b["idle_pulse"] = bool(b["idle_pulse"])
    b["idle_pulse_speed"] = _clamp(_num(b["idle_pulse_speed"], 0.25), 0.01, 5.0)
    b["cycle_flash_hz"] = _clamp(_num(b["cycle_flash_hz"], 8), 0.5, 30.0)
    b["landing_flash_hz"] = _clamp(_num(b["landing_flash_hz"], 3), 0.5, 30.0)

    # Render --------------------------------------------------------------
    r = cfg["render"]
    r["fps"] = int(_clamp(_num(r["fps"], 50), 5.0, 120.0))
    r["master_brightness"] = _clamp(_num(r["master_brightness"], 1), 0.0, 1.0)
    r["gamma"] = _clamp(_num(r["gamma"], 2.2), 1.0, 3.5)
    r["enabled"] = bool(r["enabled"])
    r["watts_per_pixel"] = _clamp(_num(r.get("watts_per_pixel"), 0.78), 0.0, 20.0)
    r["supply_voltage"] = _clamp(_num(r.get("supply_voltage"), 24), 1.0, 60.0)

    # Web -----------------------------------------------------------------
    w = cfg["web"]
    w["port"] = int(_clamp(_num(w["port"], 8080), 1.0, 65535.0))
    w["preview_fps"] = int(_clamp(_num(w["preview_fps"], 20), 1.0, 60.0))

    return cfg


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

class ConfigStore:
    """Loads, validates and atomically saves the JSON config."""

    def __init__(self, path: os.PathLike | str):
        self.path = Path(path)
        self.data: Dict[str, Any] = validate({})

    def load(self) -> Dict[str, Any]:
        raw: Dict[str, Any] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[config] could not read {self.path}: {exc}; using defaults")
                raw = {}
        self.data = validate(raw)
        return self.data

    def save(self, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if data is not None:
            self.data = validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent),
            prefix=".config-", suffix=".tmp", delete=False,
        )
        try:
            json.dump(self.data, handle, indent=2, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, self.path)
        return self.data

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Merge a partial update in and persist it."""
        merged = _deep_merge(self.data, patch or {})
        return self.save(merged)


def section_ids(cfg: Dict[str, Any]) -> List[str]:
    """Sections in canonical order, then any extras."""
    known = [s for s in SECTION_ORDER if s in cfg["sections"]]
    extra = [s for s in cfg["sections"] if s not in known]
    return known + extra


def enabled_section_ids(cfg: Dict[str, Any]) -> List[str]:
    return [s for s in section_ids(cfg)
            if cfg["sections"][s]["enabled"] and cfg["sections"][s]["weight"] > 0]
