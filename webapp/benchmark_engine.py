"""Offline, alternating-order CPU scheduling benchmark. No model/API calls.

Run while no game is analysing:
  python -m webapp.benchmark_engine --threads 10 --hash-mb 8192
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.engine

from .config import Settings
from .cpu_scheduling import PROFILE_FILE, cpu_topology, physical_first_policy, profile_key


def save_profile(settings, topology, report):
    try:
        document = json.loads(PROFILE_FILE.read_text(encoding='utf-8'))
        if not isinstance(document.get('profiles'), dict):
            document = {'profiles': {}}
    except (OSError, ValueError, AttributeError):
        document = {'profiles': {}}
    document['profiles'][profile_key(settings, topology)] = report
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=PROFILE_FILE.parent,
                                         suffix='.tmp', delete=False) as target:
            name = target.name
            json.dump(document, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, PROFILE_FILE)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', default=str(Path(__file__).resolve().parent.parent /
                                               'stockfish/stockfish/stockfish-windows-x86-64-universal.exe'))
    parser.add_argument('--threads', type=int, default=10)
    parser.add_argument('--hash-mb', type=int, default=8192)
    parser.add_argument('--seconds', type=float, default=2)
    parser.add_argument('--rounds', type=int, default=4)
    args = parser.parse_args()
    if args.rounds < 2 or args.seconds < .5 or not 1 <= args.threads <= (os.cpu_count() or 1) or not 16 <= args.hash_mb <= 32768:
        parser.error('Use at least 2 rounds, 0.5 seconds, valid thread count and 16–32768 MB Hash.')
    settings = Settings(stockfish_path=args.engine, engine_threads=args.threads, engine_hash_mb=args.hash_mb)
    topology = cpu_topology()
    candidate = physical_first_policy(topology, args.threads)
    if candidate == 'auto':
        parser.error('No supported hybrid CPU binding for this configuration; keep auto.')
    board = chess.Board()
    for san in 'e4 e5 Nf3 Nc6 Bb5 Nf6 d3 Bc5 Bxc6 dxc6 O-O Nd7'.split():
        board.push_san(san)
    boards = [board, chess.Board('r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1')]
    policies = {'auto': 'auto', 'physical_first': candidate}
    samples = {key: [] for key in policies}
    logging.getLogger('chess.engine').setLevel(logging.ERROR)
    with chess.engine.SimpleEngine.popen_uci(args.engine, timeout=30) as engine:
        engine.configure({'Threads': args.threads, 'Hash': args.hash_mb})
        engine.analyse(board, chess.engine.Limit(time=1))  # warm CPU/NNUE before comparisons
        for turn in range(args.rounds):
            names = list(policies)
            for name in names if turn % 2 == 0 else reversed(names):
                engine.configure({'NumaPolicy': policies[name]})
                engine.ping()
                for position in boards:
                    info = engine.analyse(position, chess.engine.Limit(time=args.seconds),
                                          game=object(), info=chess.engine.INFO_BASIC)
                    samples[name].append(info['nodes'] / info['time'])
                print(f'{name}: round {turn + 1}, mean {statistics.mean(samples[name]):,.0f} nodes/s', flush=True)
    means = {name: statistics.mean(values) for name, values in samples.items()}
    speedup = means['physical_first'] / means['auto']
    report = {'saved_at': datetime.now(timezone.utc).isoformat(),
              'policy': candidate if speedup >= 1.03 else 'auto', 'speedup': speedup,
              'threads': args.threads, 'hash_mb': args.hash_mb,
              'seconds_per_position': args.seconds, 'rounds': args.rounds,
              'mean_nps': means, 'samples': samples}
    save_profile(settings, topology, report)
    print(f"Saved CPU policy {report['policy']}; measured throughput change {(speedup-1)*100:+.1f}%.")


if __name__ == '__main__':
    main()
