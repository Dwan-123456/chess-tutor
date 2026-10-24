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
                            changed = {'threads': 2, 'hash_mb': 1024, 'depth': 18}
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
                    self.assertEqual(client.get('/api/settings/engine').json()['engine'],changed)
                with TestClient(app) as client:
                    self.assertEqual(client.get('/api/settings/engine').json()['engine'],changed)


if __name__ == '__main__':
    unittest.main()
