# Chess Tutor

**Replay games. Play together. Understand every move.**

A local chess learning app for **PGN review and live, same-computer games with optional Chinese commentary**. Stockfish calculates variations; DeepSeek explains verified board facts. Basic live play needs neither a PGN nor an AI service.

## Features

- **Review:** import PGN, browse multi-game files, step through positions, view evaluations, read streamed commentary and export annotated PGN.
- **Live:** two people share one computer and page. Drag pieces or click starting/destination squares. Legal moves, castling, en passant, promotion and endings use python-chess rules.
- **Chess clock:** choose time and increment, start/pause/resume, stop at game end or timeout. Returning to review pauses play. Export PGN before refreshing or starting another game.
- **Persistent preferences:** live SF Effort and DS depth are independent from review settings. Live parameters apply next move; review parameters apply next loaded game. Switches apply immediately. The retry button explicitly analyzes with current live settings.
- **Full commentary context:** all moves played so far, initial/current FEN, exact piece locations and program-generated position analysis. Review does not expose future PGN moves ahead of the current move.
- **Local review history:** reuse successful DeepSeek reviews when the game, prompts, engine and settings match. Thinking depth is part of cache identity.

## Live assistance

| Stockfish | DeepSeek | Output |
|---|---|---|
| Off | Off | Board, moves, clock; no engine/model calls |
| On | Off | Current side’s best move and principal variation |
| Off | On | Position/last-move commentary; prompt forbids next-move suggestions and numerical evaluation |
| On | On | Stockfish best move with DeepSeek explanation |

Both switches default off on fresh installations; saved choices are restored later.
**instant** disables DeepSeek thinking; **high/max** enable the corresponding provider reasoning effort. See the [Alibaba Cloud reference](https://www.alibabacloud.com/help/en/model-studio/deepseek-api). Thinking can take longer and use more tokens. Analysis does not block moves or the clock.

Live state stays in the page: refreshing resets the game, while server preferences persist. The clock counts elapsed wall time in background tabs. Pause disables moves. This release supports same-computer play, without remote multiplayer rooms.

## Quick start

Python 3.10+:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-app.txt
```

Download [Stockfish](https://stockfishchess.org/download/) for analysis. Put the Windows executable at `stockfish/stockfish/stockfish-windows-x86-64-universal.exe` or set `STOCKFISH_PATH` to its full path.

For AI commentary, copy [.env.example](.env.example) to `.env`, configure `DEEPSEEK_API_KEY`, HTTPS `DEEPSEEK_BASE_URL`, `COMMENTATOR_BACKEND=deepseek` and the model. The Windows launcher can instead read the single Alibaba Cloud `*apiKey*.csv` in the project root. Credentials stay on the server and are excluded from Git.

**Windows desktop:**

```powershell
.venv/Scripts/python.exe launch.py
```

Opens `http://127.0.0.1:8765`, restarts only this checkout’s previous service and closes its service/engine after the final page disconnects. No model configuration means engine-only review. This launcher requires Stockfish.

**Development / other platforms:**

```powershell
$env:COMMENTATOR_BACKEND="disabled"
$env:STOCKFISH_PATH="C:/path/to/stockfish.exe"
.venv/Scripts/python.exe -m uvicorn webapp.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. POSIX shells use `export NAME=value` and `.venv/bin/python`. Direct uvicorn reads environment variables; use `--env-file .env` to load a file. Development mode stays running after pages close. Basic live play works without Stockfish or DeepSeek in this mode.

## Settings and data

- Review **Setting** panel: engine modes/resources and DeepSeek depth. Running games and retries keep the original snapshot.
- Live **实时辅助**: switches, SF `low/mid/high/xhigh/max/ultra`, DS `instant/high/max`. Never plays a move for either player.
- `runtime/engine-settings.json`: review engine; `runtime/review-settings.json` and `runtime/live-settings.json`: independent preferences.
- `history/`: local review records and seven-piece tablebase cache. Live commentary is not saved as review history.
- Stockfish shares one reusable worker; old search results cannot replace newer live positions.
- Lichess seven-piece Syzygy is available for review (`TABLEBASE_ENABLED=false` disables it). Live analysis uses local Stockfish only.
- Commentary sends the played mainline and board positions to your configured provider. Displayed CNY costs and daily USD budget are estimates; the provider’s bill is authoritative.

Bind to `127.0.0.1` for same-computer use. There is no account system for public hosting.
See [setup](LOCAL_SETUP.md), [backend documentation](webapp/README.md) and [release notes](CHANGELOG.md).

## Checks

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
node tests/test_stream.cjs
node tests/test_time_mode.cjs
node tests/test_live_clock.cjs
```

## Credits and license

Live commentary prompts are adapted from Apache-2.0-licensed chess-coach;
see [prompt sources and modifications](docs/live-prompt-sources.md).

Based on [shashank-v-98/chess-annotator](https://github.com/shashank-v-98/chess-annotator), retaining the original [MIT license](LICENSE). Upstream training scripts and optional student backends remain in source, but are not required for live play/DeepSeek. [Stockfish](https://github.com/official-stockfish/Stockfish) is GPL-licensed and its executable is not included in this source release.
