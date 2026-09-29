"""Physical button input and illuminated lamp output.

The switch is a plain contact to ground on a Pi GPIO.  The lamp is 12V so it
runs through a logic level MOSFET driven from another GPIO, which lets us hold
it at a steady glow, pulse it slowly while idle and flash it during a round.

On anything that is not a Pi (or with lgpio missing) this falls back to a mock
so the rest of the application, including the web interface, still runs.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional


class ButtonIO:
    def __init__(self, on_press: Callable[[], None]):
        self._on_press = on_press
        self._lock = threading.Lock()
        self.backend = "none"
        self.status = "not initialised"
        self.switch_gpio: Optional[int] = None
        self.lamp_gpio: Optional[int] = None
        self.lamp_level = 0.0
        self._button = None
        self._lamp = None
        self._cfg: Dict = {}
        self.press_count = 0

    # -- lifecycle --------------------------------------------------------

    def start(self, cfg: Dict) -> None:
        self.stop()
        self._cfg = dict(cfg)
        if not cfg.get("enabled", True):
            self.backend = "disabled"
            self.status = "button disabled in config"
            return
        try:
            from gpiozero import Button, LED, PWMLED  # noqa: PLC0415
        except Exception as exc:
            self.backend = "mock"
            self.status = f"gpiozero unavailable ({exc}); web trigger only"
            return

        try:
            self._button = Button(
                int(cfg["switch_gpio"]),
                pull_up=bool(cfg.get("pull_up", True)),
                bounce_time=max(0.0, float(cfg.get("bounce_ms", 60)) / 1000.0),
            )
            self._button.when_pressed = self._handle_press
            self.switch_gpio = int(cfg["switch_gpio"])
        except Exception as exc:
            self._button = None
            self.backend = "mock"
            self.status = f"could not claim GPIO {cfg.get('switch_gpio')}: {exc}"
            return

        if cfg.get("lamp_enabled", True):
            try:
                if cfg.get("lamp_pwm", True):
                    self._lamp = PWMLED(int(cfg["lamp_gpio"]),
                                        frequency=int(cfg.get("lamp_frequency", 200)))
                else:
                    self._lamp = LED(int(cfg["lamp_gpio"]))
                self.lamp_gpio = int(cfg["lamp_gpio"])
            except Exception as exc:
                self._lamp = None
                self.status = f"lamp GPIO unavailable: {exc}"

        self.backend = "gpiozero"
        if self.status in ("", "not initialised"):
            self.status = "ready"
        elif not self.status.startswith("lamp"):
            self.status = "ready"

    def stop(self) -> None:
        for device in (self._button, self._lamp):
            try:
                if device is not None:
                    device.close()
            except Exception:
                pass
        self._button = None
        self._lamp = None
        self.lamp_level = 0.0

    def reconfigure(self, cfg: Dict) -> None:
        watched = ("enabled", "switch_gpio", "pull_up", "bounce_ms",
                   "lamp_enabled", "lamp_gpio", "lamp_pwm", "lamp_frequency")
        if any(self._cfg.get(k) != cfg.get(k) for k in watched):
            self.start(cfg)
        else:
            self._cfg = dict(cfg)

    # -- input ------------------------------------------------------------

    def _handle_press(self) -> None:
        with self._lock:
            self.press_count += 1
        try:
            self._on_press()
        except Exception as exc:  # pragma: no cover - defensive
            self.status = f"press handler error: {exc}"

    # -- output -----------------------------------------------------------

    def set_lamp(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        if abs(level - self.lamp_level) < 0.004:
            return
        self.lamp_level = level
        if self._lamp is None:
            return
        try:
            if hasattr(self._lamp, "value"):
                self._lamp.value = level
            else:  # plain on/off LED
                if level >= 0.5:
                    self._lamp.on()
                else:
                    self._lamp.off()
        except Exception:
            pass

    def describe(self) -> Dict:
        return {
            "backend": self.backend,
            "status": self.status,
            "switch_gpio": self.switch_gpio,
            "lamp_gpio": self.lamp_gpio,
            "lamp_level": round(self.lamp_level, 3),
            "presses": self.press_count,
        }
