"""
app.py — Kano AI Prediction Engine v4
Architecture : Claude
Algorithm    : DeepSeek (modules 1-4)
AI hẹp       : Claude pass 3 — GradientBoostEngine (module 5)
API/Memory   : ChatGPT
Deploy       : Gemini

Thay đổi v4:
  [+] GradientBoostEngine — scikit-learn GradientBoostingClassifier
      · 18 features từ cửa sổ 20 kết quả
      · Retrain background thread mỗi 50 updates, không block /predict
      · Warmup 80 samples — trước đó engine trả (T, 0.5), MetaLearner bỏ qua
      · RAM: model ~110 KB, scaler ~1 KB — an toàn với Render Free 512 MB
      · Nếu import sklearn thất bại, GB bị vô hiệu hoá và 4 module cũ chạy như bình thường
  [=] Modules 1-4 giữ nguyên từ pass 2 (đã fix bugs B, C, D)

Dependency mới (requirements.txt):
  scikit-learn>=1.3.0
  numpy>=1.24.0
"""

import os
import math
import time
import json
import logging
import threading
import urllib.request
from collections import deque, defaultdict

from flask import Flask, jsonify, request

# ── Optional AI dependency ────────────────────────────────────────────────────
try:
    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    log_msg = "scikit-learn/numpy not found — GradientBoostEngine disabled."

# ── Constants ─────────────────────────────────────────────────────────────────

try:
    HISTORY_LIMIT = max(10, int(os.getenv("HISTORY_LIMIT", "500")))
except ValueError:
    HISTORY_LIMIT = 500

STREAK_WINDOW   = 20
FREQ_WINDOWS    = [10, 30, 50]
MARKOV_ORDERS   = [1, 2, 3]
FLIP_THRESHOLD  = 0.80
EMA_ALPHA       = 0.1
MIN_WEIGHT      = 0.05
MAX_WEIGHT      = 0.60

# GradientBoostEngine
GB_FEATURE_WINDOW = 20    # cửa sổ feature
GB_WARMUP         = 80    # min samples trước khi GB tham gia vote
GB_MAX_SAMPLES    = 500   # số training samples tối đa giữ trong RAM
GB_RETRAIN_EVERY  = 50    # retrain sau mỗi N updates

LABELS = ("T", "X")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("kano")
if not SKLEARN_AVAILABLE:
    log.warning("scikit-learn/numpy not found — GradientBoostEngine disabled.")


# ── Utility ───────────────────────────────────────────────────────────────────

def _entropy(counts: dict) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -sum(
        (c / total) * math.log2(c / total)
        for c in counts.values()
        if c > 0
    )


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _normalize_weights(weights: dict) -> dict:
    total = sum(weights.values())
    if total == 0:
        n = len(weights)
        return {k: 1.0 / n for k in weights}
    return {k: v / total for k, v in weights.items()}


def _safe_label(value) -> str | None:
    """
    Chuyển đổi giá trị từ API về T hoặc X.
    API game trả về: "TAI", "TÀI", "T" → "T"
                     "XIU", "XỈU", "X" → "X"
    """
    if value is None:
        return None
    label = str(value).strip().upper()
    if label in ("TAI", "TÀI", "T", "OVER"):
        return "T"
    if label in ("XIU", "XỈU", "X", "UNDER"):
        return "X"
    return None


# ── Module base ───────────────────────────────────────────────────────────────

class _BaseModule:
    name: str = "base"

    def predict(self, history: deque) -> tuple[str, float]:
        if len(history) < self._min_history():
            return "T", 0.5
        label, conf = self._compute(history)
        conf = _clamp(conf, 0.0, 1.0)
        return label, conf

    def _min_history(self) -> int:
        return 1

    def _compute(self, history: deque) -> tuple[str, float]:
        raise NotImplementedError


# ── Module 1: PatternEngine ───────────────────────────────────────────────────

class PatternEngine(_BaseModule):
    """
    23 named pattern types. Each entry: (id, min_len, predicate, label_func).
    Rarity = 1 - (frequency of pattern in full history).
    Highest-rarity matching pattern wins.
    """
    name = "pattern"

    PATTERNS = [
        ("alt_TX",      2, lambda t: t[-1] != t[-2] and t[-1] == "X", lambda t: t[-1]),
        ("alt_XT",      2, lambda t: t[-1] != t[-2] and t[-1] == "T", lambda t: t[-1]),
        ("TTT",         3, lambda t: t[-1] == t[-2] == t[-3] == "T",  lambda t: "X"),
        ("XXX",         3, lambda t: t[-1] == t[-2] == t[-3] == "X",  lambda t: "T"),
        ("T_X_T",       3, lambda t: t[-1] == "T" and t[-2] == "X" and t[-3] == "T", lambda t: "T"),
        ("X_T_X",       3, lambda t: t[-1] == "X" and t[-2] == "T" and t[-3] == "X", lambda t: "X"),
        ("TTTT",        4, lambda t: all(x == "T" for x in t[-4:]), lambda t: "X"),
        ("XXXX",        4, lambda t: all(x == "X" for x in t[-4:]), lambda t: "T"),
        ("T_T_X_X",     4, lambda t: t[-1] == t[-2] == "X" and t[-3] == t[-4] == "T", lambda t: "X"),
        ("X_X_T_T",     4, lambda t: t[-1] == t[-2] == "T" and t[-3] == t[-4] == "X", lambda t: "T"),
        ("T_X_X_T",     4, lambda t: t[-1] == "T" and t[-2] == t[-3] == "X" and t[-4] == "T", lambda t: "T"),
        ("X_T_T_X",     4, lambda t: t[-1] == "X" and t[-2] == t[-3] == "T" and t[-4] == "X", lambda t: "X"),
        ("T_T_X_T",     4, lambda t: t[-1] == "T" and t[-2] == "X" and t[-3] == "T" and t[-4] == "T", lambda t: "T"),
        ("X_X_T_X",     4, lambda t: t[-1] == "X" and t[-2] == "T" and t[-3] == "X" and t[-4] == "X", lambda t: "X"),
        ("T_T_T_X",     4, lambda t: t[-1] == "X" and t[-2] == t[-3] == t[-4] == "T", lambda t: "X"),
        ("X_X_X_T",     4, lambda t: t[-1] == "T" and t[-2] == t[-3] == t[-4] == "X", lambda t: "T"),
        ("T_X_T_X_b",   4, lambda t: t[-1] == "T" and t[-2] == "X" and t[-3] == "X" and t[-4] == "T", lambda t: "T"),
        ("X_T_X_T_b",   4, lambda t: t[-1] == "X" and t[-2] == "T" and t[-3] == "T" and t[-4] == "X", lambda t: "X"),
        ("T_X_T_X_T",   5, lambda t: t[-1]=="T" and t[-2]=="X" and t[-3]=="T" and t[-4]=="X" and t[-5]=="T", lambda t: "X"),
        ("X_T_X_T_X",   5, lambda t: t[-1]=="X" and t[-2]=="T" and t[-3]=="X" and t[-4]=="T" and t[-5]=="X", lambda t: "T"),
        ("T_T_X_T_X",   5, lambda t: t[-1]=="X" and t[-2]=="T" and t[-3]=="X" and t[-4]=="T" and t[-5]=="T", lambda t: "X"),
        ("X_X_T_X_T",   5, lambda t: t[-1]=="T" and t[-2]=="X" and t[-3]=="T" and t[-4]=="X" and t[-5]=="X", lambda t: "T"),
        ("pair_break",  6,
            lambda t: t[-1] != t[-2] and t[-3] == t[-4] and t[-5] == t[-6] and t[-3] != t[-5],
            lambda t: t[-1]),
    ]

    def _min_history(self) -> int:
        return 2

    def _compute(self, history: deque) -> tuple[str, float]:
        hist_list = list(history)
        tail_len = len(hist_list)
        matched = []
        for pname, min_len, pred, label_func in self.PATTERNS:
            if tail_len < min_len:
                continue
            if not pred(hist_list):
                continue
            count = 0
            total_windows = max(0, tail_len - min_len + 1)
            for i in range(total_windows):
                window = hist_list[i: i + min_len]
                if pred(window):
                    count += 1
            rarity = 1.0 - (count / total_windows) if total_windows > 0 else 1.0
            matched.append((pname, label_func(hist_list), _clamp(rarity, 0.0, 1.0)))
        if not matched:
            return "T", 0.5
        best = max(matched, key=lambda x: x[2])
        return best[1], best[2]


# ── Module 2: MarkovEngine ────────────────────────────────────────────────────

class MarkovEngine(_BaseModule):
    """
    Orders 1-3 maintained simultaneously.
    Score = log(count+1) * confidence / sqrt(order) — higher orders compete fairly.
    Laplace smoothing k=1.
    """
    name = "markov"

    def __init__(self):
        self._tables: dict[int, dict] = {
            o: defaultdict(lambda: defaultdict(int))
            for o in MARKOV_ORDERS
        }

    def train(self, history: list[str]) -> None:
        for i in range(len(history)):
            for order in MARKOV_ORDERS:
                if i < order:
                    continue
                state = tuple(history[i - order: i])
                self._tables[order][state][history[i]] += 1

    def update(self, label: str, history: deque) -> None:
        lst = list(history)
        for order in MARKOV_ORDERS:
            if len(lst) <= order:
                continue
            state = tuple(lst[-(order + 1): -1])
            self._tables[order][state][label] += 1

    def _min_history(self) -> int:
        return max(MARKOV_ORDERS)

    def _compute(self, history: deque) -> tuple[str, float]:
        hist_list = list(history)
        best_score = -1.0
        best_probs: dict[str, float] = {}
        for order in MARKOV_ORDERS:
            if len(hist_list) < order:
                continue
            state = tuple(hist_list[-order:])
            counts = self._tables[order][state]
            total_counts = sum(counts.values())
            if total_counts == 0:
                continue
            smoothed_total = total_counts + 2
            prob_T = (counts.get("T", 0) + 1) / smoothed_total
            prob_X = (counts.get("X", 0) + 1) / smoothed_total
            confidence = max(prob_T, prob_X)
            score = math.log(total_counts + 1) * confidence / math.sqrt(order)
            if score > best_score:
                best_score = score
                best_probs = {"T": prob_T, "X": prob_X}
        if not best_probs:
            return "T", 0.5
        if best_probs["T"] >= best_probs["X"]:
            return "T", best_probs["T"]
        return "X", best_probs["X"]


# ── Module 3: StreakEngine ────────────────────────────────────────────────────

class StreakEngine(_BaseModule):
    """
    streak_ratio = dominant_count / window, rescaled (ratio-0.5)*2 → [0,1].
    High ratio = long streak = high break probability.
    Không dùng entropy — entropy binary luôn gần 1.0 trên dữ liệu ngẫu nhiên.
    """
    name = "streak"

    def _min_history(self) -> int:
        return STREAK_WINDOW

    def _compute(self, history: deque) -> tuple[str, float]:
        window = list(history)[-STREAK_WINDOW:]
        counts = {label: window.count(label) for label in LABELS}
        dominant_count = max(counts.values())
        streak_ratio = dominant_count / STREAK_WINDOW
        break_prob = _clamp((streak_ratio - 0.5) * 2.0, 0.0, 1.0)
        last_label = window[-1]
        if break_prob > 0.5:
            pred = "X" if last_label == "T" else "T"
            conf = break_prob
        else:
            pred = last_label
            conf = 0.5 + (0.5 - break_prob) * 0.4
        return pred, _clamp(conf, 0.0, 1.0)


# ── Module 4: FrequencyEngine ─────────────────────────────────────────────────

class FrequencyEngine(_BaseModule):
    """
    Windows 10/30/50. Reversion wins over momentum when |short-long| > 0.20.
    """
    name = "frequency"
    REVERSION_THRESHOLD = 0.20

    def _min_history(self) -> int:
        return max(FREQ_WINDOWS)

    def _compute(self, history: deque) -> tuple[str, float]:
        hist_list = list(history)
        ratios: dict[int, float] = {}
        for w in FREQ_WINDOWS:
            ratios[w] = hist_list[-w:].count("T") / w if len(hist_list) >= w else 0.5
        short, mid, long = ratios[10], ratios[30], ratios[50]
        momentum_label = None
        if short > mid > long:
            momentum_label = "T"
        elif short < mid < long:
            momentum_label = "X"
        reversion_label = None
        if abs(short - long) > self.REVERSION_THRESHOLD:
            reversion_label = "X" if short > long else "T"
        pred = (reversion_label if reversion_label is not None
                else (momentum_label if momentum_label is not None
                      else hist_list[-1]))
        conf = abs(short - 0.5) * 2.0
        return pred, _clamp(conf, 0.0, 1.0)


# ── Module 5: GradientBoostEngine ────────────────────────────────────────────

class GradientBoostEngine:
    """
    AI hẹp thực sự — scikit-learn GradientBoostingClassifier.

    Kiến trúc:
    - 18 features từ cửa sổ GB_FEATURE_WINDOW (=20) kết quả gần nhất.
    - Retrain nền mỗi GB_RETRAIN_EVERY (=50) updates, không block /predict.
    - Model mới được swap vào sau khi retrain xong (atomic).
    - Warmup GB_WARMUP (=80) samples — trước đó predict() trả (T, 0.5).
    - RAM: model ≈ 110 KB, buffer 500×18 float64 ≈ 70 KB. Tổng < 1 MB.

    18 features:
      0  ratio_full     — T-ratio toàn window 20
      1  ratio_5        — T-ratio 5 kết quả cuối
      2  ratio_10       — T-ratio 10 kết quả cuối
      3  ratio_15       — T-ratio 15 kết quả cuối
      4  streak_norm    — độ dài streak / window
      5  switch_rate    — tần suất đổi chiều trong window
      6  momentum_5_15  — ratio_5 - ratio_15 (xu hướng ngắn vs dài)
      7  momentum_10_15 — ratio_10 - ratio_15
      8  entropy        — Shannon entropy nhị phân của window
      9  last1          — kết quả -1 (binary)
      10 last2          — kết quả -2
      11 last3          — kết quả -3
      12 p_tx           — P(X | prev=T) trong window
      13 p_xt           — P(T | prev=X) trong window
      14 double_end     — 1 nếu 2 kết quả cuối giống nhau
      15 triple_end     — 1 nếu 3 kết quả cuối giống nhau
      16 alt_end        — 1 nếu 4 kết quả cuối xen kẽ hoàn toàn
      17 current        — kết quả cuối (binary)
    """

    name = "gb"

    def __init__(self):
        self._enabled = SKLEARN_AVAILABLE
        if not self._enabled:
            return

        self._lock         = threading.Lock()
        self._model        = None   # GradientBoostingClassifier | None
        self._scaler       = None   # StandardScaler | None
        self._X_buf: list  = []     # feature buffer
        self._y_buf: list  = []     # label buffer
        self._update_count = 0      # số updates kể từ retrain cuối
        self._ready        = False  # True sau warmup + lần retrain đầu tiên
        self._retrain_thread: threading.Thread | None = None

    # ── Feature engineering ───────────────────────────────────────────────

    @staticmethod
    def _build_features(history: list[str]) -> list[float]:
        w = history[-GB_FEATURE_WINDOW:] if len(history) >= GB_FEATURE_WINDOW else history
        n = len(w)
        nums = [1 if x == "T" else 0 for x in w]

        ratio_full = sum(nums) / n
        ratio_5    = sum(nums[-5:])  / min(n, 5)
        ratio_10   = sum(nums[-10:]) / min(n, 10)
        ratio_15   = sum(nums[-15:]) / min(n, 15)

        current = nums[-1]
        streak = 0
        for v in reversed(nums):
            if v == current:
                streak += 1
            else:
                break
        streak_norm = streak / n

        switches = sum(1 for i in range(1, n) if nums[i] != nums[i - 1])
        switch_rate = switches / max(n - 1, 1)

        momentum_5_15  = ratio_5  - ratio_15
        momentum_10_15 = ratio_10 - ratio_15

        p = ratio_full
        entropy = -(p * math.log2(p) + (1 - p) * math.log2(1 - p)) if 0 < p < 1 else 0.0

        last1 = nums[-1] if n >= 1 else 0.5
        last2 = nums[-2] if n >= 2 else 0.5
        last3 = nums[-3] if n >= 3 else 0.5

        tt = tx = xt = xx = 0
        for i in range(1, n):
            if   nums[i - 1] == 1 and nums[i] == 1: tt += 1
            elif nums[i - 1] == 1 and nums[i] == 0: tx += 1
            elif nums[i - 1] == 0 and nums[i] == 1: xt += 1
            else:                                     xx += 1

        p_tx = tx / max(tt + tx, 1)
        p_xt = xt / max(xt + xx, 1)

        double_end = 1 if n >= 2 and nums[-1] == nums[-2] else 0
        triple_end = 1 if n >= 3 and nums[-1] == nums[-2] == nums[-3] else 0
        alt_end    = 1 if (n >= 4 and nums[-1] != nums[-2]
                           and nums[-2] != nums[-3] and nums[-3] != nums[-4]) else 0

        return [
            ratio_full, ratio_5, ratio_10, ratio_15,
            streak_norm, switch_rate,
            momentum_5_15, momentum_10_15,
            entropy,
            last1, last2, last3,
            p_tx, p_xt,
            double_end, triple_end, alt_end,
            current,
        ]

    # ── Predict ───────────────────────────────────────────────────────────

    def predict(self, history: deque) -> tuple[str, float]:
        if not self._enabled or not self._ready:
            return "T", 0.5
        hist_list = list(history)
        if len(hist_list) < GB_FEATURE_WINDOW:
            return "T", 0.5
        feats = self._build_features(hist_list)
        with self._lock:
            model, scaler = self._model, self._scaler
        if model is None or scaler is None:
            return "T", 0.5
        try:
            X = np.array(feats, dtype=np.float64).reshape(1, -1)
            X_s = scaler.transform(X)
            proba = model.predict_proba(X_s)[0]  # [P(X), P(T)]
            # class ordering: 0=X, 1=T (sklearn sorts classes numerically: 0<1)
            p_t = float(proba[1])
            p_x = float(proba[0])
            if p_t >= p_x:
                return "T", _clamp(p_t, 0.0, 1.0)
            return "X", _clamp(p_x, 0.0, 1.0)
        except Exception as exc:
            log.warning("GradientBoostEngine.predict error: %s", exc)
            return "T", 0.5

    # ── Update (called after each confirmed result) ───────────────────────

    def add_sample(self, history: list[str], true_label: str) -> None:
        """Buffer one sample, trigger retrain when threshold reached."""
        if not self._enabled:
            return
        if len(history) < GB_FEATURE_WINDOW:
            return
        feats = self._build_features(history)
        y = 1 if true_label == "T" else 0
        with self._lock:
            self._X_buf.append(feats)
            self._y_buf.append(y)
            if len(self._X_buf) > GB_MAX_SAMPLES:
                self._X_buf.pop(0)
                self._y_buf.pop(0)
            buf_len = len(self._X_buf)
            self._update_count += 1
            should_retrain = (
                self._update_count >= GB_RETRAIN_EVERY
                and buf_len >= GB_WARMUP
                and (self._retrain_thread is None
                     or not self._retrain_thread.is_alive())
            )
            if should_retrain:
                X_snap = list(self._X_buf)
                y_snap = list(self._y_buf)
                self._update_count = 0

        if should_retrain:
            self._retrain_thread = threading.Thread(
                target=self._retrain,
                args=(X_snap, y_snap),
                daemon=True,
                name="kano-gb-retrain",
            )
            self._retrain_thread.start()

    def _retrain(self, X_snap: list, y_snap: list) -> None:
        """Background retrain. Swaps model atomically when done."""
        try:
            X = np.array(X_snap, dtype=np.float64)
            y = np.array(y_snap, dtype=np.int32)
            scaler = StandardScaler()
            X_s = scaler.fit_transform(X)
            model = GradientBoostingClassifier(
                n_estimators=80,
                max_depth=3,
                learning_rate=0.1,
                subsample=0.8,
                random_state=42,
            )
            model.fit(X_s, y)
            with self._lock:
                self._model  = model
                self._scaler = scaler
                self._ready  = True
            log.info(
                "GradientBoostEngine retrained on %d samples. Ready=%s",
                len(X_snap),
                self._ready,
            )
        except Exception as exc:
            log.error("GradientBoostEngine retrain failed: %s", exc)

    def bulk_train(self, history: list[str]) -> None:
        """Called once on startup from load_history."""
        if not self._enabled or len(history) < GB_WARMUP:
            return
        X_buf, y_buf = [], []
        for i in range(GB_FEATURE_WINDOW, len(history)):
            feats = self._build_features(history[:i])
            label = 1 if history[i] == "T" else 0
            X_buf.append(feats)
            y_buf.append(label)
            if len(X_buf) > GB_MAX_SAMPLES:
                X_buf.pop(0)
                y_buf.pop(0)
        with self._lock:
            self._X_buf = X_buf
            self._y_buf = y_buf
            self._update_count = 0
        # Retrain inline on startup (blocking, before first request)
        self._retrain(X_buf, y_buf)

    @property
    def is_ready(self) -> bool:
        return self._enabled and self._ready

    def status(self) -> dict:
        if not self._enabled:
            return {"enabled": False, "reason": "scikit-learn not installed"}
        with self._lock:
            return {
                "enabled":       True,
                "ready":         self._ready,
                "buffer_size":   len(self._X_buf),
                "warmup_needed": max(0, GB_WARMUP - len(self._X_buf)),
                "updates_since_retrain": self._update_count,
            }


# ── Module 6: MetaLearner ─────────────────────────────────────────────────────

class MetaLearner:
    """
    EMA-weighted voting. Số module = 4 hoặc 5 tuỳ sklearn availability.

    Contrarian flip: raw_winning > FLIP_THRESHOLD (tuyệt đối, không tương đối).
    Điều này ngăn low-confidence unanimous votes kích flip.
      conf=0.55, 5 modules đồng thuận → raw_winning=0.55 < 0.80 → không flip.
      conf=0.95, 5 modules đồng thuận → raw_winning=0.95 > 0.80 → flip.
    """

    def __init__(self, module_names: list[str]):
        n = len(module_names)
        self.weights: dict[str, float] = {name: 1.0 / n for name in module_names}

    def vote(self, signals: dict[str, tuple[str, float]]) -> tuple[str, float]:
        weighted_T = 0.0
        weighted_X = 0.0
        for name, (label, conf) in signals.items():
            w = self.weights.get(name, 0.0)
            if label == "T":
                weighted_T += w * conf
            else:
                weighted_X += w * conf
        total = weighted_T + weighted_X
        if total == 0:
            return "T", 0.5
        prob_T = weighted_T / total
        prob_X = weighted_X / total
        if prob_T >= prob_X:
            label, score, raw_winning = "T", prob_T, weighted_T
        else:
            label, score, raw_winning = "X", prob_X, weighted_X
        if raw_winning > FLIP_THRESHOLD:
            label = "X" if label == "T" else "T"
        return label, _clamp(score, 0.0, 1.0)

    def adjust_weights(
        self,
        signals: dict[str, tuple[str, float]],
        true_label: str,
    ) -> None:
        for name, (pred_label, _) in signals.items():
            w = self.weights.get(name, 0.0)
            if pred_label == true_label:
                w += EMA_ALPHA * (MAX_WEIGHT - w)
            else:
                w -= EMA_ALPHA * (w - MIN_WEIGHT)
            self.weights[name] = _clamp(w, MIN_WEIGHT, MAX_WEIGHT)
        self.weights = _normalize_weights(self.weights)


# ── Core predictor ────────────────────────────────────────────────────────────

class KanoPredictor:
    def __init__(self):
        self.history: deque[str] = deque(maxlen=HISTORY_LIMIT)
        self._session_id: str | None = None
        self._lock = threading.RLock()

        self.pattern   = PatternEngine()
        self.markov    = MarkovEngine()
        self.streak    = StreakEngine()
        self.frequency = FrequencyEngine()
        self.gb        = GradientBoostEngine()

        module_names = [
            self.pattern.name,
            self.markov.name,
            self.streak.name,
            self.frequency.name,
            self.gb.name,
        ]
        self.meta = MetaLearner(module_names)

        self._total_predictions   = 0
        self._correct_predictions = 0
        self._last_predict: dict | None = None
        self._startup_time = time.time()

        log.info(
            "KanoPredictor initialised. HISTORY_LIMIT=%d sklearn=%s",
            HISTORY_LIMIT,
            SKLEARN_AVAILABLE,
        )

    def load_history(self, results: list[str]) -> None:
        cleaned = [_safe_label(x) for x in results]
        cleaned = [x for x in cleaned if x is not None]
        trimmed = cleaned[-HISTORY_LIMIT:]
        with self._lock:
            self.history.clear()
            self.history.extend(trimmed)
            self.markov = MarkovEngine()
            self.markov.train(trimmed)
        # GB bulk train runs outside lock (blocking, ~0.1s)
        self.gb.bulk_train(trimmed)
        log.info("Loaded %d historical results. GB ready=%s", len(trimmed), self.gb.is_ready)

    def predict(self) -> dict:
        with self._lock:
            if len(self.history) < 3:
                return {
                    "label":        "T",
                    "confidence":   0.50,
                    "reason":       "Chưa đủ lịch sử",
                    "module_votes": {},
                    "weights":      dict(self.meta.weights),
                    "history_len":  len(self.history),
                    "gb_ready":     self.gb.is_ready,
                }
            signals = {
                self.pattern.name:   self.pattern.predict(self.history),
                self.markov.name:    self.markov.predict(self.history),
                self.streak.name:    self.streak.predict(self.history),
                self.frequency.name: self.frequency.predict(self.history),
                self.gb.name:        self.gb.predict(self.history),
            }
            label, conf = self.meta.vote(signals)
            self._total_predictions += 1
            result = {
                "label":      label,
                "confidence": round(conf, 4),
                "reason":     self._build_reason(signals, label),
                "module_votes": {
                    k: {"label": v[0], "confidence": round(v[1], 4)}
                    for k, v in signals.items()
                },
                "weights": {
                    k: round(v, 4) for k, v in self.meta.weights.items()
                },
                "history_len": len(self.history),
                "gb_ready":    self.gb.is_ready,
            }
            self._last_predict = result
            return result

    def update(self, true_label: str, session_id: str | None = None) -> None:
        true_label = _safe_label(true_label)
        if true_label is None:
            log.warning("Invalid label ignored.")
            return
        with self._lock:
            if self._last_predict:
                if self._last_predict["label"] == true_label:
                    self._correct_predictions += 1
                snapshot = {
                    name: (vote["label"], vote["confidence"])
                    for name, vote in self._last_predict["module_votes"].items()
                }
                self.meta.adjust_weights(snapshot, true_label)
            history_snapshot = list(self.history)
            self.history.append(true_label)
            self.markov.update(true_label, self.history)
            if session_id and session_id != self._session_id:
                self._session_id = session_id
                log.info("New session detected: %s", session_id)
        # GB add_sample outside RLock (has its own lock)
        history_snapshot.append(true_label)
        self.gb.add_sample(history_snapshot, true_label)

    def stats(self) -> dict:
        with self._lock:
            acc = (self._correct_predictions / self._total_predictions
                   if self._total_predictions else 0.0)
            return {
                "total_predictions":   self._total_predictions,
                "correct_predictions": self._correct_predictions,
                "accuracy":            round(acc, 4),
                "history_len":         len(self.history),
                "history_limit":       HISTORY_LIMIT,
                "uptime_seconds":      round(time.time() - self._startup_time, 1),
                "session_id":          self._session_id,
                "module_weights": {
                    k: round(v, 4) for k, v in self.meta.weights.items()
                },
                "gb": self.gb.status(),
            }

    def summary(self, n: int = 50) -> dict:
        n = max(1, min(int(n), HISTORY_LIMIT))
        with self._lock:
            window = list(self.history)[-n:]
        counts = {label: window.count(label) for label in LABELS}
        return {
            "window":        n,
            "actual_window": len(window),
            "counts":        counts,
            "t_ratio":       round(counts.get("T", 0) / max(len(window), 1), 4),
        }

    def streak_info(self, n: int = 20) -> dict:
        n = max(1, min(int(n), HISTORY_LIMIT))
        with self._lock:
            window = list(self.history)[-n:]
        if not window:
            return {"streak_label": None, "streak_length": 0}
        current = window[-1]
        length = 0
        for lbl in reversed(window):
            if lbl == current:
                length += 1
            else:
                break
        return {
            "streak_label":  current,
            "streak_length": length,
            "window":        n,
            "entropy":       round(_entropy({lb: window.count(lb) for lb in LABELS}), 4),
        }

    @staticmethod
    def _build_reason(signals: dict, final_label: str) -> str:
        agreement = sum(1 for v in signals.values() if v[0] == final_label)
        total = len(signals)
        return f"{agreement}/{total} modules đồng thuận → {final_label}"


# ── Singleton ─────────────────────────────────────────────────────────────────

predictor = KanoPredictor()


# ── Startup loader ────────────────────────────────────────────────────────────

def startup_load(history_api_url: str, max_pages: int = 10) -> None:
    """
    Load lịch sử từ API game khi khởi động.

    Fix v4.1:
      [1] API trả về key "list", không phải "data" — đã sửa.
      [2] API trả 403 khi không có browser header — đã thêm User-Agent.
      [3] URL đã có params sẵn → không append thêm ?page= nữa, chỉ dùng URL gốc.
    """
    if not history_api_url:
        log.info("HISTORY_API_URL not configured; skipping startup load.")
        return

    # Header giả browser để tránh 403 Forbidden
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://bettv-predictor.onrender.com/",
    }

    all_results: list[str] = []

    # API này không hỗ trợ pagination — chỉ gọi 1 lần với URL gốc
    try:
        req = urllib.request.Request(history_api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        # API trả về {"list": [...], "typeStat": {...}}
        # Thử cả "list" và "data" để tương thích
        rows = data.get("list") or data.get("data") or []
        if not isinstance(rows, list):
            log.warning("startup_load: API response không có key list/data.")
            return

        all_results = [
            _safe_label(r.get("resultTruyenThong"))
            for r in rows if isinstance(r, dict)
        ]
        all_results = [x for x in all_results if x in LABELS]
        log.info("startup_load: lấy được %d kết quả từ API.", len(all_results))

    except Exception as exc:
        log.warning("startup_load failed: %s", exc)
        return

    if all_results:
        predictor.load_history(all_results)
    else:
        log.warning("startup_load: không lấy được kết quả hợp lệ nào.")


# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.get("/ping")
def ping():
    return jsonify({
        "status":      "ok",
        "service":     "kano-ai",
        "history_len": len(predictor.history),
        "gb_ready":    predictor.gb.is_ready,
    })


@app.get("/predict")
def predict_endpoint():
    return jsonify(predictor.predict())


@app.post("/update")
def update_endpoint():
    payload = request.get_json(silent=True) or {}
    label = (payload.get("label")
             or payload.get("resultTruyenThong")
             or payload.get("result"))
    session_id = payload.get("session_id")
    label = _safe_label(label)
    if label is None:
        return jsonify({"ok": False, "error": "label must be T or X"}), 400
    predictor.update(label, session_id)
    return jsonify({"ok": True, "updated_label": label, "stats": predictor.stats()})


@app.get("/stats")
def stats_endpoint():
    return jsonify(predictor.stats())


@app.get("/summary_50")
def summary_50_endpoint():
    return jsonify(predictor.summary(50))


@app.get("/streak_20")
def streak_20_endpoint():
    return jsonify(predictor.streak_info(20))


@app.get("/accuracy")
def accuracy_endpoint():
    return jsonify({"accuracy": predictor.stats()["accuracy"]})


@app.get("/history")
def history_endpoint():
    with predictor._lock:
        history = list(predictor.history)[-100:]
    return jsonify({"history": history, "count": len(history)})


@app.get("/")
def root_endpoint():
    return jsonify({
        "service":   "kano-ai",
        "status":    "running",
        "endpoints": ["/ping", "/predict", "/update", "/stats",
                      "/summary_50", "/streak_20", "/accuracy", "/history"],
    })


# ── Environment wiring ────────────────────────────────────────────────────────

def initialize_from_environment() -> None:
    enabled = os.getenv("ENABLE_STARTUP_LOAD", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not enabled:
        return
    api_url = os.getenv("HISTORY_API_URL", "").strip()
    if not api_url:
        return
    try:
        max_pages = max(1, int(os.getenv("STARTUP_MAX_PAGES", "10")))
    except ValueError:
        max_pages = 10
    startup_load(api_url, max_pages=max_pages)


def startup_initialize() -> None:
    background = os.getenv("STARTUP_LOAD_BACKGROUND", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if background:
        threading.Thread(
            target=initialize_from_environment,
            name="kano-startup-loader",
            daemon=True,
        ).start()
    else:
        initialize_from_environment()


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    startup_initialize()
    host = os.getenv("HOST", "0.0.0.0")
    try:
        port = int(os.getenv("PORT", "10000"))
    except ValueError:
        port = 10000
    log.info("Starting Kano AI on %s:%d", host, port)
    app.run(host=host, port=port, threaded=True)
