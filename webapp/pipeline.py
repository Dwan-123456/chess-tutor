"""PGN -> engine analysis + insights + commentary, as a stream of events.

Engine analysis runs in a worker thread and hands each finished move to the
event loop; commentary for that move starts immediately (bounded concurrency),
so the first comments arrive while Stockfish is still working through the game.

Events (dicts, sent to the browser as NDJSON):
  {"type": "meta",    "headers": {...}, "plies": N, "cached": bool}
  {"type": "move",    "ply": .., "san": .., "fen_after": .., "eval_after_cp": .., ... , "insights": {...}}
  {"type": "comment", "ply": .., "text": "..."}            or {"type": "comment", "ply": .., "error": "..."}
  {"type": "done",    "cost_usd": .., "cached": bool}
  {"type": "error",   "message": "..."}
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading

import chess
import chess.pgn

from chess_annotator import app_prompts, features, positional, prompts

from .config import PIPELINE_VERSION, Settings
from .history import History, rules_fingerprint, engine_fingerprint

MOVE_FIELDS = ("ply", "move_number", "side", "san", "uci", "fen_before", "fen_after", "phase",
               "eval_before_cp", "eval_after_cp", "mate_before", "mate_after", "cp_loss",
               "quality_label", "nag_symbol", "is_best_move", "best_move_san",
               "principal_variation_san", "captured_piece", "material_after", "tags", "is_checkmate", "winner", "result")


class PGNError(ValueError):
    pass


class _Cancelled(Exception):
    pass


def parse_single_game(pgn_text: str, settings: Settings) -> chess.pgn.Game:
    if len(pgn_text) > settings.max_pgn_chars:
        raise PGNError(f"PGN is too long (max {settings.max_pgn_chars} characters).")
    stream = io.StringIO(pgn_text)
    game = chess.pgn.read_game(stream)
    if game is None:
        raise PGNError("Could not find a game in that PGN.")
    if game.errors:
        raise PGNError(f"The PGN has an illegal or unreadable move: {game.errors[0]}")
    variant = game.headers.get("Variant", "Standard").lower()
    if variant not in ("standard", "chess", "from position"):
        raise PGNError(f"Only standard chess is supported (this game is '{game.headers.get('Variant')}').")
    plies = sum(1 for _ in game.mainline_moves())
    if plies == 0:
        raise PGNError("The game has no moves.")
    if plies > settings.max_plies:
        raise PGNError(f"The game is too long ({plies} half-moves; max {settings.max_plies}).")
    if chess.pgn.read_game(stream) is not None:
        raise PGNError("Please paste a single game (this PGN contains several).")
    return game


def analysis_signature(game: chess.pgn.Game, settings: Settings, factcheck: bool) -> dict:
    return {
        "fen": game.board().fen(),
        "moves": [m.uci() for m in game.mainline_moves()],
        "v": PIPELINE_VERSION, "model": settings.model, "effort": settings.reasoning_effort,
        "factcheck": factcheck, "fc_model": settings.factcheck_model if factcheck else None,
        "depth": settings.engine_depth,
        "threads": settings.engine_threads, "hash_mb": settings.engine_hash_mb,
        "backend": settings.commentator_backend,
        "rules": rules_fingerprint(), "engine": engine_fingerprint(settings.stockfish_path),
        "endpoint": hashlib.sha256(settings.deepseek_base_url.encode()).hexdigest(),
        "max_plies": settings.max_plies,
    }


def cache_key(game: chess.pgn.Game, settings: Settings, factcheck: bool) -> str:
    blob = json.dumps(analysis_signature(game, settings, factcheck), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _move_event(m: dict, insights: dict) -> dict:
    ev = {"type": "move", **{k: m.get(k) for k in MOVE_FIELDS}, "insights": insights}
    return ev


class GamePipeline:
    def __init__(self, settings: Settings, llm, store, llm_semaphore: asyncio.Semaphore):
        self.s = settings
        self.llm = llm
        self.store = store
        self.llm_sem = llm_semaphore

    # ---------------------------------------------------------------- engine (thread)
    def _engine_run(self, game, on_move):
        analyser = features._make_analyser("local", self.s.stockfish_path, self.s.engine_depth, 1)
        try:
            # Configure Threads before allocating the transposition table.
            analyser.engine.configure({"Threads": self.s.engine_threads})
            analyser.engine.configure({"Hash": self.s.engine_hash_mb})
            features._analyze_parsed_game(game, analyser, self.s.max_plies, on_move=on_move)
        finally:
            analyser.close()

    # ---------------------------------------------------------------- commentary
    async def _comment(self, m: dict, history: list, insights: dict, factcheck: bool) -> dict:
        ply = m["ply"]
        if self.s.commentator_backend == "disabled":
            return {"type": "comment", "ply": ply, "error": "尚未接入解说模型；当前仅提供 Stockfish 分析。"}
        if self.store.spent_today() >= self.s.daily_budget_usd:
            return {"type": "comment", "ply": ply, "error": "Daily commentary budget reached; try again tomorrow."}
        sys_p, user_p = app_prompts.build_app_move_prompt(m, history, insights)
        if getattr(self.llm, "is_student", False):
            # Same facts, compact student prompt it was fine-tuned on; the fact-check pass is a teacher-only step.
            sys_p, user_p = app_prompts.build_student_prompt(app_prompts.facts_from_teacher_prompt(user_p), history)
            factcheck = False
        cost = 0.0
        generation = None
        billing = []
        try:
            async with self.llm_sem:
                text, usage = await self.llm.chat(self.s.model, sys_p, user_p)
                generation = usage.get("generation")
                if usage.get("billing"):
                    billing.append(usage["billing"])
                cost += self.s.cost(self.s.model, usage)
                if factcheck:
                    fsys, fuser = app_prompts.build_factcheck_prompt(user_p, text)
                    try:
                        checked, usage2 = await self.llm.chat(self.s.factcheck_model, fsys, fuser)
                        cost += self.s.cost(self.s.factcheck_model, usage2)
                        if usage2.get("billing"):
                            billing.append(usage2["billing"])
                        if (checked and checked.strip() != "NO_CHANGES" and len(checked) >= 0.6 * len(text)
                                and checked.rstrip().endswith((".", "!", "?", '"', ")"))):
                            text = checked
                    except Exception:
                        pass  # keep the unchecked draft rather than failing the move
        except Exception as e:  # noqa: BLE001
            self.store.add_spend(cost)
            return {"type": "comment", "ply": ply, "error": f"Commentary unavailable ({e})."}
        self.store.add_spend(cost)
        return {"type": "comment", "ply": ply, "text": text, "generation": generation, "billing": billing, "_cost": cost}

    # ---------------------------------------------------------------- orchestration
    async def run(self, game: chess.pgn.Game, factcheck: bool, cancel: threading.Event):
        headers = dict(game.headers)
        plies = sum(1 for _ in game.mainline_moves())
        key = cache_key(game, self.s, factcheck)
        signature = analysis_signature(game, self.s, factcheck)

        history_store = History()
        saved = history_store.read(key, plies) if self.s.commentator_backend == 'deepseek' else None
        cached = saved['events'] if saved else (None if self.s.commentator_backend == 'deepseek' else self.store.get_cached(key))
        if cached is not None:
            yield {"type": "meta", "headers": headers, "plies": plies, "cached": True}
            for ev in cached:
                # Preserve original provenance and text, but a replay incurs no new token cost.
                ev = dict(ev)
                if ev.get('type') == 'comment':
                    ev['billing'] = []
                yield ev
            yield {"type": "done", "cost_usd": 0.0, "cached": True}
            return

        yield {"type": "meta", "headers": headers, "plies": plies, "cached": False}

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def on_move(m):
            if cancel.is_set():
                raise _Cancelled()
            loop.call_soon_threadsafe(q.put_nowait, ("move", m))

        engine_future = asyncio.ensure_future(asyncio.to_thread(self._engine_run, game, on_move))
        engine_future.add_done_callback(lambda f: q.put_nowait(("engine_done", f)))

        moves, events, tasks = [], [], set()
        engine_done, comments_pending, total_cost, failed = False, 0, 0.0, False
        try:
            while not engine_done or comments_pending:
                kind, payload = await q.get()
                if kind == "move":
                    m = payload
                    try:
                        insights = positional.insights_for_move_record(m)
                    except Exception:  # noqa: BLE001 -- a detector bug must not kill the game
                        insights = {"move_ideas": [], "position_context": [], "best_move_ideas": [],
                                    "opponent_reply_ideas": []}
                    history = moves[-prompts.HISTORY_LEN:]
                    moves.append(m)
                    ev = _move_event(m, insights)
                    events.append(ev)
                    yield ev
                    comments_pending += 1
                    t = asyncio.ensure_future(self._comment(m, history, insights, factcheck))
                    tasks.add(t)
                    t.add_done_callback(lambda f: q.put_nowait(("comment", f)))
                elif kind == "comment":
                    comments_pending -= 1
                    tasks.discard(payload)
                    ev = payload.result()
                    total_cost += ev.pop("_cost", 0.0)
                    if "error" in ev:
                        failed = True
                    events.append(ev)
                    yield ev
                elif kind == "engine_done":
                    engine_done = True
                    exc = payload.exception()
                    if exc is not None:
                        failed = True
                        msg = "Analysis was cancelled." if isinstance(exc, _Cancelled) else f"Engine analysis failed: {exc}"
                        yield {"type": "error", "message": msg}
                        for t in tasks:
                            t.cancel()
                        return
            if not failed and self.s.commentator_backend != "deepseek":
                self.store.put_cached(key, events)
            if not failed and self.s.commentator_backend == 'deepseek' and signature == analysis_signature(game, self.s, factcheck):
                history_store.write(key, game, events, signature)
            records = [b for event in events for b in event.get("billing", [])]
            summary = None
            if self.s.commentator_backend == "deepseek":
                summary = {"currency": "CNY", "cost_cny": sum(b['cost_cny'] for b in records),
                           "calls": len(records), "peak_calls": sum(b['period'] == 'peak' for b in records),
                           "cache_reported": all(b['cache_reported'] for b in records)}
                for name in ('prompt_tokens', 'cache_hit_tokens', 'cache_miss_tokens', 'completion_tokens'):
                    summary[name] = sum(b[name] for b in records)
            yield {"type": "done", "cost_usd": round(total_cost, 5), "billing": summary, "cached": False}
        finally:
            cancel.set()
            for t in tasks:
                t.cancel()
