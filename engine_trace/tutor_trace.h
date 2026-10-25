/*
 * Experimental Stockfish 19 search instrumentation, Chess Tutor contributors.
 * Copyright (C) 2004-2026 The Stockfish developers; modifications (C) 2026.
 * SPDX-License-Identifier: GPL-3.0-or-later
 * This header is compiled into a separate Stockfish executable, not the web app.
 */
#ifndef TUTOR_TRACE_H
#define TUTOR_TRACE_H
#include <array>
#include <atomic>
#include <sstream>
#include <string>
#include "misc.h"
#include "position.h"
#include "search.h"
#include "score.h"
#include "uci.h"

namespace Stockfish::TutorTrace {
inline std::atomic<bool> enabled{false};
inline std::array<unsigned long long, 5> counts{};
inline std::array<unsigned, 5> samples{};
inline unsigned sequence = 0;
inline unsigned emitted = 0;
inline unsigned dropped = 0;
constexpr unsigned MAX_EVENTS = 12000;
constexpr std::array<const char*, 5> kinds = {
    "beta_cutoff", "tt_cutoff", "null_cutoff", "futility_cutoff", "root_probe"};

inline void send(const std::string& json) {
    sync_cout << "info string tutortrace " << json << sync_endl;
}

inline void begin() {
    counts.fill(0); samples.fill(0); sequence = emitted = dropped = 0;
    send("{\"kind\":\"start\",\"schema\":1,\"engine\":\"Stockfish 19 TutorTrace v1\","
         "\"scope\":\"single-thread, root probes and main-search cutoffs at ply 1-3\"}");
}

inline void next_depth() { samples.fill(0); }

inline void record(int kind, const Position& pos, const Search::Stack* ss,
                   int rootDepth, int depth, Value value, Value alpha, Value beta,
                   Move move = Move::none(), const Search::PVMoves* pv = nullptr) {
    if (ss->ply > 3) return;
    ++counts[kind];
    // Bound both the stream and the overhead. Cutoffs are a depth-stratified sample.
    if ((kind != 4 && samples[kind]++ >= 8) || emitted >= MAX_EVENTS) {
        ++dropped; return;
    }
    ++emitted;
    std::ostringstream out;
    const char* bound = value >= beta ? "lower" : value <= alpha ? "upper" : "exact";
    out << "{\"kind\":\"" << kinds[kind] << "\",\"seq\":" << ++sequence
        << ",\"iteration\":" << rootDepth << ",\"depth\":" << depth
        << ",\"ply\":" << ss->ply << ",\"raw\":" << value
        << ",\"alpha\":" << alpha << ",\"beta\":" << beta
        << ",\"bound\":\"" << bound << "\",\"path\":[";
    for (int i = 0; i < ss->ply; ++i) {
        if (i) out << ',';
        out << '"' << UCIEngine::move((ss - ss->ply + i)->currentMove, pos.is_chess960()) << '"';
    }
    out << "],\"move\":\"" << UCIEngine::move(move, pos.is_chess960()) << '"';
    if (kind == 4) {
        out << ",\"score_uci\":\"" << UCIEngine::format_score(Score(value, pos)) << "\",\"pv\":["
            << '"' << UCIEngine::move(move, pos.is_chess960()) << '"';
        if (pv) {
            unsigned n = 0;
            for (Move child : *pv) {
                if (++n > 15) break;
                out << ",\"" << UCIEngine::move(child, pos.is_chess960()) << '"';
            }
        }
        out << ']';
    }
    out << '}'; send(out.str());
}

inline void iteration(const Position& pos, const Search::RootMoves& moves, int depth) {
    std::ostringstream out;
    out << "{\"kind\":\"iteration\",\"depth\":" << depth << ",\"candidates\":[";
    for (unsigned i = 0; i < 3 && i < moves.size(); ++i) {
        if (i) out << ',';
        const auto& m = moves[i];
        out << "{\"rank\":" << i + 1 << ",\"move\":\"" << UCIEngine::move(m.pv[0], pos.is_chess960())
            << "\",\"score_uci\":\"" << UCIEngine::format_score(Score(m.score, pos))
            << "\",\"bound\":\"" << (m.inexactLower ? "lower" : m.inexactUpper ? "upper" : "exact")
            << "\",\"pv\":[";
        unsigned n = 0;
        for (Move child : m.pv) {
            if (n >= 16) break;
            if (n++) out << ',';
            out << '"' << UCIEngine::move(child, pos.is_chess960()) << '"';
        }
        out << "]}";
    }
    out << "]}"; send(out.str());
}

inline void finish() {
    std::ostringstream out;
    out << "{\"kind\":\"end\",\"sampled_events\":" << emitted
        << ",\"omitted_events\":" << dropped << ",\"counts\":{";
    for (unsigned i = 0; i < kinds.size(); ++i) {
        if (i) out << ',';
        out << '"' << kinds[i] << "\":" << counts[i];
    }
    out << "}}"; send(out.str());
}
}
#endif
