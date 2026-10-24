import io
import tempfile
import threading
import unittest
from concurrent.futures import Future, CancelledError
from pathlib import Path
from unittest.mock import patch

import chess
import chess.engine
import chess.pgn

from webapp.config import Settings
from webapp.cpu_scheduling import physical_first_policy, profile_key, selected_policy
from webapp.engine_scheduler import EngineScheduler, AnalysisCancelled


def game():
    return chess.pgn.read_game(io.StringIO('1. e4 e5 *'))


class FakeSearch:
    def __init__(self, engine, board):
        self.engine = engine
        self.release = threading.Event()
        if not engine.block:
            self.release.set()
        move = next(iter(board.legal_moves))
        self.info = {'score': chess.engine.PovScore(chess.engine.Cp(30), chess.WHITE), 'pv': [move]}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()

    def stop(self):
        self.engine.stops += 1
        self.release.set()

    def wait(self):
        self.engine.started.set()
        if not self.release.wait(5):
            raise RuntimeError('Test search did not stop')
        if self.engine.fail:
            raise chess.engine.EngineTerminatedError('test crash')


class FakeEngine:
    def __init__(self, block=False, fail=False):
        self.block, self.fail = block, fail
        self.started = threading.Event()
        self.returncode = Future()
        self.options = {'NumaPolicy': None}
        self.transport = type('Transport', (), {'get_pid': lambda _: 123})()
        self.config, self.games = {}, []
        self.protocol = type('Protocol', (), {'config': self.config})()
        self.stops = 0
        self.closed = False

    def configure(self, values):
        self.config.update(values)

    def ping(self):
        pass

    def analysis(self, board, limit, **options):
        self.games.append(options['game'])
        return FakeSearch(self, board)

    def close(self):
        self.closed = True


class SchedulerTests(unittest.TestCase):
    def test_cancel_reuse_serialization_and_settings(self):
        engine = FakeEngine(block=True)
        settings = Settings(engine_threads=10, engine_hash_mb=8192, engine_depth=21)
        scheduler = EngineScheduler()
        try:
            with patch('webapp.engine_scheduler.chess.engine.SimpleEngine.popen_uci', return_value=engine) as spawn:
                with patch('webapp.engine_scheduler.selected_policy', return_value='auto'):
                    first_moves, second_moves = [], []
                    first = scheduler.submit(game(), settings, first_moves.append, threading.Event())
                    self.assertTrue(engine.started.wait(3))
                    second = scheduler.submit(game(), settings, second_moves.append, threading.Event())
                    self.assertFalse(second.future.done())
                    # New parameters are applied by the owner only after stop is acknowledged.
                    settings2 = Settings(engine_threads=2, engine_hash_mb=256, engine_depth=12)
                    third = scheduler.submit(game(), settings2, lambda _: None, threading.Event())
                    third.stop()
                    self.assertTrue(third.future.cancelled())
                    engine.block = False
                    first.stop()
                    with self.assertRaises(AnalysisCancelled):
                        first.future.result(timeout=3)
                    second.future.result(timeout=3)
                    self.assertEqual(first_moves, [])
                    self.assertEqual(len(second_moves), 2)
                    self.assertEqual(spawn.call_count, 1)
                    self.assertEqual(engine.config['Threads'], 10)
                    self.assertEqual(engine.config['Hash'], 8192)
                    tokens = engine.games[:]
                    self.assertIsNot(tokens[0], tokens[1])
                    self.assertTrue(all(token is tokens[1] for token in tokens[1:]))
                    scheduler.submit(game(), settings2, lambda _: None, threading.Event()).future.result(3)
                    self.assertEqual(engine.config['Threads'], 2)
                    self.assertEqual(engine.config['Hash'], 256)
        finally:
            scheduler.close()
        self.assertTrue(engine.closed)

    def test_failed_engine_replaced(self):
        broken, good = FakeEngine(fail=True), FakeEngine()
        scheduler = EngineScheduler()
        try:
            with patch('webapp.engine_scheduler.chess.engine.SimpleEngine.popen_uci', side_effect=[broken, good]):
                with patch('webapp.engine_scheduler.selected_policy', return_value='auto'):
                    failed = scheduler.submit(game(), Settings(), lambda _: None, threading.Event())
                    with self.assertRaises(chess.engine.EngineTerminatedError):
                        failed.future.result(3)
                    scheduler.submit(game(), Settings(), lambda _: None, threading.Event()).future.result(3)
                    self.assertTrue(broken.closed)
        finally:
            scheduler.close()

    def test_cancel_during_search_start(self):
        engine = FakeEngine(block=True)
        starting, allow_attach = threading.Event(), threading.Event()
        original = engine.analysis

        def slow_start(*args, **kwargs):
            search = original(*args, **kwargs)
            starting.set()
            self.assertTrue(allow_attach.wait(3))
            return search

        engine.analysis = slow_start
        scheduler = EngineScheduler()
        try:
            with patch('webapp.engine_scheduler.chess.engine.SimpleEngine.popen_uci', return_value=engine):
                with patch('webapp.engine_scheduler.selected_policy', return_value='auto'):
                    job = scheduler.submit(game(), Settings(), lambda _: self.fail('Partial result published'), threading.Event())
                    self.assertTrue(starting.wait(3))
                    job.stop()
                    allow_attach.set()
                    with self.assertRaises(AnalysisCancelled):
                        job.future.result(3)
                    self.assertGreater(engine.stops, 0)
        finally:
            allow_attach.set()
            scheduler.close()


class CpuPolicyTests(unittest.TestCase):
    def test_physical_before_smt_and_profile_validation(self):
        topology = [{'cpu': 0, 'core': 0, 'efficiency': 1},
                    {'cpu': 1, 'core': 0, 'efficiency': 1},
                    {'cpu': 2, 'core': 1, 'efficiency': 0}]
        self.assertEqual(physical_first_policy(topology, 2), '0,2')
        self.assertEqual(physical_first_policy(topology, 3), 'auto')
        self.assertEqual(physical_first_policy([], 2), 'auto')
        self.assertEqual(physical_first_policy(topology[:2], 1), 'auto')
        settings = Settings(engine_threads=2, engine_hash_mb=256)
        import json
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / 'profile.json'
            with patch('webapp.cpu_scheduling.PROFILE_FILE', profile), patch('webapp.cpu_scheduling.cpu_topology', return_value=topology):
                self.assertEqual(selected_policy(settings), 'auto')
                profile.write_text(json.dumps({'profiles': {profile_key(settings, topology): {'policy': '0,2', 'speedup': 1.1}}}))
                self.assertEqual(selected_policy(settings), '0,2')
                settings.engine_hash_mb = 512
                self.assertEqual(selected_policy(settings), 'auto')
                profile.write_text('[]')
                self.assertEqual(selected_policy(settings), 'auto')


if __name__ == '__main__':
    unittest.main()
