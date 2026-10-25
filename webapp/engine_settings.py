"""Persistent engine defaults, copied into each analysis request."""
import json
import os
import tempfile
from pathlib import Path
from typing import Literal
from .engine_effort import policy_dict, LEVELS
from pydantic import BaseModel, ConfigDict, Field

MAX_THREADS = os.cpu_count() or 1
SETTINGS_FILE = Path(__file__).resolve().parent.parent / 'runtime' / 'engine-settings.json'


class EngineSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    threads: int = Field(ge=1, le=MAX_THREADS)
    hash_mb: int = Field(ge=256, le=32768, multiple_of=256)
    depth: int = Field(ge=1, le=40)
    time_limit_s: float = Field(default=5.0, ge=0.1, le=60, multiple_of=0.1)

    mode: Literal["manual", "adaptive", "stockfish"] = "adaptive"
    effort: Literal["low", "mid", "high", "xhigh", "max", "ultra"] = "high"

    def apply(self, settings):
        from dataclasses import replace
        return replace(settings, engine_threads=self.threads,
                       engine_hash_mb=self.hash_mb, engine_depth=self.depth,
                       engine_mode=self.mode, engine_effort=self.effort,
                       engine_time_limit_s=self.time_limit_s)


def load(settings):
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        return EngineSettings.model_validate(stored)
    except (OSError, ValueError):
        return EngineSettings(threads=min(MAX_THREADS, max(1, settings.engine_threads)),
                              hash_mb=min(32768, max(256, settings.engine_hash_mb // 256 * 256)),
                              depth=min(40, max(1, settings.engine_depth)))


def save(value):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=SETTINGS_FILE.parent,
                                         suffix='.tmp', delete=False) as output:
            name = output.name
            json.dump(value.model_dump(), output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, SETTINGS_FILE)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def response(value):
    return {'engine': value.model_dump(), 'effort_policies': [policy_dict(level) for level in LEVELS], 'limits': {
        'threads': {'min': 1, 'max': MAX_THREADS, 'step': 1},
        'hash_mb': {'min': 256, 'max': 32768, 'step': 256},
        'depth': {'min': 1, 'max': 40, 'step': 1},
        'time_limit_s': {'min': 0.1, 'max': 60, 'step': 0.1},
        'effort': {'min': 0, 'max': len(LEVELS) - 1, 'step': 1}}}
