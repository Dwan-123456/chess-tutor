"""Versioned, atomic local analysis records. Never stores API credentials."""
import hashlib
import json
from pathlib import Path
import os
import tempfile
from datetime import datetime, timezone
from functools import lru_cache

ROOT = Path(__file__).resolve().parent.parent
RULE_FILES = ('chess_annotator/features.py', 'chess_annotator/positional.py',
              'chess_annotator/prompts.py', 'chess_annotator/app_prompts.py',
              'webapp/pipeline.py', 'webapp/llm.py',
              'webapp/engine_scheduler.py', 'webapp/cpu_scheduling.py', 'webapp/tablebase.py')


def rules_fingerprint():
    digest = hashlib.sha256()
    for name in RULE_FILES:
        digest.update(name.encode())
        digest.update((ROOT / name).read_text(encoding='utf-8-sig').encode())
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _engine_hash(path, size, mtime):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def engine_fingerprint(path):
    import shutil
    resolved = Path(shutil.which(path) or path).resolve()
    if not resolved.is_file():
        return str(resolved)
    stat = resolved.stat()
    return _engine_hash(str(resolved), stat.st_size, stat.st_mtime_ns)


class History:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else ROOT / 'history'

    def path(self, key):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Invalid history key')
        return self.folder / (key + '.json')

    def read(self, key, plies):
        try:
            record = json.loads(self.path(key).read_text(encoding='utf-8'))
            if record.get('schema') != 1 or record.get('key') != key:
                return None
            events = record['events']
            moves = [e['ply'] for e in events if e['type'] == 'move']
            comments = [e for e in events if e['type'] == 'comment']
            expected = list(range(1, plies + 1))
            if sorted(moves) != expected or sorted(e['ply'] for e in comments) != expected:
                return None
            if any(not e.get('text') or e.get('error') or
                   (e.get('generation') or {}).get('provider') != 'deepseek' for e in comments):
                return None
            return record
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def write(self, key, game, events, signature):
        self.folder.mkdir(parents=True, exist_ok=True)
        record = {'schema': 1, 'key': key, 'saved_at': datetime.now(timezone.utc).isoformat(),
                  'headers': dict(game.headers), 'pgn': str(game),
                  'signature': signature, 'events': events}
        # No original annotations: they may contain private notes and are not model input.
        import chess.pgn
        record['pgn'] = game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.folder,
                                             suffix='.tmp', delete=False) as out:
                name = out.name
                json.dump(record, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path(key))
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
