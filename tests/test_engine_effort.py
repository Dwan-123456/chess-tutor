import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

import chess
import chess.engine

from webapp.engine_effort import policy_for, StabilityTracker, LEVELS
from webapp.engine_scheduler import _Analyser, EngineJob, AnalysisCancelled
from webapp.config import Settings
from webapp.engine_settings import EngineSettings
from webapp.pipeline import cache_key
import chess.pgn
import io


def observation(depth, cp=20, move="e2e4", nodes=1000000):
    return {'depth': depth, 'nodes': nodes, 'pv': [chess.Move.from_uci(move)],
            'score': chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


class EffortTests(unittest.TestCase):
    def test_mapping_monotonic_and_anchors(self):
        policies = [policy_for(i) for i in LEVELS]
        for a, b in zip(policies, policies[1:]):
            self.assertLessEqual(a.max_time_s, b.max_time_s)
            self.assertLessEqual(a.min_depth, b.min_depth)
            self.assertLessEqual(a.stable_iterations, b.stable_iterations)
            self.assertGreaterEqual(a.score_tolerance_cp, b.score_tolerance_cp)
        self.assertEqual(policy_for('low').max_time_s, 1)
        self.assertEqual(policy_for('low').min_nodes, 62500)
        self.assertEqual(policy_for('low').min_depth, 10)
        self.assertEqual(policy_for('low').stable_iterations, 3)
        self.assertEqual(policy_for('low').score_tolerance_cp, 25)
        self.assertEqual(policy_for('mid').max_time_s, 5)
        self.assertEqual(policy_for('mid').min_nodes, 250000)
        self.assertEqual(policy_for('high').min_depth, 13)
        self.assertEqual(policy_for('high').stable_iterations, 4)
        self.assertEqual(policy_for('high').score_tolerance_cp, 15)
        self.assertEqual(policy_for('high').min_nodes, 630000)
        for level in ('xhigh', 'max', 'ultra'):
            self.assertEqual(policy_for(level).min_nodes, 1000000)
            self.assertEqual(policy_for(level).min_depth, 14)
        self.assertEqual(policy_for('high').max_time_s, 8)
        self.assertEqual(policy_for('max').max_time_s, 30)
        self.assertEqual(policy_for('ultra').max_time_s, 60)
        for invalid in (-1, 101, True, 2.5, 'unknown'):
            with self.assertRaises(ValueError): policy_for(invalid)

    def test_only_distinct_exact_depths_stop(self):
        tracker = StabilityTracker(replace(policy_for('mid'), min_depth=8, stable_iterations=2))
        self.assertFalse(tracker.update(observation(8)))
        self.assertFalse(tracker.update(observation(8)))
        self.assertFalse(tracker.update({**observation(9), 'lowerbound': True}))
        self.assertFalse(tracker.update({**observation(9), 'pv': []}))
        self.assertTrue(tracker.update(observation(9)))

    def test_instability_budget_floor_and_mate(self):
        tracker = StabilityTracker(replace(policy_for('mid'), min_depth=8, stable_iterations=2))
        self.assertFalse(tracker.update(observation(8)))
        self.assertFalse(tracker.update(observation(9, move='d2d4')))
        self.assertFalse(tracker.update(observation(10, cp=200, move='d2d4')))
        self.assertFalse(tracker.update(observation(11, cp=200, move='d2d4', nodes=1)))
        self.assertTrue(tracker.update(observation(12, cp=200, move='d2d4')))
        tracker = StabilityTracker(replace(policy_for('mid'), min_depth=8, stable_iterations=2))
        mate = chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE)
        self.assertFalse(tracker.update({**observation(8), 'score': mate}))
        self.assertTrue(tracker.update({**observation(9), 'score': mate}))

    def test_policy_snapshot_and_cache_identity(self):
        value = EngineSettings(threads=1, hash_mb=256, depth=21, mode='adaptive', effort='max')
        settings = value.apply(Settings())
        self.assertEqual((settings.engine_mode, settings.engine_effort), ('adaptive', 'max'))
        game = chess.pgn.read_game(io.StringIO('1. e4 *'))
        with patch('webapp.pipeline.rules_fingerprint', return_value='rules'), patch('webapp.pipeline.engine_fingerprint', return_value='engine'):
            key = cache_key(game, settings, False)
            self.assertNotEqual(key, cache_key(game, replace(settings, engine_effort='ultra'), False))
            self.assertNotEqual(key, cache_key(game, replace(settings, engine_mode='manual'), False))
            self.assertEqual(key, cache_key(game, replace(settings, engine_depth=30), False))

    def test_adaptive_stream_and_cancellation(self):
        class Search:
            def __init__(self, updates): self.updates, self.info, self.stopped = updates, {}, False
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self):
                for update in self.updates:
                    self.info = update
                    yield update
            def stop(self): self.stopped = True
            def wait(self):
                if self.stopped:
                    self.info = {**observation(99, cp=900), "lowerbound": True}
        class Engine:
            def analysis(self, board, limit, **kwargs): self.limit = limit; return self.search
        engine = Engine()
        engine.search = Search([observation(10), observation(11), observation(12), observation(13, cp=500)])
        job = EngineJob(threading.Event())
        analyser = _Analyser(engine, 40, job, Settings(engine_mode='adaptive', engine_effort='mid'))
        result = analyser.analyse(chess.Board())
        self.assertEqual(result['search']['stop_reason'], 'stable')
        self.assertEqual(result['search']['depth'], 12)
        self.assertEqual(result['cp'], 20)
        self.assertFalse(result['search']['score_bound'])
        self.assertTrue(engine.search.stopped)
        self.assertIsNone(engine.limit.depth)
        self.assertIsNone(engine.limit.nodes)
        self.assertEqual(engine.limit.time, 5.0)
        engine.search = Search([observation(8)])
        self.assertEqual(analyser.analyse(chess.Board())['search']['stop_reason'], 'budget')
        job.stop()
        with self.assertRaises(AnalysisCancelled): analyser.analyse(chess.Board())


class OfficialTimeTests(unittest.TestCase):
    def test_limits_and_native_stop_only(self):
        class Search:
            info = observation(20, nodes=10000000)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self):
                for depth in range(14, 25):
                    self.info = observation(depth, nodes=10000000)
                    yield self.info
            def wait(self): pass
            def stop(self): raise AssertionError('External early stop must not run')
        class Engine:
            def analysis(self, board, limit, **kwargs):
                self.limit = limit
                return Search()
        for seconds in (0.1, 5.0, 60.0):
            engine = Engine()
            settings = Settings(engine_mode='stockfish', engine_time_limit_s=seconds)
            with patch('webapp.engine_scheduler.StabilityTracker', side_effect=AssertionError('Custom tracker used')):
                result = _Analyser(engine, 1, EngineJob(threading.Event()), settings).analyse(chess.Board())
            self.assertEqual(engine.limit.time, seconds)
            self.assertEqual(engine.limit.white_clock, seconds)
            self.assertEqual(engine.limit.black_clock, seconds)
            self.assertEqual(engine.limit.remaining_moves, 1)
            self.assertIsNone(engine.limit.depth)
            self.assertIsNone(engine.limit.nodes)
            self.assertEqual(result['search']['mode'], 'stockfish')
            self.assertEqual(result['search']['stop_reason'], 'stockfish')
            self.assertEqual(result['search']['depth'], 24)

    def test_time_validation_and_cache(self):
        from pydantic import ValidationError
        for seconds in (0.1, 5, 60):
            value = EngineSettings(threads=1, hash_mb=256, depth=21, mode='stockfish', time_limit_s=seconds)
            self.assertEqual(value.apply(Settings()).engine_time_limit_s, seconds)
        for seconds in (0, 60.1, 0.15, float('nan'), float('inf')):
            with self.assertRaises(ValidationError):
                EngineSettings(threads=1, hash_mb=256, depth=21, mode='stockfish', time_limit_s=seconds)
        game = chess.pgn.read_game(io.StringIO('1. e4 *'))
        settings = Settings(engine_mode='stockfish', engine_time_limit_s=5)
        key = cache_key(game, settings, False)
        self.assertNotEqual(key, cache_key(game, replace(settings, engine_time_limit_s=6), False))
        self.assertNotEqual(key, cache_key(game, replace(settings, engine_mode='adaptive'), False))
        self.assertEqual(key, cache_key(game, replace(settings, engine_depth=40, engine_effort='ultra'), False))


if __name__ == '__main__': unittest.main()
