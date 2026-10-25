# Live commentary prompt sources

Reviewed on 2026-10-25. The replacement affects live play, both DS-only and
Stockfish + DS. PGN-review prompts remain separate.

## Primary adaptation: qam4/chess-coach

- Author/project: [qam4/chess-coach](https://github.com/qam4/chess-coach).
- Pinned source: [src/chess_coach/prompts.py](https://github.com/qam4/chess-coach/blob/20a5252620ca3939a4730c799196bea9525e2bb0/src/chess_coach/prompts.py).
- License: Apache License 2.0; the original license text is retained in
  [licenses/chess-coach-Apache-2.0.txt](licenses/chess-coach-Apache-2.0.txt).
- Relevant templates: SYSTEM_PROMPT_V2, RICH_COACHING_PROMPT_V2, and the short
  move-feedback instructions. These prioritize a few concrete features, explain
  why they matter, ground claims in supplied facts, avoid invented continuations
  and omit motivational sign-offs. The source also describes withholding fields
  the model should not repeat rather than relying only on negative instructions.

Modifications in `webapp/live_prompts.py`: translated and rewritten for short
Chinese commentary on a two-human live game; reduced output to 1–2 sentences and
100 Chinese characters; replaced student-level and move-rating branches with
DS-only observation and current-position engine-explanation modes; retained
complete played-game context; omitted false check and unfinished-result fields;
added computed latest-move effects so earlier events are not attributed to it;
prohibited next-move advice in DS-only mode and default-status narration in both
modes. These are adapted prompts, not a verbatim upstream installation. The
adapted prompt text retains Apache-2.0 attribution; the project's original MIT
license remains in place for its other code.

## Other live-commentary reference reviewed

[souravsahums/Real-Time-Chess-Commentator-App/prompts.py](https://github.com/souravsahums/Real-Time-Chess-Commentator-App/blob/0cfa489701205c646ed18b294a48afacc9c11d76/prompts.py)
uses professional-commentator and brief natural-speech instructions, targeting a
spoken response under five seconds. It explicitly allows upcoming moves, so its
prompt was not copied into this app. Its repository license is CC BY 4.0.

## Validation

Prompt integration checks verify that full game and piece-position context still
reach the model, normal nonterminal status fields are omitted, DS-only requests
contain no engine recommendations, and combined mode distinguishes a current best
move from a move already played. Live provider samples are useful for style, but
instruction-following by a language model is not a deterministic guarantee.
