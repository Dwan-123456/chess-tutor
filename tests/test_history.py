import io
import json
import tempfile
import unittest
from unittest.mock import patch
import chess.pgn
from webapp.config import Settings
from webapp.history import History
from webapp.pipeline import cache_key


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.game = chess.pgn.read_game(io.StringIO('1. e4 *'))
        self.settings = Settings(commentator_backend='deepseek', model='deepseek-v4.1-flash-1017')

    def test_identity_and_invalidation(self):
        key = cache_key(self.game, self.settings, False)
        self.game.headers['White'] = 'Different display name'
        self.assertEqual(key, cache_key(self.game, self.settings, False))
        with patch('webapp.pipeline.rules_fingerprint', return_value='changed'):
            self.assertNotEqual(key, cache_key(self.game, self.settings, False))
        self.assertNotEqual(key, cache_key(self.game, self.settings, True))
        self.settings.engine_threads += 1
        self.assertNotEqual(key, cache_key(self.game, self.settings, False))
        self.settings.engine_threads -= 1
        self.settings.engine_hash_mb += 1
        self.assertNotEqual(key, cache_key(self.game, self.settings, False))
        self.settings.engine_hash_mb -= 1
        self.settings.engine_depth += 1
        self.assertNotEqual(key, cache_key(self.game, self.settings, False))

    def test_persistence_and_corruption(self):
        key = 'a' * 64
        events = [{'type': 'move', 'ply': 1},
                  {'type': 'comment', 'ply': 1, 'text': 'test',
                   'generation': {'provider': 'deepseek', 'request_id': 'request-test'}}]
        with tempfile.TemporaryDirectory() as folder:
            store = History(folder)
            store.write(key, self.game, events, {'rules': 'test'})
            self.assertEqual(History(folder).read(key, 1)['events'], events)
            self.assertIsNone(store.read(key, 2))
            store.path(key).write_text('[]')
            self.assertIsNone(store.read(key, 1))
            store.write(key, self.game, events[:1], {})
            self.assertIsNone(store.read(key, 1))


if __name__ == '__main__':
    unittest.main()
