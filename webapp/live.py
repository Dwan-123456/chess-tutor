"""Legal same-computer play and optional analysis of only the current position."""
import asyncio
import threading
import chess
import chess.pgn
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from chess_annotator import app_prompts, positional
from .tutor_settings import TutorSettings
from .live_prompts import build_live_prompt, build_search_prompt

router = APIRouter()


class PositionRequest(BaseModel):
    moves: list[str] = Field(default_factory=list, max_length=1000)


class AnalysisRequest(PositionRequest):
    settings: TutorSettings = Field(default_factory=TutorSettings)
    explain_search: bool = False


def replay(moves):
    board = chess.Board()
    sans = []
    try:
        for uci in moves:
            if board.is_game_over():
                raise ValueError('Game already ended')
            move = board.parse_uci(uci)
            sans.append(board.san(move))
            board.push(move)
    except ValueError:
        raise HTTPException(400, 'Illegal move sequence') from None
    return board, sans


def position(board, sans):
    outcome = board.outcome()
    game = chess.pgn.Game.from_board(board)
    return {'fen': board.fen(), 'legal': [m.uci() for m in board.legal_moves],
            'sans': sans, 'turn': 'white' if board.turn else 'black',
            'check': board.is_check(), 'result': outcome.result() if outcome else '*',
            'termination': outcome.termination.name if outcome else None,
            'can_claim_draw': board.can_claim_draw(), 'pgn': str(game)}


@router.post('/api/live/position')
async def get_position(req: PositionRequest):
    board, sans = replay(req.moves)
    return position(board, sans)


@router.post('/api/live/analyze')
async def analyze(req: AnalysisRequest, request: Request):
    board, sans = replay(req.moves)
    st = request.app.state
    options = req.settings.model_copy(deep=True)
    s = (options.engine or st.engine_defaults).apply(st.settings)
    facts = {'initial_fen': chess.STARTING_FEN, 'moves': [dict(uci=u, san=v) for u, v in zip(req.moves, sans)],
             'current_fen': board.fen(), 'pieces': app_prompts._pieces(board.fen()),
             'side_to_move': 'white' if board.turn else 'black',
             'check': board.is_check(), 'result': board.result(), 'last_move': sans[-1] if sans else None}
    if req.moves:
        before = board.copy()
        last = before.pop()
        facts['fen_before'] = before.fen()
        facts['pieces_before'] = app_prompts._pieces(before.fen())
        piece = before.piece_at(last.from_square)
        center = chess.SquareSet([chess.D4, chess.E4, chess.D5, chess.E5])
        gained = (board.attacks(last.to_square) - before.attacks(last.from_square)) & center
        facts['last_move_effects'] = {
            'piece': chess.piece_name(piece.piece_type),
            'newly_controlled_center_squares': [chess.square_name(square) for square in gained],
            'develops_minor_piece_from_back_rank': piece.piece_type in (chess.KNIGHT, chess.BISHOP)
                and chess.square_rank(last.from_square) in (0, 7)
                and chess.square_rank(last.to_square) not in (0, 7),
            'capture': before.is_capture(last), 'castling': before.is_castling(last),
        }
        facts['insights'] = positional.analyze_move(before, last)
    else:
        facts['insights'] = {'position_context': [t for _, t in positional.position_context(board, None, None)]}
    output = {'ply': len(req.moves), 'fen': board.fen(), 'best': None, 'comment': None,
              'settings': options.model_dump()}
    job = None
    try:
        if options.stockfish:
            submit = st.engine_scheduler.submit_explanation if req.explain_search else st.engine_scheduler.submit_position
            job = submit(board, s, threading.Event())
            future = asyncio.wrap_future(job.future)
            while not future.done():
                if await request.is_disconnected():
                    job.stop()
                    return output
                await asyncio.wait({future}, timeout=0.1)
            info = await future
            if req.explain_search:
                output['trace'] = info
                facts['search_evidence'] = info
                if not info.get('candidates'):
                    output['error'] = info.get('error', '尚无完整候选比较')
                    return output
                candidate = info.get('final_best') or info['candidates'][0]
                output['best'] = {'san':candidate['san'], 'uci':candidate['uci'], 'pv':candidate['pv'],
                                  **candidate['score_white'], 'search':{'depth':info['depth'], 'mode':'trace'}}
            else:
                output['best'] = best_output(board, info)
            facts['stockfish_current_position'] = output['best']
        if options.deepseek:
            if st.settings.commentator_backend != 'deepseek' or st.llm is None:
                output['error'] = 'DeepSeek 尚未配置，请在服务器配置 API 后重试。'
                return output
            async with st.llm_sem:
                if await request.is_disconnected():
                    return output
                if st.store.spent_today() >= s.daily_budget_usd:
                    output['error'] = '今日解说预算已用完。'
                    return output
                system, user = build_search_prompt(facts) if req.explain_search and options.stockfish else build_live_prompt(facts, options.stockfish)
                token_limit = 500
                if req.explain_search and options.stockfish:
                    token_limit = {1:400,2:900,3:1200}[len(info['candidates'])]
                text, usage = await st.llm.chat(s.model, system, user, max_tokens=token_limit, depth=options.deepseek_depth)
                st.store.add_spend(s.cost(s.model, usage))
                output.update(comment=text, generation=usage.get('generation'), billing=usage.get('billing'))
        return output
    except asyncio.CancelledError:
        raise
    except Exception:
        output['error'] = '分析暂时不可用，请检查引擎或 DeepSeek 配置后重试。'
        return output
    finally:
        if job is not None:
            job.stop()


def best_output(board, info):
    pvboard = board.copy()
    pv = []
    for move in info['pv']:
        pv.append(pvboard.san(move))
        pvboard.push(move)
    best = info['best_move']
    return {'san':board.san(best) if best else None, 'uci':best.uci() if best else None,
            'pv':pv, 'cp':info['cp'], 'mate':info['mate'], 'search':info['search']}
