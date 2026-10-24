import json
import tempfile
import unittest
from pathlib import Path

import chess
import httpx

from chess_annotator import app_prompts, features
from webapp.tablebase import OnlineTablebase, eligible, normalize


def payload(board, white_outcome=1):
    value = white_outcome * (1 if board.turn else -1)
    category = {1:'win',0:'draw',-1:'loss'}
    return {'category':category[value], 'dtz':12,
            'moves':[{'uci':m.uci(), 'category':category[-value]} for m in board.legal_moves]}


class TablebaseTests(unittest.TestCase):
    def setUp(self):
        self.board = chess.Board('7k/8/8/8/8/8/8/K2Q4 w - - 0 1')

    def test_cache_clock_identity_and_timings(self):
        calls=[]
        def serve(request):
            calls.append(request)
            return httpx.Response(200,json=payload(chess.Board(request.url.params['fen'])))
        with tempfile.TemporaryDirectory() as folder:
            client=OnlineTablebase(folder,httpx.MockTransport(serve))
            try:
                first=client.lookup(self.board)
                self.assertEqual(first['white_outcome'],1)
                self.assertNotIn('mate',first)
                self.assertIn('NOT mate',first['dtz_unit'])
                same=self.board.copy();same.fullmove_number=40
                self.assertEqual(client.lookup(same),first)
                same.halfmove_clock=99
                client.lookup(same)
                self.assertEqual(len(calls),2)
                log=[json.loads(x) for x in (Path(folder)/'requests.jsonl').read_text().splitlines()]
                self.assertEqual([r['source'] for r in log],['network','cache','network'])
                self.assertTrue(all(r['elapsed_ms']>=0 for r in log))
                self.assertIsNone(log[1]['network_ms'])
                self.assertFalse(eligible(chess.Board()))
                self.assertIsNone(client.lookup(chess.Board()))
            finally:client.close()

    def test_rate_limit_fallback_and_corrupt_response(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[]
            def limited(request):
                calls.append(request);return httpx.Response(429)
            client=OnlineTablebase(folder,httpx.MockTransport(limited))
            try:
                self.assertIsNone(client.lookup(self.board))
                self.assertIsNone(client.lookup(self.board))
                self.assertEqual(len(calls),1)
            finally:client.close()
        bad=payload(self.board);bad['moves'][0]['uci']='a1a8'
        with self.assertRaises(ValueError):normalize(self.board,bad)
        bad=payload(self.board);bad['category']='maybe-win'
        with self.assertRaises(KeyError):normalize(self.board,bad)

    def test_repetition_and_cursed_win(self):
        repeated=self.board.copy()
        for san in ('Ka2','Kh7','Ka1','Kh8')*2:repeated.push_san(san)
        self.assertTrue(repeated.can_claim_threefold_repetition())
        self.assertFalse(eligible(repeated))
        response=payload(self.board,0);response['category']='cursed-win'
        self.assertEqual(normalize(self.board,response)['white_outcome'],0)

    def test_losing_tablebase_win_is_blunder(self):
        def serve(request):
            board=chess.Board(request.url.params['fen'])
            return httpx.Response(200,json=payload(board,1 if board.turn else 0))
        after=self.board.copy();move=self.board.parse_san('Qe2');after.push(move)
        record={'fen_before':self.board.fen(),'fen_after':after.fen(),'side':'white','uci':move.uci()}
        with tempfile.TemporaryDirectory() as folder:
            client=OnlineTablebase(folder,httpx.MockTransport(serve))
            try:client.enrich(record)
            finally:client.close()
        self.assertEqual(record['quality_label'],'blunder')
        self.assertEqual(record['nag_symbol'],'??')

    def test_tablebase_prompt_overrides_mate_distance_rating(self):
        def serve(request):
            return httpx.Response(200,json=payload(chess.Board(request.url.params['fen'])))
        import io
        import chess.pgn
        g=chess.pgn.Game();g.setup(self.board)
        g.add_variation(self.board.parse_san('Qe2'))
        class Stub:
            def analyse(_,board):
                move=next(iter(board.legal_moves))
                return {'cp':900,'mate':None,'best_move':move,'pv':[move]}
        move=features._analyze_parsed_game(g,Stub())['moves'][0]
        move['quality_label']='blunder';move['nag_symbol']='??'
        with tempfile.TemporaryDirectory() as folder:
            client=OnlineTablebase(folder,httpx.MockTransport(serve))
            try:client.enrich(move)
            finally:client.close()
        self.assertIn(move['quality_label'],('best','good'))
        self.assertEqual(move['nag_symbol'],'')
        system,user=app_prompts.build_app_move_prompt(move,[],{})
        facts=json.loads(user.split('JSON):\n')[1].split('\n\nRecent moves')[0])
        self.assertIsNone(facts['eval_after']['mate_in'])
        self.assertIn('theoretical win',facts['eval_after']['meaning'])
        self.assertIn('NOT a distance to checkmate',system)


if __name__=='__main__':unittest.main()
