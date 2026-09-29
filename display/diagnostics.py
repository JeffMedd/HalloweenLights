"""Ask each WLED controller how it is getting on.

WLED answers GET /json/info with its version, configured LED count, Wi-Fi
signal and whether it is currently receiving realtime data. That is enough to
catch the common set-up faults from the web interface: the wrong IP address,
a pixel count that does not match, a weak signal upstairs, or DDP not arriving.
"""

from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

# LAN only: never route controller probes through a proxy from the environment.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _probe(ctrl: Dict[str, Any], expected: int, timeout: float) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "name": ctrl["name"], "host": ctrl["host"], "expected_pixels": expected,
        "reachable": False,
    }
    if not ctrl.get("host"):
        result["error"] = "no IP address set"
        return result
    started = time.monotonic()
    try:
        with _OPENER.open(f"http://{ctrl['host']}/json/info", timeout=timeout) as resp:
            info = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - report anything to the user
        result["error"] = str(exc)
        return result
    result["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    leds = info.get("leds") or {}
    wifi = info.get("wifi") or {}
    count = leds.get("count")
    result.update({
        "reachable": True,
        "wled_name": info.get("name"),
        "version": info.get("ver"),
        "led_count": count,
        "count_matches": count == expected if isinstance(count, int) else None,
        "rssi": wifi.get("rssi"),
        "signal": wifi.get("signal"),
        "channel": wifi.get("channel"),
        "uptime_s": info.get("uptime"),
        "free_heap": info.get("freeheap"),
        "realtime": bool(info.get("live")),
        "realtime_source": info.get("lm"),
        "realtime_ip": info.get("lip"),
        "max_current_ma": leds.get("maxpwr"),
    })
    notes = []
    if result["count_matches"] is False:
        notes.append(f"WLED has {count} LEDs configured but the Pi sends {expected}")
    if isinstance(result["signal"], (int, float)) and result["signal"] < 50:
        notes.append("weak Wi-Fi signal; expect dropped frames")
    if result["max_current_ma"]:
        notes.append("WLED current limiter is on and will dim full-brightness frames")
    if not result["realtime"]:
        notes.append("not receiving realtime data right now")
    result["notes"] = notes
    return result


def probe_controllers(controllers: List[Dict[str, Any]], expected: Dict[str, int],
                      timeout: float = 1.5) -> List[Dict[str, Any]]:
    if not controllers:
        return []
    with ThreadPoolExecutor(max_workers=len(controllers)) as pool:
        futures = [pool.submit(_probe, c, expected.get(c["name"], 0), timeout)
                   for c in controllers]
        return [f.result() for f in futures]
