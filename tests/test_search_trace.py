import json
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
import chess
import chess.engine
from webapp.config import Settings
from webapp.engine_scheduler import EngineJob, EngineScheduler, AnalysisCancelled
from webapp.search_trace import Recorder, analyze_search, build_report, select_candidates
from webapp.live_prompts import build_search_prompt

ROOT = Path(__file__).resolve().parent.parent
TRACE_ENGINE = Path(os.getenv('TRACE_TEST_ENGINE', str(ROOT / 'runtime/stockfish-trace' / ('stockfish-trace.exe' if os.name=='nt' else 'stockfish-trace'))))


def iteration(depth, scores=(100, 0, -100), moves=('e2e4','d2d4','g1f3')):
    return {'depth':depth,'candidates':[
        {'rank':i+1,'move':move,'score_uci':f'cp {score}','bound':'exact','pv':[move]}
        for i,(move,score) in enumerate(zip(moves,scores))]}


class SelectionTests(unittest.TestCase):
    def test_dynamic_one_two_three_and_unstable(self):
        def choose(scores):return select_candidates([iteration(d,scores) for d in (10,11,12)])
        selected,reason,stable=choose((160,0,-40))
        self.assertEqual((len(selected),reason,stable),(1,'clear_stable_lead',True))
        self.assertEqual(len(choose((100,80,-100))[0]),2)
        self.assertEqual(len(choose((40,35,25))[0]),3)
        history=[iteration(10),iteration(11,moves=('d2d4','e2e4','g1f3')),iteration(12)]
        self.assertEqual(select_candidates(history)[1],'ranking_unstable')
        self.assertGreater(len(select_candidates(history)[0]),1)
        self.assertGreater(len(select_candidates([iteration(3,(500,0,-50))])[0]),1)
        self.assertEqual(len(select_candidates([iteration(10,(0,),('e2e4',))])[0]),1)

    def test_bounds_never_support_clear_lead(self):
        history=[iteration(d,(500,0,-50)) for d in (10,11,12)]
        history[-1]['candidates'][0]['bound']='lower'
        self.assertNotEqual(select_candidates(history)[1],'clear_stable_lead')

    def test_native_paths_scoring_and_prompt_budget(self):
        board=chess.Board();board.push_san('e4')
        rec=Recorder()
        for event in [{'kind':'start','schema':1}]+[
            iteration(d,(40,35,25),('e7e5','c7c5','e7e6')) for d in (10,11,12)]:
            event.setdefault('kind','iteration')
            rec.ingest({'string':'tutortrace '+json.dumps(event)})
        rec.ingest({'string':'tutortrace '+json.dumps({'kind':'beta_cutoff','path':['e7e5','g1f3'],
            'move':'b8c6','iteration':12,'ply':2,'depth':9,'raw':10,'alpha':0,'beta':1,'bound':'lower'})})
        report=build_report(board,rec,0.1,3)
        self.assertEqual(report['source'],'native')
        self.assertEqual(report['candidates'][0]['score_white']['cp'],-40)
        self.assertEqual(report['events'][0]['path_san'],['e5','Nf3'])
        facts={'search_evidence':report,'moves':[{'san':'e4'}]}
        system,user=build_search_prompt(facts)
        self.assertIn('3个候选',system)
        self.assertNotIn('events',json.loads(user)['search_evidence'])
        self.assertIn('不是完整搜索树',system)

    def test_uci_fallback_requires_complete_depth(self):
        rec=Recorder();board=chess.Board()
        for rank,move in enumerate(('e2e4','d2d4','g1f3'),1):
            rec.ingest({'depth':10,'multipv':rank,'score':chess.engine.PovScore(chess.engine.Cp(20-rank),board.turn),
                        'pv':[chess.Move.from_uci(move)]})
        rec.ingest({'depth':11,'multipv':1,'score':chess.engine.PovScore(chess.engine.Cp(70),board.turn),
                    'pv':[chess.Move.from_uci('e2e4')],'lowerbound':True})
        result=build_report(board,rec,0.1,3)
        self.assertEqual((result['source'],result['depth']),('uci_only',10))
        self.assertFalse(result['events'])
        self.assertEqual(build_report(board,Recorder(),0,3)['candidates'],[])

    def test_partial_iteration_decision_kept_separate_from_complete_comparison(self):
        rec=Recorder()
        for depth in (10,11,12):
            rec.uci[depth]={r['rank']:r for r in iteration(depth,(200,0,-100))['candidates']}
        rec.final_best='d2d4'
        rec.uci[13]={1:{'rank':1,'move':'d2d4','score_uci':'cp 85','pv':['d2d4'],'bound':'exact'}}
        result=build_report(chess.Board(),rec,1,3)
        self.assertEqual(result['depth'],12)
        self.assertEqual(result['selection_reason'],'stopped_during_iteration')
        self.assertFalse(result['comparison_complete'])
        self.assertEqual(result['final_best']['san'],'d4')
        self.assertEqual(result['final_best']['score_white']['cp'],85)
        self.assertEqual(result['candidates'][1]['score_for_turn']['cp'],0)
        self.assertEqual(result['candidate_count'],3)

    def test_castling_consequences_identify_the_actual_rook(self):
        from webapp.search_trace import line_facts
        board=chess.Board('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1')
        self.assertEqual(line_facts(board,['e1c1'])[0]['rook_shift'],{'from':'a1','to':'d1'})
        self.assertEqual(line_facts(board,['e1g1'])[0]['rook_shift'],{'from':'h1','to':'f1'})


@unittest.skipUnless(TRACE_ENGINE.is_file(),'Build the optional native trace engine first')
class NativeTraceTests(unittest.TestCase):
    def settings(self,**kwargs):
        return Settings(stockfish_path=str(TRACE_ENGINE),trace_stockfish_path=str(TRACE_ENGINE),
                        engine_threads=1,engine_hash_mb=256,engine_mode='manual',engine_depth=10,engine_effort='low',**kwargs)

    def test_recording_does_not_change_fixed_depth_search(self):
        engine=chess.engine.SimpleEngine.popen_uci(str(TRACE_ENGINE),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            engine.configure({'Threads':1,'Hash':64})
            board=chess.Board();board.push_san('e4');board.push_san('e5');board.push_san('Nf3')
            runs=[]
            for enabled in (False,True):
                rows=engine.analyse(board,chess.engine.Limit(depth=10),multipv=3,game=object(),options={'TutorTrace':enabled})
                runs.append([(row['pv'][0],row['score'],row['nodes']) for row in rows])
            self.assertEqual(runs[0],runs[1])
        finally:engine.quit()

    def test_native_events_and_clear_mate(self):
        scheduler=EngineScheduler()
        try:
            close=scheduler.submit_explanation(chess.Board(),self.settings(),threading.Event()).future.result(15)
            self.assertEqual(close['source'],'native')
            self.assertEqual(close['invalid_events'],0)
            self.assertLessEqual(close['candidate_count'],3)
            self.assertTrue(close['native_summary']['counts']['root_probe'])
            self.assertTrue(any(key!='root_probe' for key in close['event_counts']))
            board=chess.Board('r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4')
            clear=scheduler.submit_explanation(board,self.settings(),threading.Event()).future.result(15)
            self.assertEqual(clear['candidate_count'],1)
            self.assertEqual(clear['candidates'][0]['san'],'Qxf7#')
            self.assertEqual(clear['selection_reason'],'clear_stable_lead')
        finally:scheduler.close()

    def test_cancel_then_worker_reuse(self):
        from dataclasses import replace
        scheduler=EngineScheduler()
        try:
            first=scheduler.submit_explanation(chess.Board(),replace(self.settings(),engine_mode='adaptive',engine_effort='ultra'),threading.Event())
            time.sleep(0.15)
            first.stop()
            try:first.future.result(10)
            except (AnalysisCancelled,__import__('concurrent.futures').futures.CancelledError):pass
            result=scheduler.submit_explanation(chess.Board(),self.settings(),threading.Event()).future.result(15)
            self.assertEqual(result['source'],'native')
        finally:scheduler.close()
