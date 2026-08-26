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
    "meta": None,
}
MODEL_LOCK = threading.RLock()

# Lightweight online meta-model. Pure NumPy: no sklearn/TensorFlow/PyTorch required.
META_FEATURES = 25
META_L2 = 0.025
META_LR = 0.045
META_EPOCHS = 4
META_REFRESH_ROUNDS = int(os.getenv("META_REFRESH_ROUNDS", "100"))


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
            # replay oldest -> newest so deque reflects temporal order
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

    # Walk over all eligible contexts. Weight newer contexts more heavily.
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

    # Beta smoothing prevents tiny sample matches from becoming overconfident.
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

    # Recent occurrences are mildly favored.
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

    # Scan historical runs of the same length (or close lengths).
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

    # Probability next is TAI. For current TAI streak continuation => TAI.
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

    # Stable global prior with smoothing.
    global_p = (sum(x == "TAI" for x in seq) + 2.0) / (len(seq) + 4.0)
    signals["global"] = {"p_tai": clamp_prob(global_p), "support": len(seq)}

    return signals



def feature_vector(seq: List[str]) -> np.ndarray:
    """Compact state vector used by the lightweight meta learner."""
    x = np.zeros(META_FEATURES, dtype=np.float64)
    if not seq:
        return x

    # Recent balances at multiple horizons.
    horizons = (5, 10, 20, 40, 80)
    for j, h in enumerate(horizons):
        part = seq[-h:]
        x[j] = (sum(v == "TAI" for v in part) / len(part)) - 0.5

    # Last 8 outcomes encoded as +/-1.
    last8 = seq[-8:]
    for j, v in enumerate(last8):
        x[5 + j] = 1.0 if v == "TAI" else -1.0

    # Current streak, direction and transition tendency.
    last = seq[-1]
    run = 1
    for v in reversed(seq[:-1]):
        if v == last:
            run += 1
        else:
            break
    x[13] = min(run, 10) / 10.0
    x[14] = 1.0 if last == "TAI" else -1.0

    trans = Counter(zip(seq[:-1], seq[1:]))
    for idx, pair in enumerate((("TAI", "TAI"), ("TAI", "XIU"), ("XIU", "TAI"), ("XIU", "XIU"))):
        total = trans[(pair[0], "TAI")] + trans[(pair[0], "XIU")]
        x[15 + idx] = ((trans[pair] / total) if total else 0.5) - 0.5

    # Current context probabilities from the non-meta ensemble components.
    sigs = collect_signals(seq)
    vals = [
        sigs.get("pattern_5", {}).get("p_tai", 0.5),
        sigs.get("pattern_10", {}).get("p_tai", 0.5),
        sigs.get("markov_2", {}).get("p_tai", 0.5),
        sigs.get("markov_3", {}).get("p_tai", 0.5),
        sigs.get("streak", {}).get("p_tai", 0.5),
        sigs.get("ewma", {}).get("p_tai", 0.5),
    ]
    for j, p in enumerate(vals):
        x[19 + j] = 2.0 * (p - 0.5)
    return x


def sigmoid(z):
    z = np.clip(z, -12.0, 12.0)
    return 1.0 / (1.0 + np.exp(-z))


def train_meta_model(seq: List[str]) -> Dict:
    """Fast walk-forward meta learner.

    IMPORTANT: this training path intentionally does NOT call collect_signals()
    for every historical row. The old implementation did that, making training
    roughly quadratic in the number of rounds and causing Render request timeouts.
    """
    if len(seq) < 120:
        return {"w": np.zeros(META_FEATURES), "b": 0.0,
                "mu": np.zeros(META_FEATURES), "sd": np.ones(META_FEATURES), "samples": 0}

    start = max(30, len(seq) - MAX_MODEL_HISTORY)
    X, y = [], []

    # Prefix transition counts: updated once per row, so feature generation is O(1).
    trans = Counter()
    tai_total = 0
    xiu_total = 0
    run_len = 0
    prev = None

    for i, value in enumerate(seq):
        value = normalize_result(value)
        if value is None:
            continue

        if prev == value:
            run_len += 1
        else:
            run_len = 1

        if i >= start and i >= 30:
            x = np.zeros(META_FEATURES, dtype=np.float64)
            horizons = (5, 10, 20, 40, 80)
            for j, h in enumerate(horizons):
                part = seq[max(0, i-h):i]
                if part:
                    x[j] = sum(v == "TAI" for v in part) / len(part) - 0.5

            last8 = seq[max(0, i-8):i]
            for j, v in enumerate(last8):
                x[5 + j] = 1.0 if v == "TAI" else -1.0

            x[13] = min(run_len, 10) / 10.0
            x[14] = 1.0 if prev == "TAI" else -1.0

            # Conditional transition tendencies learned only from the prefix.
            for idx, pair in enumerate((("TAI", "TAI"), ("TAI", "XIU"),
                                        ("XIU", "TAI"), ("XIU", "XIU"))):
                denom = trans[(pair[0], "TAI")] + trans[(pair[0], "XIU")]
                x[15 + idx] = (trans[pair] / denom if denom else 0.5) - 0.5

            # Cheap context features; the expensive pattern search is reserved
            # for the live prediction, where it is executed only once per poll.
            for j, h in enumerate((20, 50)):
                part = seq[max(0, i-h):i]
                x[19 + j] = 2.0 * ((sum(v == "TAI" for v in part) / len(part) if part else 0.5) - 0.5)
            x[21] = 2.0 * ((tai_total / (tai_total + xiu_total)) - 0.5) if (tai_total + xiu_total) else 0.0
            x[22] = min(run_len, 10) / 10.0
            x[23] = 1.0 if prev == "TAI" else -1.0
            x[24] = 0.0

            X.append(x)
            y.append(1.0 if value == "TAI" else 0.0)

        if prev is not None:
            trans[(prev, value)] += 1
        if value == "TAI":
            tai_total += 1
        else:
            xiu_total += 1
        prev = value

    if len(y) < 50:
        return {"w": np.zeros(META_FEATURES), "b": 0.0,
                "mu": np.zeros(META_FEATURES), "sd": np.ones(META_FEATURES), "samples": len(y)}

    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-6] = 1.0
    Z = (X - mu) / sd

    w = np.zeros(META_FEATURES, dtype=np.float64)
    b = 0.0
    n = len(y)
    for _ in range(META_EPOCHS):
        p = sigmoid(Z @ w + b)
        grad_w = (Z.T @ (p - y)) / n + META_L2 * w
        grad_b = float(np.mean(p - y))
        w -= META_LR * grad_w
        b -= META_LR * grad_b

    return {"w": w, "b": b, "mu": mu, "sd": sd, "samples": n}


def meta_probability(seq: List[str], meta: Dict) -> float:
    if not meta or meta.get("samples", 0) < 50:
        return 0.5
    x = feature_vector(seq)
    z = (x - meta["mu"]) / meta["sd"]
    return float(sigmoid(np.dot(z, meta["w"]) + meta["b"]))


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

    # Recent hit-rate controls each component's influence.
    hit_rate = sum(history) / len(history)
    reliability = 0.45 + 1.35 * hit_rate

    # Penalize persistent recent failure more aggressively.
    loss_streak = 0
    for x in reversed(history):
        if x == 0:
            loss_streak += 1
        else:
            break
    penalty = 1.0 / (1.0 + 0.08 * loss_streak)

    return max(0.10, min(2.2, base * reliability * penalty))


def ensemble_predict(seq: List[str], meta: Optional[Dict] = None) -> Dict:
    signals = collect_signals(seq)
    if not signals:
        return {"predicted": "TAI", "p_tai": 0.5, "confidence": 0.5, "signals": {}, "agreement": 0.0, "meta_probability": 0.5}

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

    base_p = numerator / denominator if denominator else 0.5
    consensus = sum(v[2] for v in votes if (v[1] >= 0.5) == (base_p >= 0.5)) / (sum(v[2] for v in votes) or 1.0)

    mp = meta_probability(seq, meta)
    # Meta model gets stronger only when it has enough walk-forward samples.
    meta_strength = min(0.42, 0.10 + 0.32 * min(1.0, (meta or {}).get("samples", 0) / 3000.0))
    blended = (1.0 - meta_strength) * base_p + meta_strength * mp

    # Hysteresis: avoid flip-flopping on tiny probability changes.
    previous = state.get("last_prediction")
    if previous in RESULTS and 0.485 <= blended <= 0.515:
        predicted = previous
    else:
        predicted = "TAI" if blended >= 0.5 else "XIU"

    strength = abs(blended - 0.5) * 2.0
    confidence = 0.5 + 0.48 * strength * (0.55 + 0.45 * consensus)
    confidence = max(0.50, min(0.98, confidence))

    return {
        "predicted": predicted,
        "p_tai": round(float(blended), 6),
        "confidence": round(float(confidence), 6),
        "signals": {
            name: {"p_tai": round(p, 4), "weight": round(w, 4), "support": int(info["support"])}
            for (name, p, w), (_, info) in zip(votes, signals.items())
        },
        "agreement": round(float(consensus), 4),
        "meta_probability": round(float(mp), 6),
        "meta_strength": round(float(meta_strength), 4),
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
            # Score each component against the actual result, not merely against
            # the ensemble decision. This lets weak components lose weight and
            # reliable components gain weight over time.
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

    # Use a temporary sequence-specific scoring state implicitly through current model.
    # Disable adaptive live state mutation by computing with default weights.
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

        if len(df) < 100:
            return jsonify({
                "status": "WAIT",
                "reason": "Chưa đủ dữ liệu để xây mô hình.",
                "learned": int(len(df)),
            })

        seq = df["result"].tolist()
        # Get the newest database id BEFORE deciding whether the meta model needs
        # a refresh. The previous V3 used latest_id before assigning it, which
        # caused /predict to fail with an UnboundLocalError and could surface as 502.
        latest_id = int(df["id"].iloc[-1])
        data_key = (int(df["id"].iloc[0]), latest_id, len(seq))
        with MODEL_LOCK:
            old_key = model_cache.get("data_key")
            old_meta = model_cache.get("meta")
            old_id = old_key[1] if old_key else 0
            refresh_due = (old_meta is None or
                           latest_id - int(old_id or 0) >= META_REFRESH_ROUNDS or
                           time.time() - float(model_cache.get("updated_at", 0.0)) >= 300)
            if refresh_due:
                model_cache["meta"] = train_meta_model(seq)
                model_cache["data_key"] = data_key
                model_cache["updated_at"] = time.time()
            meta = model_cache["meta"]
        analysis = ensemble_predict(seq, meta)

        # ALWAYS PREDICT: there is intentionally no loss-streak stop/lockout.
        # A losing streak only changes adaptive component weights; it never
        # suppresses the next prediction.

        # The target id is the next unseen round. If the upstream source only exposes
        # known rounds, frontends can still poll this endpoint and use this prediction
        # until a new session arrives.
        target_id = latest_id + 1

        # Persist the prediction for later objective scoring.
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
            "status": "PREDICT",
            "prediction_mode": "CONTINUOUS",
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


@app.route("/backtest_compare")
def backtest_compare():
    """Compare the old 10-pattern rule against V3's base ensemble on unseen data."""
    try:
        df = get_history(HISTORY_LIMIT)
        seq = df["result"].tolist()
        if len(seq) < 150:
            return jsonify({"status": "WAIT", "samples": len(seq)}), 400

        start = max(50, len(seq) - 1000)
        old_ok = new_ok = 0
        old_n = new_n = 0
        old_win = old_loss = new_win = new_loss = 0
        old_max_win = old_max_loss = new_max_win = new_max_loss = 0

        def old_predict(hist):
            if len(hist) < 10:
                return hist[-1]
            target = tuple(hist[-10:])
            c = Counter()
            for i in range(len(hist) - 10):
                if tuple(hist[i:i+10]) == target:
                    c[hist[i+10]] += 1
            if c:
                return c.most_common(1)[0][0]
            return "TAI" if hist.count("TAI") >= hist.count("XIU") else "XIU"

        for i in range(start, len(seq)):
            hist = seq[:i]
            actual = seq[i]
            op = old_predict(hist)
            if op == actual:
                old_ok += 1; old_win += 1; old_loss = 0
            else:
                old_loss += 1; old_win = 0
            old_max_win = max(old_max_win, old_win); old_max_loss = max(old_max_loss, old_loss)

            # Train a fresh meta model only at checkpoints; base ensemble remains strict and cheap.
            bp = ensemble_predict(hist, None)["predicted"]
            if bp == actual:
                new_ok += 1; new_win += 1; new_loss = 0
            else:
                new_loss += 1; new_win = 0
            new_max_win = max(new_max_win, new_win); new_max_loss = max(new_max_loss, new_loss)
            old_n += 1; new_n += 1

        return jsonify({
            "status": "ready",
            "samples": old_n,
            "old_v1": {"accuracy": round(old_ok * 100 / old_n, 2), "max_win_streak": old_max_win, "max_loss_streak": old_max_loss},
            "v3_base_ensemble": {"accuracy": round(new_ok * 100 / new_n, 2), "max_win_streak": new_max_win, "max_loss_streak": new_max_loss},
        })
    except Exception as exc:
        return jsonify({"status": "ERROR", "reason": str(exc)}), 500


@app.route("/model")
def model_info():
    try:
        df = get_history(HISTORY_LIMIT)
        if len(df) < 100:
            return jsonify({"status": "WAIT", "learned": len(df)})

        seq = df["result"].tolist()
        with MODEL_LOCK:
            data_key = (int(df["id"].iloc[0]), int(df["id"].iloc[-1]), len(seq))
            if model_cache.get("data_key") != data_key or model_cache.get("meta") is None:
                model_cache["meta"] = train_meta_model(seq)
                model_cache["data_key"] = data_key
            meta = model_cache["meta"]
        result = ensemble_predict(seq, meta)
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