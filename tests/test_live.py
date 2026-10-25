import asyncio
import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import chess
import httpx
from fastapi.testclient import TestClient
from webapp import engine_settings
from webapp.config import Settings
from webapp.live import replay
from webapp.llm import DeepSeekClient
from webapp.pipeline import analysis_signature, parse_single_game
from webapp.server import app


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'COMMENTATOR_BACKEND': 'disabled', 'DB_PATH': str(Path(self.temp.name)/'db.sqlite3')})
        self.file = patch.object(engine_settings, 'SETTINGS_FILE', Path(self.temp.name)/'engine.json')
        self.env.start(); self.file.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.file.stop(); self.env.stop(); self.temp.cleanup()

    def test_legal_play_special_moves_and_endings(self):
        initial = self.client.post('/api/live/position', json={}).json()
        self.assertEqual(len(initial['legal']), 20)
        self.assertEqual(initial['result'], '*')
        self.assertEqual(self.client.post('/api/live/position', json={'moves':['e2e5']}).status_code, 400)
        # En passant must remove the passed pawn.
        board, _ = replay(['e2e4','a7a6','e4e5','d7d5','e5d6'])
        self.assertIsNone(board.piece_at(chess.D5))
        board, _ = replay(['e2e4','e7e5','g1f3','b8c6','f1c4','g8f6','e1g1'])
        self.assertEqual(board.king(chess.WHITE), chess.G1)
        self.assertEqual(board.piece_at(chess.F1).piece_type, chess.ROOK)
        mate = ['f2f3','e7e5','g2g4','d8h4']
        result = self.client.post('/api/live/position', json={'moves':mate}).json()
        self.assertEqual(result['result'], '0-1')
        self.assertEqual(result['legal'], [])
        self.assertEqual(self.client.post('/api/live/position',json={'moves':mate+['a2a3']}).status_code,400)

    def test_independent_persistent_settings_and_validation(self):
        for mode in ('live','review'):
            data = self.client.get(f'/api/settings/tutor/{mode}').json()
            self.assertFalse(data['stockfish']); self.assertFalse(data['deepseek'])
        value={'deepseek_depth':'max','deepseek':True}
        self.assertEqual(self.client.put('/api/settings/tutor/live',json=value).status_code,200)
        self.assertEqual(self.client.get('/api/settings/tutor/live').json()['deepseek_depth'],'max')
        self.assertEqual(self.client.get('/api/settings/tutor/review').json()['deepseek_depth'],'instant')
        self.assertEqual(self.client.put('/api/settings/tutor/live',json={'deepseek_depth':'bogus'}).status_code,422)

    def test_off_means_no_engine_or_model_calls(self):
        with patch.object(app.state.engine_scheduler,'submit_position') as engine:
            result=self.client.post('/api/live/analyze',json={'moves':['e2e4']}).json()
            self.assertIsNone(result['best']); self.assertIsNone(result['comment'])
            engine.assert_not_called()

    def test_ds_only_context_and_combined_current_best(self):
        app.state.settings.commentator_backend='deepseek'
        app.state.llm=SimpleNamespace(chat=AsyncMock(return_value=('局面评论',{})),aclose=AsyncMock())
        moves=['e2e4','e7e5','g1f3']
        response=self.client.post('/api/live/analyze',json={'moves':moves,'settings':{'deepseek':True,'deepseek_depth':'high'}})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['comment'],'局面评论')
        args=app.state.llm.chat.call_args
        self.assertIn('严格禁止',args.args[1])
        facts=json.loads(args.args[2]); self.assertEqual(len(facts['moves']),3)
        self.assertIn('f3',facts['pieces']['White']); self.assertIn('insights',facts)
        self.assertNotIn('stockfish_current_position',facts)
        self.assertEqual(args.kwargs['depth'],'high')
        future=Future();future.set_result({'best_move':chess.Move.from_uci('b8c6'),'pv':[chess.Move.from_uci('b8c6')],'cp':15,'mate':None,'search':{}})
        job=SimpleNamespace(future=future,stop=lambda:None)
        with patch.object(app.state.engine_scheduler,'submit_position',return_value=job):
            result=self.client.post('/api/live/analyze',json={'moves':moves,'settings':{'stockfish':True,'deepseek':True,'deepseek_depth':'max'}}).json()
        self.assertEqual(result['best']['san'],'Nc6')
        self.assertEqual(json.loads(app.state.llm.chat.call_args.args[2])['stockfish_current_position']['san'],'Nc6')


class DepthTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_payload_per_request(self):
        client=DeepSeekClient('test-not-real','https://example.invalid/v1')
        payloads=[]
        async def respond(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'message':{'content':'评论'}}],'usage':{},'model':'test'})
        await client._client.aclose()
        client._client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            for depth in ('instant','high','max'):
                await client.chat('test','coach','facts',depth=depth)
        finally:
            await client.aclose()
        self.assertFalse(payloads[0]['enable_thinking'])
        self.assertNotIn('reasoning_effort',payloads[0])
        self.assertEqual([p['reasoning_effort'] for p in payloads[1:]],['high','max'])
        self.assertTrue(all(p['enable_thinking'] for p in payloads[1:]))

    async def test_review_context_and_cache_depth(self):
        from dataclasses import replace
        from webapp.pipeline import GamePipeline
        from chess_annotator import app_prompts
        settings=Settings(commentator_backend='deepseek',deepseek_depth='max')
        game=parse_single_game('1. e4 e5 2. Nf3 *',settings)
        self.assertNotEqual(analysis_signature(game,settings,False),analysis_signature(game,replace(settings,deepseek_depth='instant'),False))
        # Exercise the actual stream, capturing the prompt instead of calling a provider.
        from webapp.engine_scheduler import EngineScheduler
        from chess_annotator.features import _analyze_parsed_game
        class Analyser:
            def analyse(self,board):
                return {'cp':0,'mate':None,'best_move':next(iter(board.legal_moves),None),'pv':[]}
        class Scheduler:
            def submit(self,game,settings,on_move,cancel):
                _analyze_parsed_game(game,Analyser(),on_move=on_move)
                future=Future();future.set_result(None)
                return SimpleNamespace(future=future,stop=lambda:None)
        store=SimpleNamespace(spent_today=lambda:0,add_spend=lambda x:None)
        captured=[]
        async def chat(model,system,user,**kw):
            captured.append((user,kw));return '测试',{'generation':{'provider':'deepseek'}}
        pipeline=GamePipeline(settings,SimpleNamespace(chat=chat),store,asyncio.Semaphore(1),Scheduler())
        with patch('webapp.pipeline.History') as history:
            history.return_value.read.return_value=None
            events=[event async for event in pipeline.run(game,False,__import__('threading').Event())]
        self.assertEqual(len(captured),3)
        for ply,(prompt,kw) in enumerate(captured,1):
            record=json.loads(prompt.split('JSON):\n',1)[1].split('\n\nRecent moves',1)[0])
            self.assertEqual(len(record['game_context']['moves']),ply)
            self.assertIn('pieces_before',record);self.assertIn('pieces_after',record)
            self.assertEqual(kw['depth'],'max')
