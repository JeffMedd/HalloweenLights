"""Idle effects, plus the cycle pacing curve used by the game.

Every effect renders the whole house in one go as an (N, 3) numpy array of
floats in 0..1, in canonical pixel order. Effects read per-pixel coordinates
from the Geometry, so they know where each pixel physically sits: which sash,
which side of it, how far up the window, where on the house.

Each effect declares its parameters with a small schema. The web interface
builds its editor from that schema and the config validator clamps against it,
so adding a parameter here is all it takes to expose it.

Instances hold their own state (lightning strikes in flight, bubbles rising,
eyes open) and their own random generator, so the live display and the web
preview can run the same effect side by side without interfering.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np

from .colour import hex_to_rgb, normalise_hex

HALLOWEEN = ["#FF6A00", "#8A2BE2", "#39FF14", "#C81010", "#F2F0E6"]


# ---------------------------------------------------------------------------
# Parameter schema
# ---------------------------------------------------------------------------

class P:
    """One effect parameter."""

    def __init__(self, key: str, label: str, kind: str, default: Any,
                 lo: float = 0.0, hi: float = 1.0, step: float = 0.01,
                 options: Optional[List[List[str]]] = None, help: str = ""):
        self.key, self.label, self.kind, self.default = key, label, kind, default
        self.lo, self.hi, self.step = lo, hi, step
        self.options = options or []
        self.help = help

    def coerce(self, value: Any) -> Any:
        if value is None:
            return self.default
        if self.kind == "colour":
            return normalise_hex(value, self.default)
        if self.kind == "bool":
            return bool(value)
        if self.kind == "choice":
            keys = [o[0] for o in self.options]
            return value if value in keys else self.default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return self.default
        if math.isnan(number):
            return self.default
        number = max(self.lo, min(self.hi, number))
        return int(round(number)) if self.kind == "int" else number

    def to_client(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "kind": self.kind,
                "default": self.default, "min": self.lo, "max": self.hi,
                "step": self.step, "options": self.options, "help": self.help}


BRIGHTNESS = P("brightness", "Brightness", "float", 1.0, 0.0, 1.0, 0.01)


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------

def hash01(a, b=0.0):
    """Cheap deterministic pseudo random in 0..1, vectorised."""
    v = np.sin(np.asarray(a, dtype=float) * 12.9898 + np.asarray(b, dtype=float) * 78.233) * 43758.5453
    return v - np.floor(v)


def vnoise(seed, t):
    """Smooth 1D value noise: seed selects the stream, t moves along it."""
    t = np.asarray(t, dtype=float)
    i = np.floor(t)
    f = t - i
    a = hash01(seed, i)
    b = hash01(seed, i + 1.0)
    f = f * f * (3.0 - 2.0 * f)
    return a + (b - a) * f


def colour(value: str) -> np.ndarray:
    return np.array(hex_to_rgb(value), dtype=float)


def ramp(heat: np.ndarray, stops: List[np.ndarray]) -> np.ndarray:
    """Map 0..1 through a list of colours, black at zero."""
    heat = np.clip(heat, 0.0, 1.0)
    points = [np.zeros(3)] + stops
    segments = len(points) - 1
    scaled = heat * segments
    index = np.minimum(np.floor(scaled).astype(int), segments - 1)
    frac = (scaled - index)[:, None]
    table = np.array(points)
    return table[index] * (1.0 - frac) + table[index + 1] * frac


# ---------------------------------------------------------------------------
# Effect base
# ---------------------------------------------------------------------------

class Effect:
    key = ""
    label = ""
    description = ""
    params: List[P] = []

    def __init__(self, params: Optional[Dict[str, Any]] = None, seed: Optional[int] = None):
        self.p = validate_params(self.key, params or {})
        self.rng = np.random.default_rng(seed)
        self.seed = int(self.rng.integers(0, 100000))
        self._token = None
        self._t_last: Optional[float] = None

    # Subclasses override these two.
    def prepare(self, geo) -> None:
        """Called whenever the geometry changes; cache per-pixel data here."""

    def frame(self, t: float, dt: float, geo) -> np.ndarray:
        return geo.blank()

    def c(self, key: str) -> np.ndarray:
        return colour(self.p[key])

    def render(self, t: float, geo) -> np.ndarray:
        if self._token != geo.token:
            self._token = geo.token
            self.pixel_seed = np.arange(geo.n, dtype=float) + self.seed
            self.prepare(geo)
            self._t_last = None
        dt = 0.0 if self._t_last is None else max(0.0, min(0.5, t - self._t_last))
        self._t_last = t
        out = self.frame(t, dt, geo)
        out = np.clip(np.nan_to_num(out, nan=0.0), 0.0, 1.0)
        return out * float(self.p.get("brightness", 1.0))


REGISTRY: Dict[str, type] = {}


def register(cls):
    cls.params = list(cls.params) + [BRIGHTNESS]
    REGISTRY[cls.key] = cls
    return cls


def validate_params(key: str, params: Dict[str, Any]) -> Dict[str, Any]:
    cls = REGISTRY.get(key)
    if cls is None:
        return {}
    params = params if isinstance(params, dict) else {}
    return {p.key: p.coerce(params.get(p.key)) for p in cls.params}


def create(key: str, params: Optional[Dict[str, Any]] = None,
           seed: Optional[int] = None) -> Effect:
    cls = REGISTRY.get(key) or REGISTRY["off"]
    return cls(params, seed)


def catalogue() -> List[Dict[str, Any]]:
    return [{"key": cls.key, "label": cls.label, "description": cls.description,
             "params": [p.to_client() for p in cls.params]}
            for cls in REGISTRY.values()]


# ---------------------------------------------------------------------------
# The effects
# ---------------------------------------------------------------------------

@register
class Off(Effect):
    key, label = "off", "Off"
    description = "Everything dark."
    params: List[P] = []


@register
class Solid(Effect):
    key, label = "solid", "Solid colour"
    description = "One steady colour on every window."
    params = [P("colour", "Colour", "colour", "#FF6A00")]

    def frame(self, t, dt, geo):
        return np.tile(self.c("colour"), (geo.n, 1))


@register
class Breathe(Effect):
    key, label = "breathe", "Breathe"
    description = "A slow pulse, each window a little behind the last, so the house seems to breathe."
    params = [
        P("colour", "Colour", "colour", "#8A2BE2"),
        P("speed", "Breaths per second", "float", 0.12, 0.01, 1.0, 0.01),
        P("depth", "Depth", "float", 0.6, 0.0, 1.0, 0.05),
        P("stagger", "Stagger between windows", "float", 0.5, 0.0, 1.0, 0.05),
    ]

    def frame(self, t, dt, geo):
        phase = self.p["stagger"] * 2 * np.pi * geo.sec / max(1, geo.nsec)
        wave = 0.5 + 0.5 * np.sin(2 * np.pi * self.p["speed"] * t - phase)
        level = (1 - self.p["depth"]) + self.p["depth"] * wave
        return self.c("colour") * level[:, None]


@register
class Candle(Effect):
    key, label = "candle", "Candlelight"
    description = "Warm, restless flicker, as if every window had a candle behind it."
    params = [
        P("colour", "Colour", "colour", "#FF7A1A"),
        P("flicker", "Flicker", "float", 0.55, 0.0, 1.0, 0.05),
        P("speed", "Speed", "float", 1.0, 0.1, 4.0, 0.1),
    ]

    def frame(self, t, dt, geo):
        s = self.p["speed"]
        n1 = vnoise(self.pixel_seed, t * s * 6.0)
        n2 = vnoise(self.pixel_seed + 37.0, t * s * 15.0)
        n3 = vnoise(geo.sec + self.seed + 100.0, t * s * 2.0)
        level = 1.0 - self.p["flicker"] * (0.45 * n1 + 0.25 * n2 + 0.3 * n3)
        out = self.c("colour") * level[:, None]
        out[:, 1] *= 0.7 + 0.3 * level      # dimmer candles look redder
        return out


@register
class Fireflies(Effect):
    key, label = "fireflies", "Fireflies"
    description = "A dim glow with points of light fading in and out at random."
    params = [
        P("colour", "Firefly colour", "colour", "#C8FF40"),
        P("background", "Background", "colour", "#0A1405"),
        P("density", "Density", "float", 0.35, 0.05, 1.0, 0.05),
        P("speed", "Speed", "float", 1.0, 0.2, 4.0, 0.1),
        P("mixed", "Mixed Halloween colours", "bool", False),
    ]

    def prepare(self, geo):
        self.rate = self.rng.uniform(0.08, 0.3, geo.n)
        self.phase = self.rng.uniform(0, 2 * np.pi, geo.n)
        picks = self.rng.integers(0, len(HALLOWEEN), geo.n)
        self.mixed = np.array([hex_to_rgb(HALLOWEEN[i]) for i in picks]).reshape(-1, 3)

    def frame(self, t, dt, geo):
        v = 0.5 + 0.5 * np.sin(2 * np.pi * self.rate * self.p["speed"] * t + self.phase)
        threshold = 1.0 - self.p["density"] * 0.5
        pulse = np.clip((v - threshold) / (1.0 - threshold), 0, 1) ** 2
        tint = self.mixed if self.p["mixed"] else self.c("colour")
        return self.c("background") + tint * pulse[:, None]


@register
class Lightning(Effect):
    key, label = "lightning", "Lightning storm"
    description = "A dark stormy sky; lightning strikes a sash, a window, a floor or the whole house, with the flicker of a real strike."
    params = [
        P("sky", "Sky colour", "colour", "#140A33"),
        P("flash", "Flash colour", "colour", "#DDE4FF"),
        P("rate", "Strikes per minute", "float", 8.0, 1.0, 60.0, 1.0),
        P("afterglow", "Glow elsewhere", "float", 0.2, 0.0, 1.0, 0.05),
    ]

    def prepare(self, geo):
        self.strikes: List[Dict[str, Any]] = []
        self.next_at = 0.5

    def _new_strike(self, t, geo):
        roll = self.rng.random()
        if roll < 0.15:
            target = np.ones(geo.n, dtype=bool)
        elif roll < 0.4:
            target = geo.floor == self.rng.integers(0, 2)
        elif roll < 0.7:
            target = geo.window == self.rng.integers(0, 4)
        else:
            target = geo.sec == self.rng.integers(0, max(1, geo.nsec))
        weight = np.where(target, 1.0, self.p["afterglow"])
        pulses = []
        offset = 0.0
        for i in range(int(self.rng.integers(2, 5))):
            pulses.append((offset, self.rng.uniform(0.45, 1.0) * (1.0 if i else 1.1),
                           self.rng.uniform(0.05, 0.12)))
            offset += self.rng.uniform(0.05, 0.16)
        pulses.append((offset, 0.35, 0.35))   # the lingering glow at the end
        self.strikes.append({"start": t, "pulses": pulses, "weight": weight})

    def frame(self, t, dt, geo):
        if t >= self.next_at:
            self._new_strike(t, geo)
            self.next_at = t + max(0.7, self.rng.exponential(60.0 / self.p["rate"]))
        out = np.tile(self.c("sky"), (geo.n, 1))
        flash = self.c("flash")
        keep = []
        for strike in self.strikes:
            age = t - strike["start"]
            level = 0.0
            for offset, amp, tau in strike["pulses"]:
                if age >= offset:
                    level += amp * math.exp(-(age - offset) / tau)
            if age < 3.0:
                keep.append(strike)
                out += flash * (min(1.0, level) * strike["weight"])[:, None]
        self.strikes = keep
        return out


@register
class Heartbeat(Effect):
    key, label = "heartbeat", "Heartbeat"
    description = "A lub-dub pulse in blood red that ripples out from the middle of the house."
    params = [
        P("colour", "Colour", "colour", "#C81010"),
        P("bpm", "Beats per minute", "float", 60.0, 30.0, 160.0, 1.0),
        P("base", "Resting glow", "float", 0.06, 0.0, 0.5, 0.01),
        P("ripple", "Ripple", "float", 0.5, 0.0, 1.0, 0.05),
    ]

    def prepare(self, geo):
        cx, cy = geo.width_n / 2, geo.height_n / 2
        d = np.hypot(geo.ix - cx, geo.iy - cy)
        self.dist = d / max(d.max(), 1e-6) if geo.n else d

    def frame(self, t, dt, geo):
        period = 60.0 / self.p["bpm"]
        local = ((t - self.dist * self.p["ripple"] * 0.4) % period) / period
        beat = (np.exp(-((local - 0.0) / 0.045) ** 2)
                + np.exp(-((local - 1.0) / 0.045) ** 2)
                + 0.7 * np.exp(-((local - 0.2) / 0.05) ** 2))
        level = self.p["base"] + (1 - self.p["base"]) * np.clip(beat, 0, 1)
        return self.c("colour") * level[:, None]


@register
class Ghosts(Effect):
    key, label = "ghosts", "Ghost drift"
    description = "Soft pale shapes wander slowly from window to window across the house."
    params = [
        P("colour", "Ghost colour", "colour", "#F2F0E6"),
        P("background", "Background", "colour", "#08040F"),
        P("count", "Ghosts", "int", 2, 1, 5, 1),
        P("size", "Size", "float", 0.2, 0.05, 0.6, 0.01),
        P("speed", "Speed", "float", 0.25, 0.05, 1.5, 0.05),
    ]

    def prepare(self, geo):
        self.freq = self.rng.uniform(0.25, 0.8, (5, 4))
        self.phase = self.rng.uniform(0, 2 * np.pi, (5, 4))

    def frame(self, t, dt, geo):
        s = self.p["speed"]
        sigma = self.p["size"] * geo.width_n
        total = np.zeros(geo.n)
        for g in range(int(self.p["count"])):
            f, ph = self.freq[g], self.phase[g]
            gx = geo.width_n * (0.5 + 0.45 * math.sin(f[0] * s * t + ph[0]) * math.cos(f[1] * s * t + ph[1]))
            gy = geo.height_n * (0.5 + 0.48 * math.sin(f[2] * s * t + ph[2]))
            d2 = (geo.ix - gx) ** 2 + (geo.iy - gy) ** 2
            shimmer = 0.85 + 0.15 * math.sin(3.1 * t + g)
            total += np.exp(-d2 / (2 * sigma ** 2)) * shimmer
        return self.c("background") + self.c("colour") * np.clip(total, 0, 1)[:, None]


@register
class Hellfire(Effect):
    key, label = "hellfire", "Hellfire"
    description = "Flames rise from the bottom of every sash, with embers drifting at the top."
    params = [
        P("low", "Base colour", "colour", "#C81010"),
        P("mid", "Flame colour", "colour", "#FF6A00"),
        P("high", "Tip colour", "colour", "#FFC040"),
        P("height", "Flame height", "float", 0.8, 0.2, 1.5, 0.05),
        P("speed", "Speed", "float", 1.0, 0.2, 3.0, 0.1),
        P("embers", "Embers", "float", 0.3, 0.0, 1.0, 0.05),
    ]

    def frame(self, t, dt, geo):
        s = self.p["speed"]
        rise = 1.0 - geo.ly                      # 0 at the bottom of a sash
        base = np.clip(1.0 - rise / self.p["height"], 0, 1)
        n = vnoise(self.pixel_seed, t * s * 5.0)
        band = 0.5 + 0.5 * np.sin(2 * np.pi * (rise * 1.5 - t * s * 0.8) + geo.sec * 1.3)
        heat = base * (0.45 + 0.35 * n + 0.35 * band)
        spark = hash01(self.pixel_seed, np.floor(t * s * 4.0)) > (1.0 - 0.06 * self.p["embers"] * 3)
        heat = np.maximum(heat, spark * (rise > 0.5) * 0.55 * self.p["embers"] * 2)
        return ramp(heat, [self.c("low"), self.c("mid"), self.c("high")])


@register
class Cauldron(Effect):
    key, label = "cauldron", "Toxic cauldron"
    description = "A simmering green glow with bubbles rising up the sides of each sash and popping at the top."
    params = [
        P("colour", "Brew colour", "colour", "#39FF14"),
        P("bubble", "Bubble colour", "colour", "#D8FF90"),
        P("rate", "Bubbles per second", "float", 1.5, 0.1, 8.0, 0.1),
        P("rise", "Rise speed", "float", 0.35, 0.05, 2.0, 0.05),
        P("simmer", "Simmer glow", "float", 0.4, 0.0, 1.0, 0.05),
    ]

    def prepare(self, geo):
        self.bubbles: List[Dict[str, Any]] = []
        self.next_at = 0.0

    def frame(self, t, dt, geo):
        while t >= self.next_at:
            self.bubbles.append({
                "sec": int(self.rng.integers(0, max(1, geo.nsec))),
                "side": int(self.rng.choice([1, 3])),
                "born": self.next_at, "size": self.rng.uniform(0.05, 0.1),
                "speed": self.p["rise"] * self.rng.uniform(0.7, 1.3),
            })
            self.next_at += self.rng.exponential(1.0 / self.p["rate"])
        simmer = self.p["simmer"] * (0.6 + 0.4 * vnoise(self.pixel_seed, t * 1.5))
        simmer = np.where(geo.side == 2, np.minimum(1.0, simmer * 2.2), simmer)
        out = self.c("colour") * simmer[:, None]
        bub = np.zeros(geo.n)
        keep = []
        for b in self.bubbles:
            y = 1.0 - (t - b["born"]) * b["speed"]
            in_sec = geo.sec == b["sec"]
            if y > 0:
                on_side = in_sec & (geo.side == b["side"])
                bub += on_side * np.exp(-((geo.ly - y) / b["size"]) ** 2)
                keep.append(b)
            else:
                popped = (t - b["born"]) - 1.0 / b["speed"]
                if popped < 0.6:
                    bub += (in_sec & (geo.side == 0)) * math.exp(-popped / 0.12)
                    keep.append(b)
        self.bubbles = keep
        return out + self.c("bubble") * np.clip(bub, 0, 1)[:, None]


@register
class Marquee(Effect):
    key, label = "marquee", "Marquee chase"
    description = "Bands of colour march around the frame of every sash, like a fairground sign."
    params = [
        P("colour1", "Colour 1", "colour", "#FF6A00"),
        P("colour2", "Colour 2", "colour", "#8A2BE2"),
        P("colour3", "Colour 3 (black to skip)", "colour", "#39FF14"),
        P("band", "Band length (pixels)", "int", 4, 1, 26, 1),
        P("gap", "Gap", "float", 0.3, 0.0, 0.9, 0.05),
        P("speed", "Speed (pixels per second)", "float", 8.0, 0.5, 60.0, 0.5),
        P("direction", "Direction", "choice", "cw", options=[["cw", "Clockwise"], ["ccw", "Anticlockwise"]]),
        P("stagger", "Offset each sash", "bool", True),
    ]

    def frame(self, t, dt, geo):
        cols = [self.c(k) for k in ("colour1", "colour2", "colour3") if self.c(k).sum() > 0]
        cols = np.array(cols or [np.ones(3)])
        band = float(self.p["band"])
        sign = 1.0 if self.p["direction"] == "cw" else -1.0
        pos = geo.perim * geo.sec_pixels - sign * t * self.p["speed"]
        if self.p["stagger"]:
            pos = pos + geo.sec * band * 1.5
        seg = np.floor(pos / band)
        within = pos / band - seg
        on = within < (1.0 - self.p["gap"])
        return cols[seg.astype(int) % len(cols)] * on[:, None]


@register
class Wave(Effect):
    key, label = "wave", "Colour wave"
    description = "The Halloween palette flows across the whole house as one picture."
    params = [
        P("colour1", "Colour 1", "colour", "#FF6A00"),
        P("colour2", "Colour 2", "colour", "#8A2BE2"),
        P("colour3", "Colour 3", "colour", "#39FF14"),
        P("colour4", "Colour 4", "colour", "#C81010"),
        P("direction", "Direction", "choice", "horizontal",
          options=[["horizontal", "Left to right"], ["vertical", "Top to bottom"],
                   ["diagonal", "Diagonal"], ["radial", "From the centre"]]),
        P("speed", "Speed", "float", 0.08, 0.01, 1.0, 0.01),
        P("scale", "Colours across the house", "float", 1.0, 0.25, 4.0, 0.05),
    ]

    def frame(self, t, dt, geo):
        d = self.p["direction"]
        if d == "vertical":
            u = geo.ny
        elif d == "diagonal":
            u = (geo.nx + geo.ny) / 2
        elif d == "radial":
            u = np.hypot(geo.nx - 0.5, geo.ny - 0.5) * 1.4
        else:
            u = geo.nx
        cols = np.array([self.c(k) for k in ("colour1", "colour2", "colour3", "colour4")])
        k = ((u * self.p["scale"] - t * self.p["speed"]) % 1.0) * len(cols)
        i0 = np.floor(k).astype(int) % len(cols)
        i1 = (i0 + 1) % len(cols)
        f = (k - np.floor(k))
        f = (f * f * (3 - 2 * f))[:, None]
        return cols[i0] * (1 - f) + cols[i1] * f


@register
class Eyes(Effect):
    key, label = "eyes", "Eyes in the dark"
    description = "Pairs of eyes open along the edges of the windows, blink, and slowly close."
    params = [
        P("colour", "Eye colour", "colour", "#FF2200"),
        P("background", "Background", "colour", "#000000"),
        P("pairs", "Pairs at once", "int", 3, 1, 8, 1),
        P("blink", "Blink", "bool", True),
    ]

    def prepare(self, geo):
        self.eyes: List[Dict[str, Any]] = []
        self.next_at = 0.3
        self.spots = [(s["start"], s["pixels"], s["section"]) for s in geo.strips
                      if s["side"] in ("top", "bottom") and s["pixels"] >= 4]

    def frame(self, t, dt, geo):
        out = np.tile(self.c("background"), (geo.n, 1))
        if t >= self.next_at and len(self.eyes) < self.p["pairs"] and self.spots:
            busy = {e["section"] for e in self.eyes}
            free = [s for s in self.spots if s[2] not in busy] or self.spots
            start, n, sid = free[int(self.rng.integers(0, len(free)))]
            i = int(self.rng.integers(0, n - 3))
            life = self.rng.uniform(3.0, 8.0)
            self.eyes.append({
                "a": start + i, "b": start + i + 2, "section": sid,
                "born": t, "life": life,
                "blinks": sorted(self.rng.uniform(0.8, life - 1.0, int(self.rng.integers(0, 3)))),
            })
            self.next_at = t + self.rng.uniform(0.4, 2.5)
        level = np.zeros(geo.n)
        keep = []
        for e in self.eyes:
            age = t - e["born"]
            if age > e["life"]:
                continue
            keep.append(e)
            env = min(1.0, age / 0.5, (e["life"] - age) / 0.8)
            if self.p["blink"] and any(abs(age - b) < 0.07 for b in e["blinks"]):
                env = 0.0
            level[e["a"]] = max(level[e["a"]], env)
            level[e["b"]] = max(level[e["b"]], env)
        self.eyes = keep
        eye = self.c("colour")
        return out * (1 - level)[:, None] + eye * level[:, None]


@register
class Searchlight(Effect):
    key, label = "searchlight", "Searchlight"
    description = "A beam sweeps across the house, or turns like a lighthouse above it."
    params = [
        P("colour", "Beam colour", "colour", "#F2F0E6"),
        P("background", "Background", "colour", "#05020A"),
        P("width", "Beam width", "float", 0.12, 0.03, 0.5, 0.01),
        P("speed", "Sweeps per minute", "float", 12.0, 1.0, 60.0, 1.0),
        P("mode", "Style", "choice", "sweep",
          options=[["sweep", "Sweep side to side"], ["lighthouse", "Lighthouse"]]),
    ]

    def frame(self, t, dt, geo):
        f = self.p["speed"] / 60.0
        w = self.p["width"]
        if self.p["mode"] == "lighthouse":
            angle = 2 * np.pi * f * t
            pix = np.arctan2(geo.ny + 0.15, geo.nx - 0.5)       # from a point above the roof
            diff = np.angle(np.exp(1j * (pix - (np.pi / 2 + 1.2 * np.sin(angle)))))
            beam = np.exp(-(diff / (w * 2.5)) ** 2) * (np.cos(angle) > -0.2)
        else:
            bx = 0.5 + 0.55 * math.sin(2 * np.pi * f * t)
            beam = np.exp(-((geo.nx - bx) / w) ** 2)
        return self.c("background") + self.c("colour") * beam[:, None]


@register
class Tour(Effect):
    key, label = "tour", "Haunted tour"
    description = "A comet with a glowing tail runs round each sash in turn, touring the whole house, or round all of them at once."
    params = [
        P("colour", "Tail colour", "colour", "#8A2BE2"),
        P("head", "Head colour", "colour", "#F2F0E6"),
        P("tail", "Tail length", "float", 0.45, 0.05, 0.9, 0.05),
        P("laps", "Laps per second", "float", 0.6, 0.1, 3.0, 0.05),
        P("mode", "Style", "choice", "tour",
          options=[["tour", "One sash at a time"], ["all", "Every sash at once"]]),
    ]

    def prepare(self, geo):
        order = {sid: i for i, sid in enumerate(geo.tour)}
        self.tour_pos = np.array([order[sid] for sid in geo.section_order])[geo.sec] if geo.n else geo.sec

    def frame(self, t, dt, geo):
        laps = t * self.p["laps"]
        if self.p["mode"] == "all":
            head = (laps + geo.sec / max(1, geo.nsec)) % 1.0
            active = np.ones(geo.n, dtype=bool)
        else:
            head = laps % 1.0
            active = self.tour_pos == int(np.floor(laps)) % max(1, geo.nsec)
        d = (head - geo.perim) % 1.0
        tail = self.p["tail"]
        body = np.clip(1 - d / tail, 0.0, 1.0) ** 1.4 * active
        tip = np.clip(1 - d / 0.03, 0, 1) * active
        return self.c("colour") * body[:, None] + self.c("head") * tip[:, None]


@register
class Poltergeist(Effect):
    key, label = "poltergeist", "Poltergeist"
    description = "Windows snap on and off in random colours, and now and then the whole house stutters."
    params = [
        P("colour1", "Colour 1", "colour", "#FF6A00"),
        P("colour2", "Colour 2", "colour", "#8A2BE2"),
        P("colour3", "Colour 3", "colour", "#39FF14"),
        P("activity", "Events per second", "float", 1.5, 0.2, 8.0, 0.1),
        P("hold", "Hold time (seconds)", "float", 0.7, 0.1, 5.0, 0.1),
        P("stutter", "House stutters per minute", "float", 3.0, 0.0, 20.0, 0.5),
    ]

    def prepare(self, geo):
        self.until = np.full(geo.nsec, -10.0)
        self.col = np.zeros((geo.nsec, 3))
        self.next_at = 0.0
        self.stutter_at = 5.0
        self.stutter_until = -1.0

    def frame(self, t, dt, geo):
        cols = [self.c(k) for k in ("colour1", "colour2", "colour3")]
        while t >= self.next_at and geo.nsec:
            s = int(self.rng.integers(0, geo.nsec))
            self.col[s] = cols[int(self.rng.integers(0, 3))]
            self.until[s] = self.next_at + self.p["hold"] * self.rng.uniform(0.5, 1.5)
            self.next_at += self.rng.exponential(1.0 / self.p["activity"])
        if self.p["stutter"] > 0 and t >= self.stutter_at:
            self.stutter_until = t + 0.45
            self.stutter_at = t + max(2.0, self.rng.exponential(60.0 / self.p["stutter"]))
        on = np.where(t < self.until, 1.0, np.exp(-np.maximum(0, t - self.until) / 0.08))
        out = self.col[geo.sec] * on[geo.sec][:, None]
        if t < self.stutter_until:
            flick = hash01(geo.sec + self.seed, np.floor(t * 15)) > 0.45
            out = np.where(flick[:, None], colour("#F2F0E6") * 0.7, out * 0.2)
        return out


@register
class Drip(Effect):
    key, label = "drip", "Blood drip"
    description = "Blood oozes along the tops of the windows, swells, and runs down the sides to pool at the bottom."
    params = [
        P("colour", "Colour", "colour", "#C81010"),
        P("ooze", "Ooze along the top", "float", 0.35, 0.0, 1.0, 0.05),
        P("rate", "Drips per second", "float", 0.6, 0.1, 4.0, 0.1),
        P("gravity", "Fall speed", "float", 1.5, 0.3, 6.0, 0.1),
    ]

    def prepare(self, geo):
        self.drips: List[Dict[str, Any]] = []
        self.next_at = 0.2

    def frame(self, t, dt, geo):
        while t >= self.next_at and geo.nsec:
            self.drips.append({
                "sec": int(self.rng.integers(0, geo.nsec)),
                "side": int(self.rng.choice([1, 3])),
                "born": self.next_at, "swell": self.rng.uniform(0.8, 1.6),
            })
            self.next_at += self.rng.exponential(1.0 / self.p["rate"])
        g = self.p["gravity"]
        level = (geo.side == 0) * self.p["ooze"] * (0.6 + 0.4 * vnoise(self.pixel_seed, t * 0.7))
        keep = []
        for d in self.drips:
            in_sec = geo.sec == d["sec"]
            side = in_sec & (geo.side == d["side"])
            age = t - d["born"]
            if age < d["swell"]:
                level = np.maximum(level, side * np.exp(-(geo.ly / 0.06) ** 2) * (age / d["swell"]))
                keep.append(d)
                continue
            tf = age - d["swell"]
            head = 0.5 * g * tf * tf
            passed = np.sqrt(2 * np.clip(geo.ly, 0, None) / g)
            trail = np.where(geo.ly <= head, np.exp(-np.maximum(0, tf - passed) / 1.6), 0.0)
            glow = np.exp(-((geo.ly - head) / 0.05) ** 2)
            level = np.maximum(level, side * np.maximum(trail * 0.8, glow))
            t_pool = math.sqrt(2.0 / g)
            if tf > t_pool:
                corner = 1.0 if d["side"] == 1 else 0.0
                spread = 0.1 + 0.4 * min(1.0, (tf - t_pool) / 1.5)
                pool = np.exp(-np.abs(geo.lx - corner) / spread) * math.exp(-(tf - t_pool) / 3.0)
                level = np.maximum(level, (in_sec & (geo.side == 2)) * pool)
            if tf < t_pool + 5.0:
                keep.append(d)
        self.drips = keep
        return self.c("colour") * np.clip(level, 0, 1)[:, None]


@register
class Bats(Effect):
    key, label = "bats", "Bat flight"
    description = "Dark bat shapes flap across a glowing sky, blotting out the light as they pass."
    params = [
        P("sky_top", "Sky, top", "colour", "#8A2BE2"),
        P("sky_bottom", "Sky, bottom", "colour", "#FF6A00"),
        P("count", "Bats", "int", 5, 1, 12, 1),
        P("size", "Size", "float", 0.1, 0.02, 0.25, 0.01),
        P("speed", "Speed", "float", 0.2, 0.05, 1.0, 0.05),
    ]

    def prepare(self, geo):
        self.y0 = self.rng.uniform(0.05, 0.95, 12)
        self.x0 = self.rng.uniform(0, 1, 12)
        self.v = self.rng.uniform(0.7, 1.3, 12) * self.rng.choice([-1, 1], 12)
        self.flap = self.rng.uniform(2.0, 4.0, 12)

    def frame(self, t, dt, geo):
        sky = (self.c("sky_top") * (1 - geo.ny)[:, None] + self.c("sky_bottom") * geo.ny[:, None])
        dark = np.zeros(geo.n)
        span = geo.width_n * 1.4
        for i in range(int(self.p["count"])):
            x = ((self.x0[i] * span + self.v[i] * self.p["speed"] * geo.width_n * t) % span) - geo.width_n * 0.2
            y = geo.height_n * (self.y0[i] + 0.03 * math.sin(t * 1.3 + i))
            wing = 0.55 + 0.45 * abs(math.sin(self.flap[i] * t))
            sx = self.p["size"] * geo.width_n * (0.6 + wing)
            sy = self.p["size"] * geo.width_n * 0.5
            dark += np.exp(-((geo.ix - x) / sx) ** 2 - ((geo.iy - y) / sy) ** 2)
        return sky * (1 - np.clip(dark * 1.6, 0, 1))[:, None]


# ---------------------------------------------------------------------------
# Cycle pacing (used by the game)
# ---------------------------------------------------------------------------

def step_durations(cycle_seconds: float, min_ms: float, max_ms: float,
                   easing: float, steps: int = 0) -> List[float]:
    """Ease-out dwell times, in seconds, summing exactly to cycle_seconds.

    When steps is 0 the count is chosen as the smallest number of steps whose
    natural (unscaled) duration reaches cycle_seconds, which keeps the shape of
    the curve honest instead of squashing a fixed step count into the window.
    """
    low = max(0.001, min_ms / 1000.0)
    high = max(low, max_ms / 1000.0)

    def natural(count: int) -> List[float]:
        if count <= 1:
            return [high]
        return [low + (high - low) * ((i / (count - 1)) ** easing)
                for i in range(count)]

    if steps and steps > 0:
        durations = natural(int(steps))
    else:
        count = 1
        durations = natural(count)
        while sum(durations) < cycle_seconds and count < 400:
            count += 1
            durations = natural(count)

    total = sum(durations)
    if total <= 0:
        return [cycle_seconds]
    factor = cycle_seconds / total
    return [d * factor for d in durations]


def square_wave(elapsed: float, hz: float, duty: float) -> bool:
    if hz <= 0:
        return True
    period = 1.0 / hz
    return (elapsed % period) < (period * duty)
