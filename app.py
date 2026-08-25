import os
import time
import threading
import json
import math
import warnings
from collections import Counter, deque, defaultdict
from typing import Dict, List, Tuple, Optional

import requests
import psycopg2
import redis
import numpy as np
import pandas as pd
from flask import Flask, jsonify
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

app = Flask(__name__)

DB_URL = os.getenv("DB_URL")
REDIS_URL = os.getenv("REDIS_URL")
API_URL = os.getenv("API_URL")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "5600"))
MAX_MODEL_HISTORY = int(os.getenv("MAX_MODEL_HISTORY", "5600"))
PREDICTION_LOG_LIMIT = int(os.getenv("PREDICTION_LOG_LIMIT", "500"))

# Pattern sizes deliberately use multiple horizons instead of a single 10-result match.
PATTERN_LENGTHS = (3, 5, 8, 10, 15, 20)
MARKOV_ORDERS = (1, 2, 3)
RESULTS = ("TAI", "XIU")

# -----------------------------------------------------------------------------
# Runtime memory
# -----------------------------------------------------------------------------
state = {
    "last_seen_id": 0,
    "last_prediction_id": None,
    "last_prediction": None,
    "last_prediction_created_at": None,
    "correct": 0,
    "wrong": 0,
    "current_win_streak": 0,
    "current_loss_streak": 0,
    "max_win_streak": 0,
    "max_loss_streak": 0,
    "recent_outcomes": deque(maxlen=100),
    "model_recent_scores": defaultdict(lambda: deque(maxlen=60)),
    "lock": threading.RLock(),
}
model_cache = {
    "data_key": None,
    "sequence": [],
    "predictions": None,
    "updated_at": 0.0,
}
MODEL_LOCK = threading.RLock()


def normalize_result(value) -> Optional[str]:
    """Normalize T/X, TAI/XIU and common textual variants to TAI/XIU."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if s in {"TAI", "T", "APPLE", "TÁO"}:
        return "TAI"
    if s in {"XIU", "X", "MANGO", "XOAI", "XOÀI"}:
        return "XIU"
    return None


def short_result(value: str) -> str:
    return "T" if normalize_result(value) == "TAI" else "X"


def get_db():
    if not DB_URL:
        raise RuntimeError("DB_URL chưa được cấu hình.")
    return psycopg2.connect(DB_URL, connect_timeout=8)


def get_redis():
    if not REDIS_URL:
        return None
    return redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)


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


def restore_adaptive_scores():
    """Restore recent component feedback from PostgreSQL after a process restart."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signals_json, correct
            FROM prediction_log
            WHERE actual IS NOT NULL
              AND signals_json IS NOT NULL
            ORDER BY id DESC
            LIMIT 500
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        with state["lock"]:
            for signals_json, correct in reversed(rows):
                try:
                    names = json.loads(signals_json or "{}").keys()
                except Exception:
                    names = []
                for name in names:
                    state["model_recent_scores"][name].append(1 if correct else 0)
    except Exception as exc:
        print(f"[-] restore_adaptive_scores: {exc}")


def get_last_id_from_db() -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM sessions")
    value = int(cur.fetchone()[0] or 0)
    cur.close()
    conn.close()
    return value


def fetch_and_save():
    """Fetch upstream data and insert only unseen rounds."""
    try:
        if not API_URL:
            return

        rds = get_redis()
        last_id = 0
        if rds is not None:
            try:
                last_id = int(rds.get("last_id") or 0)
            except Exception:
                last_id = 0

        if last_id <= 0:
            last_id = get_last_id_from_db()

        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("list", []) if isinstance(payload, dict) else payload
        if not data:
            return

        valid = []
        for item in data:
            try:
                rid = int(item.get("id"))
                result = normalize_result(item.get("resultTruyenThong", item.get("result")))
                dices = item.get("dices") or [None, None, None]
                point = item.get("point")
                if result is None or rid <= 0:
                    continue
                valid.append(
                    {
                        "id": rid,
                        "result": result,
                        "dice1": dices[0] if len(dices) > 0 else None,
                        "dice2": dices[1] if len(dices) > 1 else None,
                        "dice3": dices[2] if len(dices) > 2 else None,
                        "point": point,
                    }
                )
            except Exception:
                continue

        valid.sort(key=lambda x: x["id"])
        new_records = [d for d in valid if d["id"] > last_id]
        if not new_records:
            return

        conn = get_db()
        cur = conn.cursor()
        for d in new_records:
            cur.execute(
                """
                INSERT INTO sessions (id, result, dice1, dice2, dice3, point)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    d["id"],
                    d["result"],
                    d["dice1"],
                    d["dice2"],
                    d["dice3"],
                    d["point"],
                ),
            )
        conn.commit()
        cur.close()
        conn.close()

        newest = new_records[-1]["id"]
        if rds is not None:
            try:
                rds.set("last_id", newest)
            except Exception:
                pass

        with MODEL_LOCK:
            model_cache["data_key"] = None

        print(f"[+] Đã lưu {len(new_records)} ván mới, latest_id={newest}")
    except Exception as exc:
        print(f"[-] fetch_and_save: {exc}")


# -----------------------------------------------------------------------------
# Prediction engines
# -----------------------------------------------------------------------------
def clamp_prob(p: float, low: float = 0.02, high: float = 0.98) -> float:
    return float(max(low, min(high, p)))


def weighted_binary_probability(values: List[Tuple[str, float]]) -> Optional[float]:
    if not values:
        return None
    total = sum(max(0.0, w) for _, w in values)
    if total <= 0:
        return None
    tai = sum(w for result, w in values if result == "TAI")
    return tai / total


def ewma_probability(seq: List[str], decay: float = 0.96) -> float:
    p = 0.5
    for result in seq:
        y = 1.0 if result == "TAI" else 0.0
        p = decay * p + (1.0 - decay) * y
    return clamp_prob(p)


def recent_bias_probability(seq: List[str], window: int = 30) -> float:
    if not seq:
        return 0.5
    s = seq[-window:]
    return clamp_prob((sum(x == "TAI" for x in s) + 1.0) / (len(s) + 2.0))


def ngram_probability(seq: List[str], n: int, recency_decay: float = 0.995):
    """
    Find historical occurrences of the current suffix and estimate the following result.
    More recent matches receive slightly larger weight.
    """
    if len(seq) <= n:
        return None, 0
    target = tuple(seq[-n:])
    weighted_tai = 0.0
    weighted_total = 0.0
    matches = 0

    max_i = len(seq) - n - 1
    for i in range(max_i + 1):
        if tuple(seq[i : i + n]) != target:
            continue
        nxt = seq[i + n]
        age = max_i - i
        w = recency_decay ** min(age, 2000)
        weighted_total += w
        weighted_tai += w if nxt == "TAI" else 0.0
        matches += 1

    if weighted_total <= 0:
        return None, matches

    p = (weighted_tai + 2.0 * 0.5) / (weighted_total + 2.0)
    return clamp_prob(p), matches


def markov_probability(seq: List[str], order: int):
    """
    Conditional probability P(next | last 'order' results).
    """
    if len(seq) <= order:
        return None, 0
    target = tuple(seq[-order:])
    counts = {"TAI": 1.0, "XIU": 1.0}  # Laplace smoothing
    matches = 0

    for i in range(len(seq) - order):
        if tuple(seq[i : i + order]) == target:
            nxt = seq[i + order]
            counts[nxt] += 1.0
            matches += 1

    total = counts["TAI"] + counts["XIU"]
    return clamp_prob(counts["TAI"] / total), matches


def streak_probability(seq: List[str]):
    """
    Estimate continuation vs. break conditioned on current streak length.
    It is a weak feature and is intentionally capped to avoid overfitting.
    """
    if len(seq) < 20:
        return None, 0

    last = seq[-1]
    run = 1
    for i in range(len(seq) - 2, -1, -1):
        if seq[i] == last:
            run += 1
        else:
            break

    continue_w = 1.0
    break_w = 1.0
    seen = 0

    i = 0
    while i < len(seq) - 1:
        run_result = seq[i]
        j = i + 1
        while j < len(seq) and seq[j] == run_result:
            j += 1
        run_len = j - i
        if abs(run_len - run) <= 1 and j < len(seq):
            nxt = seq[j]
            if nxt == run_result:
                continue_w += 1.0
            else:
                break_w += 1.0
            seen += 1
        i = j

    if seen == 0:
        return None, 0

    continuation = continue_w / (continue_w + break_w)
    p_tai = continuation if last == "TAI" else (1.0 - continuation)
    return clamp_prob(p_tai, 0.10, 0.90), seen


def collect_signals(seq: List[str]) -> Dict[str, Dict]:
    signals = {}

    for n in PATTERN_LENGTHS:
        p, matches = ngram_probability(seq, n)
        if p is not None:
            signals[f"pattern_{n}"] = {"p_tai": p, "support": matches}

    for order in MARKOV_ORDERS:
        p, matches = markov_probability(seq, order)
        if p is not None:
            signals[f"markov_{order}"] = {"p_tai": p, "support": matches}

    streak_p, streak_support = streak_probability(seq)
    if streak_p is not None:
        signals["streak"] = {"p_tai": streak_p, "support": streak_support}

    signals["recent_20"] = {"p_tai": recent_bias_probability(seq, 20), "support": min(20, len(seq))}
    signals["recent_50"] = {"p_tai": recent_bias_probability(seq, 50), "support": min(50, len(seq))}
    signals["ewma"] = {"p_tai": ewma_probability(seq), "support": len(seq)}

    global_p = (sum(x == "TAI" for x in seq) + 2.0) / (len(seq) + 4.0)
    signals["global"] = {"p_tai": clamp_prob(global_p), "support": len(seq)}

    return signals


DEFAULT_WEIGHTS = {
    "pattern_3": 0.75,
    "pattern_5": 0.90,
    "pattern_8": 1.05,
    "pattern_10": 1.00,
    "pattern_15": 0.85,
    "pattern_20": 0.70,
    "markov_1": 0.85,
    "markov_2": 1.00,
    "markov_3": 1.05,
    "streak": 0.55,
    "recent_20": 0.75,
    "recent_50": 0.55,
    "ewma": 0.60,
    "global": 0.35,
}


def learned_weight(name: str) -> float:
    base = DEFAULT_WEIGHTS.get(name, 0.5)
    with state["lock"]:
        history = list(state["model_recent_scores"].get(name, []))
    if len(history) < 8:
        return base

    hit_rate = sum(history) / len(history)
    reliability = 0.45 + 1.35 * hit_rate

    loss_streak = 0
    for x in reversed(history):
        if x == 0:
            loss_streak += 1
        else:
            break
    penalty = 1.0 / (1.0 + 0.08 * loss_streak)

    return max(0.10, min(2.2, base * reliability * penalty))


def ensemble_predict(seq: List[str]) -> Dict:
    signals = collect_signals(seq)
    if not signals:
        return {
            "predicted": seq[-1] if seq else "TAI",
            "p_tai": 0.5,
            "confidence": 0.5,
            "signals": {},
            "agreement": 0.0,
        }

    numerator = 0.0
    denominator = 0.0
    votes = []

    for name, info in signals.items():
        p = info["p_tai"]
        support = max(1.0, float(info["support"]))
        weight = learned_weight(name)

        support_factor = 0.70 + 0.30 * (math.log1p(support) / math.log1p(max(50, len(seq))))
        effective = weight * support_factor

        numerator += p * effective
        denominator += effective
        votes.append((name, p, effective))

    p_tai = clamp_prob(numerator / denominator if denominator else 0.5)

    total_w = sum(v[2] for v in votes) or 1.0
    consensus = sum(v[2] for v in votes if (v[1] >= 0.5) == (p_tai >= 0.5)) / total_w

    raw_strength = abs(p_tai - 0.5) * 2.0
    confidence = 0.5 + 0.48 * raw_strength * (0.55 + 0.45 * consensus)
    confidence = max(0.50, min(0.98, confidence))

    predicted = "TAI" if p_tai >= 0.5 else "XIU"
    return {
        "predicted": predicted,
        "p_tai": round(p_tai, 6),
        "confidence": round(confidence, 6),
        "signals": {
            name: {
                "p_tai": round(p, 4),
                "weight": round(w, 4),
                "support": int(info["support"]),
            }
            for (name, p, w), (_, info) in zip(votes, signals.items())
        },
        "agreement": round(consensus, 4),
    }


# -----------------------------------------------------------------------------
# Prediction/result bookkeeping
# -----------------------------------------------------------------------------
def log_prediction_for_session(session_id: int, predicted: str, confidence: float, signals: Optional[Dict] = None):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM prediction_log WHERE session_id=%s", (session_id,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return

    cur.execute(
        """
        INSERT INTO prediction_log(session_id, predicted, confidence, signals_json)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(session_id) DO NOTHING
        """,
        (
            session_id,
            predicted,
            float(confidence),
            json.dumps(signals or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def settle_pending_predictions():
    """
    When a new real result exists, settle the prediction that targeted that session.
    Objective feedback is attached to every component used by that prediction.
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, p.session_id, p.predicted, p.signals_json, s.result
        FROM prediction_log p
        JOIN sessions s ON s.id = p.session_id
        WHERE p.actual IS NULL
        ORDER BY p.session_id ASC
        LIMIT 100
        """
    )
    rows = cur.fetchall()

    for pid, sid, predicted, signals_json, actual in rows:
        correct = normalize_result(predicted) == normalize_result(actual)

        cur.execute(
            """
            UPDATE prediction_log
            SET actual=%s, correct=%s
            WHERE id=%s
            """,
            (normalize_result(actual), bool(correct), pid),
        )

        try:
            stored_signals = json.loads(signals_json or "{}")
        except Exception:
            stored_signals = {}

        with state["lock"]:
            actual_norm = normalize_result(actual)
            for name, info in stored_signals.items():
                try:
                    component_p = float(info.get("p_tai", 0.5))
                    component_pred = "TAI" if component_p >= 0.5 else "XIU"
                    component_ok = component_pred == actual_norm
                    state["model_recent_scores"][name].append(1 if component_ok else 0)
                except Exception:
                    pass

            state["recent_outcomes"].append(1 if correct else 0)
            if correct:
                state["correct"] += 1
                state["current_win_streak"] += 1
                state["current_loss_streak"] = 0
                state["max_win_streak"] = max(
                    state["max_win_streak"], state["current_win_streak"]
                )
            else:
                state["wrong"] += 1
                state["current_loss_streak"] += 1
                state["current_win_streak"] = 0
                state["max_loss_streak"] = max(
                    state["max_loss_streak"], state["current_loss_streak"]
                )

    conn.commit()
    cur.close()
    conn.close()


def get_history(limit: int = HISTORY_LIMIT) -> pd.DataFrame:
    limit = max(20, min(int(limit), MAX_MODEL_HISTORY))
    conn = get_db()
    df = pd.read_sql(
        """
        SELECT id, result, point, dice1, dice2, dice3, created_at
        FROM sessions
        ORDER BY id DESC
        LIMIT %s
        """,
        conn,
        params=(limit,),
    )
    conn.close()

    if df.empty:
        return df

    df["result"] = df["result"].map(normalize_result)
    df = df[df["result"].isin(RESULTS)].copy()
    return df.sort_values("id").reset_index(drop=True)


def get_recent_accuracy() -> Dict:
    conn = get_db()
    df = pd.read_sql(
        """
        SELECT predicted, actual, correct
        FROM prediction_log
        WHERE actual IS NOT NULL
        ORDER BY id DESC
        LIMIT %s
        """,
        conn,
        params=(PREDICTION_LOG_LIMIT,),
    )
    conn.close()

    if df.empty:
        return {
            "samples": 0,
            "accuracy": None,
            "current_win_streak": 0,
            "current_loss_streak": 0,
            "max_win_streak": 0,
            "max_loss_streak": 0,
        }

    corrects = [int(bool(x)) for x in df["correct"].tolist()]
    accuracy = sum(corrects) / len(corrects)

    current_win = 0
    current_loss = 0
    for value in corrects:
        if value:
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0

    max_win = max_loss = 0
    run = 0
    prev = None
    for value in corrects:
        if value == prev:
            run += 1
        else:
            run = 1
            prev = value
        if value:
            max_win = max(max_win, run)
        else:
            max_loss = max(max_loss, run)

    return {
        "samples": len(df),
        "accuracy": round(accuracy * 100, 2),
        "current_win_streak": current_win,
        "current_loss_streak": current_loss,
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
    }


# -----------------------------------------------------------------------------
# Walk-forward validation
# -----------------------------------------------------------------------------
def walk_forward_backtest(sequence: List[str], max_points: int = 500) -> Dict:
    """
    Strict walk-forward validation:
    prediction for index i can only use sequence[:i].
    """
    n = len(sequence)
    if n < 80:
        return {"status": "WAIT", "samples": 0, "accuracy": None}

    start = max(50, n - max_points)
    tested = 0
    correct = 0
    current_win = current_loss = 0
    max_win = max_loss = 0
    score_history = []

    def static_predict(hist):
        sigs = collect_signals(hist)
        if not sigs:
            return hist[-1] if hist else "TAI"
        num = den = 0.0
        for name, info in sigs.items():
            p = info["p_tai"]
            support = max(1.0, float(info["support"]))
            weight = DEFAULT_WEIGHTS.get(name, 0.5)
            support_factor = 0.70 + 0.30 * (math.log1p(support) / math.log1p(max(50, len(hist))))
            eff = weight * support_factor
            num += p * eff
            den += eff
        p = num / den if den else 0.5
        return "TAI" if p >= 0.5 else "XIU"

    for i in range(start, n):
        pred = static_predict(sequence[:i])
        actual = sequence[i]
        ok = pred == actual
        tested += 1

        if ok:
            correct += 1
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0

        max_win = max(max_win, current_win)
        max_loss = max(max_loss, current_loss)
        score_history.append(1 if ok else 0)

    return {
        "status": "READY",
        "samples": tested,
        "accuracy": round(100.0 * correct / tested, 2) if tested else None,
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
        "last_200_accuracy": (
            round(100.0 * sum(score_history[-200:]) / len(score_history[-200:]), 2)
            if score_history else None
        ),
    }


# -----------------------------------------------------------------------------
# Flow analysis (kept compatible with old endpoint semantics)
# -----------------------------------------------------------------------------
def analyze_flow(sequence):
    if len(sequence) < 8:
        return {"status": "learning", "advice": "Đang quan sát..."}

    seq_list = list(sequence)
    streak_lengths = []
    current_streak = 1

    for i in range(1, len(seq_list)):
        if seq_list[i] == seq_list[i - 1]:
            current_streak += 1
        else:
            streak_lengths.append(current_streak)
            current_streak = 1
    streak_lengths.append(current_streak)

    avg_streak = sum(streak_lengths) / len(streak_lengths)
    reversals = sum(1 for i in range(1, len(seq_list)) if seq_list[i] != seq_list[i - 1])
    reversal_freq = reversals / len(seq_list)
    tai_count = seq_list.count("TAI")
    xiu_count = seq_list.count("XIU")
    bias = tai_count / len(seq_list)
    volatility = np.std(streak_lengths) if len(streak_lengths) > 1 else 0.0

    return {
        "avg_streak": avg_streak,
        "reversal_freq": reversal_freq,
        "bias": bias,
        "volatility": volatility,
        "last_result": seq_list[-1],
    }


# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------
@app.route("/predict")
def predict():
    try:
        settle_pending_predictions()
        df = get_history(HISTORY_LIMIT)

        # NẾU CHƯA ĐỦ DỮ LIỆU, VẪN ĐƯA RA DỰ ĐOÁN MẶC ĐỊNH
        if len(df) < 100:
            return jsonify({
                "status": "PREDICT",  # Luôn là PREDICT, không bao giờ WAIT
                "predict": "TAI",
                "predict_short": "T",
                "confidence": 0.55,
                "probability": {"tai": 0.55, "xiu": 0.45},
                "reason": "Đang xây dựng mô hình với dữ liệu hiện có.",
                "context": {
                    "current_streak": 0,
                    "total_rounds_learned": int(len(df)),
                }
            })

        seq = df["result"].tolist()
        analysis = ensemble_predict(seq)

        latest_id = int(df["id"].iloc[-1])
        target_id = latest_id + 1

        log_prediction_for_session(target_id, analysis["predicted"], analysis["confidence"], analysis["signals"])

        with state["lock"]:
            state["last_prediction_id"] = target_id
            state["last_prediction"] = analysis["predicted"]
            state["last_prediction_created_at"] = time.time()

        flow = analyze_flow(seq[-20:])
        recent_acc = get_recent_accuracy()

        reason_parts = [
            f"Ensemble p(Tài)={analysis['p_tai']:.3f}",
            f"đồng thuận={analysis['agreement']:.3f}",
            f"{len(analysis['signals'])} tín hiệu đang hoạt động",
        ]

        return jsonify({
            "status": "PREDICT",  # Luôn là PREDICT
            "predict": analysis["predicted"],
            "predict_short": short_result(analysis["predicted"]),
            "confidence": analysis["confidence"],
            "probability": {
                "tai": analysis["p_tai"],
                "xiu": round(1.0 - analysis["p_tai"], 6),
            },
            "reason": " | ".join(reason_parts),
            "target_session_id": target_id,
            "latest_session_id": latest_id,
            "context": {
                "current_streak": (
                    sum(1 for x in reversed(seq) if x == seq[-1])
                ),
                "flow_analysis": {
                    "avg_streak": round(float(flow["avg_streak"]), 3),
                    "reversal_freq": round(float(flow["reversal_freq"]), 3),
                    "bias": round(float(flow["bias"]), 3),
                    "volatility": round(float(flow["volatility"]), 3),
                },
                "total_rounds_learned": int(len(seq)),
                "recent_accuracy": recent_acc,
            },
            "signals": analysis["signals"],
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
        conn.close()

        accuracy = get_recent_accuracy()
        return jsonify({
            "total_rounds_learned": total,
            "tai": tai,
            "xiu": xiu,
            "status": "running" if total > 0 else "waiting_for_data",
            "prediction_metrics": accuracy,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/history")
def history():
    try:
        df = get_history(20)
        if len(df) < 10:
            return jsonify({"error": "Not enough data"}), 400

        rows = []
        for _, row in df.tail(20).iloc[::-1].iterrows():
            rows.append({
                "id": int(row["id"]),
                "result": row["result"],
                "point": int(row["point"]) if pd.notna(row["point"]) else None,
                "dices": [
                    int(row["dice1"]) if pd.notna(row["dice1"]) else None,
                    int(row["dice2"]) if pd.notna(row["dice2"]) else None,
                    int(row["dice3"]) if pd.notna(row["dice3"]) else None,
                ],
            })
        return jsonify({"recent": rows, "status": "ready"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/summary_50")
def summary_50():
    try:
        df = get_history(50)
        if len(df) < 10:
            return jsonify({"error": "Not enough data"}), 400

        recent = df["result"].tolist()
        tai_count = recent.count("TAI")
        xiu_count = recent.count("XIU")
        history_str = "".join(short_result(x) for x in recent)
        win = max(tai_count, xiu_count)
        lose = min(tai_count, xiu_count)

        return jsonify({
            "total_rounds": len(recent),
            "win": win,
            "lose": lose,
            "win_rate": round(100.0 * win / len(recent), 1),
            "history": history_str,
            "status": "ready",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/streak_20")
def streak_20():
    try:
        df = get_history(20)
        if len(df) < 2:
            return jsonify({"error": "Not enough data"}), 400
        recent = df["result"].tolist()

        max_tai = max_xiu = current_tai = current_xiu = 0
        for res in recent:
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
            "total_tai": recent.count("TAI"),
            "total_xiu": recent.count("XIU"),
            "status": "ready",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/accuracy")
def accuracy():
    try:
        df = get_history(HISTORY_LIMIT)
        if len(df) < 80:
            return jsonify({"error": "Need at least 80 rounds"}), 400

        seq = df["result"].tolist()
        live = get_recent_accuracy()
        backtest = walk_forward_backtest(seq, max_points=min(500, len(seq) - 50))

        return jsonify({
            "status": "ready",
            "samples_in_database": len(seq),
            "live_prediction_metrics": live,
            "walk_forward_backtest": backtest,
            "note": (
                "Walk-forward là đánh giá dự đoán trên dữ liệu chưa nhìn thấy; "
                "không phải accuracy của một bộ dữ liệu đã học."
            ),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/model")
def model_info():
    try:
        df = get_history(HISTORY_LIMIT)
        if len(df) < 100:
            return jsonify({"status": "WAIT", "learned": len(df)})

        result = ensemble_predict(df["result"].tolist())
        return jsonify({
            "status": "ready",
            "algorithm": "adaptive-ensemble",
            "pattern_lengths": PATTERN_LENGTHS,
            "markov_orders": MARKOV_ORDERS,
            "signals": result["signals"],
            "agreement": result["agreement"],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/history50")
def history50():
    try:
        df = get_history(50)
        if len(df) < 10:
            return jsonify({"error": "Not enough data (need at least 10 rounds)"}), 400

        tai_count = int((df["result"] == "TAI").sum())
        xiu_count = int((df["result"] == "XIU").sum())
        total = len(df)

        history_data = []
        for _, row in df.iloc[::-1].iterrows():
            created = row["created_at"]
            history_data.append({
                "id": int(row["id"]),
                "result": row["result"],
                "point": int(row["point"]) if pd.notna(row["point"]) else None,
                "dices": [
                    int(row["dice1"]) if pd.notna(row["dice1"]) else None,
                    int(row["dice2"]) if pd.notna(row["dice2"]) else None,
                    int(row["dice3"]) if pd.notna(row["dice3"]) else None,
                ],
                "time": created.strftime("%d-%m-%Y %H:%M:%S") if pd.notna(created) else None,
            })

        return jsonify({
            "total_rounds": total,
            "tai": tai_count,
            "xiu": xiu_count,
            "win_rate": round(100.0 * max(tai_count, xiu_count) / total, 1),
            "history": history_data,
            "status": "ready",
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def background_worker():
    try:
        init_db()
        restore_adaptive_scores()
    except Exception as exc:
        print(f"[-] init_db: {exc}")

    while True:
        try:
            fetch_and_save()
            settle_pending_predictions()
        except Exception as exc:
            print(f"[-] background_worker: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    threading.Thread(target=background_worker, daemon=True, name="data-worker").start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)