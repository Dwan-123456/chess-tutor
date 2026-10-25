import asyncio
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from webapp import engine_settings
from webapp.server import app


class EngineSettingsTests(unittest.TestCase):
    def test_no_hourly_request_limit(self):
        async def analysis(pipeline, game, factcheck, cancel):
            yield {'type': 'done', 'cached': False}
        with tempfile.TemporaryDirectory() as folder:
            env = {'COMMENTATOR_BACKEND': 'disabled', 'DB_PATH': str(Path(folder)/'test.sqlite3'),
                   'GAMES_PER_IP_PER_HOUR': '1'}
            with patch.dict(os.environ, env), patch.object(engine_settings, 'SETTINGS_FILE', Path(folder)/'settings.json'):
                with patch('webapp.server.GamePipeline.run', analysis), TestClient(app) as client:
                    for _ in range(15):
                        response = client.post('/api/annotate', json={'pgn': '1. e4 *'})
                        self.assertEqual(response.status_code, 200)
                        self.assertIn('"type": "done"', response.text)

    def test_adaptive_defaults_and_low_persistence(self):
        from webapp.config import Settings
        import json
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'settings.json'
            with patch.object(engine_settings, 'SETTINGS_FILE', target):
                self.assertEqual(engine_settings.load(Settings()).mode, 'adaptive')
                legacy = {'threads': 1, 'hash_mb': 256, 'depth': 21}
                target.write_text(json.dumps(legacy), encoding='utf-8')
                self.assertEqual(engine_settings.load(Settings()).effort, 'high')
                self.assertEqual(engine_settings.load(Settings()).mode, 'adaptive')
                target.write_text(json.dumps({**legacy, 'mode': 'adaptive', 'effort': 'low'}), encoding='utf-8')
                migrated = engine_settings.load(Settings())
                self.assertEqual(migrated.effort, 'low')
                self.assertEqual((migrated.threads, migrated.hash_mb, migrated.depth), (1, 256, 21))
                self.assertEqual(engine_settings.response(migrated)['limits']['effort']['max'], 5)

    def test_persistence_validation_and_game_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {'COMMENTATOR_BACKEND': 'disabled', 'DB_PATH': str(Path(folder)/'test.sqlite3')}
            with patch.dict(os.environ, env), patch.object(engine_settings, 'SETTINGS_FILE', Path(folder)/'settings.json'):
                entered, release = threading.Event(), threading.Event()
                snapshots = []
                async def analysis(pipeline, game, factcheck, cancel):
                    snapshots.append((pipeline.s.engine_threads, pipeline.s.engine_hash_mb, pipeline.s.engine_depth))
                    entered.set()
                    await asyncio.to_thread(release.wait, 10)
                    yield {'type': 'done', 'cached': False}
                with TestClient(app) as client:
                    original = client.get('/api/settings/engine').json()['engine']
                    with patch('webapp.server.GamePipeline.run', analysis):
                        with ThreadPoolExecutor() as pool:
                            pending = pool.submit(client.post, '/api/annotate', json={'pgn':'1. e4 *'})
                            self.assertTrue(entered.wait(5))
                            changed = {'threads': 2, 'hash_mb': 1024, 'depth': 18, 'mode': 'manual', 'effort': 'high', 'time_limit_s': 5.0}
                            self.assertEqual(client.put('/api/settings/engine', json=changed).status_code, 200)
                            release.set()
                            self.assertEqual(pending.result(timeout=10).status_code, 200)
                        self.assertEqual(snapshots[0], (original['threads'], original['hash_mb'], original['depth']))
                        client.post('/api/annotate', json={'pgn':'1. d4 *'})
                        self.assertEqual(snapshots[-1], (2,1024,18))
                        # Retrying an old game retains that game's captured parameters.
                        client.post('/api/annotate', json={'pgn':'1. e4 *', 'engine':original})
                        self.assertEqual(snapshots[-1], snapshots[0])
                    self.assertEqual(client.put('/api/settings/engine', json={**changed,'threads':0}).status_code,422)
                    self.assertEqual(client.put('/api/settings/engine', json={**changed,'hash_mb':257}).status_code,422)
                    self.assertEqual(client.put('/api/settings/engine', json={**changed,'depth':41}).status_code,422)
                    for level in ('low', 'mid', 'high', 'xhigh', 'max', 'ultra'):
                        adaptive = {**changed, 'mode': 'adaptive', 'effort': level}
                        response = client.put('/api/settings/engine', json=adaptive)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()['engine'], adaptive)
                        self.assertEqual(len(response.json()['effort_policies']), 6)
                    for invalid in (50, 'medium', 'unknown', None):
                        self.assertEqual(client.put('/api/settings/engine', json={**changed, 'effort': invalid}).status_code, 422)
                    self.assertEqual(client.put('/api/settings/engine', json={**changed, 'mode': 'unknown'}).status_code, 422)
                    client.put('/api/settings/engine', json=changed)
                    self.assertEqual(client.get('/api/settings/engine').json()['engine'],changed)
                with TestClient(app) as client:
                    self.assertEqual(client.get('/api/settings/engine').json()['engine'],changed)


if __name__ == '__main__':
    unittest.main()
