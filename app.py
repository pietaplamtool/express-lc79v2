import os
import time
import json
import threading
from collections import Counter, defaultdict, deque
from typing import Optional

import requests
import psycopg2
import redis
from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

DB_URL = os.getenv("DB_URL")
REDIS_URL = os.getenv("REDIS_URL")
API_URL = os.getenv("API_URL")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
HISTORY_LIMIT = min(5600, int(os.getenv("HISTORY_LIMIT", "5600")))
EXPERT_FEEDBACK_LIMIT = int(os.getenv("EXPERT_FEEDBACK_LIMIT", "300"))
CACHE_REFRESH_SECONDS = int(os.getenv("CACHE_REFRESH_SECONDS", "300"))

RESULTS = ("TAI", "XIU")
EXPERTS = (
    "streak",
    "alternation",
    "pattern",
    "markov",
    "state",
    "break",
    "recent",
    "global",
)

# Light base weights. The Supreme layer adjusts them using live feedback,
# support, state relevance and ensemble agreement.
BASE_WEIGHTS = {
    "streak": 1.05,
    "alternation": 0.95,
    "pattern": 1.15,
    "markov": 1.05,
    "state": 1.25,
    "break": 1.15,
    "recent": 0.70,
    "global": 0.25,
}

runtime = {
    "lock": threading.RLock(),
    "last_prediction": None,
    "last_prediction_id": None,
    "expert_scores": defaultdict(lambda: deque(maxlen=EXPERT_FEEDBACK_LIMIT)),
}

cache = {
    "lock": threading.RLock(),
    "key": None,
    "seq": [],
    "stats": None,
    "built_at": 0.0,
}


def norm(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in {"T", "TAI", "APPLE", "TÁO"}:
        return "TAI"
    if s in {"X", "XIU", "MANGO", "XOAI", "XOÀI"}:
        return "XIU"
    return None


def short(v):
    return "T" if norm(v) == "TAI" else "X"


def get_db():
    if not DB_URL:
        raise RuntimeError("DB_URL chưa được cấu hình")
    return psycopg2.connect(DB_URL, connect_timeout=8)


def get_redis():
    if not REDIS_URL:
        return None
    return redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id BIGINT PRIMARY KEY,
            result TEXT,
            dice1 INT,
            dice2 INT,
            dice3 INT,
            point INT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_log (
            id BIGSERIAL PRIMARY KEY,
            session_id BIGINT UNIQUE,
            predicted TEXT NOT NULL,
            actual TEXT,
            confidence DOUBLE PRECISION,
            correct BOOLEAN,
            signals_json TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    try:
        cur.execute("ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS signals_json TEXT")
    except Exception:
        conn.rollback()
    conn.commit()
    cur.close()
    conn.close()


def restore_feedback():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signals_json, actual
            FROM prediction_log
            WHERE actual IS NOT NULL AND signals_json IS NOT NULL
            ORDER BY id DESC
            LIMIT %s
            """,
            (EXPERT_FEEDBACK_LIMIT,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        with runtime["lock"]:
            for raw, actual in reversed(rows):
                try:
                    data = json.loads(raw or "{}")
                except Exception:
                    data = {}
                actual = norm(actual)
                if actual not in RESULTS:
                    continue
                for name in EXPERTS:
                    info = data.get(name)
                    if isinstance(info, dict):
                        p = float(info.get("p", 0.5))
                        pred = "TAI" if p >= 0.5 else "XIU"
                        runtime["expert_scores"][name].append(int(pred == actual))
    except Exception as exc:
        print("restore_feedback:", exc)


def get_history():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, result, point, dice1, dice2, dice3, created_at
        FROM sessions
        ORDER BY id DESC
        LIMIT %s
        """,
        (HISTORY_LIMIT,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    rows.reverse()
    seq = [norm(r[1]) for r in rows]
    clean = [(rows[i], seq[i]) for i in range(len(rows)) if seq[i] in RESULTS]
    return [r for r, _ in clean], [s for _, s in clean]


def fetch_and_save():
    if not API_URL:
        return
    try:
        rds = get_redis()
        last_id = 0
        if rds is not None:
            try:
                last_id = int(rds.get("last_id") or 0)
            except Exception:
                last_id = 0
        if last_id <= 0:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(MAX(id),0) FROM sessions")
            last_id = int(cur.fetchone()[0] or 0)
            cur.close()
            conn.close()

        resp = requests.get(API_URL, timeout=8)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("list", []) if isinstance(payload, dict) else payload
        if not data:
            return

        valid = []
        for d in data:
            try:
                rid = int(d.get("id"))
                result = norm(d.get("resultTruyenThong", d.get("result")))
                dices = d.get("dices") or []
                if rid <= 0 or result not in RESULTS:
                    continue
                valid.append((
                    rid,
                    result,
                    dices[0] if len(dices) > 0 else None,
                    dices[1] if len(dices) > 1 else None,
                    dices[2] if len(dices) > 2 else None,
                    d.get("point"),
                ))
            except Exception:
                continue

        valid.sort(key=lambda x: x[0])
        new = [x for x in valid if x[0] > last_id]
        if not new:
            return

        conn = get_db()
        cur = conn.cursor()
        for rid, result, d1, d2, d3, point in new:
            cur.execute(
                """
                INSERT INTO sessions(id,result,dice1,dice2,dice3,point)
                VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO NOTHING
                """,
                (rid, result, d1, d2, d3, point),
            )
        conn.commit()
        cur.close()
        conn.close()
        if rds is not None:
            try:
                rds.set("last_id", new[-1][0])
            except Exception:
                pass
        with cache["lock"]:
            cache["key"] = None
        print(f"[+] saved {len(new)} rounds, latest={new[-1][0]}")
    except Exception as exc:
        print("fetch_and_save:", exc)


# -----------------------------------------------------------------------------
# Ultra-light feature/state engine
# -----------------------------------------------------------------------------
def run_len(seq):
    if not seq:
        return 0
    last = seq[-1]
    n = 0
    for x in reversed(seq):
        if x != last:
            break
        n += 1
    return n


def run_profile(seq):
    if not seq:
        return []
    out = []
    last = seq[0]
    n = 1
    for x in seq[1:]:
        if x == last:
            n += 1
        else:
            out.append((last, n))
            last, n = x, 1
    out.append((last, n))
    return out


def classify_state(seq):
    if len(seq) < 6:
        return "UNKNOWN"
    r = run_len(seq)
    runs = run_profile(seq)[-6:]
    lens = [n for _, n in runs]
    alt_tail = 1
    for i in range(len(seq) - 1, 0, -1):
        if seq[i] != seq[i - 1]:
            alt_tail += 1
        else:
            break
    if r >= 9:
        return "STREAK_9P"
    if r >= 7:
        return "STREAK_7_8"
    if r >= 5:
        return "STREAK_5_6"
    if r >= 3:
        return "STREAK_3_4"
    if alt_tail >= 10:
        return "ALT_LONG"
    if alt_tail >= 6:
        return "ALT_6P"
    if lens[-4:] == [2, 2, 2, 2]:
        return "PAIR_2_2"
    if lens[-3:] == [3, 3, 3]:
        return "TRIPLE_3_3"
    if lens[-3:] == [1, 2, 1]:
        return "MIX_1_2_1"
    if lens[-3:] == [2, 1, 2]:
        return "MIX_2_1_2"
    if len(lens) >= 2 and lens[-2] >= 4 and lens[-1] <= 2:
        return "BREAK_LONG"
    if len(lens) >= 3 and lens[-3] >= 3 and lens[-2] == 1 and lens[-1] <= 2:
        return "BREAK_DOUBLE"
    return "MIXED"


def state_key(seq):
    if not seq:
        return ("UNKNOWN", 0, "TAI")
    return (classify_state(seq), min(run_len(seq), 12), seq[-1])


# -----------------------------------------------------------------------------
# Build one compact knowledge base from all 5600 rounds.
# -----------------------------------------------------------------------------
def build_knowledge(seq):
    # O(n) incremental tables. The old V4 repeatedly rebuilt prefix state,
    # which was needlessly expensive on Render Free.
    patterns = {n: defaultdict(lambda: [1.0, 1.0]) for n in (3, 5, 8, 10, 15, 20)}
    markov = {n: defaultdict(lambda: [1.0, 1.0]) for n in (1, 2, 3)}
    states = defaultdict(lambda: [1.0, 1.0])
    breaks = defaultdict(lambda: [1.0, 1.0])
    streaks = defaultdict(lambda: [1.0, 1.0])
    alternations = defaultdict(lambda: [1.0, 1.0])

    run_len_now = 0
    last = None
    alt_tail = 0
    switches = 0
    transitions = 0
    runs = []  # compact recent run lengths only
    total_tai = 0

    for i, nxt in enumerate(seq):
        prefix_len = i
        if prefix_len > 0:
            # --- Pattern expert ---
            for n in patterns:
                if prefix_len >= n:
                    patterns[n][tuple(seq[i-n:i])][0 if nxt == 'TAI' else 1] += 1.0

            # --- Markov expert ---
            for n in markov:
                if prefix_len >= n:
                    markov[n][tuple(seq[i-n:i])][0 if nxt == 'TAI' else 1] += 1.0

            # --- State/streak/alternation experts from incremental context ---
            if last is None:
                run_len_now = 1
            else:
                run_len_now = run_len_now + 1 if nxt == last else 1

            current_last = last if last is not None else nxt
            state_name = 'UNKNOWN'
            if prefix_len >= 6:
                if run_len_now >= 9:
                    state_name = 'STREAK_9P'
                elif run_len_now >= 7:
                    state_name = 'STREAK_7_8'
                elif run_len_now >= 5:
                    state_name = 'STREAK_5_6'
                elif run_len_now >= 3:
                    state_name = 'STREAK_3_4'
                elif alt_tail >= 10:
                    state_name = 'ALT_LONG'
                elif alt_tail >= 6:
                    state_name = 'ALT_6P'
                elif len(runs) >= 4 and runs[-4:] == [2, 2, 2, 2]:
                    state_name = 'PAIR_2_2'
                elif len(runs) >= 3 and runs[-3:] == [3, 3, 3]:
                    state_name = 'TRIPLE_3_3'
                elif len(runs) >= 3 and runs[-3:] == [1, 2, 1]:
                    state_name = 'MIX_1_2_1'
                elif len(runs) >= 3 and runs[-3:] == [2, 1, 2]:
                    state_name = 'MIX_2_1_2'
                elif len(runs) >= 2 and runs[-2] >= 4 and runs[-1] <= 2:
                    state_name = 'BREAK_LONG'
                elif len(runs) >= 3 and runs[-3] >= 3 and runs[-2] == 1 and runs[-1] <= 2:
                    state_name = 'BREAK_DOUBLE'
                states[(state_name, min(run_len_now, 12), current_last)][0 if nxt == 'TAI' else 1] += 1.0

            if run_len_now >= 3:
                streaks[(min(run_len_now, 10), current_last)][0 if nxt == 'TAI' else 1] += 1.0

            rate = round(switches / transitions, 1) if transitions else 0.0
            if prefix_len >= 4:
                alternations[rate][0 if nxt == 'TAI' else 1] += 1.0

            # Break markers are based on the immediately preceding completed runs.
            if len(runs) >= 2 and runs[-2] >= 4 and runs[-1] <= 2:
                key = ('LONG', min(runs[-2], 10), runs[-1], current_last)
                breaks[key][0 if nxt == 'TAI' else 1] += 1.0
            if len(runs) >= 3 and runs[-3] >= 3 and runs[-2] == 1 and runs[-1] <= 2:
                key = ('DOUBLE', min(runs[-3], 10), current_last)
                breaks[key][0 if nxt == 'TAI' else 1] += 1.0

        # Update prefix state AFTER scoring the current next-result label.
        if last is None:
            run_len_now = 1
            alt_tail = 1
        elif nxt != last:
            switches += 1
            transitions += 1
            runs.append(run_len_now)
            if len(runs) > 6:
                runs.pop(0)
            run_len_now = 1
            alt_tail += 1
        else:
            transitions += 1
            run_len_now += 1
            alt_tail = 1
        last = nxt
        total_tai += int(nxt == 'TAI')

    return {
        'patterns': patterns,
        'markov': markov,
        'states': states,
        'breaks': breaks,
        'streaks': streaks,
        'alternations': alternations,
        'global': Counter(seq),
    }


def prob_from_counts(pair):
    a, b = pair
    return a / (a + b)


def live_stats(seq):
    # Small current-context stats, independent of the full KB.
    recent20 = seq[-20:]
    recent50 = seq[-50:]
    return {
        "run": run_len(seq),
        "state": classify_state(seq),
        "recent20": (recent20.count("TAI") + 1) / (len(recent20) + 2) if recent20 else 0.5,
        "recent50": (recent50.count("TAI") + 1) / (len(recent50) + 2) if recent50 else 0.5,
    }


# -----------------------------------------------------------------------------
# Eight small expert AIs
# -----------------------------------------------------------------------------
def expert_streak(seq, kb):
    r = run_len(seq)
    if r < 3:
        return 0.5, 0, "no-streak"
    key = (min(r, 10), seq[-1])
    pair = kb["streaks"].get(key)
    if not pair:
        return 0.5, 0, f"streak-{r}"
    return prob_from_counts(pair), int(sum(pair)), f"streak-{r}"


def expert_alternation(seq, kb):
    if len(seq) < 5:
        return 0.5, 0, "alternation"
    rate = round(sum(seq[i] != seq[i - 1] for i in range(1, len(seq))) / (len(seq) - 1), 1)
    pair = kb["alternations"].get(rate)
    if not pair:
        return 0.5, 0, f"switch-{rate}"
    return prob_from_counts(pair), int(sum(pair)), f"switch-{rate}"


def expert_pattern(seq, kb):
    results = []
    for n in (3, 5, 8, 10, 15, 20):
        if len(seq) < n:
            continue
        pair = kb["patterns"][n].get(tuple(seq[-n:]))
        if not pair:
            continue
        support = int(sum(pair))
        if support >= 8:
            # Longer exact contexts get more authority, but support still matters.
            length_weight = 0.7 + 0.07 * n
            results.append((prob_from_counts(pair), support, length_weight))
    if not results:
        return 0.5, 0, "pattern-none"
    num = sum(p * w for p, s, w in results if s >= 8)
    den = sum(w for p, s, w in results if s >= 8)
    support = sum(s for _, s, _ in results)
    return (num / den if den else 0.5), support, "pattern-multi"


def expert_markov(seq, kb):
    parts = []
    for n in (1, 2, 3):
        if len(seq) < n:
            continue
        pair = kb["markov"][n].get(tuple(seq[-n:]))
        if pair:
            parts.append((prob_from_counts(pair), int(sum(pair)), n))
    if not parts:
        return 0.5, 0, "markov-none"
    weights = {1: 0.7, 2: 1.0, 3: 1.15}
    num = sum(p * weights[n] for p, s, n in parts if s >= 6)
    den = sum(weights[n] for p, s, n in parts if s >= 6)
    support = sum(s for _, s, _ in parts)
    return (num / den if den else 0.5), support, "markov-ensemble"


def expert_state(seq, kb):
    if len(seq) < 6:
        return 0.5, 0, "state-unknown"
    key = state_key(seq)
    pair = kb["states"].get(key)
    if not pair:
        # fall back to state-only key, ignoring exact run length
        state_only = key[0]
        candidates = []
        for k, v in kb["states"].items():
            if k[0] == state_only:
                candidates.append(v)
        if not candidates:
            return 0.5, 0, state_only
        a = sum(x[0] for x in candidates)
        b = sum(x[1] for x in candidates)
        return a / (a + b), int(a + b), state_only
    return prob_from_counts(pair), int(sum(pair)), key[0]


def expert_break(seq, kb):
    if len(seq) < 8:
        return 0.5, 0, "break-none"
    rp = run_profile(seq)
    key = None
    label = "break-none"
    if len(rp) >= 2 and rp[-2][1] >= 4 and rp[-1][1] <= 2:
        key = ("LONG", min(rp[-2][1], 10), rp[-1][1], seq[-1])
        label = "long-break"
    elif len(rp) >= 3 and rp[-3][1] >= 3 and rp[-2][1] == 1 and rp[-1][1] <= 2:
        key = ("DOUBLE", min(rp[-3][1], 10), seq[-1])
        label = "double-break"
    if key is None:
        return 0.5, 0, label
    pair = kb["breaks"].get(key)
    if not pair:
        return 0.5, 0, label
    return prob_from_counts(pair), int(sum(pair)), label


def expert_recent(seq, kb):
    if not seq:
        return 0.5, 0, "recent"
    p20 = (seq[-20:].count("TAI") + 1) / (min(20, len(seq)) + 2)
    p50 = (seq[-50:].count("TAI") + 1) / (min(50, len(seq)) + 2)
    return 0.65 * p20 + 0.35 * p50, min(50, len(seq)), "recent"


def expert_global(seq, kb):
    total = len(seq)
    if not total:
        return 0.5, 0, "global"
    c = kb["global"]
    return (c["TAI"] + 2) / (total + 4), total, "global"


def all_experts(seq, kb):
    funcs = {
        "streak": expert_streak,
        "alternation": expert_alternation,
        "pattern": expert_pattern,
        "markov": expert_markov,
        "state": expert_state,
        "break": expert_break,
        "recent": expert_recent,
        "global": expert_global,
    }
    out = {}
    for name, fn in funcs.items():
        try:
            p, support, label = fn(seq, kb)
        except Exception:
            p, support, label = 0.5, 0, "error"
        out[name] = {"p": float(max(0.02, min(0.98, p))), "support": int(support), "label": label}
    return out


# -----------------------------------------------------------------------------
# Big AI: router / manager
# -----------------------------------------------------------------------------
def learned_reliability(name):
    base = BASE_WEIGHTS[name]
    with runtime["lock"]:
        hist = list(runtime["expert_scores"].get(name, ()))
    if len(hist) < 8:
        return base
    hit = sum(hist) / len(hist)
    # Gentle adaptation; do not overreact to short losing streaks.
    return base * (0.75 + 1.0 * hit)


def state_specialist_boost(name, state_name):
    if name == "break" and state_name.startswith("BREAK"):
        return 1.55
    if name == "streak" and state_name.startswith("STREAK"):
        return 1.35
    if name == "alternation" and state_name.startswith(("ALT", "PAIR_", "TRIPLE_")):
        return 1.30
    if name == "state":
        return 1.35
    if name == "pattern" and state_name == "MIXED":
        return 1.20
    return 1.0


def supreme_review(seq, experts):
    state_name = classify_state(seq)
    weighted = []
    for name, info in experts.items():
        p = info["p"]
        support = info["support"]
        base = learned_reliability(name)
        support_factor = 0.55 + 0.45 * min(1.0, (support + 2) / 40.0)
        state_factor = state_specialist_boost(name, state_name)
        # Ignore a weak 50/50 expert unless most experts are also uncertain.
        signal_strength = abs(p - 0.5) * 2
        confidence_factor = 0.65 + 0.70 * signal_strength
        eff = base * support_factor * state_factor * confidence_factor
        weighted.append((name, p, eff))

    denominator = sum(w for _, _, w in weighted) or 1.0
    big_p = sum(p * w for _, p, w in weighted) / denominator
    agree = sum(w for _, p, w in weighted if (p >= 0.5) == (big_p >= 0.5)) / denominator

    # Supreme reviewer: strongest well-supported specialist gets a controlled
    # influence when the committee is strongly divided. This prevents the global
    # prior from overpowering specific state evidence.
    ranked = sorted(weighted, key=lambda x: x[2] * abs(x[1] - 0.5), reverse=True)
    if ranked:
        top_name, top_p, top_w = ranked[0]
        top_strength = abs(top_p - 0.5) * 2
        if top_strength >= 0.50 and agree < 0.62 and top_w >= 1.0:
            big_p = 0.78 * big_p + 0.22 * top_p

    # Final review: if the committee is almost exactly neutral, don't invent an
    # artificial edge. Still make a decision (continuous mode) using a stable tie
    # breaker based on the strongest supported expert, then last result.
    if 0.495 <= big_p <= 0.505:
        candidates = [x for x in ranked if x[2] >= 0.9 and abs(x[1] - 0.5) >= 0.04]
        if candidates:
            big_p = 0.65 * big_p + 0.35 * candidates[0][1]
        else:
            big_p = 0.505 if seq[-1] == "TAI" else 0.495

    pred = "TAI" if big_p >= 0.5 else "XIU"
    strength = abs(big_p - 0.5) * 2
    confidence = 0.50 + 0.46 * strength * (0.55 + 0.45 * agree)
    confidence = max(0.50, min(0.96, confidence))

    return {
        "predicted": pred,
        "p_tai": round(big_p, 6),
        "confidence": round(confidence, 6),
        "state": state_name,
        "agreement": round(agree, 4),
        "ranking": [
            {"expert": n, "p_tai": round(p, 4), "weight": round(w, 4)}
            for n, p, w in ranked
        ],
    }


def ensure_cache(rows, seq):
    latest = int(rows[-1][0]) if rows else 0
    first = int(rows[0][0]) if rows else 0
    key = (first, latest, len(seq))
    now = time.time()
    with cache["lock"]:
        if cache["key"] == key and cache["stats"] is not None and now - cache["built_at"] < CACHE_REFRESH_SECONDS:
            return cache["stats"]
        stats = build_knowledge(seq)
        cache["key"] = key
        cache["seq"] = list(seq)
        cache["stats"] = stats
        cache["built_at"] = now
        return stats


def log_prediction(session_id, prediction, confidence, experts):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM prediction_log WHERE session_id=%s", (session_id,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return
    payload = {}
    for name, info in experts.items():
        payload[name] = {"p": info["p"], "support": info["support"]}
    cur.execute(
        """
        INSERT INTO prediction_log(session_id,predicted,confidence,signals_json)
        VALUES(%s,%s,%s,%s)
        ON CONFLICT(session_id) DO NOTHING
        """,
        (session_id, prediction, float(confidence), json.dumps(payload)),
    )
    conn.commit()
    cur.close()
    conn.close()


def settle_predictions():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id,p.session_id,p.predicted,p.signals_json,s.result
        FROM prediction_log p
        JOIN sessions s ON s.id=p.session_id
        WHERE p.actual IS NULL
        ORDER BY p.session_id
        LIMIT 100
        """
    )
    rows = cur.fetchall()
    for pid, sid, predicted, raw, actual in rows:
        actual = norm(actual)
        correct = norm(predicted) == actual
        cur.execute(
            "UPDATE prediction_log SET actual=%s,correct=%s WHERE id=%s",
            (actual, correct, pid),
        )
        try:
            data = json.loads(raw or "{}")
        except Exception:
            data = {}
        with runtime["lock"]:
            for name in EXPERTS:
                info = data.get(name, {})
                p = float(info.get("p", 0.5))
                expert_pred = "TAI" if p >= 0.5 else "XIU"
                runtime["expert_scores"][name].append(int(expert_pred == actual))
    conn.commit()
    cur.close()
    conn.close()


def accuracy_metrics():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT correct
        FROM prediction_log
        WHERE actual IS NOT NULL
        ORDER BY id DESC
        LIMIT %s
        """,
        (EXPERT_FEEDBACK_LIMIT,),
    )
    vals = [int(bool(x[0])) for x in cur.fetchall()]
    cur.close()
    conn.close()
    if not vals:
        return {
            "samples": 0,
            "accuracy": None,
            "current_win_streak": 0,
            "current_loss_streak": 0,
            "max_win_streak": 0,
            "max_loss_streak": 0,
        }
    # DB is newest first. Reverse for chronological streak calculation.
    vals.reverse()
    current = 0
    current_loss = 0
    max_win = 0
    max_loss = 0
    for x in vals:
        if x:
            current += 1
            current_loss = 0
        else:
            current_loss += 1
            current = 0
        max_win = max(max_win, current)
        max_loss = max(max_loss, current_loss)
    return {
        "samples": len(vals),
        "accuracy": round(100.0 * sum(vals) / len(vals), 2),
        "current_win_streak": current,
        "current_loss_streak": current_loss,
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
    }


@app.route("/predict")
def predict():
    try:
        settle_predictions()
        rows, seq = get_history()
        if len(seq) < 100:
            return jsonify({"status": "WAIT", "learned": len(seq), "reason": "Chưa đủ dữ liệu."})

        kb = ensure_cache(rows, seq)
        experts = all_experts(seq, kb)
        decision = supreme_review(seq, experts)
        latest_id = int(rows[-1][0])
        target_id = latest_id + 1
        log_prediction(target_id, decision["predicted"], decision["confidence"], experts)
        with runtime["lock"]:
            runtime["last_prediction"] = decision["predicted"]
            runtime["last_prediction_id"] = target_id

        return jsonify({
            "status": "PREDICT",
            "prediction_mode": "CONTINUOUS",
            "predict": decision["predicted"],
            "predict_short": short(decision["predicted"]),
            "confidence": decision["confidence"],
            "probability": {
                "tai": decision["p_tai"],
                "xiu": round(1.0 - decision["p_tai"], 6),
            },
            "learned_rounds": len(seq),
            "target_session_id": target_id,
            "latest_session_id": latest_id,
            "supreme_ai": {
                "state": decision["state"],
                "agreement": decision["agreement"],
                "expert_ranking": decision["ranking"],
            },
            "experts": experts,
            "metrics": accuracy_metrics(),
        })
    except Exception as exc:
        return jsonify({"status": "ERROR", "reason": str(exc)}), 500


@app.route("/experts")
def experts_endpoint():
    try:
        rows, seq = get_history()
        if len(seq) < 100:
            return jsonify({"status": "WAIT", "learned": len(seq)})
        kb = ensure_cache(rows, seq)
        ex = all_experts(seq, kb)
        decision = supreme_review(seq, ex)
        return jsonify({
            "status": "ready",
            "learned_rounds": len(seq),
            "state": decision["state"],
            "experts": ex,
            "supreme": decision,
        })
    except Exception as exc:
        return jsonify({"status": "ERROR", "reason": str(exc)}), 500


@app.route("/stats")
def stats():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sessions")
        total = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM sessions WHERE result='TAI'")
        tai = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM sessions WHERE result='XIU'")
        xiu = int(cur.fetchone()[0] or 0)
        cur.close()
        conn.close()
        return jsonify({
            "total_rounds_learned": min(total, HISTORY_LIMIT),
            "total_database_rounds": total,
            "tai": tai,
            "xiu": xiu,
            "prediction_metrics": accuracy_metrics(),
            "status": "running" if total else "waiting_for_data",
        })
    except Exception as exc:
        return jsonify({"status": "ERROR", "reason": str(exc)}), 500


@app.route("/accuracy")
def accuracy():
    try:
        return jsonify({"status": "ready", **accuracy_metrics()})
    except Exception as exc:
        return jsonify({"status": "ERROR", "reason": str(exc)}), 500


@app.route("/state_backtest")
def state_backtest():
    try:
        rows, seq = get_history()
        if len(seq) < 120:
            return jsonify({"status": "WAIT", "samples": len(seq)})
        # Lightweight state-only validation on the last 600 points.
        start = max(60, len(seq) - 600)
        table = defaultdict(lambda: [0, 0])
        correct = total = 0
        by_state = defaultdict(lambda: [0, 0])
        for i in range(start, len(seq)):
            hist = seq[:i]
            state_name = classify_state(hist)
            if len(hist) >= 6:
                # nearest prior same-state samples via a rolling suffix lookup
                key = state_key(hist)
                pair = table.get(key)
                if pair and sum(pair) >= 8:
                    pred = "TAI" if pair[0] >= pair[1] else "XIU"
                    total += 1
                    ok = pred == seq[i]
                    correct += int(ok)
                    by_state[state_name][0] += int(ok)
                    by_state[state_name][1] += 1
            table[state_key(hist)][0 if seq[i] == "TAI" else 1] += 1
        details = {}
        for name, (ok, n) in by_state.items():
            details[name] = {"samples": n, "accuracy": round(100.0 * ok / n, 2) if n else None}
        return jsonify({
            "status": "ready",
            "samples": total,
            "accuracy": round(100.0 * correct / total, 2) if total else None,
            "states": details,
        })
    except Exception as exc:
        return jsonify({"status": "ERROR", "reason": str(exc)}), 500


@app.route("/history")
def history():
    try:
        rows, _ = get_history()
        recent = rows[-20:]
        return jsonify({
            "status": "ready",
            "recent": [
                {
                    "id": int(r[0]),
                    "result": norm(r[1]),
                    "point": r[2],
                    "dices": [r[3], r[4], r[5]],
                }
                for r in recent
            ],
        })
    except Exception as exc:
        return jsonify({"status": "ERROR", "reason": str(exc)}), 500


@app.route("/history50")
def history50():
    try:
        rows, _ = get_history()
        recent = rows[-50:]
        return jsonify({
            "status": "ready",
            "total_rounds": len(recent),
            "tai": sum(norm(r[1]) == "TAI" for r in recent),
            "xiu": sum(norm(r[1]) == "XIU" for r in recent),
            "history": "".join(short(r[1]) for r in recent),
            "history_data": [
                {
                    "id": int(r[0]),
                    "result": norm(r[1]),
                    "point": r[2],
                    "dices": [r[3], r[4], r[5]],
                    "time": r[6].strftime("%d-%m-%Y %H:%M:%S") if r[6] else None,
                }
                for r in recent
            ],
        })
    except Exception as exc:
        return jsonify({"status": "ERROR", "reason": str(exc)}), 500


# ===== ENDPOINT STREAK_20 =====
@app.route("/streak_20")
def streak_20():
    try:
        rows, _ = get_history()
        recent = rows[-20:]
        if len(recent) < 2:
            return jsonify({"error": "Not enough data"}), 400
        seq = [norm(r[1]) for r in recent]
        max_tai = 0
        max_xiu = 0
        current_tai = 0
        current_xiu = 0
        for res in seq:
            if res == "TAI":
                current_tai += 1
                current_xiu = 0
                max_tai = max(max_tai, current_tai)
            else:
                current_xiu += 1
                current_tai = 0
                max_xiu = max(max_xiu, current_xiu)
        return jsonify({
            "max_win_streak_20": max_tai,
            "max_loss_streak_20": max_xiu,
            "total_tai": seq.count("TAI"),
            "total_xiu": seq.count("XIU"),
            "status": "ready",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ===== ENDPOINT SUMMARY_50 =====
@app.route("/summary_50")
def summary_50():
    try:
        rows, _ = get_history()
        recent = rows[-50:]
        if len(recent) < 10:
            return jsonify({"error": "Not enough data"}), 400
        seq = [norm(r[1]) for r in recent]
        tai = sum(x == "TAI" for x in seq)
        xiu = len(seq) - tai
        history_str = "".join(short(x) for x in seq)
        win = max(tai, xiu)
        lose = min(tai, xiu)
        return jsonify({
            "total_rounds": len(seq),
            "win": win,
            "lose": lose,
            "win_rate": round(100.0 * win / len(seq), 1),
            "history": history_str,
            "status": "ready",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ===== CHẠY AI VÀ TELEGRAM BOT SONG SONG =====
def run_bot():
    try:
        os.system("python bot.py")
    except Exception as e:
        print(f"Lỗi bot: {e}")

# Khởi chạy bot trong thread riêng
if os.environ.get("RUN_BOT", "true").lower() == "true":
    threading.Thread(target=run_bot, daemon=True).start()
    print("[+] Telegram Bot đã được kích hoạt trong thread riêng.")

def worker():
    try:
        init_db()
        restore_feedback()
    except Exception as exc:
        print("init:", exc)
    while True:
        try:
            fetch_and_save()
            settle_predictions()
        except Exception as exc:
            print("worker:", exc)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True, name="data-worker").start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)