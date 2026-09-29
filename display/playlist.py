"""Runs a playlist of idle effects with crossfades between them."""

from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional

import numpy as np

from . import effects


def _signature(item: Dict[str, Any]) -> str:
    return json.dumps([item.get("effect"), item.get("params")], sort_keys=True)


class PlaylistRunner:
    """Plays enabled items in order (or shuffled), crossfading at each change.

    Each item fades into the next over its own `fade` seconds, taken from the
    end of the item. Editing the playlist while it runs keeps the current item
    playing where it can, so a save does not visibly restart the show.
    """

    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)
        self.playlist: Dict[str, Any] = {"id": "", "name": "", "items": []}
        self.order: List[int] = []
        self.position = 0
        self.item_start = 0.0
        self.current: Optional[effects.Effect] = None
        self.current_sig = ""
        self.current_id = ""
        self.next: Optional[effects.Effect] = None
        self.next_position: Optional[int] = None
        self._t0 = 0.0

    # -- setup ------------------------------------------------------------

    def _items(self) -> List[Dict[str, Any]]:
        return [i for i in self.playlist.get("items", []) if i.get("enabled", True)]

    def _build_order(self, avoid_first: Optional[int] = None) -> None:
        count = len(self._items())
        self.order = list(range(count))
        if self.playlist.get("shuffle") and count > 1:
            self.rng.shuffle(self.order)
            if avoid_first is not None and self.order[0] == avoid_first:
                self.order.append(self.order.pop(0))

    def load(self, playlist: Optional[Dict[str, Any]], now: float) -> None:
        playlist = playlist or {"id": "", "name": "", "items": []}
        same_list = playlist.get("id") == self.playlist.get("id")
        self.playlist = playlist
        items = self._items()
        if same_list and self.current_id:
            for index, item in enumerate(items):
                if item["id"] == self.current_id:
                    # Keep playing; rebuild the instance only if it changed.
                    self._build_order()
                    self.position = self.order.index(index) if index in self.order else 0
                    if _signature(item) != self.current_sig:
                        self._start_current(now, keep_time=True)
                    self.next = None
                    self.next_position = None
                    return
        self._build_order()
        self.position = 0
        self._start_current(now)

    def _item_at(self, position: int) -> Optional[Dict[str, Any]]:
        items = self._items()
        if not items or not self.order:
            return None
        return items[self.order[position % len(self.order)]]

    def _start_current(self, now: float, keep_time: bool = False) -> None:
        item = self._item_at(self.position)
        if not keep_time:
            self.item_start = now
        if item is None:
            self.current = None
            self.current_sig = ""
            self.current_id = ""
            return
        self.current = effects.create(item["effect"], item["params"],
                                      seed=self.rng.randrange(1 << 30))
        self.current_sig = _signature(item)
        self.current_id = item["id"]
        self._t0 = self.item_start

    # -- control ----------------------------------------------------------

    def skip(self, now: float) -> None:
        self._advance(now)

    def _advance(self, now: float) -> None:
        if not self.order:
            return
        previous = self.order[self.position % len(self.order)]
        self.position += 1
        if self.position >= len(self.order):
            self.position = 0
            if self.playlist.get("shuffle"):
                self._build_order(avoid_first=previous)
        if self.next is not None and self.next_position == self.position:
            self.current = self.next
            item = self._item_at(self.position)
            self.current_sig = _signature(item) if item else ""
            self.current_id = item["id"] if item else ""
            self.item_start = now
            self._t0 = self._next_t0
        else:
            self._start_current(now)
        self.next = None
        self.next_position = None

    # -- rendering --------------------------------------------------------

    def render(self, now: float, geo) -> np.ndarray:
        item = self._item_at(self.position)
        if item is None or self.current is None:
            return geo.blank()
        duration = float(item["duration"])
        elapsed = now - self.item_start
        if elapsed >= duration:
            self._advance(now)
            return self.render(now, geo)

        frame = self.current.render(now - self._t0, geo)
        fade = min(float(item.get("fade", 0.0)), duration / 2.0)
        if len(self.order) > 1 and fade > 0 and elapsed > duration - fade:
            next_position = (self.position + 1) % len(self.order)
            if self.next is None or self.next_position != next_position:
                upcoming = self._item_at(next_position)
                self.next = effects.create(upcoming["effect"], upcoming["params"],
                                           seed=self.rng.randrange(1 << 30))
                self.next_position = next_position
                self._next_t0 = now
            mix = (elapsed - (duration - fade)) / fade
            mix = mix * mix * (3 - 2 * mix)
            incoming = self.next.render(now - self._next_t0, geo)
            frame = frame * (1 - mix) + incoming * mix
        return frame

    def status(self, now: float) -> Dict[str, Any]:
        item = self._item_at(self.position)
        if item is None:
            return {"playlist": self.playlist.get("id"), "name": self.playlist.get("name"),
                    "effect": None, "label": "Nothing to play", "remaining": 0,
                    "position": 0, "count": 0}
        cls = effects.REGISTRY.get(item["effect"])
        return {
            "playlist": self.playlist.get("id"),
            "name": self.playlist.get("name"),
            "item": item["id"],
            "effect": item["effect"],
            "label": cls.label if cls else item["effect"],
            "remaining": round(max(0.0, float(item["duration"]) - (now - self.item_start)), 1),
            "position": self.position + 1,
            "count": len(self.order),
        }


class SingleEffect:
    """Wraps one effect so it can stand in wherever a runner is expected."""

    def __init__(self, key: str, params: Dict[str, Any], now: float):
        self.key = key
        self.params = effects.validate_params(key, params)
        self.effect = effects.create(key, self.params, seed=random.randrange(1 << 30))
        self.t0 = now

    def render(self, now: float, geo) -> np.ndarray:
        return self.effect.render(now - self.t0, geo)

    def status(self, now: float) -> Dict[str, Any]:
        cls = effects.REGISTRY.get(self.key)
        return {"effect": self.key, "label": cls.label if cls else self.key,
                "remaining": None, "position": 1, "count": 1}
