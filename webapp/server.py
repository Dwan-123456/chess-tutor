"""FastAPI server for the chess commentary app.

    uvicorn webapp.server:app --reload          (from the chess-annotator folder)

Endpoints:
  POST /api/annotate   body {"pgn": "...", "factcheck": true}  -> NDJSON event stream
  GET  /api/health     engine + budget status
  GET  /               the web page (webapp/static/index.html)
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .llm import DeepSeekClient, GroqClient, LlamaServerClient, MockClient, StudentClient
from .pipeline import GamePipeline, PGNError, parse_single_game, cache_key
from .store import Store
from . import engine_settings
from .engine_scheduler import EngineScheduler
from .page_lifetime import PageLifetime
from . import tutor_settings
from .live import router as live_router
from dataclasses import replace
from typing import Literal

STATIC = Path(__file__).parent / "static"


class AnnotateRequest(BaseModel):
    pgn: str = Field(..., min_length=1)
    factcheck: bool | None = None
    engine: engine_settings.EngineSettings | None = None
    deepseek_depth: Literal['instant', 'high', 'max'] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = Settings()
    app.state.settings = s
    app.state.engine_defaults = engine_settings.load(s)
    app.state.engine_scheduler = EngineScheduler()
    app.state.analysis_locks = {}
    app.state.store = Store(s.db_path)
    if s.commentator_backend == "disabled":
        app.state.llm = None
        s.model = "none"
        s.factcheck_default = False
    elif s.commentator_backend == "deepseek":
        app.state.llm = DeepSeekClient(s.deepseek_api_key, s.deepseek_base_url)
    elif s.commentator_backend == "mock":
        app.state.llm = MockClient()
    elif s.commentator_backend == "student":
        print(f"Loading student: {s.student_base_model} + {s.student_adapter} (first run downloads the base model)...")
        app.state.llm = StudentClient(s.student_base_model, s.student_adapter, s.student_max_tokens)
        print(f"Student ready on {app.state.llm.device}.")
    elif s.commentator_backend == "student_gguf":
        app.state.llm = LlamaServerClient(s.student_gguf_url, s.student_gguf, s.llama_server, s.llama_port,
                                          s.student_threads, s.student_max_tokens)
        s.model = f"student-v3/{app.state.llm.name}"
        s.factcheck_default = False
        print(f"Student ready: {app.state.llm.device} at {app.state.llm.url}.")
        s.model = f"student-v3/{app.state.llm.name}"  # shown in /api/health and part of the cache key
        s.factcheck_default = False
    else:
        app.state.llm = GroqClient(s.groq_api_key, s.reasoning_effort)
    app.state.llm_sem = asyncio.Semaphore(s.llm_concurrency)
    app.state.game_sem = asyncio.Semaphore(s.max_concurrent_games)
    shutdown = getattr(app.state, 'desktop_shutdown', None)
    app.state.page_lifetime = PageLifetime(shutdown) if shutdown else None
    try:
        yield
    finally:
        if app.state.page_lifetime:
            await app.state.page_lifetime.close()
        await asyncio.to_thread(app.state.engine_scheduler.close)
        if app.state.llm is not None:
            await app.state.llm.aclose()
        app.state.store.close()


app = FastAPI(title="Chess Tutor", version="1.0.2", lifespan=lifespan)
app.include_router(live_router)


@app.get('/api/settings/tutor/{mode}')
async def get_tutor_settings(mode: Literal['live', 'review']):
    return tutor_settings.load(mode).model_dump()


@app.put('/api/settings/tutor/{mode}')
async def put_tutor_settings(mode: Literal['live', 'review'], value: tutor_settings.TutorSettings):
    tutor_settings.save(mode, value)
    return value.model_dump()


@app.websocket("/api/page-lifetime")
async def page_lifetime(socket: WebSocket):
    # Only the app's own origin may control the local desktop process.
    origin = urlsplit(socket.headers.get('origin', ''))
    if origin.scheme not in ('http', 'https') or origin.netloc != socket.headers.get('host'):
        await socket.close(code=1008)
        return
    await socket.accept()
    lifetime = socket.app.state.page_lifetime
    await socket.send_json({'enabled': lifetime is not None})
    if lifetime is None:
        await socket.close()
        return
    page = lifetime.connect()
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        lifetime.disconnect(page)


@app.get("/api/health")
async def health(request: Request):
    s, store = request.app.state.settings, request.app.state.store
    return {"ok": True, "application": "chess-annotator-desktop", "model": s.model, "backend": s.commentator_backend,
            "factcheck_default": s.factcheck_default,
            "tablebase": {"enabled": s.tablebase_enabled, "provider": "Lichess", "max_pieces": 7},
            "spent_today_usd": round(store.spent_today(), 4), "daily_budget_usd": s.daily_budget_usd}


@app.get("/api/settings/engine")
async def get_engine_settings(request: Request):
    return engine_settings.response(request.app.state.engine_defaults)


@app.put("/api/settings/engine")
async def put_engine_settings(value: engine_settings.EngineSettings, request: Request):
    engine_settings.save(value)
    request.app.state.engine_defaults = value
    return engine_settings.response(value)


@app.post("/api/annotate")
async def annotate(req: AnnotateRequest, request: Request):
    st = request.app.state
    # Freeze the parameters before queueing, never mutate a running game's settings.
    s: Settings = (req.engine or st.engine_defaults).apply(st.settings)
    s = replace(s, deepseek_depth=req.deepseek_depth or tutor_settings.load('review').deepseek_depth)
    try:
        game = parse_single_game(req.pgn, s)
    except PGNError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        await asyncio.wait_for(st.game_sem.acquire(), timeout=s.queue_wait_s)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="The server is busy analysing other games. Please try again shortly.")

    factcheck = s.factcheck_default if req.factcheck is None else req.factcheck
    if s.commentator_backend.startswith("student"):
        factcheck = False
    pipeline = GamePipeline(s, st.llm, st.store, st.llm_sem, st.engine_scheduler)
    cancel = threading.Event()

    async def stream():
        events = pipeline.run(game, factcheck, cancel)
        key = cache_key(game, s, factcheck)
        lock = st.analysis_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                async for ev in events:
                    if await request.is_disconnected():
                        cancel.set()
                        break
                    yield json.dumps(ev) + "\n"
        except Exception as e:  # noqa: BLE001
            yield json.dumps({"type": "error", "message": f"Unexpected server error: {e}"}) + "\n"
        finally:
            cancel.set()
            try:
                await events.aclose()
            finally:
                st.game_sem.release()

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    page = STATIC / "index.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"message": "Frontend not built yet. POST a PGN to /api/annotate."})
