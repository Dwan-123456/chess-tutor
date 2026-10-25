# Stockfish search explanation experiment

This directory builds an instrumented **separate Stockfish 19 executable**.
The original installed engine is kept. Normal live analysis and PGN review keep
their existing paths. The new **Explain Search** button requests the experiment
for the current live position; opening the records needs Stockfish, while prose
explanation additionally needs DeepSeek enabled.

## What is recorded

The upstream source is pinned to Stockfish tag `sf_19`, commit
[`edb0d9db6731067ec50ce619ff372b463bc4dd5d`](https://github.com/official-stockfish/Stockfish/tree/edb0d9db6731067ec50ce619ff372b463bc4dd5d).
`prepare.py` inserts hooks without changing search decisions:

- A root-candidate probe after search returns: actual fail-soft score, original
  comparison window at that probe, score bound and available PV.
- A snapshot of at most three candidates after a complete iterative-deepening
  iteration.
- Actual beta, transposition-table, null-move and child-futility cutoffs in the
  main search at plies 1–3, with the path, local search window and triggering move
  where applicable.
- Counts of the instrumented shallow events, emitted-event count and omissions.

Events use `info string tutortrace {JSON}` behind a `TutorTrace` UCI check option,
which defaults off. Cutoff samples are limited to eight per type per iteration;
the total event stream is capped at 12,000. The web report retains the latest 240
events belonging to the selected candidates. All limits are intentional sampling,
not a record of the whole search tree. Quiescence search is not instrumented.

Raw event `raw/alpha/beta` numbers are **Stockfish internal units, local side to
move**, not normalized centipawns. Root `score_uci` is converted with Stockfish's
own Score formatter. Candidate `score_for_turn` is from the root side's view;
`score_white` is White-relative. A lower/upper bound is never an exact score.
Pruning means a search threshold was satisfied; it does not prove the root move
is bad. Null-move paths are hypothetical and marked `--`, not played game moves.

## Dynamic 1–3 explanations

The engine calculates at most three MultiPV candidates. Presentation chooses:

- One candidate when there is one legal move, or the leader stays first in the
  last three completed iterations, depth is at least ten, scores are exact and
  it leads the runner-up by at least 100 cp (or is the sole reported mating line).
- Three when the third move is within 50 cp of the first with a stable leader,
  or within 120 cp while the leader is unstable. Multiple mating lines are also
  compared rather than calling another winning move bad.
- Two otherwise.

These are configurable engineering heuristics in `webapp/search_trace.py`, not
calibrated confidence probabilities. One candidate receives at most two short
sentences; close/unstable candidates receive more detailed comparisons. If the
final bestmove changes during an unfinished iteration, the final decision and
the earlier complete-depth comparison are shown separately without a superiority
verdict. If that move is outside the three compared candidates, no comparison is
generated; the user is asked to increase the budget.

The explanation job uses **one thread** so events and the decision come from the
same worker. It uses the configured Hash and Effort time budget; manual mode also
caps depth. Official-time mode uses its selected per-position time cap. This is
an explicit MultiPV diagnostic run, not a replay of a previous single-PV search.
Recording and MultiPV can reduce the depth reached within a time budget.

DS receives selected candidate PVs, verified board consequences, per-depth
observations, score changes and a small contextualized event sample. It is told
to explain only those candidates, respect bounds, distinguish observations from
causes, and state when evidence cannot establish the chess reason. It does not
receive the raw internal-unit thresholds. This experiment does **not** provide
NNUE neuron attribution or establish that a human chess concept caused a score.

## Build

Python 3.10+, GNU make and GCC with C++17 support are needed. On Windows a portable
[w64devkit](https://github.com/skeeto/w64devkit) toolchain works; no global compiler
installation is needed. On the development machine v2.10.1 x64 was downloaded
from the author's official GitHub release and verified against its published
SHA-256 (`add1edf984ce4d473cfeb7c52e0ab38db73a855bba6c7f564d5e16648eb2896a`).

```powershell
.venv/Scripts/python.exe -m engine_trace.build --toolchain runtime/toolchain/w64devkit/bin
```

On Linux, omit `--toolchain` if make/g++ are on PATH. The default architecture is
AVX2; use `--arch x86-64` for a generic build. The builder downloads only the
pinned official source, exports the matching embedded NNUE network from the
existing official binary (or downloads it from the official network endpoint),
verifies its filename hash, and builds without GCC LTO for w64devkit compatibility.
It keeps full corresponding source and build manifests under
`runtime/stockfish-trace/`. A fresh source directory is required if changing the
hook inserter itself; header-only changes are rebuilt automatically.

The application auto-detects `runtime/stockfish-trace/stockfish-trace.exe` on
Windows (`stockfish-trace` elsewhere). `TRACE_STOCKFISH_PATH` overrides it. When
the custom binary is absent, the button can still compare standard UCI output;
the report explicitly says that internal cutoff records are unavailable.

## Test

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Native tests run when the local binary exists (or `TRACE_TEST_ENGINE` is set).
They cover trace-on/off equality of fixed-depth move, score and node counts,
actual cutoff events, a clear mating preference, cancellation/worker reuse,
selection of one/two/three moves, score orientation and incomplete/bound results.

The user-provided Paul Morphy–Duke Karl / Count Isouard game was also tested at
plies 16, 18, 22 and 26, with a depth-16/5-second diagnostic budget and real
DeepSeek instant calls. Those runs selected 2, 1, 1 and 1 moves respectively.
The PGN and generated reports stay local under `runtime/stockfish-trace/`.

## License

Upstream Stockfish, the inserted hooks, `prepare.py` and `tutor_trace.h` are
GPL-3.0-or-later; the license is retained in [COPYING.txt](COPYING.txt). Original
Stockfish copyright notices stay intact. The web application communicates with
this executable as a separate UCI process. No compiler, NNUE file, executable,
personal PGN or trace history is included in the Git repository. This is an
experimental modified Stockfish build, not an official Stockfish release.
