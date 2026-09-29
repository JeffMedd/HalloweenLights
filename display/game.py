"""The engine: game state machine, idle playlist, test mode, and the frame loop.

One round is:

    press -> cycle (ease-out random hopping) -> flash the winner
          -> hold it solid -> fade back to idle -> cooldown -> idle

Between rounds the idle playlist runs. During a round it keeps running behind
the game, dimmed, so the losing windows never go completely dead.

Test mode takes the display over completely: the game is suspended and the
physical button only reports presses, which is what you want with the ladder
out and the strips half wired.

Everything is driven from a single fixed-rate tick so the visuals, the audio
cues and the button lamp all stay on the same clock.
"""

from __future__ import annotations

import base64
import json
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import effects
from .audio import CH_FINALE, CH_STING, CH_TICK, AudioPlayer
from .button import ButtonIO
from .colour import hex_to_rgb
from .config import ConfigStore, enabled_section_ids
from .ddp import DDPSender
from .geometry import Geometry
from .playlist import PlaylistRunner, SingleEffect
from .testmode import TestMode

STATE_IDLE = "idle"
STATE_CYCLE = "cycle"
STATE_FLASH = "flash"
STATE_HOLD = "hold"
STATE_FADE = "fade"
STATE_COOLDOWN = "cooldown"
STATE_SPOT = "spotlight"      # a sash lit briefly from the Play tab

PREVIEW_KEEPALIVE = 15.0      # seconds an effect preview runs without a refresh


def _geometry_signature(cfg: Dict[str, Any]) -> str:
    return json.dumps({
        "layout": cfg["layout"],
        "sections": {sid: [s["strips"], s["floor"], s["row"], s["col"],
                           s["width_mm"], s["height_mm"]]
                     for sid, s in cfg["sections"].items()},
    }, sort_keys=True)


def _b64(frame: Optional[np.ndarray], scale: float = 1.0) -> Optional[str]:
    if frame is None:
        return None
    data = (np.clip(frame * scale, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return base64.b64encode(data.tobytes()).decode("ascii")


class Engine:
    def __init__(self, store: ConfigStore, sounds_dir: Path, data_dir: Path):
        self.store = store
        self.cfg = store.data
        self.sounds_dir = Path(sounds_dir)
        self.data_dir = Path(data_dir)
        self.history_path = self.data_dir / "history.json"
        self.lock = threading.RLock()

        self.geo = Geometry(self.cfg)
        self._geo_sig = _geometry_signature(self.cfg)
        self.audio = AudioPlayer(self.sounds_dir)
        self.button = ButtonIO(on_press=self.trigger_from_button)
        self.sender = self._build_sender()
        self.testmode = TestMode(self.geo, self.cfg)

        now = time.monotonic()
        self.idle = PlaylistRunner()
        self.idle.load(self._active_playlist(), now)
        self.override: Optional[SingleEffect] = None
        self.override_until: Optional[float] = None
        self.fx_preview: Any = None
        self.fx_preview_at = 0.0
        self.fx_frame: Optional[np.ndarray] = None

        self.state = STATE_IDLE
        self.state_started = now
        self.rng = random.Random()

        self.schedule: List[Tuple[float, float, str]] = []   # (start, duration, sid)
        self.cycle_start = 0.0
        self.cycle_end = 0.0
        self.press_at = 0.0
        self.step_index = -1
        self.step_started = 0.0
        self.step_duration = 0.0
        self.prev_section: Optional[str] = None
        self.current_section: Optional[str] = None
        self.winner: Optional[str] = None

        self.audio_events: List[Dict] = []
        self.spot: Dict[str, Tuple[float, float, float]] = {}
        self.spot_until = 0.0

        self.rounds = 0
        self.ignored_presses = 0
        self.last_winner: Optional[str] = None
        self.last_round_at: Optional[float] = None
        self.last_press_at: Optional[float] = None
        self.press_mono = -10.0
        self.history: List[Dict] = self._load_history()
        self.frames = 0
        self.measured_fps = 0.0
        self._last_frame_time = now
        self._trigger_pending = False
        self._last_frame: np.ndarray = self.geo.blank()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.audio.start(self.cfg["audio"])
        self.audio.start_ambient()
        self.button.start(self.cfg["button"])
        self.state = STATE_IDLE
        self.state_started = time.monotonic()

    def shutdown(self) -> None:
        try:
            self.sender.blackout()
        except Exception:
            pass
        self.button.set_lamp(0.0)
        self.button.stop()
        self.audio.stop()
        self.sender.close()

    def _build_sender(self) -> DDPSender:
        r = self.cfg["render"]
        return DDPSender(self.cfg["controllers"], self.geo, gamma=r["gamma"],
                         master=r["master_brightness"],
                         watts_per_pixel=r["watts_per_pixel"])

    def _active_playlist(self) -> Optional[Dict[str, Any]]:
        wanted = self.cfg["idle"].get("playlist")
        for playlist in self.cfg["playlists"]:
            if playlist["id"] == wanted:
                return playlist
        return self.cfg["playlists"][0] if self.cfg["playlists"] else None

    def apply_config(self, new_cfg: Dict) -> None:
        """Adopt a new config, rebuilding only what actually changed."""
        with self.lock:
            old = self.cfg
            self.cfg = new_cfg
            sig = _geometry_signature(new_cfg)
            geo_changed = sig != self._geo_sig
            if geo_changed:
                self.geo = Geometry(new_cfg)
                self._geo_sig = sig
                self._last_frame = self.geo.blank()
                self.fx_frame = None
            if geo_changed or (json.dumps(old["controllers"], sort_keys=True)
                               != json.dumps(new_cfg["controllers"], sort_keys=True)):
                self.sender.close()
                self.sender = self._build_sender()
            else:
                self.sender.set_gamma(new_cfg["render"]["gamma"])
                self.sender.set_master(new_cfg["render"]["master_brightness"])
                self.sender.watts_per_pixel = new_cfg["render"]["watts_per_pixel"]
            self.testmode.rebind(self.geo, new_cfg)
            self.idle.load(self._active_playlist(), time.monotonic())
            self.audio.reconfigure(new_cfg["audio"])
            if self.state in (STATE_IDLE, STATE_COOLDOWN):
                self.audio.start_ambient()
            else:
                self.audio.set_ambient_volume()
            self.button.reconfigure(new_cfg["button"])

    # ------------------------------------------------------------------
    # Triggering
    # ------------------------------------------------------------------

    def trigger_from_button(self) -> None:
        """Called from the gpiozero thread; just raise a flag."""
        self._trigger_pending = True

    def trigger(self, forced: bool = False) -> Dict:
        if self.testmode.active:
            return {"accepted": False, "reason": "test mode is on"}
        if self.state not in (STATE_IDLE, STATE_SPOT) and not forced:
            self.ignored_presses += 1
            return {"accepted": False, "reason": f"busy ({self.state})"}
        candidates = enabled_section_ids(self.cfg)
        if not candidates:
            return {"accepted": False, "reason": "no sections enabled"}
        self._begin_round(candidates)
        return {"accepted": True, "winner": self.winner}

    def _begin_round(self, candidates: Sequence[str]) -> None:
        cfg = self.cfg
        timing = cfg["timing"]
        now = time.monotonic()

        weights = [max(0.0, float(cfg["sections"][s]["weight"])) for s in candidates]
        if sum(weights) <= 0:
            weights = [1.0] * len(candidates)
        winner = self.rng.choices(list(candidates), weights=weights, k=1)[0]

        durations = effects.step_durations(
            timing["cycle_seconds"], timing["min_step_ms"], timing["max_step_ms"],
            timing["easing"], timing["steps"],
        )
        sequence = self._build_sequence(list(candidates), len(durations), winner,
                                        timing["order"])

        # Audio cues are fired ahead of their visual beat by the output latency.
        # Nothing can be played before the button was pressed, so the lights are
        # held back by the same amount instead: the press is heard immediately
        # and the first hop lands with its tick. With a wired output the offset
        # is zero and there is no delay at all.
        offset = float(cfg["audio"].get("offset_ms", 0.0)) / 1000.0
        self.schedule = []
        cursor = now + offset
        for duration, sid in zip(durations, sequence):
            self.schedule.append((cursor, duration, sid))
            cursor += duration

        self.cycle_start = now + offset
        self.cycle_end = cursor
        self.press_at = now
        self.winner = winner
        self.step_index = -1
        self.prev_section = None
        self.current_section = None
        self.spot = {}
        self.state = STATE_CYCLE
        self.state_started = now

        self.audio_events = self._build_audio_events()
        self.audio.stop_all_effects()

    def _build_sequence(self, ids: List[str], count: int, winner: str,
                        order: str) -> List[str]:
        if count <= 1:
            return [winner]
        if order == "sequential":
            start = ids.index(winner)
            return [ids[(start - (count - 1 - i)) % len(ids)] for i in range(count)]
        sequence: List[str] = []
        previous: Optional[str] = None
        for index in range(count - 1):
            choices = [s for s in ids if s != previous] or list(ids)
            if index == count - 2 and len(choices) > 1:
                # Do not sit on the winner immediately before landing on it.
                choices = [s for s in choices if s != winner] or choices
            pick = self.rng.choice(choices)
            sequence.append(pick)
            previous = pick
        sequence.append(winner)
        return sequence

    def _build_audio_events(self) -> List[Dict]:
        audio = self.cfg["audio"]
        events: List[Dict] = []
        if audio.get("button"):
            events.append({"at": self.cycle_start, "channel": CH_STING, "sound": audio["button"],
                           "volume": audio["button_volume"], "fired": False})
        if audio.get("tick") and audio.get("tick_enabled", True):
            for start, _duration, _sid in self.schedule:
                events.append({"at": start, "channel": CH_TICK, "sound": audio["tick"],
                               "volume": audio["tick_volume"], "fired": False})
        if audio.get("landing"):
            events.append({"at": self.cycle_end, "channel": CH_STING,
                           "sound": audio["landing"],
                           "volume": audio["landing_volume"], "fired": False})
        finale = self.cfg["sections"].get(self.winner, {}).get("sound") or ""
        if finale:
            events.append({
                "at": self.cycle_end + float(audio.get("finale_delay_ms", 0)) / 1000.0,
                "channel": CH_FINALE, "sound": finale,
                "volume": audio.get("finale_volume", 1.0), "fired": False,
            })
        events.sort(key=lambda e: e["at"])
        return events

    # ------------------------------------------------------------------
    # Spotlight (Play tab: click a sash to light it briefly)
    # ------------------------------------------------------------------

    def show_test(self, colours: Dict[str, str], seconds: float = 5.0) -> None:
        self.spot = {sid: hex_to_rgb(value) for sid, value in colours.items()
                     if sid in self.geo.section_slices}
        self.spot_until = time.monotonic() + max(0.2, seconds)
        if self.state in (STATE_IDLE, STATE_SPOT, STATE_COOLDOWN):
            self.state = STATE_SPOT
            self.state_started = time.monotonic()

    def clear_test(self) -> None:
        self.spot = {}
        self.spot_until = 0.0
        if self.state == STATE_SPOT:
            self.state = STATE_IDLE
            self.state_started = time.monotonic()

    def stop_round(self) -> None:
        self.audio.stop_all_effects()
        self.schedule = []
        self.audio_events = []
        self.current_section = None
        self.prev_section = None
        self.spot = {}
        self.state = STATE_IDLE
        self.state_started = time.monotonic()
        self.audio.start_ambient()

    # ------------------------------------------------------------------
    # Idle effects: the playlist, a "show on house" override, the preview
    # ------------------------------------------------------------------

    def show_effect(self, key: str, params: Dict[str, Any], minutes: float = 0.0) -> None:
        now = time.monotonic()
        self.override = SingleEffect(key, params, now)
        self.override_until = now + minutes * 60.0 if minutes > 0 else None

    def resume_playlist(self) -> None:
        self.override = None
        self.override_until = None

    def skip_idle(self) -> None:
        self.override = None
        self.override_until = None
        self.idle.skip(time.monotonic())

    def set_preview(self, target: Any) -> None:
        self.fx_preview = target
        self.fx_preview_at = time.monotonic()

    def preview_effect(self, key: str, params: Dict[str, Any]) -> None:
        self.set_preview(SingleEffect(key, params, time.monotonic()))

    def preview_playlist(self, playlist: Dict[str, Any]) -> None:
        runner = PlaylistRunner()
        runner.load(playlist, time.monotonic())
        self.set_preview(runner)

    def keep_preview(self) -> None:
        self.fx_preview_at = time.monotonic()

    def stop_preview(self) -> None:
        self.fx_preview = None
        self.fx_frame = None

    def _idle_frame(self, now: float) -> np.ndarray:
        if self.override_until is not None and now >= self.override_until:
            self.resume_playlist()
        source = self.override or self.idle
        return source.render(now, self.geo) * float(self.cfg["idle"]["master"])

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.testmode.check_timeout(now)
            if self._trigger_pending:
                self._trigger_pending = False
                self.last_press_at = time.time()
                self.press_mono = now
                if self.testmode.active:
                    self.testmode.touch()      # reported, not played
                else:
                    self.trigger()
                now = time.monotonic()

            self._advance(now)
            self._fire_audio(now)

            idle = self._idle_frame(now)
            frame = self.testmode.render(now) if self.testmode.active else self.compose(now, idle)
            self._last_frame = frame
            self.sender.set_gamma(self.cfg["render"]["gamma"])
            self.sender.set_master(self.cfg["render"]["master_brightness"])
            self.sender.send(frame, transmit=self.cfg["render"]["enabled"])

            if self.fx_preview is not None:
                if now - self.fx_preview_at > PREVIEW_KEEPALIVE:
                    self.stop_preview()
                else:
                    self.fx_frame = self.fx_preview.render(now, self.geo)

            self.button.set_lamp(self._lamp_level(now))

            self.frames += 1
            delta = now - self._last_frame_time
            self._last_frame_time = now
            if delta > 0:
                self.measured_fps = self.measured_fps * 0.9 + (1.0 / delta) * 0.1

    def _advance(self, now: float) -> None:
        timing = self.cfg["timing"]
        state = self.state

        if state == STATE_SPOT:
            if now >= self.spot_until:
                self.clear_test()
            return

        if state == STATE_CYCLE:
            index = self._step_at(now)
            if index != self.step_index:
                if self.current_section is not None:
                    self.prev_section = self.current_section
                self.step_index = index
                if 0 <= index < len(self.schedule):
                    start, duration, sid = self.schedule[index]
                    self.current_section = sid
                    self.step_started = start
                    self.step_duration = duration
            if now >= self.cycle_end:
                self.current_section = self.winner
                self._enter(STATE_FLASH, now)
            return

        elapsed = now - self.state_started
        if state == STATE_FLASH and elapsed >= timing["flash_seconds"]:
            self._enter(STATE_HOLD, now)
        elif state == STATE_HOLD and elapsed >= timing["hold_seconds"]:
            self._enter(STATE_FADE, now)
        elif state == STATE_FADE and elapsed >= timing["fade_seconds"]:
            self._finish_round(now)
        elif state == STATE_COOLDOWN and elapsed >= timing["cooldown_seconds"]:
            self._enter(STATE_IDLE, now)

    def _enter(self, state: str, now: float) -> None:
        self.state = state
        self.state_started = now

    def _finish_round(self, now: float) -> None:
        self.rounds += 1
        self.last_winner = self.winner
        self.last_round_at = time.time()
        self.current_section = None
        self.prev_section = None
        self.history.insert(0, {"winner": self.winner, "at": self.last_round_at})
        del self.history[200:]
        self._save_history()
        self.audio.start_ambient()
        self._enter(STATE_COOLDOWN, now)

    def _step_at(self, now: float) -> int:
        if not self.schedule or now < self.schedule[0][0]:
            return -1        # still in the audio latency pre-roll
        for index, (start, duration, _sid) in enumerate(self.schedule):
            if start <= now < start + duration:
                return index
        return len(self.schedule) - 1

    def _fire_audio(self, now: float) -> None:
        """Fire each cue ahead of its visual beat by the output latency."""
        if not self.audio_events:
            return
        offset = float(self.cfg["audio"].get("offset_ms", 0.0)) / 1000.0
        for event in self.audio_events:
            if not event["fired"] and event["at"] - offset <= now:
                event["fired"] = True
                self.audio.play(event["channel"], event["sound"], event["volume"])
        if all(e["fired"] for e in self.audio_events):
            self.audio_events = []

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def _section_rgb(self, sid: Optional[str], flash: bool = False) -> np.ndarray:
        section = self.cfg["sections"].get(sid or "")
        if not section:
            return np.zeros(3)
        if flash and section.get("flash_colour"):
            return np.array(hex_to_rgb(section["flash_colour"]))
        return np.array(hex_to_rgb(section["colour"]))

    def compose(self, now: float, idle: np.ndarray) -> np.ndarray:
        geo = self.geo
        timing = self.cfg["timing"]
        dim = float(self.cfg["idle"]["cycle_background"])
        state = self.state
        sl = geo.section_slices

        # Disabled sections stay dark whatever the idle effect is doing.
        for sid, section in self.cfg["sections"].items():
            if not section["enabled"] and sid in sl:
                idle[sl[sid]] = 0.0

        if state in (STATE_IDLE, STATE_COOLDOWN):
            return idle

        if state == STATE_SPOT:
            frame = idle.copy()
            for sid, rgb in self.spot.items():
                frame[sl[sid]] = rgb
            return frame

        if state == STATE_CYCLE:
            frame = idle * dim
            trail = timing["trail"]
            if trail > 0 and self.prev_section in sl:
                span = max(0.001, trail * max(self.step_duration, 0.001))
                remaining = 1.0 - (now - self.step_started) / span
                if remaining > 0:
                    ghost = self._section_rgb(self.prev_section) * remaining * trail
                    part = frame[sl[self.prev_section]]
                    frame[sl[self.prev_section]] = np.maximum(part, ghost)
            if self.current_section in sl:
                frame[sl[self.current_section]] = self._section_rgb(self.current_section)
            return frame

        if self.winner not in sl:
            return idle
        win = sl[self.winner]

        if state == STATE_FLASH:
            frame = idle * dim
            if effects.square_wave(now - self.state_started, timing["flash_hz"],
                                   timing["flash_duty"]):
                frame[win] = self._section_rgb(self.winner, flash=True)
            return frame

        if state == STATE_HOLD:
            frame = idle * dim
            frame[win] = self._section_rgb(self.winner, flash=True)
            return frame

        if state == STATE_FADE:
            progress = min(1.0, (now - self.state_started) / max(0.001, timing["fade_seconds"]))
            level = dim + (1.0 - dim) * progress
            frame = idle * level
            lit = self._section_rgb(self.winner, flash=True) * (1.0 - progress)
            frame[win] = np.maximum(frame[win], lit)
            return frame

        return idle

    def _lamp_level(self, now: float) -> float:
        cfg = self.cfg["button"]
        if not cfg.get("lamp_enabled", True):
            return 0.0
        if self.testmode.active:
            if now - self.press_mono < 0.3:
                return 1.0                     # echo each press on the lamp
            if self.testmode.lamp_override is not None:
                return float(self.testmode.lamp_override)
            return cfg["idle_brightness"]
        state = self.state
        if state == STATE_CYCLE:
            on = effects.square_wave(now - self.press_at, cfg["cycle_flash_hz"], 0.5)
            return 1.0 if on else 0.05
        if state in (STATE_FLASH, STATE_HOLD):
            on = effects.square_wave(now - self.state_started, cfg["landing_flash_hz"], 0.5)
            return 1.0 if on else 0.15
        if state == STATE_FADE:
            span = max(0.001, self.cfg["timing"]["fade_seconds"])
            progress = min(1.0, (now - self.state_started) / span)
            return cfg["idle_brightness"] + (1.0 - cfg["idle_brightness"]) * (1.0 - progress)
        if state == STATE_COOLDOWN:
            return cfg["cooldown_brightness"]
        if cfg.get("idle_pulse", True):
            return cfg["idle_brightness"] * effects_breathe(now, cfg["idle_pulse_speed"], 0.45)
        return cfg["idle_brightness"]

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def idle_status(self, now: float) -> Dict[str, Any]:
        if self.override is not None:
            status = self.override.status(now)
            status["override"] = True
            status["remaining"] = (round(self.override_until - now)
                                   if self.override_until else None)
            return status
        status = self.idle.status(now)
        status["override"] = False
        return status

    def preview(self) -> Dict:
        """Everything the web interface animates, cheap enough for 20fps."""
        now = time.monotonic()
        progress = 0.0
        if self.state == STATE_CYCLE and self.cycle_end > self.cycle_start:
            progress = max(0.0, min(1.0, (now - self.cycle_start)
                                    / (self.cycle_end - self.cycle_start)))
        return {
            "state": "test" if self.testmode.active else self.state,
            "game_state": self.state,
            "pixels": self.geo.n,
            "live": _b64(self._last_frame, self.cfg["render"]["master_brightness"]),
            "fx": _b64(self.fx_frame),
            "active": self.current_section,
            "winner": self.winner if self.state in (STATE_FLASH, STATE_HOLD, STATE_FADE) else None,
            "progress": round(progress, 3),
            "lamp": round(self.button.lamp_level, 3),
            "fps": round(self.measured_fps, 1),
            "idle": self.idle_status(now),
            "test": self.testmode.state(now),
            "power": self.sender.power(self.cfg["render"]["supply_voltage"]),
            "presses": self.button.press_count,
            "last_press_at": self.last_press_at,
        }

    def status(self) -> Dict:
        now = time.monotonic()
        return {
            "state": "test" if self.testmode.active else self.state,
            "rounds": self.rounds,
            "ignored_presses": self.ignored_presses,
            "last_winner": self.last_winner,
            "last_round_at": self.last_round_at,
            "fps": round(self.measured_fps, 1),
            "frames": self.frames,
            "packets": self.sender.packets_sent,
            "ddp_error": self.sender.last_error,
            "pixels": self.geo.n,
            "controllers": self.sender.describe(),
            "power": self.sender.power(self.cfg["render"]["supply_voltage"]),
            "audio": self.audio.describe(),
            "button": self.button.describe(),
            "idle": self.idle_status(now),
            "test": self.testmode.state(now),
            "steps_last_round": len(self.schedule),
            "history": self.history[:20],
        }

    def expected_pixels(self) -> Dict[str, int]:
        return {t.name: t.pixel_count for t in self.sender.targets}

    # ------------------------------------------------------------------
    # History persistence
    # ------------------------------------------------------------------

    def _load_history(self) -> List[Dict]:
        try:
            return json.loads(self.history_path.read_text("utf-8"))[:200]
        except Exception:
            return []

    def _save_history(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(json.dumps(self.history[:200]), "utf-8")
        except OSError:
            pass


def effects_breathe(now: float, speed: float, depth: float) -> float:
    wave = 0.5 + 0.5 * np.sin(2 * np.pi * speed * now)
    return float((1.0 - depth) + depth * wave)
