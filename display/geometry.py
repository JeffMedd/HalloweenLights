"""Physical model of the display: where every pixel is.

The house is modelled the way it looks from the street: the upstairs bay above
the ground bay, each bay two windows side by side, each window an upper sash
(row 0) above a lower sash (row 1). Each sash is framed by strips, normally
four, running clockwise from the top left corner in data order.

Everything downstream works on one flat "canonical" pixel array: sections in
canonical order, strips in data order within each section. The DDP sender maps
that array onto each controller's own pixel run; effects read per-pixel
coordinates from here; test mode resolves "UL1", "window:UL" or "strip:UL1:2"
into pixel masks from here.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, Iterable, List, Set

import numpy as np

from .config import section_ids

_TOKENS = itertools.count(1)

FLOORS = ("upstairs", "ground")
SIDE_INDEX = {"top": 0, "right": 1, "bottom": 2, "left": 3}
WINDOW_NAMES = {(0, 0): "UL", (0, 1): "UR", (1, 0): "DL", (1, 1): "DR"}


class Geometry:
    def __init__(self, cfg: Dict[str, Any]):
        # Effects cache per-pixel data keyed on this, and rebuild when it changes.
        self.token = next(_TOKENS)
        self.section_order: List[str] = section_ids(cfg)
        sections = cfg["sections"]
        layout = cfg["layout"]
        gap_window = float(layout["window_gap_mm"])
        gap_sash = float(layout["sash_gap_mm"])
        gap_floor = float(layout["floor_gap_mm"])

        # --- sash rectangles in millimetres --------------------------------
        # Column widths and row heights per floor come from the widest and
        # tallest sash in that column or row, so mixed sizes still line up.
        col_w: Dict[tuple, float] = {}
        row_h: Dict[tuple, float] = {}
        for sid in self.section_order:
            s = sections[sid]
            f = FLOORS.index(s["floor"])
            col_w[(f, s["col"])] = max(col_w.get((f, s["col"]), 0.0), s["width_mm"])
            row_h[(f, s["row"])] = max(row_h.get((f, s["row"]), 0.0), s["height_mm"])

        def col_x(f: int, col: int) -> float:
            return sum(col_w.get((f, c), 0.0) + gap_window for c in range(col))

        def row_y(f: int, row: int) -> float:
            return sum(row_h.get((f, r), 0.0) + gap_sash for r in range(row))

        floor_h = []
        floor_w = []
        for f in range(len(FLOORS)):
            rows = [r for (ff, r) in row_h if ff == f]
            cols = [c for (ff, c) in col_w if ff == f]
            floor_h.append(row_y(f, max(rows) + 1) - gap_sash if rows else 0.0)
            floor_w.append(col_x(f, max(cols) + 1) - gap_window if cols else 0.0)
        self.width_mm = max(floor_w) if floor_w else 1.0
        floor_top = [0.0]
        for f in range(1, len(FLOORS)):
            floor_top.append(floor_top[-1] + floor_h[f - 1] + gap_floor)
        self.height_mm = (floor_top[-1] + floor_h[-1]) if floor_h else 1.0

        self.rects: Dict[str, tuple] = {}
        self.floor_rects: Dict[str, tuple] = {}
        for sid in self.section_order:
            s = sections[sid]
            f = FLOORS.index(s["floor"])
            # Centre each floor horizontally if one bay is narrower.
            x_off = (self.width_mm - floor_w[f]) / 2.0
            x = x_off + col_x(f, s["col"])
            y = floor_top[f] + row_y(f, s["row"])
            self.rects[sid] = (x, y, float(s["width_mm"]), float(s["height_mm"]))
        for f, name in enumerate(FLOORS):
            x_off = (self.width_mm - floor_w[f]) / 2.0
            self.floor_rects[name] = (x_off, floor_top[f], floor_w[f], floor_h[f])

        # --- pixels ----------------------------------------------------------
        xs: List[float] = []
        ys: List[float] = []
        sec_idx: List[int] = []
        strip_idx: List[int] = []
        pos: List[int] = []
        length: List[int] = []
        side_idx: List[int] = []
        perim: List[float] = []
        lx: List[float] = []
        ly: List[float] = []
        floor_idx: List[int] = []
        window_idx: List[int] = []
        row_idx: List[int] = []

        self.strips: List[Dict[str, Any]] = []
        self.section_slices: Dict[str, slice] = {}
        self.section_strips: Dict[str, List[str]] = {}
        cursor = 0
        for s_index, sid in enumerate(self.section_order):
            s = sections[sid]
            x, y, w, h = self.rects[sid]
            f = FLOORS.index(s["floor"])
            window = f * 2 + min(1, s["col"])
            per = 2.0 * (w + h)
            start_section = cursor
            self.section_strips[sid] = []
            for k, strip in enumerate(s["strips"]):
                n = int(strip["pixels"])
                side = strip["side"]
                key = f"{sid}:{k}"
                self.section_strips[sid].append(key)
                self.strips.append({
                    "key": key, "section": sid, "index": k, "side": side,
                    "pixels": n, "reverse": bool(strip["reverse"]),
                    "start": cursor, "end": cursor + n,
                })
                for i in range(n):
                    j = (n - 1 - i) if strip["reverse"] else i
                    u = (j + 0.5) / n            # 0..1 clockwise along the side
                    if side == "top":
                        px, py, d = x + u * w, y, u * w
                    elif side == "right":
                        px, py, d = x + w, y + u * h, w + u * h
                    elif side == "bottom":
                        px, py, d = x + w - u * w, y + h, w + h + u * w
                    else:  # left
                        px, py, d = x, y + h - u * h, 2 * w + h + u * h
                    xs.append(px)
                    ys.append(py)
                    sec_idx.append(s_index)
                    strip_idx.append(len(self.strips) - 1)
                    pos.append(i)
                    length.append(n)
                    side_idx.append(SIDE_INDEX[side])
                    perim.append(d / per)
                    lx.append((px - x) / w)
                    ly.append((py - y) / h)
                    floor_idx.append(f)
                    window_idx.append(window)
                    row_idx.append(s["row"])
                cursor += n
            self.section_slices[sid] = slice(start_section, cursor)

        self.n = cursor
        self.x_mm = np.array(xs, dtype=float)
        self.y_mm = np.array(ys, dtype=float)
        scale = max(self.width_mm, self.height_mm, 1.0)
        # Normalised coordinates on a common scale so distances are isotropic;
        # nx spans 0..width/scale, ny spans 0..height/scale.
        self.ix = self.x_mm / scale
        self.iy = self.y_mm / scale
        # Normalised to the house bounds, 0..1 each way; y 0 is the top.
        self.nx = self.x_mm / max(self.width_mm, 1.0)
        self.ny = self.y_mm / max(self.height_mm, 1.0)
        self.sec = np.array(sec_idx, dtype=int)
        self.strip = np.array(strip_idx, dtype=int)
        self.pos = np.array(pos, dtype=int)
        self.length = np.array(length, dtype=int)
        self.frac = (self.pos + 0.5) / np.maximum(self.length, 1)  # along data direction
        self.side = np.array(side_idx, dtype=int)
        self.perim = np.array(perim, dtype=float)
        self.lx = np.array(lx, dtype=float)
        self.ly = np.array(ly, dtype=float)
        self.floor = np.array(floor_idx, dtype=int)
        self.window = np.array(window_idx, dtype=int)
        self.row = np.array(row_idx, dtype=int)
        self.nsec = len(self.section_order)
        self.strip_by_key = {s["key"]: s for s in self.strips}
        sec_sizes = np.array([self.section_slices[s].stop - self.section_slices[s].start
                              for s in self.section_order], dtype=int)
        self.sec_pixels = sec_sizes[self.sec] if self.n else np.zeros(0, dtype=int)
        self.width_n = self.width_mm / scale     # house extent in ix/iy units
        self.height_n = self.height_mm / scale

        # Handy groupings
        self.floors: Dict[str, List[str]] = {name: [] for name in FLOORS}
        self.windows: Dict[str, List[str]] = {}
        for sid in self.section_order:
            s = sections[sid]
            self.floors[s["floor"]].append(sid)
            name = WINDOW_NAMES.get((FLOORS.index(s["floor"]), min(1, s["col"])), sid[:2])
            self.windows.setdefault(name, []).append(sid)

        # A tour order that snakes around the house: upstairs upper row left to
        # right, lower row right to left, then the same on the ground floor.
        def tour_key(sid: str):
            s = sections[sid]
            f = FLOORS.index(s["floor"])
            col = s["col"] if s["row"] % 2 == 0 else -s["col"]
            return (f, s["row"], col)
        self.tour = sorted(self.section_order, key=tour_key)

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def resolve(self, targets: Iterable[str]) -> List[str]:
        """Turn group names into an ordered list of strip keys.

        Accepts 'all', 'floor:upstairs', 'window:UL', 'section:UL1', 'UL1',
        'strip:UL1:2' and 'UL1:2'.
        """
        wanted: Set[str] = set()
        for raw in targets or []:
            target = str(raw).strip()
            if not target:
                continue
            if target == "all":
                wanted.update(s["key"] for s in self.strips)
                continue
            kind, _, rest = target.partition(":")
            if kind == "floor":
                for sid in self.floors.get(rest, []):
                    wanted.update(self.section_strips[sid])
            elif kind == "window":
                for sid in self.windows.get(rest, []):
                    wanted.update(self.section_strips[sid])
            elif kind == "section":
                wanted.update(self.section_strips.get(rest, []))
            elif kind == "strip":
                if rest in self.strip_by_key:
                    wanted.add(rest)
            elif target in self.section_strips:
                wanted.update(self.section_strips[target])
            elif target in self.strip_by_key:
                wanted.add(target)
        return [s["key"] for s in self.strips if s["key"] in wanted]

    def strip_slice(self, key: str) -> slice:
        s = self.strip_by_key[key]
        return slice(s["start"], s["end"])

    def mask(self, keys: Iterable[str]) -> np.ndarray:
        out = np.zeros(self.n, dtype=bool)
        for key in keys:
            if key in self.strip_by_key:
                out[self.strip_slice(key)] = True
        return out

    def blank(self) -> np.ndarray:
        return np.zeros((self.n, 3), dtype=float)

    # ------------------------------------------------------------------
    # For the web interface
    # ------------------------------------------------------------------

    def to_client(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "pixels": self.n,
            "floors": {name: {"rect": self.floor_rects[name], "sections": sids}
                       for name, sids in self.floors.items()},
            "windows": self.windows,
            "sections": [
                {
                    "id": sid,
                    "label": cfg["sections"][sid]["label"],
                    "floor": cfg["sections"][sid]["floor"],
                    "rect": self.rects[sid],
                    "start": self.section_slices[sid].start,
                    "end": self.section_slices[sid].stop,
                    "strips": self.section_strips[sid],
                }
                for sid in self.section_order
            ],
            "strips": [
                {k: s[k] for k in ("key", "section", "index", "side", "pixels",
                                   "reverse", "start", "end")}
                for s in self.strips
            ],
            "x": [round(v, 1) for v in self.x_mm.tolist()],
            "y": [round(v, 1) for v in self.y_mm.tolist()],
        }
