"""Audio playback via pygame's mixer.

Four reserved channels so the layers never cut each other off:
    0  ambient bed (looped)
    1  cycling tick
    2  button sting / landing stab
    3  the winning section's finale sound

If pygame or an audio device is unavailable the player degrades to a no-op and
reports why, rather than taking the whole display down.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

CH_AMBIENT = 0
CH_TICK = 1
CH_STING = 2
CH_FINALE = 3


class AudioPlayer:
    def __init__(self, sounds_dir: os.PathLike | str):
        self.sounds_dir = Path(sounds_dir)
        self.sounds_dir.mkdir(parents=True, exist_ok=True)
        self.available = False
        self.status = "not initialised"
        self.device = ""
        self._pygame = None
        self._cache: Dict[str, object] = {}
        self._cache_stamp: Dict[str, float] = {}
        self._cfg: Dict = {}
        self._ambient_name = ""

    # -- lifecycle --------------------------------------------------------

    def start(self, cfg: Dict) -> None:
        self._cfg = cfg
        driver = (cfg.get("driver") or "").strip()
        if driver:
            os.environ["SDL_AUDIODRIVER"] = driver
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        try:
            import pygame  # noqa: PLC0415 - optional dependency
        except Exception as exc:  # pragma: no cover - environment dependent
            self.status = f"pygame unavailable: {exc}"
            return
        self._pygame = pygame
        device = (cfg.get("device") or "").strip()
        buffer = int(cfg.get("buffer") or 512)
        try:
            pygame.mixer.pre_init(44100, -16, 2, buffer)
            if device:
                pygame.mixer.init(frequency=44100, size=-16, channels=2,
                                  buffer=buffer, devicename=device)
            else:
                pygame.mixer.init(frequency=44100, size=-16, channels=2,
                                  buffer=buffer)
            pygame.mixer.set_num_channels(8)
            pygame.mixer.set_reserved(4)
        except Exception as exc:  # pragma: no cover - environment dependent
            self.status = f"mixer init failed: {exc}"
            self.available = False
            return
        self.available = True
        self.device = device or "system default"
        self.status = "ready"

    def stop(self) -> None:
        if self.available and self._pygame is not None:
            try:
                self._pygame.mixer.stop()
                self._pygame.mixer.quit()
            except Exception:
                pass
        self.available = False
        self._cache.clear()
        self._cache_stamp.clear()

    def reconfigure(self, cfg: Dict) -> None:
        """Restart the mixer only when the device level settings changed."""
        old = self._cfg or {}
        needs_restart = (
            not self.available
            or old.get("device") != cfg.get("device")
            or old.get("driver") != cfg.get("driver")
            or old.get("buffer") != cfg.get("buffer")
        )
        self._cfg = cfg
        if needs_restart:
            self.stop()
            if cfg.get("enabled", True):
                self.start(cfg)
        if not cfg.get("enabled", True):
            self.stop()
            self.status = "disabled"

    # -- helpers ----------------------------------------------------------

    def list_devices(self) -> List[str]:
        try:
            import pygame
            from pygame._sdl2 import audio as sdl2_audio  # type: ignore
            started = pygame.mixer.get_init() is not None
            if not started:
                pygame.mixer.init()
            names = list(sdl2_audio.get_audio_device_names(False))
            if not started:
                pygame.mixer.quit()
            return names
        except Exception:
            return []

    def list_sounds(self) -> List[str]:
        if not self.sounds_dir.exists():
            return []
        allowed = {".wav", ".ogg", ".mp3", ".flac"}
        return sorted(p.name for p in self.sounds_dir.iterdir()
                      if p.is_file() and p.suffix.lower() in allowed)

    def resolve(self, name: str) -> Optional[Path]:
        if not name:
            return None
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = self.sounds_dir / candidate.name
        return candidate if candidate.is_file() else None

    def _load(self, name: str):
        path = self.resolve(name)
        if path is None or self._pygame is None:
            return None
        key = str(path)
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return None
        if key in self._cache and self._cache_stamp.get(key) == stamp:
            return self._cache[key]
        try:
            sound = self._pygame.mixer.Sound(key)
        except Exception as exc:
            self.status = f"could not load {path.name}: {exc}"
            return None
        self._cache[key] = sound
        self._cache_stamp[key] = stamp
        return sound

    def duration(self, name: str) -> float:
        sound = self._load(name)
        try:
            return float(sound.get_length()) if sound else 0.0
        except Exception:
            return 0.0

    # -- playback ---------------------------------------------------------

    def _master(self) -> float:
        return float(self._cfg.get("master_volume", 1.0) or 0.0)

    def play(self, channel: int, name: str, volume: float = 1.0,
             loops: int = 0) -> bool:
        if not self.available or not self._cfg.get("enabled", True):
            return False
        sound = self._load(name)
        if sound is None:
            return False
        try:
            chan = self._pygame.mixer.Channel(channel)
            sound.set_volume(max(0.0, min(1.0, volume * self._master())))
            chan.play(sound, loops=loops)
            return True
        except Exception as exc:
            self.status = f"playback failed: {exc}"
            return False

    def start_ambient(self) -> None:
        name = self._cfg.get("ambient") or ""
        if not name:
            self.stop_ambient()
            return
        if name == self._ambient_name and self._channel_busy(CH_AMBIENT):
            self.set_ambient_volume()
            return
        if self.play(CH_AMBIENT, name,
                     float(self._cfg.get("ambient_volume", 0.3)), loops=-1):
            self._ambient_name = name

    def set_ambient_volume(self) -> None:
        if not self.available:
            return
        try:
            chan = self._pygame.mixer.Channel(CH_AMBIENT)
            chan.set_volume(max(0.0, min(1.0,
                float(self._cfg.get("ambient_volume", 0.3)) * self._master())))
        except Exception:
            pass

    def stop_ambient(self) -> None:
        self._ambient_name = ""
        if not self.available:
            return
        try:
            self._pygame.mixer.Channel(CH_AMBIENT).stop()
        except Exception:
            pass

    def _channel_busy(self, channel: int) -> bool:
        if not self.available:
            return False
        try:
            return bool(self._pygame.mixer.Channel(channel).get_busy())
        except Exception:
            return False

    def stop_all_effects(self) -> None:
        for channel in (CH_TICK, CH_STING, CH_FINALE):
            if not self.available:
                return
            try:
                self._pygame.mixer.Channel(channel).stop()
            except Exception:
                pass

    def describe(self) -> Dict:
        return {
            "available": self.available,
            "status": self.status,
            "device": self.device,
            "sounds": self.list_sounds(),
        }
