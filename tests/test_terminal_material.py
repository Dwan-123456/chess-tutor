import io
import json
import unittest
from pathlib import Path
import chess
import chess.pgn
from chess_annotator import features, prompts, positional

class Stub:
    def __init__(self, move, before, after):
        self.move, self.before, self.after = move, before, after
    def analyse(self, board):
        terminal = board.is_checkmate()
        return dict(cp=None, mate=self.after if terminal else self.before,
                    best_move=None if terminal else self.move, pv=[] if terminal else [self.move])

class RegressionTests(unittest.TestCase):
    def check_terminal(self, board, move, winner):
        game=chess.pgn.Game(); game.setup(board.copy(stack=False))
        game.add_variation(move)
        row=features._analyze_parsed_game(game, Stub(move, 1 if winner=='white' else -1, 0))['moves'][0]
        self.assertTrue(row['is_checkmate'])
        self.assertEqual(row['winner'],winner)
        self.assertEqual(row['quality_label'],'best')
        self.assertEqual(row['cp_loss'],0)
        self.assertFalse(row['missed_mate'])
        self.assertTrue(row['is_best_move'])
        _,user=prompts.build_move_prompt(row,None)
        record=json.loads(user.split('JSON):\n')[1].split('\n\nRecent move history')[0])
        self.assertIn('has delivered checkmate', record['eval_after']['meaning'])
        self.assertNotIn('gets mated',record['eval_change_for_mover']['meaning'])
    def test_white_mate(self):
        board=chess.Board('5Q2/3k4/p6R/1p1BQ1p1/8/2N5/PPP5/1K3R2 w - - 0 47')
        self.check_terminal(board,board.parse_san('Qfe8#'),'white')
    def test_black_mate(self):
        board=chess.Board()
        for san in ('f3','e5','g4'): board.push_san(san)
        self.check_terminal(board,board.parse_san('Qh4#'),'black')
    def test_exchange(self):
        for fen,first,reply,gain in [
            ('7k/8/8/3r4/3p4/8/8/K2R4 w - - 0 1','Rxd4','Rxd4','loses 4'),
            ('8/8/2k5/3r4/2P5/8/8/K7 w - - 0 1','cxd5','Kxd5','gains 4')]:
            b=chess.Board(fen); m=b.parse_san(first); a=b.copy(); a.push(m)
            result=positional.analyze_move(b,m,reply_move=a.parse_san(reply))
            self.assertIn(gain,' '.join(result['move_ideas']))
        b=chess.Board(); b.push_san('e4'); b.push_san('d5');m=b.parse_san('exd5'); a=b.copy();a.push(m)
        result=positional.analyze_move(b,m,reply_move=a.parse_san('Qxd5'))
        self.assertIn('no net material gain',' '.join(result['move_ideas']))
    def test_mate_distance_unchanged(self):
        # Explicitly preserve the requested scope: nonterminal M2 -> M7 still uses old logic.
        self.assertEqual(features._quality_label(500,False)[0],'blunder')

if __name__=='__main__': unittest.main()
