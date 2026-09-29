#!/usr/bin/env python3
"""Generate a starter set of placeholder sounds.

These are synthesised, not recorded, so treat them as scaffolding: they let you
test timing and levels on day one, and you can drop real files in over the top
later without touching the config beyond the filename.

    python3 tools/make_sounds.py --out sounds
"""

from __future__ import annotations

import argparse
import math
import wave
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    raise SystemExit("numpy is required: pip install numpy")

RATE = 44100


def write_wav(path: Path, samples: "np.ndarray") -> None:
    data = np.asarray(samples, dtype=float)
    # Pull back rather than clip, so nothing distorts on the way out.
    peak = float(np.abs(data).max()) if data.size else 0.0
    if peak > 0.95:
        data = data * (0.95 / peak)
    data = np.clip(data, -1.0, 1.0)
    stereo = np.column_stack([data, data]) if data.ndim == 1 else data
    pcm = (stereo * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm.tobytes())
    print(f"  {path.name}  {len(data) / RATE:.2f}s")


def envelope(length: int, attack: float, decay: float) -> "np.ndarray":
    attack_n = max(1, int(attack * RATE))
    decay_n = max(1, int(decay * RATE))
    env = np.ones(length)
    env[:attack_n] = np.linspace(0.0, 1.0, attack_n)
    tail = min(decay_n, length)
    env[length - tail:] *= np.linspace(1.0, 0.0, tail) ** 1.6
    return env


def tone(freq: float, seconds: float, harmonics=(1.0, 0.35, 0.18),
         detune: float = 0.0) -> "np.ndarray":
    t = np.linspace(0, seconds, int(seconds * RATE), endpoint=False)
    out = np.zeros_like(t)
    for index, level in enumerate(harmonics, start=1):
        out += level * np.sin(2 * np.pi * freq * index * t
                              + detune * np.sin(2 * np.pi * 3.1 * t))
    return out / max(1e-6, sum(harmonics))


def noise(seconds: float, seed: int = 0) -> "np.ndarray":
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.35, int(seconds * RATE))


def mix_into(target: "np.ndarray", piece: "np.ndarray", start: int,
             level: float = 1.0) -> None:
    """Add piece into target at start, clipping anything past the end."""
    if start >= len(target):
        return
    span = min(len(piece), len(target) - start)
    target[start:start + span] += piece[:span] * level


def lowpass(signal: "np.ndarray", cutoff: float) -> "np.ndarray":
    """Single pole IIR, plenty good enough for placeholders."""
    alpha = math.exp(-2.0 * math.pi * cutoff / RATE)
    out = np.empty_like(signal)
    acc = 0.0
    for index, value in enumerate(signal):
        acc = alpha * acc + (1.0 - alpha) * value
        out[index] = acc
    return out


# ---------------------------------------------------------------------------

def make_tick() -> "np.ndarray":
    length = int(0.055 * RATE)
    body = tone(1650, 0.055, harmonics=(1.0, 0.5)) * envelope(length, 0.001, 0.05)
    click = noise(0.055, 7)[:length] * envelope(length, 0.0005, 0.012) * 0.5
    return (body * 0.7 + click) * 0.8


def make_button() -> "np.ndarray":
    seconds = 0.9
    t = np.linspace(0, seconds, int(seconds * RATE), endpoint=False)
    sweep = np.sin(2 * np.pi * (180 + 900 * (t / seconds) ** 2) * t)
    shimmer = 0.3 * np.sin(2 * np.pi * 1320 * t) * np.linspace(0, 1, len(t))
    return (sweep + shimmer) * envelope(len(t), 0.01, 0.4) * 0.75


def make_landing() -> "np.ndarray":
    seconds = 1.6
    length = int(seconds * RATE)
    chord = (tone(110, seconds) + tone(164.81, seconds) * 0.8
             + tone(220, seconds) * 0.6 + tone(261.63, seconds) * 0.45)
    hit = noise(seconds, 3)[:length] * envelope(length, 0.001, 0.25) * 0.4
    return (chord / 2.8 + hit) * envelope(length, 0.004, 1.2) * 0.9


def make_ambient(seconds: float = 30.0) -> "np.ndarray":
    length = int(seconds * RATE)
    t = np.linspace(0, seconds, length, endpoint=False)
    drone = (np.sin(2 * np.pi * 55 * t) * 0.5
             + np.sin(2 * np.pi * 82.4 * t + 0.6) * 0.28
             + np.sin(2 * np.pi * 110.2 * t) * 0.16)
    wind = lowpass(noise(seconds, 11)[:length], 320) * 1.8
    swell = 0.6 + 0.4 * np.sin(2 * np.pi * 0.035 * t)
    mixed = (drone * 0.55 + wind * 0.45) * swell
    # Cross-fade the ends into each other so the loop does not click.
    fade = int(1.5 * RATE)
    ramp = np.linspace(0, 1, fade)
    mixed[:fade] = mixed[:fade] * ramp + mixed[-fade:][::-1] * (1 - ramp)
    return mixed * 0.5


def make_finale(kind: str, seed: int) -> "np.ndarray":
    if kind == "bell":
        seconds = 2.6
        length = int(seconds * RATE)
        body = (tone(523.25, seconds, (1.0, 0.6, 0.4, 0.25))
                + tone(783.99, seconds, (0.7, 0.3)) * 0.6)
        return body / 1.7 * envelope(length, 0.002, 2.4) * 0.85
    if kind == "howl":
        seconds = 2.8
        t = np.linspace(0, seconds, int(seconds * RATE), endpoint=False)
        pitch = 210 + 130 * np.sin(2 * np.pi * 0.35 * t) * np.linspace(1, 0.3, len(t))
        body = np.sin(2 * np.pi * np.cumsum(pitch) / RATE)
        breath = lowpass(noise(seconds, seed)[:len(t)], 900) * 0.5
        return (body * 0.8 + breath) * envelope(len(t), 0.25, 1.3) * 0.8
    if kind == "organ":
        seconds = 2.4
        length = int(seconds * RATE)
        chord = (tone(146.83, seconds) + tone(174.61, seconds)
                 + tone(220.00, seconds) + tone(293.66, seconds) * 0.7)
        return chord / 3.7 * envelope(length, 0.05, 1.4) * 0.85
    if kind == "chime":
        seconds = 2.2
        length = int(seconds * RATE)
        out = np.zeros(length)
        for index, freq in enumerate((880, 1174.66, 1396.91)):
            piece = tone(freq, seconds - index * 0.13, (1.0, 0.25))
            piece = piece * envelope(len(piece), 0.001, 1.6)
            mix_into(out, piece, int(index * 0.13 * RATE), 0.9 - index * 0.2)
        return out / 2.0 * 0.85
    if kind == "growl":
        seconds = 2.2
        t = np.linspace(0, seconds, int(seconds * RATE), endpoint=False)
        body = np.sin(2 * np.pi * 68 * t) + 0.6 * np.sin(2 * np.pi * 97 * t)
        rumble = lowpass(noise(seconds, seed)[:len(t)], 180) * 2.2
        wobble = 0.75 + 0.25 * np.sin(2 * np.pi * 5.5 * t)
        return (body * 0.5 + rumble * 0.5) * wobble * envelope(len(t), 0.06, 1.2) * 0.8
    if kind == "shriek":
        seconds = 1.8
        t = np.linspace(0, seconds, int(seconds * RATE), endpoint=False)
        pitch = 520 + 1400 * (t / seconds) ** 1.7
        body = np.sin(2 * np.pi * np.cumsum(pitch) / RATE)
        return body * envelope(len(t), 0.02, 0.9) * 0.7
    if kind == "clock":
        seconds = 2.6
        length = int(seconds * RATE)
        out = np.zeros(length)
        for index in range(3):
            piece = tone(196, 1.6, (1.0, 0.5, 0.3, 0.2))
            piece = piece * envelope(len(piece), 0.002, 1.4)
            mix_into(out, piece, int(index * 0.75 * RATE), 0.6)
        return out * 0.85
    # "whoosh"
    seconds = 2.0
    length = int(seconds * RATE)
    swept = lowpass(noise(seconds, seed)[:length], 2400)
    shape = np.sin(np.linspace(0, np.pi, length)) ** 1.4
    tonal = tone(330, seconds, (1.0, 0.4)) * 0.35
    return (swept * 2.2 * shape + tonal * shape) * 0.75


FINALES = {
    "finale_UL1": "bell",
    "finale_UL2": "howl",
    "finale_UR1": "organ",
    "finale_UR2": "chime",
    "finale_DL1": "growl",
    "finale_DL2": "shriek",
    "finale_DR1": "clock",
    "finale_DR2": "whoosh",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("sounds"))
    parser.add_argument("--ambient-seconds", type=float, default=30.0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Writing placeholder sounds to {args.out}/")
    write_wav(args.out / "tick.wav", make_tick())
    write_wav(args.out / "button.wav", make_button())
    write_wav(args.out / "landing.wav", make_landing())
    write_wav(args.out / "ambient.wav", make_ambient(args.ambient_seconds))
    for index, (name, kind) in enumerate(FINALES.items()):
        write_wav(args.out / f"{name}.wav", make_finale(kind, seed=100 + index))
    print("Done. Assign them in the web interface, or run tools/apply_default_sounds.py")


if __name__ == "__main__":
    main()
