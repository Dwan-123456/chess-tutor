"""Apply small, fail-closed trace hooks to the pinned Stockfish 19 source.

GPL-3.0-or-later. The upstream source and these hooks build a separate process.
"""
from pathlib import Path
import shutil


def prepare(source):
    source = Path(source)
    header = Path(__file__).with_name('tutor_trace.h')
    shutil.copyfile(header, source / 'tutor_trace.h')
    engine = source / 'engine.cpp'
    text = engine.read_text(encoding='utf-8')
    text = once(text, '#include "engine.h"', '#include "engine.h"\n#include "tutor_trace.h"')
    text = once(text, '    options.add("Skill Level",', '''    options.add("TutorTrace", Option(false, [](const Option& o) {
        TutorTrace::enabled.store(bool(o));
        return std::optional<std::string>{};
    }));
    options.add("Skill Level",''')
    engine.write_text(text, encoding='utf-8')
    search = source / 'search.cpp'
    text = search.read_text(encoding='utf-8')
    text = once(text, '#include "search.h"', '#include "search.h"\n#include "tutor_trace.h"')
    text = once(text, '    main_manager()->tm.init(', '    if (TutorTrace::enabled) TutorTrace::begin();\n\n    main_manager()->tm.init(')
    text = once(text, '    main_manager()->updates.onBestmove(bestmove, ponder);',
                '    if (TutorTrace::enabled) TutorTrace::finish();\n    main_manager()->updates.onBestmove(bestmove, ponder);')
    text = once(text, '        rootDepth++;',
                '        rootDepth++;\n        if (mainThread && TutorTrace::enabled) TutorTrace::next_depth();')
    text = once(text, '        const bool forgottenMate =',
                '        if (mainThread && TutorTrace::enabled && !threads.stop)\n            TutorTrace::iteration(rootPos, rootMoves, rootDepth);\n\n        const bool forgottenMate =')
    text = once(text, '        if (rootNode)\n        {\n            RootMove& rm', '''        if (rootNode && is_mainthread() && TutorTrace::enabled)
            TutorTrace::record(4, pos, ss, rootDepth, depth, value, alpha, beta, move,
                              (moveCount == 1 || value > alpha) ? (ss + 1)->pv : nullptr);

        if (rootNode)
        {
            RootMove& rm''')
    # Restrict edits to the main search; qsearch has its own semantics and is untraced.
    start = text.index('Value Search::Worker::search(')
    end = text.index('Value Search::Worker::qsearch(', start)
    body = text[start:end]
    body = body.replace('return ttData.value;', '''{
                    if (is_mainthread() && TutorTrace::enabled)
                        TutorTrace::record(1, pos, ss, rootDepth, depth, ttData.value, alpha, beta, ttData.move);
                    return ttData.value;
                }''')
    body = once(body, '        if (eval - futilityMargin >= beta)\n            return (661 * beta + 363 * eval) / 1024;', '''        if (eval - futilityMargin >= beta)
        {
            Value result = (661 * beta + 363 * eval) / 1024;
            if (is_mainthread() && TutorTrace::enabled)
                TutorTrace::record(3, pos, ss, rootDepth, depth, result, alpha, beta);
            return result;
        }''')
    body = body.replace('return nullValue;', '''{
                    if (is_mainthread() && TutorTrace::enabled)
                        TutorTrace::record(2, pos, ss, rootDepth, depth, nullValue, alpha, beta);
                    return nullValue;
                }''')
    body = once(body, '                    assert(value >= beta);  // Fail high', '''                    if (is_mainthread() && TutorTrace::enabled)
                        TutorTrace::record(0, pos, ss, rootDepth, depth, value, alpha, beta, move);
                    assert(value >= beta);  // Fail high''')
    text = text[:start] + body + text[end:]
    search.write_text(text, encoding='utf-8')


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Unexpected upstream source: expected one match for {old[:65]!r}')
    return text.replace(old, new, 1)
