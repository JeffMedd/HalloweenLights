#!/usr/bin/env python3
"""Offline checks for the parts that are awkward to test with real hardware.

    python3 tools/selftest.py

Exits non-zero if anything fails. No LEDs, no audio device and no GPIO needed:
DDP goes to a local socket and audio is stubbed out.
"""

from __future__ import annotations

import socket
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from display import effects                                       # noqa: E402
from display.colour import hex_to_rgb, rgb_to_hex                  # noqa: E402
from display.config import ConfigStore, validate                   # noqa: E402
from display.ddp import DDPSender                                  # noqa: E402
from display.geometry import Geometry                              # noqa: E402
from display.playlist import PlaylistRunner                        # noqa: E402
from display.testmode import TestMode                              # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition, detail: str = "") -> None:
    ok = bool(condition)
    print(f"  [{'pass' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def section(title: str) -> None:
    print(f"\n{title}")


def quiet_cfg(**overrides):
    cfg = validate({})
    for c in cfg["controllers"]:
        c["host"], c["port"] = "127.0.0.1", 45999
    cfg["audio"]["enabled"] = False
    cfg["button"]["enabled"] = False
    for key, value in overrides.items():
        cfg[key].update(value)
    return cfg


def make_engine(cfg):
    from display.game import Engine
    root = Path(tempfile.mkdtemp())
    store = ConfigStore(root / "config.json")
    store.save(cfg)
    return Engine(store, root / "sounds", root / "data"), root


# ---------------------------------------------------------------------------

def test_colours() -> None:
    section("Colour handling")
    check("hex round trip", rgb_to_hex(hex_to_rgb("#8A2BE2")) == "#8A2BE2")
    check("short hex", hex_to_rgb("#f80") == hex_to_rgb("#FF8800"))
    check("garbage is black", hex_to_rgb("not a colour") == (0.0, 0.0, 0.0))


def test_config() -> None:
    section("Config validation and migration")
    cfg = validate({"timing": {"cycle_seconds": 999, "min_step_ms": 5, "max_step_ms": 1},
                    "render": {"fps": 500, "gamma": 9},
                    "sections": {"UL1": {"strips": [{"side": "top", "pixels": -3}],
                                         "colour": "rubbish"}},
                    "controllers": [{"name": "x", "host": "1.2.3.4", "colour_order": "ZZZ",
                                     "sections": ["UL1", "nonexistent"]}]})
    check("cycle clamped", cfg["timing"]["cycle_seconds"] == 60.0)
    check("max step never below min", cfg["timing"]["max_step_ms"] >= cfg["timing"]["min_step_ms"])
    check("fps clamped", cfg["render"]["fps"] == 120)
    check("gamma clamped", cfg["render"]["gamma"] == 3.5)
    check("strip pixels at least 1", cfg["sections"]["UL1"]["strips"][0]["pixels"] == 1)
    check("section pixels derived from strips", cfg["sections"]["UL1"]["pixels"] == 1)
    check("bad colour becomes black", cfg["sections"]["UL1"]["colour"] == "#000000")
    check("bad colour order falls back", cfg["controllers"][0]["colour_order"] == "RGB")
    check("unknown section dropped", cfg["controllers"][0]["sections"] == ["UL1"])

    empty = validate({})
    check("all eight sections", len(empty["sections"]) == 8)
    check("four strips of 13 each",
          all([s["pixels"] for s in sec["strips"]] == [13] * 4 for sec in empty["sections"].values()))
    check("416 pixels by default", sum(s["pixels"] for s in empty["sections"].values()) == 416)
    check("default playlists present", len(empty["playlists"]) >= 2)
    check("active playlist valid", empty["idle"]["playlist"] in [p["id"] for p in empty["playlists"]])

    # A config written by the first version: pixel counts, no strips, old idle keys.
    old = {"sections": {"UL1": {"pixels": 52, "colour": "#FF6A00"}},
           "idle": {"mode": "breathe", "colour": "#2A0A3C", "brightness": 0.25}}
    migrated = validate(old)
    check("old pixel count becomes four strips",
          [s["pixels"] for s in migrated["sections"]["UL1"]["strips"]] == [13, 13, 13, 13])
    check("old idle keys do not dim the new effects", migrated["idle"]["master"] == 1.0)

    pl = validate({"playlists": [{"id": "x", "name": "t", "items": [
        {"effect": "ghosts", "params": {"count": 99, "colour": "zz"}, "duration": 1},
        {"effect": "no-such-effect", "duration": 30}]}]})["playlists"][0]
    check("unknown effects dropped from playlists", len(pl["items"]) == 1)
    check("effect params clamped", pl["items"][0]["params"]["count"] == 5)
    check("bad effect colour falls back", pl["items"][0]["params"]["colour"] == "#F2F0E6")
    check("durations clamped", pl["items"][0]["duration"] == 5)


def test_geometry() -> None:
    section("Geometry")
    cfg = validate({})
    geo = Geometry(cfg)
    check("416 pixels, 32 strips", geo.n == 416 and len(geo.strips) == 32)
    ux, uy, _, _ = geo.rects["UL1"]
    dx, dy, _, _ = geo.rects["DR2"]
    check("UL1 is top left", ux == 0 and uy == 0)
    check("DR2 is bottom right", dx > ux and dy > uy)
    check("upstairs bay above ground bay", geo.rects["UL2"][1] < geo.rects["DL1"][1])
    check("upper sash above lower sash", geo.rects["UL1"][1] < geo.rects["UL2"][1])
    check("left window left of right window", geo.rects["UL1"][0] < geo.rects["UR1"][0])

    top = geo.strip_slice("UL1:0")
    xs = geo.x_mm[top]
    check("top strip runs left to right", np.all(np.diff(xs) > 0))
    check("top strip sits on the top edge", np.allclose(geo.y_mm[top], uy))
    right = geo.strip_slice("UL1:1")
    check("right strip runs downwards", np.all(np.diff(geo.y_mm[right]) > 0))

    rev = validate({})
    rev["sections"]["UL1"]["strips"][0]["reverse"] = True
    g2 = Geometry(validate(rev))
    check("reversed strip runs right to left", np.all(np.diff(g2.x_mm[g2.strip_slice("UL1:0")]) < 0))

    check("resolve all", len(geo.resolve(["all"])) == 32)
    check("resolve a bay", len(geo.resolve(["floor:upstairs"])) == 16)
    check("resolve a window", geo.resolve(["window:UL"]) == [f"UL{s}:{k}" for s in (1, 2) for k in range(4)])
    check("resolve a sash", geo.resolve(["DR1"]) == [f"DR1:{k}" for k in range(4)])
    check("resolve a strip", geo.resolve(["strip:UL2:3"]) == ["UL2:3"])
    check("rubbish resolves to nothing", geo.resolve(["banana", "floor:attic"]) == [])
    check("tour snakes round the house",
          geo.tour == ["UL1", "UR1", "UR2", "UL2", "DL1", "DR1", "DR2", "DL2"], str(geo.tour))


def test_gamma() -> None:
    section("Gamma")
    table = DDPSender._build_gamma_table(2.2)
    top = len(table) - 1
    check("full scale is 255", table[top] == 255)
    check("zero is zero", table[0] == 0)
    check("10% survives quantisation", table[int(0.10 * top)] > 0, f"-> {table[int(0.10 * top)]}/255")
    check("monotonic", all(table[i] <= table[i + 1] for i in range(top)))


def _mini(sections_px, controller_sections, **ctrl):
    cfg = validate({})
    for sid, strips in sections_px.items():
        cfg["sections"][sid]["strips"] = [{"side": "top", "pixels": n, "reverse": False} for n in strips]
    cfg = validate(cfg)
    geo = Geometry(cfg)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.5)
    spec = {"name": "t", "host": "127.0.0.1", "port": sock.getsockname()[1], "enabled": True,
            "sections": controller_sections, "colour_order": "RGB", "white_mode": "none"}
    spec.update(ctrl)
    return geo, sock, DDPSender([spec], geo, gamma=1.0, master=1.0)


def _recv_all(sock):
    out = []
    while True:
        try:
            out.append(sock.recvfrom(4096)[0])
        except socket.timeout:
            return out


def test_packing() -> None:
    section("DDP packing")
    geo, sock, sender = _mini({"UL1": [3], "UL2": [2]}, ["UL1", "UL2"])
    frame = geo.blank()
    frame[geo.section_slices["UL1"]] = (1.0, 0.0, 0.0)
    frame[geo.section_slices["UL2"]] = (0.0, 0.0, 1.0)
    sender.send(frame)
    data = _recv_all(sock)[0]
    check("header version and push", data[0] == 0x41, f"0x{data[0]:02X}")
    check("RGB data type", data[2] == 0x0B)
    check("declared length", int.from_bytes(data[8:10], "big") == 15)
    payload = list(data[10:])
    check("UL1 is red", payload[0:9] == [255, 0, 0] * 3)
    check("UL2 is blue", payload[9:15] == [0, 0, 255] * 2)
    sender.close()
    sock.close()

    geo, sock, sender = _mini({"UL1": [3], "UL2": [2]}, ["UL2", "UL1"])
    sender.send(frame)
    payload = list(_recv_all(sock)[0][10:])
    check("controller order follows its section list", payload[0:3] == [0, 0, 255])
    sender.close()
    sock.close()

    geo, sock, sender = _mini({"UL1": [3], "UL2": [2]}, ["UL1", "UL2"], colour_order="GRB")
    sender.send(frame)
    check("GRB moves red to the second byte", list(_recv_all(sock)[0][10:13]) == [0, 255, 0])
    sender.close()
    sock.close()

    geo, sock, sender = _mini({"UL1": [3], "UL2": [2]}, ["UL1", "UL2"], white_mode="auto")
    frame2 = geo.blank()
    frame2[geo.section_slices["UL1"]] = 1.0
    frame2[geo.section_slices["UL2"]] = (1.0, 0.0, 0.0)
    sender.send(frame2)
    data = _recv_all(sock)[0]
    check("RGBW data type", data[2] == 0x1B)
    check("white extracted", list(data[10:14]) == [0, 0, 0, 255])
    check("pure red leaves white off", list(data[22:26]) == [255, 0, 0, 0])
    sender.close()
    sock.close()


def test_chunking() -> None:
    section("DDP chunking")
    geo, sock, sender = _mini({"UL1": [350, 350]}, ["UL1"])
    frame = geo.blank()
    frame[:] = 1.0
    sender.send(frame)
    packets = _recv_all(sock)
    sender.close()
    sock.close()
    check("split into 2 packets", len(packets) == 2, f"got {len(packets)}")
    if len(packets) == 2:
        check("first packet has no push", packets[0][0] == 0x40)
        check("last packet has push", packets[1][0] == 0x41)
        check("offset continues", int.from_bytes(packets[1][4:8], "big") == 1440)
        check("same sequence number", packets[0][1] == packets[1][1])
        total = sum(int.from_bytes(p[8:10], "big") for p in packets)
        check("all bytes accounted for", total == 2100, str(total))


def test_power() -> None:
    section("Power estimate")
    cfg = validate({})
    geo = Geometry(cfg)
    sender = DDPSender(cfg["controllers"], geo, gamma=2.2, master=1.0, watts_per_pixel=0.78)
    frame = geo.blank()
    frame[:] = 1.0
    sender.send(frame, transmit=False)
    watts = [p["watts"] for p in sender.power(24.0)]
    check("full white is about 162W per floor", all(abs(w - 162.2) < 0.5 for w in watts), str(watts))
    sender.send(geo.blank(), transmit=False)
    check("black is 0W", all(p["watts"] == 0 for p in sender.power(24.0)))
    sender.close()


def test_effects() -> None:
    section("Idle effects")
    cfg = validate({})
    geo = Geometry(cfg)
    animated = [k for k in effects.REGISTRY if k not in ("off", "solid")]
    check("at least 10 animated effects", len(animated) >= 10, f"{len(animated)}")
    worst = 0.0
    for key in effects.REGISTRY:
        fx = effects.create(key, {}, seed=7)
        ok = True
        lit = 0.0
        started = time.perf_counter()
        for i in range(600):                         # 12 simulated seconds at 50fps
            frame = fx.render(i / 50.0, geo)
            if frame.shape != (geo.n, 3) or not np.isfinite(frame).all() \
                    or frame.min() < 0 or frame.max() > 1:
                ok = False
                break
            lit = max(lit, float(frame.max()))
        per = (time.perf_counter() - started) / 600 * 1000
        worst = max(worst, per)
        check(f"{key} renders cleanly", ok and (lit > 0.05 or key == "off"),
              f"{per:.2f} ms/frame, peak {lit:.2f}")
    check("every effect well inside the frame budget", worst < 5.0, f"worst {worst:.2f} ms")

    a = effects.create("ghosts", {"count": 2}, seed=1)
    b = effects.create("ghosts", {"count": 2}, seed=1)
    check("same seed, same picture", np.allclose(a.render(3.0, geo), b.render(3.0, geo)))
    dim = effects.create("solid", {"colour": "#FFFFFF", "brightness": 0.5}).render(0, geo)
    check("brightness parameter applies", np.allclose(dim, 0.5))


def test_playlist() -> None:
    section("Playlist runner")
    cfg = validate({})
    geo = Geometry(cfg)
    playlist = {"id": "p", "name": "t", "shuffle": False, "items": [
        {"id": "a", "effect": "solid", "params": effects.validate_params("solid", {"colour": "#FF0000"}),
         "duration": 10, "fade": 2, "enabled": True},
        {"id": "b", "effect": "solid", "params": effects.validate_params("solid", {"colour": "#0000FF"}),
         "duration": 10, "fade": 2, "enabled": True},
        {"id": "c", "effect": "solid", "params": effects.validate_params("solid", {"colour": "#00FF00"}),
         "duration": 10, "fade": 0, "enabled": False},
    ]}
    runner = PlaylistRunner(seed=1)
    runner.load(playlist, 0.0)
    f = runner.render(1.0, geo)
    check("starts on the first item", np.allclose(f[0], (1, 0, 0)))
    f = runner.render(9.0, geo)
    check("crossfades before the end", 0.1 < f[0][0] < 0.9 and 0.1 < f[0][2] < 0.9,
          f"{np.round(f[0], 2)}")
    f = runner.render(11.0, geo)
    check("then plays the second", np.allclose(f[0], (0, 0, 1)))
    check("disabled items are skipped", runner.status(11.0)["count"] == 2)
    runner.render(21.0, geo)
    check("loops back to the start", runner.status(21.0)["item"] == "a")

    edited = dict(playlist)
    edited["items"] = [dict(i) for i in playlist["items"]]
    edited["items"][1]["duration"] = 30
    before = runner.item_start
    runner.load(edited, 22.0)
    check("editing another item keeps the current one playing",
          runner.status(22.0)["item"] == "a" and runner.item_start == before)

    shuffled = dict(playlist, shuffle=True, items=[dict(i, enabled=True, fade=0) for i in playlist["items"]])
    runner = PlaylistRunner(seed=3)
    runner.load(shuffled, 0.0)
    seen, repeats, last = [], 0, None
    for step in range(60):
        item = runner.status(step * 10.0 + 0.5)["item"]
        runner.render(step * 10.0 + 0.5, geo)
        item = runner.status(step * 10.0 + 0.5)["item"]
        repeats += item == last
        last = item
        seen.append(item)
    check("shuffle plays everything", set(seen) == {"a", "b", "c"})
    check("shuffle never repeats back to back", repeats == 0, f"{repeats}")


def test_testmode() -> None:
    section("Test mode")
    cfg = validate({})
    geo = Geometry(cfg)
    tm = TestMode(geo, cfg)
    keys = tm.set(["UL1"], "on", "#FF0000", 1.0, "solid")
    check("a sash lights its four strips", len(keys) == 4 and tm.active)
    tm.set(["window:UL"], "toggle")
    check("toggling a part-lit group lights all of it", len(tm.lit) == 8)
    tm.set(["window:UL"], "toggle")
    check("toggling a fully lit group switches it off", len(tm.lit) == 0)

    tm.set(["UL1:0"], "on", "#FFFFFF", 1.0, "markers")
    frame = tm.render(0.0)
    sl = geo.strip_slice("UL1:0")
    check("markers: first pixel green", np.allclose(frame[sl][0], (0, 1, 0)))
    check("markers: last pixel red", np.allclose(frame[sl][-1], (1, 0, 0)))
    check("nothing else lit", np.count_nonzero(frame.sum(1)) == geo.strip_by_key["UL1:0"]["pixels"])

    tm.compare({"target": "UL1", "colour": "#FF6A00", "level": 1.0, "pattern": "solid"},
               {"target": "UL2", "colour": "#FF6A00", "level": 0.3, "pattern": "alternate"})
    frame = tm.render(0.0)
    a = frame[geo.section_slices["UL1"]]
    b = frame[geo.section_slices["UL2"]]
    check("A/B lights both sides", a.max() > 0.9 and 0.25 < b.max() < 0.35)
    expected = sum((s["pixels"] + 1) // 2 for s in geo.strips if s["section"] == "UL2")
    check("B every other pixel", np.count_nonzero(b.sum(1)) == expected,
          f"{np.count_nonzero(b.sum(1))} of {expected}")

    tm.start_sequence("walk", ["UL1:1"])
    tm.sequence["started"] = 0.0
    frame = tm.render(1.0)
    check("walk shows a head pixel", frame.max() > 0.9)
    check("walk reports where it is", "UL1 right strip" in tm.info, tm.info)
    tm.start_sequence("rgbw")
    tm.sequence["started"] = 0.0
    tm.render(0.1)
    check("colour check names the colour", "RED" in tm.info, tm.info)
    tm.start_sequence("load")
    tm.sequence["started"] = 0.0
    check("load test lights everything white", tm.render(1.0).min() == 1.0)
    tm.render(10_000.0)
    check("load test ends by itself", tm.sequence is None)

    tm.last_activity = -10_000.0
    tm.check_timeout(0.0)
    check("times out when left alone", not tm.active and "timed out" in tm.exit_reason)


def test_sequence_rules() -> None:
    section("Hop sequence")
    engine, _ = make_engine(quiet_cfg())
    ids = list(engine.cfg["sections"])
    repeats = penultimate = 0
    for _ in range(300):
        winner = engine.rng.choice(ids)
        seq = engine._build_sequence(ids, 18, winner, "random")
        repeats += 1000 * (seq[-1] != winner) + sum(seq[i] == seq[i + 1] for i in range(len(seq) - 1))
        penultimate += seq[-2] == winner
    check("always lands, never repeats back to back", repeats == 0, f"{repeats}")
    check("never dwells on the winner just before landing", penultimate == 0)
    seq = engine._build_sequence(ids, 18, ids[3], "sequential")
    check("sequential ends on the winner", seq[-1] == ids[3])
    engine.shutdown()


def test_engine() -> None:
    section("Engine round")
    cfg = quiet_cfg(timing={"cycle_seconds": 0.6, "flash_seconds": 0.2, "hold_seconds": 0.1,
                            "fade_seconds": 0.1, "cooldown_seconds": 0.1})
    engine, root = make_engine(cfg)
    engine.start()
    result = engine.trigger()
    check("trigger accepted", result["accepted"] is True)
    check("second press refused", engine.trigger()["accepted"] is False)
    seen = []
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        engine.tick()
        if not seen or seen[-1] != engine.state:
            seen.append(engine.state)
        if len(seen) > 1 and engine.state == "idle":
            break
        time.sleep(0.005)
    check("full state sequence", seen == ["cycle", "flash", "hold", "fade", "cooldown", "idle"],
          " -> ".join(seen))
    check("round recorded", engine.rounds == 1 and engine.last_winner == result["winner"])
    check("history written", (root / "data" / "history.json").is_file())
    check("no ddp error", engine.sender.last_error == "", engine.sender.last_error)

    engine.testmode.enter()
    check("test mode blocks the game", engine.trigger()["accepted"] is False)
    engine.trigger_from_button()
    engine.tick()
    check("button press in test mode is counted, not played",
          engine.state == "idle" and engine.last_press_at is not None)
    check("and flashes the lamp", engine._lamp_level(time.monotonic()) == 1.0)
    engine.testmode.exit()

    engine.preview_effect("lightning", {})
    engine.tick()
    payload = engine.preview()
    check("preview carries the effect frame", payload["fx"] is not None
          and len(payload["fx"]) == len(payload["live"]))
    engine.fx_preview_at = -100.0
    engine.tick()
    check("effect preview stops without keepalive", engine.fx_frame is None)

    engine.show_effect("solid", {"colour": "#00FF00"}, minutes=0)
    engine.tick()
    check("show on house overrides the playlist",
          np.allclose(engine._last_frame[0], (0, 1, 0)) and engine.idle_status(0)["override"])
    engine.resume_playlist()
    check("resume returns to the playlist", not engine.idle_status(0)["override"])

    new = validate(engine.cfg)
    new["sections"]["UL1"]["strips"].append({"side": "top", "pixels": 5, "reverse": False})
    engine.apply_config(validate(new))
    engine.tick()
    check("strip change rebuilds geometry live", engine.geo.n == 421 and engine.sender.targets[1].pixel_count == 213,
          f"{engine.geo.n}")

    cfg = validate(engine.cfg)
    for sid in list(cfg["sections"])[1:]:
        cfg["sections"][sid]["weight"] = 0.0
    engine.apply_config(cfg)
    winners = set()
    for _ in range(25):
        engine.state = "idle"
        winners.add(engine.trigger()["winner"])
    check("weights respected", winners == {list(cfg["sections"])[0]}, str(winners))
    engine.shutdown()


def test_audio_schedule() -> None:
    section("Audio scheduling")
    cfg = quiet_cfg(timing={"cycle_seconds": 1.0, "flash_seconds": 0.3, "hold_seconds": 0.1,
                            "fade_seconds": 0.1, "cooldown_seconds": 0.1})
    cfg["audio"].update(enabled=True, tick="tick.wav", button="button.wav",
                        landing="landing.wav", offset_ms=200.0)
    for sid in cfg["sections"]:
        cfg["sections"][sid]["sound"] = f"finale_{sid}.wav"
    engine, _ = make_engine(cfg)
    played = []
    engine.audio.play = lambda ch, name, vol=1.0, loops=0: played.append((time.monotonic(), name)) or True
    engine.audio.start_ambient = lambda: None
    engine.audio.set_ambient_volume = lambda: None
    engine.audio.stop_all_effects = lambda: None

    press_at = time.monotonic()
    winner = engine.trigger()["winner"]
    steps, cycle_end, first_hop = len(engine.schedule), engine.cycle_end, engine.schedule[0][0]
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and engine.state != "idle":
        engine.tick()
        time.sleep(0.004)
    names = [n for _t, n in played]
    check("one tick per hop", names.count("tick.wav") == steps)
    finales = [n for n in names if n.startswith("finale_")]
    check("one finale, for the winner", finales == [f"finale_{winner}.wav"], str(finales))
    landing_at = next(t for t, n in played if n == "landing.wav")
    check("landing fired ~200ms early", 0.15 < cycle_end - landing_at < 0.26,
          f"{(cycle_end - landing_at) * 1000:.0f}ms")
    ticks = sorted(t for t, n in played if n == "tick.wav")
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    check("opening ticks are not bunched", all(g > 0.02 for g in gaps))
    sting_at = next(t for t, n in played if n == "button.wav")
    check("sting plays on the press", abs(sting_at - press_at) < 0.05)
    check("lights held back by the offset", 0.15 < first_hop - press_at < 0.26)
    engine.shutdown()


def test_step_curve() -> None:
    section("Cycle pacing")
    for cycle in (3.0, 5.0, 10.0):
        d = effects.step_durations(cycle, 70, 700, 2.0, 0)
        check(f"auto fit sums to {cycle}s", abs(sum(d) - cycle) < 1e-6,
              f"{len(d)} steps, {d[0] * 1000:.0f} to {d[-1] * 1000:.0f}ms")
    check("explicit step count honoured", len(effects.step_durations(5.0, 70, 700, 2.0, 25)) == 25)


def main() -> None:
    print("Halloween window display: self test")
    for test in (test_colours, test_config, test_geometry, test_gamma, test_packing,
                 test_chunking, test_power, test_effects, test_playlist, test_testmode,
                 test_step_curve, test_sequence_rules, test_engine, test_audio_schedule):
        test()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
