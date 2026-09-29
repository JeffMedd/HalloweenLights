"""DDP (Distributed Display Protocol) output to WLED controllers.

WLED listens for DDP on UDP 4048 and enters realtime mode as soon as packets
arrive, which makes it a dumb pixel pusher driven entirely from here.

The engine hands over one canonical frame for the whole house: an (N, 3) array
of perceptual 0..1 values. Each controller gets the slice of that frame for the
sections wired to it, in its own LED output order, gamma corrected and packed.

Header is 10 bytes:
    0   flags1      0x40 version 1, | 0x01 push (set on the last packet)
    1   sequence    1..15, 0 means "not sequenced"
    2   data type   0x0B = RGB 8 bit, 0x1B = RGBW 8 bit
    3   output id   1 = default output device
    4-7 byte offset (big endian)
    8-9 data length (big endian)
"""

from __future__ import annotations

import socket
from typing import Dict, List, Sequence

import numpy as np

DDP_PORT = 4048
DDP_FLAGS_VER1 = 0x40
DDP_FLAGS_PUSH = 0x01
DDP_TYPE_RGB24 = 0x0B
DDP_TYPE_RGBW32 = 0x1B
DDP_OUTPUT_ID = 1
# Keep each packet inside a single ethernet frame. A multiple of 12 so a pixel
# is never split across packets for either RGB or RGBW.
MAX_DATA = 1440

_ORDER_INDEX = {
    "RGB": [0, 1, 2],
    "RBG": [0, 2, 1],
    "GRB": [1, 0, 2],
    "GBR": [1, 2, 0],
    "BRG": [2, 0, 1],
    "BGR": [2, 1, 0],
}


class ControllerTarget:
    """One WLED controller and the canonical pixels wired to it."""

    def __init__(self, spec: Dict, geo):
        self.name: str = spec["name"]
        self.host: str = spec["host"]
        self.port: int = int(spec.get("port") or DDP_PORT)
        self.enabled: bool = bool(spec.get("enabled", True)) and bool(self.host)
        self.section_ids: List[str] = [s for s in (spec.get("sections") or [])
                                       if s in geo.section_slices]
        self.order = _ORDER_INDEX.get(str(spec.get("colour_order", "RGB")).upper(),
                                      [0, 1, 2])
        self.rgbw: bool = str(spec.get("white_mode", "none")).lower() == "auto"
        self.channels: int = 4 if self.rgbw else 3
        self.psu_watts: float = float(spec.get("psu_watts") or 240)
        parts = [np.arange(geo.section_slices[s].start, geo.section_slices[s].stop)
                 for s in self.section_ids]
        self.index = np.concatenate(parts) if parts else np.zeros(0, dtype=int)
        self.pixel_count = int(self.index.size)
        self.watts = 0.0
        self._sequence = 0

    def next_sequence(self) -> int:
        self._sequence = self._sequence % 15 + 1
        return self._sequence


class DDPSender:
    GAMMA_STEPS = 4096

    def __init__(self, controllers: Sequence[Dict], geo, gamma: float = 2.2,
                 master: float = 1.0, watts_per_pixel: float = 0.78):
        self.targets: List[ControllerTarget] = [ControllerTarget(c, geo) for c in controllers]
        self.gamma = gamma
        self.master = master
        self.watts_per_pixel = watts_per_pixel
        self.packets_sent = 0
        self.last_error: str = ""
        self.last_output = np.zeros((geo.n, 3), dtype=np.uint8)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._lut = self._build_gamma_lut(gamma)

    # -- housekeeping -----------------------------------------------------

    # Gamma is applied in the float domain and quantised once, at the end.
    # Correcting an already-rounded 8 bit value throws away everything below
    # about 12% brightness, which silently kills any dim ambient effect.
    @classmethod
    def _build_gamma_lut(cls, gamma: float) -> np.ndarray:
        top = cls.GAMMA_STEPS - 1
        x = np.arange(cls.GAMMA_STEPS) / top
        return (x ** gamma * 255.0 + 0.5).astype(np.uint8)

    @classmethod
    def _build_gamma_table(cls, gamma: float) -> List[int]:
        """List form of the lookup table, kept for the self test."""
        return cls._build_gamma_lut(gamma).tolist()

    def set_gamma(self, gamma: float) -> None:
        if abs(gamma - self.gamma) > 1e-6:
            self.gamma = gamma
            self._lut = self._build_gamma_lut(gamma)

    def set_master(self, master: float) -> None:
        self.master = max(0.0, min(1.0, master))

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def describe(self) -> List[Dict]:
        return [
            {
                "name": t.name, "host": t.host, "port": t.port, "enabled": t.enabled,
                "pixels": t.pixel_count, "channels": t.channels,
                "sections": t.section_ids,
            }
            for t in self.targets
        ]

    # -- output -----------------------------------------------------------

    def encode(self, frame: np.ndarray) -> np.ndarray:
        """Perceptual floats -> gamma corrected uint8, master brightness applied."""
        top = self.GAMMA_STEPS - 1
        q = np.clip(frame * self.master, 0.0, 1.0)
        return self._lut[(q * top).astype(np.int32)]

    def send(self, frame: np.ndarray, transmit: bool = True) -> None:
        out = self.encode(frame)
        self.last_output = out
        for target in self.targets:
            if target.pixel_count == 0:
                target.watts = 0.0
                continue
            data = out[target.index]
            target.watts = float(data.sum()) / (255.0 * 3.0) * self.watts_per_pixel
            if not (transmit and target.enabled):
                continue
            data = data[:, target.order]
            if target.rgbw:
                white = data.min(axis=1, keepdims=True)
                data = np.concatenate([data - white, white], axis=1)
            self._send_bytes(target, np.ascontiguousarray(data, dtype=np.uint8).tobytes())

    def blackout(self) -> None:
        for target in self.targets:
            if target.enabled and target.pixel_count:
                self._send_bytes(target, bytes(target.pixel_count * target.channels))

    def _send_bytes(self, target: ControllerTarget, payload: bytes) -> None:
        total = len(payload)
        data_type = DDP_TYPE_RGBW32 if target.channels == 4 else DDP_TYPE_RGB24
        sequence = target.next_sequence()
        offset = 0
        while offset < total:
            chunk = min(MAX_DATA, total - offset)
            last = (offset + chunk) >= total
            header = bytes((
                DDP_FLAGS_VER1 | (DDP_FLAGS_PUSH if last else 0),
                sequence, data_type, DDP_OUTPUT_ID,
                (offset >> 24) & 0xFF, (offset >> 16) & 0xFF,
                (offset >> 8) & 0xFF, offset & 0xFF,
                (chunk >> 8) & 0xFF, chunk & 0xFF,
            ))
            try:
                self._sock.sendto(header + payload[offset:offset + chunk],
                                  (target.host, target.port))
                self.packets_sent += 1
            except OSError as exc:
                # A missing controller must never stall the frame loop.
                self.last_error = f"{target.name}: {exc}"
                return
            offset += chunk

    def power(self, voltage: float) -> List[Dict]:
        return [
            {
                "name": t.name,
                "watts": round(t.watts, 1),
                "amps": round(t.watts / max(voltage, 0.1), 2),
                "psu_watts": t.psu_watts,
                "percent": round(100.0 * t.watts / max(t.psu_watts, 1.0), 1),
                "max_watts": round(t.pixel_count * self.watts_per_pixel, 1),
            }
            for t in self.targets
        ]
