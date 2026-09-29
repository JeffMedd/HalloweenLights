"""Web layer: REST config API, live preview websocket, static admin UI.

Built directly on Starlette rather than FastAPI. The API is a handful of JSON
endpoints, so the extra layer would only add a dependency and a startup cost on
the Pi for nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import effects, testmode
from .audio import CH_FINALE
from .config import PALETTE, SECTION_ORDER, ConfigStore, _validate_playlists, section_ids
from .diagnostics import probe_controllers
from .game import Engine

WEB_DIR = Path(__file__).parent / "web"
SAFE_NAME = re.compile(r"^[A-Za-z0-9 ._-]{1,120}$")
ALLOWED_SOUND_SUFFIXES = {".wav", ".ogg", ".mp3", ".flac"}
MAX_SOUND_BYTES = 25 * 1024 * 1024


class FrameLoop:
    """Runs engine.tick() on its own thread at a steady rate.

    A thread rather than an asyncio task so that web traffic, websocket clients
    and config saves can never add jitter to the frame clock.
    """

    def __init__(self, engine: Engine):
        self.engine = engine
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.late_frames = 0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="frame-loop",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        next_at = time.monotonic()
        while not self._stop.is_set():
            interval = 1.0 / max(5, int(self.engine.cfg["render"]["fps"]))
            try:
                self.engine.tick()
            except Exception as exc:  # pragma: no cover - keep the show running
                print(f"[frame-loop] {exc!r}")
            next_at += interval
            delay = next_at - time.monotonic()
            if delay < -0.25:
                self.late_frames += 1
                next_at = time.monotonic()
                delay = 0.0
            if delay > 0:
                self._stop.wait(delay)


async def _json_body(request: Request, required: bool = True) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        if required:
            raise HTTPException(400, "expected a JSON body")
        return {}
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    return body


def create_app(config_path: Path, sounds_dir: Path, data_dir: Path) -> Starlette:
    sounds_dir = Path(sounds_dir)
    data_dir = Path(data_dir)
    sounds_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    store = ConfigStore(config_path)
    store.load()
    engine = Engine(store, sounds_dir, data_dir)
    loop = FrameLoop(engine)

    # -- config -----------------------------------------------------------

    async def get_config(request: Request) -> Response:
        return JSONResponse({
            "config": store.data,
            "palette": PALETTE,
            "section_order": section_ids(store.data),
            "known_sections": SECTION_ORDER,
        })

    async def put_config(request: Request) -> Response:
        payload = await _json_body(request)
        with engine.lock:
            data = store.save(payload)
            engine.apply_config(data)
        return JSONResponse({"ok": True, "config": data})

    async def patch_config(request: Request) -> Response:
        payload = await _json_body(request)
        with engine.lock:
            data = store.update(payload)
            engine.apply_config(data)
        return JSONResponse({"ok": True, "config": data})

    async def reload_config(request: Request) -> Response:
        with engine.lock:
            data = store.load()
            engine.apply_config(data)
        return JSONResponse({"ok": True, "config": data})

    # -- control ----------------------------------------------------------

    async def trigger(request: Request) -> Response:
        payload = await _json_body(request, required=False)
        with engine.lock:
            return JSONResponse(engine.trigger(forced=bool(payload.get("force"))))

    async def stop_round(request: Request) -> Response:
        with engine.lock:
            engine.stop_round()
        return JSONResponse({"ok": True})

    async def test(request: Request) -> Response:
        payload = await _json_body(request)
        colours = payload.get("colours") or {}
        if not isinstance(colours, dict):
            raise HTTPException(400, "colours must be an object")
        seconds = float(payload.get("seconds", 5.0))
        with engine.lock:
            engine.show_test(
                {k: v for k, v in colours.items() if k in store.data["sections"]},
                seconds,
            )
        return JSONResponse({"ok": True})

    async def test_section(request: Request) -> Response:
        section_id = request.path_params["section_id"]
        section = store.data["sections"].get(section_id)
        if section is None:
            raise HTTPException(404, "unknown section")
        payload = await _json_body(request, required=False)
        colour = payload.get("colour") or section["colour"]
        with engine.lock:
            engine.show_test({section_id: colour},
                             float(payload.get("seconds", 5.0)))
        return JSONResponse({"ok": True, "section": section_id, "colour": colour})

    async def test_all(request: Request) -> Response:
        payload = await _json_body(request, required=False)
        colour = payload.get("colour")
        seconds = float(payload.get("seconds", 10.0))
        colours = {
            sid: (colour or store.data["sections"][sid]["colour"])
            for sid in store.data["sections"]
        }
        with engine.lock:
            engine.show_test(colours, seconds)
        return JSONResponse({"ok": True})

    async def test_clear(request: Request) -> Response:
        with engine.lock:
            engine.clear_test()
        return JSONResponse({"ok": True})

    async def blackout(request: Request) -> Response:
        with engine.lock:
            engine.stop_round()
            engine.sender.blackout()
        return JSONResponse({"ok": True})

    async def status(request: Request) -> Response:
        with engine.lock:
            data = engine.status()
        data["late_frames"] = loop.late_frames
        return JSONResponse(data)

    async def preview(request: Request) -> Response:
        with engine.lock:
            if request.query_params.get("fx"):
                engine.keep_preview()
            return JSONResponse(engine.preview())

    async def geometry(request: Request) -> Response:
        with engine.lock:
            return JSONResponse(engine.geo.to_client(store.data))

    # -- idle effects and playlists ---------------------------------------

    async def effect_catalogue(request: Request) -> Response:
        return JSONResponse({"effects": effects.catalogue()})

    def _effect_from(payload: Dict[str, Any]) -> tuple:
        key = str(payload.get("effect") or "")
        if key not in effects.REGISTRY:
            raise HTTPException(400, f"unknown effect '{key}'")
        return key, effects.validate_params(key, payload.get("params") or {})

    async def effect_preview(request: Request) -> Response:
        """Render an effect or a whole playlist for the web preview only."""
        payload = await _json_body(request)
        with engine.lock:
            if "playlist" in payload:
                playlists = _validate_playlists([payload["playlist"]])
                if not playlists:
                    raise HTTPException(400, "bad playlist")
                engine.preview_playlist(playlists[0])
                return JSONResponse({"ok": True, "kind": "playlist"})
            key, params = _effect_from(payload)
            engine.preview_effect(key, params)
        return JSONResponse({"ok": True, "kind": "effect", "params": params})

    async def effect_preview_keepalive(request: Request) -> Response:
        with engine.lock:
            engine.keep_preview()
        return JSONResponse({"ok": True})

    async def effect_preview_stop(request: Request) -> Response:
        with engine.lock:
            engine.stop_preview()
        return JSONResponse({"ok": True})

    async def effect_show(request: Request) -> Response:
        """Put an effect on the house now, in place of the playlist."""
        payload = await _json_body(request)
        key, params = _effect_from(payload)
        with engine.lock:
            engine.show_effect(key, params, float(payload.get("minutes") or 0))
        return JSONResponse({"ok": True})

    async def effect_resume(request: Request) -> Response:
        with engine.lock:
            engine.resume_playlist()
        return JSONResponse({"ok": True})

    async def idle_next(request: Request) -> Response:
        with engine.lock:
            engine.skip_idle()
        return JSONResponse({"ok": True})

    # -- test mode --------------------------------------------------------

    async def testmode_info(request: Request) -> Response:
        return JSONResponse(testmode.describe())

    async def testmode_enter(request: Request) -> Response:
        with engine.lock:
            engine.stop_round()
            engine.testmode.enter()
        return JSONResponse({"ok": True})

    async def testmode_exit(request: Request) -> Response:
        with engine.lock:
            engine.testmode.exit("closed from the web interface")
        return JSONResponse({"ok": True})

    async def testmode_set(request: Request) -> Response:
        payload = await _json_body(request)
        targets = payload.get("targets") or []
        if isinstance(targets, str):
            targets = [targets]
        on = str(payload.get("on", "on"))
        if on not in ("on", "off", "toggle"):
            raise HTTPException(400, "on must be on, off or toggle")
        with engine.lock:
            if engine.state != "idle" and not engine.testmode.active:
                engine.stop_round()
            keys = engine.testmode.set(targets, on, payload.get("colour"),
                                       payload.get("level"), payload.get("pattern"))
        return JSONResponse({"ok": True, "strips": keys})

    async def testmode_restyle(request: Request) -> Response:
        payload = await _json_body(request)
        with engine.lock:
            engine.testmode.restyle(payload.get("colour"), payload.get("level"),
                                    payload.get("pattern"))
        return JSONResponse({"ok": True})

    async def testmode_clear(request: Request) -> Response:
        with engine.lock:
            engine.testmode.clear()
        return JSONResponse({"ok": True})

    async def testmode_compare(request: Request) -> Response:
        payload = await _json_body(request)
        a, b = payload.get("a") or {}, payload.get("b") or {}
        if not a.get("target") or not b.get("target"):
            raise HTTPException(400, "choose both A and B")
        with engine.lock:
            engine.stop_round()
            engine.testmode.compare(a, b, bool(payload.get("keep_others")))
        return JSONResponse({"ok": True})

    async def testmode_sequence(request: Request) -> Response:
        payload = await _json_body(request)
        kind = str(payload.get("kind") or "")
        if kind not in testmode.SEQUENCES:
            raise HTTPException(400, f"unknown sequence '{kind}'")
        with engine.lock:
            engine.stop_round()
            engine.testmode.start_sequence(kind, payload.get("scope"), payload.get("colour"),
                                           payload.get("dwell"), payload.get("speed"))
        return JSONResponse({"ok": True})

    async def testmode_sequence_stop(request: Request) -> Response:
        with engine.lock:
            engine.testmode.stop_sequence()
        return JSONResponse({"ok": True})

    async def testmode_lamp(request: Request) -> Response:
        payload = await _json_body(request, required=False)
        level = payload.get("level")
        with engine.lock:
            engine.testmode.enter()
            engine.testmode.lamp_override = (None if level is None
                                             else max(0.0, min(1.0, float(level))))
        return JSONResponse({"ok": True})

    async def diagnostics(request: Request) -> Response:
        with engine.lock:
            controllers = [dict(c) for c in store.data["controllers"]]
            expected = engine.expected_pixels()
        results = await run_in_threadpool(probe_controllers, controllers, expected)
        return JSONResponse({"controllers": results})

    # -- sounds -----------------------------------------------------------

    async def list_sounds(request: Request) -> Response:
        return JSONResponse({
            "sounds": engine.audio.list_sounds(),
            "audio": engine.audio.describe(),
            "devices": engine.audio.list_devices(),
        })

    async def upload_sound(request: Request) -> Response:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(400, "no file uploaded")
        name = Path(getattr(upload, "filename", "") or "").name
        if not name or Path(name).suffix.lower() not in ALLOWED_SOUND_SUFFIXES:
            raise HTTPException(400, "only .wav, .ogg, .mp3 or .flac files")
        if not SAFE_NAME.match(name):
            raise HTTPException(400, "file name has unsupported characters")
        target = sounds_dir / name
        size = 0
        try:
            with target.open("wb") as handle:
                while True:
                    chunk = await upload.read(1 << 16)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_SOUND_BYTES:
                        raise HTTPException(413, "file too large (25MB limit)")
                    handle.write(chunk)
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        finally:
            await form.close()
        return JSONResponse({"ok": True, "name": name, "bytes": size,
                             "sounds": engine.audio.list_sounds()})

    async def delete_sound(request: Request) -> Response:
        safe = Path(request.path_params["name"]).name
        if not SAFE_NAME.match(safe):
            raise HTTPException(400, "bad name")
        target = sounds_dir / safe
        if not target.is_file():
            raise HTTPException(404, "no such sound")
        target.unlink()
        return JSONResponse({"ok": True, "sounds": engine.audio.list_sounds()})

    async def play_sound(request: Request) -> Response:
        payload = await _json_body(request)
        name = Path(str(payload.get("name") or "")).name
        if not name:
            raise HTTPException(400, "name required")
        ok = engine.audio.play(CH_FINALE, name, float(payload.get("volume", 1.0)))
        return JSONResponse({"ok": ok, "status": engine.audio.status})

    async def sound_file(request: Request) -> Response:
        safe = Path(request.path_params["name"]).name
        target = sounds_dir / safe
        if not SAFE_NAME.match(safe) or not target.is_file():
            raise HTTPException(404, "no such sound")
        return FileResponse(target)

    # -- live preview -----------------------------------------------------

    async def preview_socket(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                interval = 1.0 / max(1, int(store.data["web"]["preview_fps"]))
                with engine.lock:
                    payload = engine.preview()
                await socket.send_text(json.dumps(payload))
                await asyncio.sleep(interval)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return
        except asyncio.CancelledError:  # pragma: no cover
            with contextlib.suppress(Exception):
                await socket.close()
            raise

    # -- static UI --------------------------------------------------------

    async def index(request: Request) -> Response:
        return FileResponse(WEB_DIR / "index.html")

    async def http_error(request: Request, exc: HTTPException) -> Response:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        engine.start()
        loop.start()
        try:
            yield
        finally:
            loop.stop()
            engine.shutdown()

    routes = [
        Route("/", index),
        Route("/api/config", get_config, methods=["GET"]),
        Route("/api/config", put_config, methods=["PUT"]),
        Route("/api/config", patch_config, methods=["PATCH"]),
        Route("/api/config/reload", reload_config, methods=["POST"]),
        Route("/api/trigger", trigger, methods=["POST"]),
        Route("/api/stop", stop_round, methods=["POST"]),
        Route("/api/test", test, methods=["POST"]),
        Route("/api/test/section/{section_id}", test_section, methods=["POST"]),
        Route("/api/test/all", test_all, methods=["POST"]),
        Route("/api/test/clear", test_clear, methods=["POST"]),
        Route("/api/blackout", blackout, methods=["POST"]),
        Route("/api/status", status, methods=["GET"]),
        Route("/api/preview", preview, methods=["GET"]),
        Route("/api/geometry", geometry, methods=["GET"]),
        Route("/api/effects", effect_catalogue, methods=["GET"]),
        Route("/api/effects/preview", effect_preview, methods=["POST"]),
        Route("/api/effects/preview/keepalive", effect_preview_keepalive, methods=["POST"]),
        Route("/api/effects/preview/stop", effect_preview_stop, methods=["POST"]),
        Route("/api/effects/show", effect_show, methods=["POST"]),
        Route("/api/effects/resume", effect_resume, methods=["POST"]),
        Route("/api/idle/next", idle_next, methods=["POST"]),
        Route("/api/testmode", testmode_info, methods=["GET"]),
        Route("/api/testmode/enter", testmode_enter, methods=["POST"]),
        Route("/api/testmode/exit", testmode_exit, methods=["POST"]),
        Route("/api/testmode/set", testmode_set, methods=["POST"]),
        Route("/api/testmode/restyle", testmode_restyle, methods=["POST"]),
        Route("/api/testmode/clear", testmode_clear, methods=["POST"]),
        Route("/api/testmode/compare", testmode_compare, methods=["POST"]),
        Route("/api/testmode/sequence", testmode_sequence, methods=["POST"]),
        Route("/api/testmode/sequence/stop", testmode_sequence_stop, methods=["POST"]),
        Route("/api/testmode/lamp", testmode_lamp, methods=["POST"]),
        Route("/api/diagnostics/controllers", diagnostics, methods=["GET"]),
        Route("/api/sounds", list_sounds, methods=["GET"]),
        Route("/api/sounds", upload_sound, methods=["POST"]),
        Route("/api/sounds/play", play_sound, methods=["POST"]),
        Route("/api/sounds/file/{name}", sound_file, methods=["GET"]),
        Route("/api/sounds/{name}", delete_sound, methods=["DELETE"]),
        WebSocketRoute("/ws/preview", preview_socket),
        Mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static"),
    ]

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        exception_handlers={HTTPException: http_error},
    )
    app.state.engine = engine
    app.state.store = store
    app.state.loop = loop
    return app
