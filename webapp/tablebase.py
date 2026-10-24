"""Seven-piece Lichess root-position queries, persistent cache and request timings.

This is application-level probing, not a remote SyzygyPath inside Stockfish.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import chess
import httpx

ENDPOINT = 'https://tablebase.lichess.ovh/standard'
CACHE = Path(__file__).resolve().parent.parent / 'history' / 'tablebase'
VALUES = {'win': 1, 'loss': -1, 'draw': 0, 'cursed-win': 0, 'blessed-loss': 0}


def eligible(board):
    return (board.is_valid() and not board.chess960 and not board.castling_rights
            and len(board.piece_map()) <= 7 and not board.is_game_over()
            and not board.can_claim_threefold_repetition())


def normalize(board, response):
    category = response['category']
    value = VALUES[category]
    moves = response['moves']
    if not isinstance(moves, list) or len(moves) != board.legal_moves.count():
        raise ValueError('Incomplete tablebase move list')
    white_sign = 1 if board.turn else -1
    parsed, seen = [], set()
    for item in moves:
        move = chess.Move.from_uci(item['uci'])
        if move not in board.legal_moves or move in seen:
            raise ValueError('Invalid tablebase move')
        seen.add(move)
        parsed.append({'uci': move.uci(), 'san': board.san(move),
                       'white_outcome': -white_sign * VALUES[item['category']]})
    if not parsed or parsed[0]['white_outcome'] != white_sign * value:
        raise ValueError('Inconsistent tablebase outcome')
    dtz = response.get('dtz')
    if dtz is not None and type(dtz) is not int:
        raise ValueError('Invalid DTZ')
    return {'provider': 'Lichess Syzygy', 'pieces': len(board.piece_map()),
            'category': category, 'white_outcome': white_sign * value,
            'dtz': dtz, 'dtz_unit': 'Syzygy progress metric in plies; may be rounded or encoded; NOT mate distance',
            'fifty_move_rule': True, 'best_move_san': parsed[0]['san'],
            'best_move_uci': parsed[0]['uci'], 'moves': parsed}


def meaning(facts):
    outcome = facts['white_outcome']
    text = '理论和棋（考虑五十步规则）' if outcome == 0 else ('白方' if outcome > 0 else '黑方') + '理论获胜（考虑五十步规则）'
    if facts['category'] in ('cursed-win', 'blessed-loss'):
        text += '；忽略五十步规则时存在胜负，但不能据此称为可强制获胜'
    return text


class OnlineTablebase:
    def __init__(self, folder=None, transport=None, timeout=3):
        self.folder = Path(folder) if folder else CACHE
        self.transport, self.timeout = transport, timeout
        self._client = None
        self._lock = threading.Lock()
        self._cooldown = 0

    def _http(self):
        if self._client is None:
            proxy = None if self.transport else urllib.request.getproxies().get('https')
            self._client = httpx.Client(proxy=proxy, transport=self.transport,
                                        timeout=self.timeout, follow_redirects=True,
                                        headers={'User-Agent': 'Chess-Annotator/1.0.1 (Syzygy root probe)'})
        return self._client

    def lookup(self, board, use_cache=True):
        if not eligible(board):
            return None
        started = time.perf_counter()
        # Halfmove clock affects the 50-move result; fullmove number does not.
        fen = ' '.join(board.fen().split()[:5]) + ' 1'
        path = self.folder / (hashlib.sha256(fen.encode()).hexdigest() + '.json')
        timing = {'at': datetime.now(timezone.utc).isoformat(), 'fen': fen,
                  'pieces': len(board.piece_map()), 'source': 'network',
                  'ok': False, 'http_status': None, 'network_ms': None}
        try:
            if use_cache:
                try:
                    cached = json.loads(path.read_text(encoding='utf-8'))
                    if cached.get('schema') == 1 and cached.get('fen') == fen:
                        facts = normalize(board, cached['response'])
                        timing.update(source='cache', ok=True)
                        return facts
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    pass
            if time.monotonic() < self._cooldown:
                timing.update(source='cooldown', error='Temporarily unavailable')
                return None
            network_start = time.perf_counter()
            try:
                response = self._http().get(ENDPOINT, params={'fen': fen})
                timing['http_status'] = response.status_code
                response.raise_for_status()
                payload = response.json()
                facts = normalize(board, payload)
            finally:
                timing['network_ms'] = (time.perf_counter() - network_start) * 1000
            timing['ok'] = True
            name = None
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.folder,
                                                 suffix='.tmp', delete=False) as target:
                    name = target.name
                    json.dump({'schema': 1, 'fen': fen, 'response': payload}, target)
                os.replace(name, path)
            except OSError:
                pass  # A cache write failure must not discard a valid online result.
            finally:
                if name and os.path.exists(name):
                    try:
                        os.unlink(name)
                    except OSError:
                        pass
            return facts
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            timing['error'] = type(exc).__name__
            self._cooldown = time.monotonic() + (60 if timing['http_status'] == 429 else 10)
            return None
        finally:
            timing['elapsed_ms'] = (time.perf_counter() - started) * 1000
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                with self._lock, (self.folder / 'requests.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps(timing, ensure_ascii=False) + '\n')
            except OSError:
                pass

    def enrich(self, move, check=lambda: None, before_board=None, after_board=None):
        before_board = before_board if before_board is not None else chess.Board(move['fen_before'])
        after_board = after_board if after_board is not None else chess.Board(move['fen_after'])
        before = self.lookup(before_board)
        check()
        after = self.lookup(after_board)
        check()
        if (before is None and eligible(before_board)) or (after is None and eligible(after_board)):
            move['tablebase_unavailable'] = True
        if before is None and after is None:
            return
        # Keep the large move lists only in the persistent query cache.
        move['tablebase'] = {name: ({k: v for k, v in facts.items() if k != 'moves'} if facts else None)
                             for name, facts in (('before', before), ('after', after))}
        if before and after and not move.get('is_checkmate'):
            sign = 1 if move['side'] == 'white' else -1
            loss = sign * (before['white_outcome'] - after['white_outcome'])
            move['best_move_san'] = before['best_move_san']
            move['is_best_move'] = move['uci'] == before['best_move_uci']
            move['quality_label'] = 'blunder' if loss > 0 else 'best' if move['is_best_move'] else 'good'
            move['nag_symbol'] = '??' if loss > 0 else ''
            move['cp_loss'] = None  # Exact WDL change is not a pawn-valued loss.
            move['missed_mate'] = move['gives_up_mate'] = False
        if after:
            move['principal_variation_san'] = [after['best_move_san']]

    def close(self):
        if self._client:
            self._client.close()
