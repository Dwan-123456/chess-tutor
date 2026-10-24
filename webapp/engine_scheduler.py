"""One reusable Stockfish process, owned by one worker, with interruptible searches."""
from __future__ import annotations

import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import chess.engine

from chess_annotator import features
from .cpu_scheduling import selected_policy
from .tablebase import OnlineTablebase

log = logging.getLogger(__name__)


class AnalysisCancelled(Exception):
    pass


class EngineJob:
    def __init__(self, cancel):
        self.cancel = cancel
        self.future = None
        self._lock = threading.Lock()
        self._search = None

    def check(self):
        if self.cancel.is_set():
            raise AnalysisCancelled()

    def attach(self, search):
        with self._lock:
            self._search = search
            if self.cancel.is_set():
                search.stop()

    def detach(self):
        with self._lock:
            self._search = None

    def stop(self):
        self.cancel.set()
        with self._lock:
            if self._search is not None:
                try:
                    self._search.stop()
                except chess.engine.EngineTerminatedError:
                    pass
        if self.future is not None:
            self.future.cancel()  # A queued job must never start searching.


class _Analyser(features._PositionAnalyser):
    def __init__(self, engine, depth, job):
        self.engine, self.depth, self.job = engine, depth, job
        # python-chess sends ucinewgame once per job, retaining Hash between its plies.
        self.game = object()

    def analyse(self, board):
        self.job.check()
        with self.engine.analysis(board, chess.engine.Limit(depth=self.depth),
                                  game=self.game, multipv=1,
                                  info=chess.engine.INFO_SCORE | chess.engine.INFO_PV) as search:
            self.job.attach(search)
            try:
                search.wait()
                self.job.check()  # Never publish an interrupted, shallower result.
                info = search.info
            finally:
                self.job.detach()
        score = info['score'].white()
        pv = info.get('pv', [])
        return {'cp': None if score.is_mate() else score.score(),
                'mate': score.mate() if score.is_mate() else None,
                'best_move': pv[0] if pv else None, 'pv': pv[:4]}


class EngineScheduler:
    def __init__(self):
        # All lifecycle, configuration and searches are serialized, even if an HTTP
        # coroutine is cancelled before the native search has acknowledged stop.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='stockfish-owner')
        self._lock = threading.Lock()
        self._jobs = set()
        self._closed = False
        self._engine = None
        self._path = None
        self.tablebase = OnlineTablebase()

    def submit(self, game, settings, on_move, cancel):
        job = EngineJob(cancel)
        with self._lock:
            if self._closed:
                raise RuntimeError('Engine scheduler is closed')
            self._jobs.add(job)
            job.future = self._pool.submit(self._run, game, settings, on_move, job)
        job.future.add_done_callback(lambda _: self._forget(job))
        return job

    def _forget(self, job):
        with self._lock:
            self._jobs.discard(job)

    def _discard_engine(self):
        if self._engine is not None:
            self._engine.close()
            self._engine = None
            self._path = None

    def _prepare(self, settings):
        if self._engine is not None and (self._path != settings.stockfish_path or
                                        self._engine.returncode.done()):
            self._discard_engine()
        if self._engine is None:
            self._engine = chess.engine.SimpleEngine.popen_uci(
                settings.stockfish_path, timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self._path = settings.stockfish_path
        engine = self._engine
        # Do not rebuild the native thread pool / Hash when settings are unchanged.
        policy = selected_policy(settings)
        rebuilding = (engine.protocol.config.get('Threads') != settings.engine_threads or
                      ('NumaPolicy' in engine.options and engine.protocol.config.get('NumaPolicy') != policy))
        if rebuilding and engine.protocol.config.get('Hash', 16) > 16:
            # Stockfish reallocates Hash when rebuilding workers. Shrink first so
            # changing CPU policy + Threads does not repeatedly allocate gigabytes.
            engine.configure({'Hash': 16})
        if 'NumaPolicy' in engine.options:
            engine.configure({'NumaPolicy': policy})
        engine.configure({'Threads': settings.engine_threads})
        engine.configure({'Hash': settings.engine_hash_mb})
        engine.ping()
        log.info('Stockfish pid=%s threads=%s hash_mb=%s depth=%s cpu_policy=%s',
                 engine.transport.get_pid(), settings.engine_threads,
                 settings.engine_hash_mb, settings.engine_depth, policy)
        return engine

    def _run(self, game, settings, on_move, job):
        try:
            job.check()
            engine = self._prepare(settings)
            job.check()
            tablebase_board = game.board()

            def publish(move):
                job.check()
                before_board = tablebase_board.copy()
                tablebase_board.push_uci(move['uci'])
                if settings.tablebase_enabled:
                    self.tablebase.enrich(move, job.check, before_board, tablebase_board)
                on_move(move)

            return features._analyze_parsed_game(
                game, _Analyser(engine, settings.engine_depth, job),
                settings.max_plies, on_move=publish)
        except AnalysisCancelled:
            raise
        except BaseException:
            # A failed engine must not poison subsequent games.
            self._discard_engine()
            raise

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            jobs = list(self._jobs)
        for job in jobs:
            job.stop()
        # Queued jobs finish/cancel before the final engine cleanup runs.
        self._pool.submit(self._discard_engine)
        self._pool.shutdown(wait=True)
        self.tablebase.close()
