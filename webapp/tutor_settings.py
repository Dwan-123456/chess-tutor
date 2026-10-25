"""Separate persistent defaults for live play and game review."""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from . import engine_settings


class TutorSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    deepseek_depth: Literal['instant', 'high', 'max'] = 'instant'
    stockfish: bool = False
    deepseek: bool = False
    engine: engine_settings.EngineSettings | None = None


def load(mode):
    import json
    try:
        return TutorSettings.model_validate(json.loads(path(mode).read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return TutorSettings()


def path(mode):
    if mode not in ('live', 'review'):
        raise ValueError('Unknown mode')
    return engine_settings.SETTINGS_FILE.parent / f'{mode}-settings.json'


def save(mode, value):
    import os
    import tempfile
    target = path(mode)
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, delete=False) as out:
            name = out.name
            out.write(value.model_dump_json())
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, target)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
