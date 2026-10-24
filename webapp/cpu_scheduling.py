"""Use a locally benchmarked CPU binding; otherwise keep Stockfish's auto policy."""
from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

from .history import engine_fingerprint

PROFILE_FILE = Path(__file__).resolve().parent.parent / 'runtime' / 'cpu-scheduling.json'


def cpu_topology():
    """Windows CPU Sets expose physical cores and hybrid efficiency classes.

    Only a single processor group / NUMA node is tuned here. Preserve inherited
    process affinity and leave unsupported machines on Stockfish's auto policy.
    """
    if os.name != 'nt':
        return []
    import ctypes
    from ctypes import wintypes
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        query = kernel.GetSystemCpuSetInformation
        query.argtypes = [ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
                          wintypes.HANDLE, wintypes.ULONG]
        query.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        affinity = kernel.GetProcessAffinityMask
        affinity.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                             ctypes.POINTER(ctypes.c_size_t)]
        affinity.restype = wintypes.BOOL
        allowed, system = ctypes.c_size_t(), ctypes.c_size_t()
        if not affinity(kernel.GetCurrentProcess(), ctypes.byref(allowed), ctypes.byref(system)):
            return []
        length = wintypes.ULONG()
        query(None, 0, ctypes.byref(length), None, 0)
        if not length.value:
            return []
        data = ctypes.create_string_buffer(length.value)
        if not query(data, length.value, ctypes.byref(length), None, 0):
            return []
        rows, offset = [], 0
        while offset + 32 <= length.value:
            size, kind = struct.unpack_from('<II', data.raw, offset)
            if size < 32 or offset + size > length.value:
                return []
            if kind == 0:
                _, group, cpu, core, _, numa, efficiency, flags = struct.unpack_from(
                    '<IHBBBBBB', data.raw, offset + 8)
                if group != 0 or numa != 0:
                    return []
                # Exclude CPU sets reserved for other processes; parked cores can
                # be unparked by Windows and are not permanently excluded.
                if allowed.value & (1 << cpu) and not (flags & 2 and not flags & 4):
                    rows.append({'cpu': cpu, 'core': core, 'efficiency': efficiency})
            offset += size
        return rows
    except (OSError, AttributeError, ValueError, struct.error):
        return []


def physical_first_policy(topology, threads):
    if not topology or len({r['efficiency'] for r in topology}) < 2:
        return 'auto'
    physical, siblings, seen = [], [], set()
    for row in sorted(topology, key=lambda r: (-r['efficiency'], r['core'], r['cpu'])):
        if row['core'] in seen:
            siblings.append(row)
        else:
            physical.append(row)
            seen.add(row['core'])
    ordered = physical + siblings
    if not 0 < threads < len(ordered):
        return 'auto'
    return ','.join(str(r['cpu']) for r in sorted(ordered[:threads], key=lambda r: r['cpu']))


def profile_key(settings, topology):
    signature = {'topology': sorted(topology, key=lambda r: r['cpu']),
                 'engine': engine_fingerprint(settings.stockfish_path),
                 'threads': settings.engine_threads, 'hash_mb': settings.engine_hash_mb}
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()


def selected_policy(settings):
    topology = cpu_topology()
    candidate = physical_first_policy(topology, settings.engine_threads)
    if candidate == 'auto':
        return 'auto'
    try:
        profile = json.loads(PROFILE_FILE.read_text(encoding='utf-8'))
        record = profile.get('profiles', {}).get(profile_key(settings, topology), {})
        # Only accept an exact, locally measured binding for this hardware/config.
        if (record.get('policy') == candidate and record.get('speedup', 0) >= 1.03):
            return candidate
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return 'auto'
