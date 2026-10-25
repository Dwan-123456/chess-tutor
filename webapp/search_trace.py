"""Bounded search evidence; native events are never treated as human intentions."""
from collections import Counter
import json
import time
import chess
import chess.engine
from chess_annotator import positional
from .engine_effort import policy_for

MAX_EVENTS = 12000
KINDS = {'root_probe', 'beta_cutoff', 'tt_cutoff', 'null_cutoff', 'futility_cutoff'}


def score_from_uci(text):
    kind, value = text.split()
    value = int(value)
    if kind not in ('cp', 'mate'):
        raise ValueError('Unsupported engine score')
    return {'cp': value if kind == 'cp' else None, 'mate': value if kind == 'mate' else None}


def san_line(board, moves):
    board = board.copy()
    out = []
    for uci in moves[:16]:
        move = chess.Move.from_uci(uci)
        if move == chess.Move.null():
            if board.is_check():
                raise ValueError('Null probe while in check')
            out.append('--')
        else:
            if move not in board.legal_moves:
                raise ValueError('Illegal engine path')
            out.append(board.san(move))
        board.push(move)
    return out


class Recorder:
    def __init__(self):
        self.native = False
        self.events = []
        self.iterations = []
        self.uci = {}
        self.end = None
        self.invalid = 0
        self.final_best = None

    def ingest(self, update):
        text = update.get('string', '')
        if text.startswith('tutortrace '):
            try:
                event = json.loads(text[len('tutortrace '):])
                kind = event['kind']
                if kind == 'start':
                    self.native = event.get('schema') == 1
                elif kind == 'iteration' and 0 < event['depth'] <= 128:
                    if len(event['candidates']) <= 3:
                        self.iterations.append(event)
                elif kind in KINDS and len(self.events) < MAX_EVENTS:
                    self.events.append(event)
                elif kind == 'end':
                    self.end = event
            except (ValueError, KeyError, TypeError):
                self.invalid += 1
        if 'score' in update and update.get('pv') and 'depth' in update:
            rank = update.get('multipv', 1)
            if rank <= 3:
                score = update['score'].relative
                self.uci.setdefault(update['depth'], {})[rank] = {
                    'rank': rank, 'move': update['pv'][0].uci(),
                    'score_uci': f'mate {score.mate()}' if score.is_mate() else f'cp {score.score()}',
                    'bound': 'lower' if update.get('lowerbound') else 'upper' if update.get('upperbound') else 'exact',
                    'pv': [move.uci() for move in update['pv'][:16]],
                }

    def completed(self, count):
        if self.native:
            return self.iterations
        return [{'kind':'iteration','depth':depth,'candidates':[rows[rank] for rank in range(1,count+1)]}
                for depth, rows in sorted(self.uci.items())
                if all(rank in rows and rows[rank]['bound'] == 'exact' for rank in range(1,count+1))]


def select_candidates(iterations):
    """1 for a stable clear lead, 2-3 for close/unstable comparisons. Never >3."""
    latest = iterations[-1]
    candidates = latest['candidates'][:3]
    if len(candidates) == 1:
        return candidates, 'only_legal_move', True
    stable = len(iterations) >= 3 and all(
        item['candidates'][0]['move'] == candidates[0]['move']
        for item in iterations[-3:])
    exact = all(item['bound'] == 'exact' for item in candidates)
    scores = [score_from_uci(item['score_uci']) for item in candidates]
    gap = scores[0]['cp'] - scores[1]['cp'] if all(s['cp'] is not None for s in scores[:2]) else None
    unique_mate = scores[0]['mate'] is not None and scores[0]['mate'] > 0 and scores[1]['mate'] is None
    if stable and exact and latest['depth'] >= 10 and ((gap is not None and gap >= 100) or unique_mate):
        return candidates[:1], 'clear_stable_lead', stable
    count = 2
    if len(candidates) == 3:
        third_gap = scores[0]['cp'] - scores[2]['cp'] if scores[0]['cp'] is not None and scores[2]['cp'] is not None else None
        if (third_gap is not None and third_gap <= (50 if stable else 120)) or (
            scores[0]['mate'] is not None and scores[2]['mate'] is not None):
            count = 3
    return candidates[:count], 'close_candidates' if stable else 'ranking_unstable', stable


def line_facts(board, pv):
    current = board.copy()
    values = {chess.PAWN:1,chess.KNIGHT:3,chess.BISHOP:3,chess.ROOK:5,chess.QUEEN:9,chess.KING:0}
    def material(position):
        return sum(values[p.piece_type] * (1 if p.color else -1) for p in position.piece_map().values())
    result = []
    for ply, uci in enumerate(pv[:12], 1):
        move = current.parse_uci(uci)
        san = current.san(move)
        capture = current.is_capture(move)
        piece = current.piece_at(move.from_square)
        captured = current.piece_at(move.to_square)
        if current.is_en_passant(move):
            captured = chess.Piece(chess.PAWN,not current.turn)
        rook_shift = None
        if current.is_castling(move):
            rank = '1' if current.turn else '8'
            rook_shift = {'from':('h' if current.is_kingside_castling(move) else 'a')+rank,
                          'to':('f' if current.is_kingside_castling(move) else 'd')+rank}
        current.push(move)
        result.append({'ply':ply,'san':san,'capture':capture,'material_white_pawns':material(current),
                       'piece':chess.piece_name(piece.piece_type),'from':chess.square_name(move.from_square),
                       'to':chess.square_name(move.to_square),'rook_shift':rook_shift,
                       'captured_piece':chess.piece_name(captured.piece_type) if captured else None,
                       'check':current.is_check(),'checkmate':current.is_checkmate(),'fen':current.fen()})
    return result


def build_report(board, recorder, elapsed, count):
    iterations = recorder.completed(count)
    if not iterations:
        return {'schema':1,'source':'native' if recorder.native else 'uci_only',
                'candidates':[],'error':'预算内尚未完成一层完整候选比较；请提高 Effort 后重试。'}
    selected, reason, stable = select_candidates(iterations)
    interrupted_change = bool(recorder.final_best and recorder.final_best != iterations[-1]['candidates'][0]['move'])
    if interrupted_change:
        # Preserve the fair, last completed comparison rather than mixing depths.
        selected = iterations[-1]['candidates'][:3]
        if recorder.final_best not in {c['move'] for c in selected}:
            return {'schema':1,'source':'native' if recorder.native else 'uci_only','candidates':[],
                    'error':'停止时首选未进入最后完整比较层，当前资料不足；请增加预算重试。'}
        reason, stable = 'stopped_during_iteration', False
    sign = 1 if board.turn else -1
    output = []
    for row in selected:
        move = board.parse_uci(row['move'])
        scores = score_from_uci(row['score_uci'])
        white = {k:sign*v if v is not None else None for k,v in scores.items()}
        pv = row['pv']
        reply = chess.Move.from_uci(pv[1]) if len(pv)>1 else None
        output.append({'rank':row['rank'],'uci':move.uci(),'san':board.san(move),
                       'score_for_turn':scores,'score_white':white,'bound':row['bound'],
                       'pv':san_line(board,pv),'pv_uci':pv,'line_facts':line_facts(board,pv),
                       'insights':positional.analyze_move(board,move,reply_move=reply)})
    selected_moves = {row['uci'] for row in output}
    events = []
    for event in recorder.events:
        root = event.get('move') if event['kind']=='root_probe' else (event.get('path') or [None])[0]
        if root not in selected_moves:
            continue
        try:
            path = san_line(board,event.get('path',[]))
            position = board.copy()
            for uci in event.get('path',[]):
                position.push(chess.Move.from_uci(uci))
            move_text = event.get('move','0000')
            trigger = chess.Move.from_uci(move_text) if move_text not in ('(none)','none','') else None
            move_san = position.san(trigger) if trigger and trigger in position.legal_moves else None
            events.append({**event,'candidate':root,'path_san':path,'move_san':move_san})
        except ValueError:
            recorder.invalid += 1
    # Keep the latest evidence, across at most three displayed candidates.
    sampled = events[-240:]
    history = []
    for iteration in iterations:
        rows = [{**row, 'san':board.san(board.parse_uci(row['move'])),
                 'score_for_turn':score_from_uci(row['score_uci'])}
                for row in iteration['candidates'] if row['move'] in selected_moves]
        history.append({'depth':iteration['depth'],'candidates':rows})
    changes = []
    for candidate in output:
        previous = None
        for entry in history:
            row = next((r for r in entry['candidates'] if r['move']==candidate['uci'] and r['bound']=='exact'),None)
            if row is None:
                continue
            if previous and row['score_for_turn'] != previous[1]['score_for_turn']:
                changes.append({'candidate':candidate['san'],'from_depth':previous[0],'to_depth':entry['depth'],
                                'score_before':previous[1]['score_for_turn'],'score_after':row['score_for_turn'],
                                'line_before':san_line(board,previous[1]['pv']), 'line_after':san_line(board,row['pv'])})
            previous = entry['depth'],row
    final_row = next((rows[1] for _,rows in sorted(recorder.uci.items(),reverse=True)
                      if 1 in rows and rows[1]['move']==recorder.final_best),None)
    final_best = None
    if final_row:
        score = score_from_uci(final_row['score_uci'])
        final_best = {'uci':final_row['move'],'san':board.san(board.parse_uci(final_row['move'])),
                      'score_white':{k:sign*v if v is not None else None for k,v in score.items()},
                      'bound':final_row['bound'],'pv':san_line(board,final_row['pv'])}
    return {'schema':1,'source':'native' if recorder.native else 'uci_only',
            'engine':'Stockfish 19 TutorTrace v1' if recorder.native else 'Standard UCI (no internal cutoff trace)',
            'fen':board.fen(),'turn':'white' if board.turn else 'black',
            'effective_threads':1,'multipv':count,'depth':iterations[-1]['depth'],
            'elapsed_s':round(elapsed,3),'candidate_count':len(output),'selection_reason':reason,
            'leader_stable':stable,'candidates':output,'iterations':history,'score_changes':changes[-12:],
            'final_best':final_best,'comparison_complete':not interrupted_change,
            'events':sampled,'event_counts':dict(Counter(event['kind'] for event in events)),
            'native_summary':recorder.end,'invalid_events':recorder.invalid,
            'limitations':['剪枝记录仅覆盖主搜索前3个半回合，且为限量采样；不含静态搜索或完整搜索树。',
                           '上下界不是精确评分；剪枝表示满足搜索阈值，不等于某候选是坏棋。',
                           '变化与局面特征是解释证据，不是NNUE神经元的归因结论。',
                           '多候选和记录开销会影响相同时间预算下的搜索深度。']}


def analyze_search(engine, board, settings, job):
    job.check()
    count = min(3,board.legal_moves.count())
    if board.is_game_over():
        return {'schema':1,'source':'terminal','candidates':[],'candidate_count':0,'error':'对局已结束，无候选着法。'}
    native = 'TutorTrace' in engine.options
    if native:
        engine.configure({'TutorTrace':True})
    recorder = Recorder()
    seconds = settings.engine_time_limit_s if settings.engine_mode=='stockfish' else policy_for(settings.engine_effort).max_time_s
    limit = chess.engine.Limit(depth=settings.engine_depth, time=seconds) if settings.engine_mode=='manual' else chess.engine.Limit(time=seconds)
    started = time.monotonic()
    try:
        with engine.analysis(board,limit,game=object(),multipv=count,info=chess.engine.INFO_ALL) as search:
            job.attach(search)
            try:
                for update in search:
                    job.check()
                    recorder.ingest(update)
                final = search.wait()
                recorder.final_best = final.move.uci() if final.move else None
                job.check()
            finally:
                job.detach()
        return build_report(board,recorder,time.monotonic()-started,count)
    finally:
        if native:
            engine.configure({'TutorTrace':False})
