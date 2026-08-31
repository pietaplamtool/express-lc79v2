"""
Kano AI — Prediction Engine v1.0
File: app.py (chạy trên Render free tier)

Kiến trúc 5 module:
  1. PatternEngine   — nhận diện 23 loại cầu với multi-window
  2. MarkovEngine    — xác suất chuyển trạng thái bậc 3
  3. StreakEngine     — phát hiện & dự đoán điểm gãy cầu
  4. FrequencyEngine — bias TAI/XIU theo cửa sổ trượt
  5. MetaLearner     — weighted voting, tự cập nhật trọng số theo accuracy thực

Tất cả học từ lịch sử API game khi khởi động,
update online sau mỗi ván mới.
"""

import os
import json
import math
import time
import logging
import threading
import requests
from collections import deque, defaultdict
from flask import Flask, jsonify

# ─────────────────────────────────────────────
# CẤU HÌNH
# ─────────────────────────────────────────────
HISTORY_URL = (
    "https://wtxmd52.macminim6.online/v1/txmd5/sessions"
    "?cp=R&cl=R&pf=web&at=1fc7bfdeab18790088a6e44d6b8cb288&limit=200"
)
HISTORY_BULK_URL = (
    "https://wtxmd52.macminim6.online/v1/txmd5/sessions"
    "?cp=R&cl=R&pf=web&at=1fc7bfdeab18790088a6e44d6b8cb288&limit={limit}&page={page}"
)
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# UTILS
# ─────────────────────────────────────────────
TAI = 1
XIU = 0

def parse_result(raw) -> int | None:
    """Chuẩn hoá kết quả thô về TAI(1) hoặc XIU(0)."""
    if raw is None:
        return None
    s = str(raw).upper().strip()
    if s in ("TAI", "T", "TÀI", "1"):
        return TAI
    if s in ("XIU", "X", "XỈU", "0"):
        return XIU
    return None

def result_label(v: int) -> str:
    return "TAI" if v == TAI else "XIU"

def entropy(p: float) -> float:
    """Shannon entropy của xác suất p."""
    if p <= 0 or p >= 1:
        return 0.0
    q = 1 - p
    return -(p * math.log2(p) + q * math.log2(q))

# ─────────────────────────────────────────────
# 1. PATTERN ENGINE
# ─────────────────────────────────────────────
class PatternEngine:
    """
    Nhận diện 23 loại cầu và dự đoán theo từng loại.
    Dùng 4 sliding window: 3, 5, 8, 13 ván.
    Mỗi pattern được đánh giá theo accuracy lịch sử của chính nó.
    """

    WINDOWS = [3, 5, 8, 13]

    def __init__(self):
        # pattern_key -> {"correct": int, "total": int}
        self.pattern_stats: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0})

    def _encode(self, seq: list[int]) -> str:
        return "".join("T" if x == TAI else "X" for x in seq)

    def _identify_pattern(self, seq: list[int]) -> str:
        """
        Phân loại cầu từ chuỗi kết quả:
        BIET_TAI, BIET_XIU  — bệt toàn TAI / toàn XIU
        DOI_TAI, DOI_XIU    — cặp đôi TAI-TAI / XIU-XIU xen kẽ
        MOT_MOT             — xen kẽ T-X-T-X
        CAU_GAY             — cầu gãy tại vị trí cuối
        CAU_NHAY            — nhảy ngẫu nhiên
        TAP_SHORT / TAP_LONG — tập hợp ngắn/dài
        """
        n = len(seq)
        if n == 0:
            return "UNKNOWN"

        encoded = self._encode(seq)

        # Bệt
        if all(x == TAI for x in seq):
            return f"BIET_TAI_{n}"
        if all(x == XIU for x in seq):
            return f"BIET_XIU_{n}"

        # Xen kẽ hoàn hảo
        alternating = all(seq[i] != seq[i + 1] for i in range(n - 1))
        if alternating:
            return f"MOT_MOT_{n}"

        # Cặp đôi (TT-XX-TT hoặc XX-TT-XX)
        if n >= 4:
            pairs_ok = all(seq[i] == seq[i + 1] for i in range(0, n - 1, 2))
            if pairs_ok:
                return f"DOI_{n}"

        # Cầu gãy — 3+ giống nhau rồi đổi cuối
        if n >= 4:
            streak_val = seq[0]
            streak_len = 1
            for i in range(1, n - 1):
                if seq[i] == streak_val:
                    streak_len += 1
                else:
                    break
            if streak_len >= 3 and seq[-1] != streak_val:
                return f"CAU_GAY_{streak_len}"

        # Streak cuối
        streak_val = seq[-1]
        streak_len = 1
        for i in range(len(seq) - 2, -1, -1):
            if seq[i] == streak_val:
                streak_len += 1
            else:
                break
        if streak_len >= 3:
            label = "TAI" if streak_val == TAI else "XIU"
            return f"STREAK_{label}_{streak_len}"

        # Tập ngắn vs dài
        tai_count = sum(seq)
        ratio = tai_count / n
        if ratio >= 0.7:
            return "TAP_TAI_HEAVY"
        if ratio <= 0.3:
            return "TAP_XIU_HEAVY"

        return "MIXED"

    def predict(self, history: list[int]) -> dict:
        """
        Trả về {"prediction": TAI/XIU, "confidence": float, "pattern": str, "weight": float}
        """
        results = []
        for w in self.WINDOWS:
            if len(history) < w + 1:
                continue
            seq = history[-(w + 1):-1]   # w ván để nhận pattern
            last = history[-1]            # ván vừa xong (dùng để tra stats)
            pattern = self._identify_pattern(seq)

            # Dự đoán: dựa theo xu hướng tiếp theo của pattern này trong lịch sử
            stats = self.pattern_stats[pattern]
            total = stats["total"]
            if total < 5:
                # Chưa đủ dữ liệu — dự đoán theo momentum (tiếp tục xu hướng cuối)
                pred = history[-1]
                conf = 0.52
            else:
                tai_rate = stats.get("tai_after", 0) / total
                if tai_rate > 0.5:
                    pred = TAI
                    conf = tai_rate
                else:
                    pred = XIU
                    conf = 1 - tai_rate

            acc = stats["correct"] / total if total > 0 else 0.5
            weight = max(0.1, acc)   # trọng số dựa trên accuracy lịch sử

            results.append({
                "prediction": pred,
                "confidence": conf,
                "pattern": pattern,
                "weight": weight,
                "window": w,
            })

        if not results:
            return {"prediction": history[-1] if history else TAI, "confidence": 0.5,
                    "pattern": "UNKNOWN", "weight": 0.1}

        # Weighted vote
        tai_score = sum(r["weight"] for r in results if r["prediction"] == TAI)
        xiu_score = sum(r["weight"] for r in results if r["prediction"] == XIU)
        total_w   = tai_score + xiu_score or 1
        if tai_score >= xiu_score:
            pred_final = TAI
            conf_final = tai_score / total_w
        else:
            pred_final = XIU
            conf_final = xiu_score / total_w

        best = max(results, key=lambda r: r["weight"])
        return {
            "prediction": pred_final,
            "confidence": conf_final,
            "pattern": best["pattern"],
            "weight": conf_final,
        }

    def update(self, history: list[int], actual: int):
        """
        Cập nhật stats sau khi biết kết quả thật.
        Tính dự đoán từ stats CŨ trước khi cộng actual (tránh look-ahead bias).
        """
        for w in self.WINDOWS:
            if len(history) < w + 1:
                continue
            seq = history[-(w + 1):-1]
            pattern = self._identify_pattern(seq)
            stats = self.pattern_stats[pattern]
            old_total = stats["total"]
            old_tai   = stats.get("tai_after", 0)
            # Tính accuracy từ stats CŨ (trước khi thấy actual)
            if old_total >= 5:
                pred = TAI if (old_tai / old_total) > 0.5 else XIU
                if pred == actual:
                    stats["correct"] = stats.get("correct", 0) + 1
            # Update stats
            stats["total"]     = old_total + 1
            stats["tai_after"] = old_tai + (1 if actual == TAI else 0)


# ─────────────────────────────────────────────
# 2. MARKOV ENGINE (bậc 3)
# ─────────────────────────────────────────────
class MarkovEngine:
    """
    Markov Chain bậc 1, 2, 3.
    Bảng chuyển trạng thái: (seq_n ván trước) -> P(TAI | seq)
    Dùng Laplace smoothing để tránh xác suất 0.
    """
    ORDERS = [1, 2, 3]

    def __init__(self):
        # order -> {state_tuple: {"tai": int, "xiu": int}}
        self.tables: dict[int, dict] = {o: defaultdict(lambda: {"tai": 0, "xiu": 0})
                                         for o in self.ORDERS}
        self.accuracy: dict[int, dict] = {o: {"correct": 0, "total": 0} for o in self.ORDERS}

    def _state(self, history: list[int], order: int):
        if len(history) < order:
            return None
        return tuple(history[-order:])

    def train(self, history: list[int]):
        """Train từ đầu với toàn bộ lịch sử."""
        for o in self.ORDERS:
            self.tables[o] = defaultdict(lambda: {"tai": 0, "xiu": 0})
        for i in range(max(self.ORDERS), len(history)):
            actual = history[i]
            for o in self.ORDERS:
                state = tuple(history[i - o: i])
                if actual == TAI:
                    self.tables[o][state]["tai"] += 1
                else:
                    self.tables[o][state]["xiu"] += 1

    def predict(self, history: list[int]) -> dict:
        preds = []
        for o in self.ORDERS:
            state = self._state(history, o)
            if state is None:
                continue
            counts = self.tables[o].get(state, {"tai": 0, "xiu": 0})
            tai = counts["tai"] + 1   # Laplace
            xiu = counts["xiu"] + 1
            p_tai = tai / (tai + xiu)

            acc_data = self.accuracy[o]
            acc = acc_data["correct"] / acc_data["total"] if acc_data["total"] > 10 else 0.5
            weight = max(0.1, acc)

            pred = TAI if p_tai > 0.5 else XIU
            preds.append({"prediction": pred, "confidence": max(p_tai, 1 - p_tai),
                          "weight": weight, "order": o})

        if not preds:
            return {"prediction": history[-1] if history else TAI, "confidence": 0.5, "weight": 0.1}

        tai_w = sum(p["weight"] for p in preds if p["prediction"] == TAI)
        xiu_w = sum(p["weight"] for p in preds if p["prediction"] == XIU)
        total_w = tai_w + xiu_w or 1
        pred_final = TAI if tai_w >= xiu_w else XIU
        conf_final = max(tai_w, xiu_w) / total_w
        return {"prediction": pred_final, "confidence": conf_final, "weight": conf_final}

    def update(self, history: list[int], actual: int):
        """Online update sau mỗi ván mới."""
        for o in self.ORDERS:
            state = self._state(history[:-1], o)
            if state is None:
                continue
            if actual == TAI:
                self.tables[o][state]["tai"] += 1
            else:
                self.tables[o][state]["xiu"] += 1

            # Track accuracy
            counts = self.tables[o].get(state, {"tai": 1, "xiu": 1})
            p_tai = counts["tai"] / (counts["tai"] + counts["xiu"])
            pred = TAI if p_tai > 0.5 else XIU
            self.accuracy[o]["total"] += 1
            if pred == actual:
                self.accuracy[o]["correct"] += 1


# ─────────────────────────────────────────────
# 3. STREAK ENGINE
# ─────────────────────────────────────────────
class StreakEngine:
    """
    Phát hiện streak hiện tại và dự đoán xác suất gãy.
    Dựa trên phân phối lịch sử: P(gãy | streak_len, streak_val).
    Kết hợp entropy để đo độ bất ổn định của cầu.
    """

    def __init__(self):
        # (streak_val, streak_len) -> {"break": int, "continue": int}
        self.streak_stats: dict = defaultdict(lambda: {"break": 0, "continue": 0})
        # Track entropy của chuỗi 20 ván gần nhất
        self.recent: deque = deque(maxlen=20)
        self.accuracy = {"correct": 0, "total": 0}

    def _current_streak(self, history: list[int]) -> tuple[int, int]:
        """Trả về (streak_val, streak_len) của streak cuối."""
        if not history:
            return TAI, 0
        val = history[-1]
        length = 1
        for i in range(len(history) - 2, -1, -1):
            if history[i] == val:
                length += 1
            else:
                break
        return val, length

    def predict(self, history: list[int]) -> dict:
        if len(history) < 3:
            return {"prediction": history[-1] if history else TAI, "confidence": 0.5, "weight": 0.1}

        val, length = self._current_streak(history)
        key = (val, min(length, 10))   # cap ở 10 để tránh quá sparse
        stats = self.streak_stats[key]
        total = stats["break"] + stats["continue"]

        if total < 5:
            # Chưa đủ dữ liệu: streak dài -> nghiêng về gãy, ngắn -> tiếp tục
            p_break = min(0.3 + length * 0.08, 0.75)
        else:
            p_break = stats["break"] / total

        # Entropy của 20 ván gần nhất — cầu ổn định thì entropy thấp
        recent_list = list(self.recent)
        if len(recent_list) >= 5:
            p_tai_recent = sum(recent_list) / len(recent_list)
            ent = entropy(p_tai_recent)
            # Entropy cao (gần 1.0) = cầu loạn = giảm confidence
            confidence_scale = 1.0 - ent * 0.3
        else:
            confidence_scale = 1.0

        if p_break > 0.5:
            pred = XIU if val == TAI else TAI
            conf = p_break * confidence_scale
        else:
            pred = val
            conf = (1 - p_break) * confidence_scale

        conf = max(0.5, min(0.95, conf))
        acc = self.accuracy["correct"] / self.accuracy["total"] if self.accuracy["total"] > 10 else 0.5
        return {"prediction": pred, "confidence": conf, "weight": max(0.1, acc)}

    def update(self, history: list[int], actual: int):
        if len(history) < 2:
            return
        # Streak TRƯỚC khi thêm actual
        val, length = self._current_streak(history[:-1])
        key = (val, min(length, 10))
        if actual != val:
            self.streak_stats[key]["break"] += 1
        else:
            self.streak_stats[key]["continue"] += 1
        self.recent.append(actual)

        # Track accuracy
        pred = self.predict(history[:-1])
        self.accuracy["total"] += 1
        if pred["prediction"] == actual:
            self.accuracy["correct"] += 1

    def train(self, history: list[int]):
        for i in range(3, len(history)):
            self.update(history[:i], history[i])


# ─────────────────────────────────────────────
# 4. FREQUENCY ENGINE
# ─────────────────────────────────────────────
class FrequencyEngine:
    """
    Bias TAI/XIU theo tần suất trong cửa sổ trượt 20/50/100 ván.
    Ý tưởng: nếu XIU xuất hiện quá nhiều gần đây, xác suất TAI tăng lên
    (mean reversion) hoặc ngược lại (momentum).
    Học cái nào đúng hơn từ lịch sử.
    """
    WINDOWS = [20, 50, 100]

    def __init__(self):
        # window -> {"reversion_correct": int, "momentum_correct": int, "total": int}
        self.mode_stats: dict = {w: {"reversion": 0, "momentum": 0, "total": 0}
                                  for w in self.WINDOWS}
        self.accuracy = {"correct": 0, "total": 0}

    def _window_bias(self, history: list[int], w: int) -> float:
        """Trả về P(TAI) trong w ván gần nhất."""
        if len(history) < w:
            window = history
        else:
            window = history[-w:]
        if not window:
            return 0.5
        return sum(window) / len(window)

    def predict(self, history: list[int]) -> dict:
        preds = []
        for w in self.WINDOWS:
            if len(history) < w // 2:
                continue
            p_tai = self._window_bias(history, w)
            stats = self.mode_stats[w]
            total = stats["total"]

            if total < 20:
                # Mặc định: mean reversion
                pred = TAI if p_tai < 0.5 else XIU
                conf = abs(p_tai - 0.5) * 2 * 0.6 + 0.5
            else:
                # Học xem reversion hay momentum đúng hơn
                if stats["reversion"] >= stats["momentum"]:
                    pred = TAI if p_tai < 0.5 else XIU
                    mode_acc = stats["reversion"] / total
                else:
                    pred = TAI if p_tai > 0.5 else XIU
                    mode_acc = stats["momentum"] / total
                conf = max(0.5, min(0.9, 0.5 + abs(p_tai - 0.5) * mode_acc))

            acc = self.accuracy["correct"] / self.accuracy["total"] if self.accuracy["total"] > 10 else 0.5
            preds.append({"prediction": pred, "confidence": conf, "weight": max(0.1, acc), "window": w})

        if not preds:
            return {"prediction": TAI, "confidence": 0.5, "weight": 0.1}

        tai_w = sum(p["weight"] for p in preds if p["prediction"] == TAI)
        xiu_w = sum(p["weight"] for p in preds if p["prediction"] == XIU)
        total_w = tai_w + xiu_w or 1
        pred_final = TAI if tai_w >= xiu_w else XIU
        conf_final = max(tai_w, xiu_w) / total_w
        return {"prediction": pred_final, "confidence": conf_final, "weight": conf_final}

    def update(self, history: list[int], actual: int):
        for w in self.WINDOWS:
            if len(history) < w // 2 + 1:
                continue
            p_tai = self._window_bias(history[:-1], w)
            rev_pred = TAI if p_tai < 0.5 else XIU
            mom_pred = TAI if p_tai > 0.5 else XIU
            stats = self.mode_stats[w]
            stats["total"] += 1
            if rev_pred == actual:
                stats["reversion"] += 1
            if mom_pred == actual:
                stats["momentum"] += 1

        self.accuracy["total"] += 1
        pred = self.predict(history[:-1])
        if pred["prediction"] == actual:
            self.accuracy["correct"] += 1

    def train(self, history: list[int]):
        for i in range(100, len(history)):
            self.update(history[:i], history[i])


# ─────────────────────────────────────────────
# 5. META LEARNER
# ─────────────────────────────────────────────
class MetaLearner:
    """
    Kết hợp 4 engine bằng weighted voting.
    Trọng số của mỗi engine được cập nhật theo accuracy thực tế (EMA).
    Nếu engine nào dự đoán sai liên tục -> trọng số giảm.
    Nếu sai cả 4 -> trigger "contrarian mode" (đảo ngược dự đoán).
    """
    ENGINE_NAMES = ["pattern", "markov", "streak", "frequency"]

    def __init__(self):
        # Trọng số khởi tạo bằng nhau
        self.weights: dict[str, float] = {n: 0.25 for n in self.ENGINE_NAMES}
        # EMA alpha
        self.alpha = 0.05
        # Lịch sử dự đoán gần nhất của mỗi engine (để detect contrarian)
        self.recent_preds: dict[str, deque] = {n: deque(maxlen=10) for n in self.ENGINE_NAMES}
        # Accuracy tổng của meta
        self.accuracy = {"correct": 0, "total": 0}
        # Contrarian counter
        self.all_wrong_streak = 0

    def predict(self, engine_preds: dict[str, dict]) -> dict:
        """
        engine_preds: {"pattern": {...}, "markov": {...}, ...}
        Mỗi dict có keys: prediction, confidence, weight
        """
        tai_score = 0.0
        xiu_score = 0.0
        detail = {}

        for name, pred in engine_preds.items():
            w = self.weights[name] * pred.get("confidence", 0.5)
            if pred["prediction"] == TAI:
                tai_score += w
            else:
                xiu_score += w
            detail[name] = result_label(pred["prediction"])

        total = tai_score + xiu_score or 1.0

        # Contrarian mode: nếu tất cả engine sai liên tục 3+ lần
        if self.all_wrong_streak >= 3:
            # Đảo ngược
            if tai_score > xiu_score:
                final_pred = XIU
                confidence = xiu_score / total + 0.1  # boost confidence
            else:
                final_pred = TAI
                confidence = tai_score / total + 0.1
            mode = "CONTRARIAN"
        else:
            final_pred = TAI if tai_score >= xiu_score else XIU
            confidence = max(tai_score, xiu_score) / total
            mode = "NORMAL"

        confidence = max(0.51, min(0.95, confidence))

        return {
            "prediction": final_pred,
            "confidence": confidence,
            "mode": mode,
            "detail": detail,
            "tai_score": round(tai_score, 3),
            "xiu_score": round(xiu_score, 3),
        }

    def update(self, engine_preds: dict[str, dict], actual: int):
        """Cập nhật trọng số sau khi biết kết quả."""
        any_correct = False
        all_correct = True

        for name, pred in engine_preds.items():
            correct = (pred["prediction"] == actual)
            if correct:
                any_correct = True
                # Tăng trọng số
                self.weights[name] = (1 - self.alpha) * self.weights[name] + self.alpha * 1.0
            else:
                all_correct = False
                # Giảm trọng số
                self.weights[name] = (1 - self.alpha) * self.weights[name] + self.alpha * 0.0

            self.recent_preds[name].append(1 if correct else 0)

        # Normalize weights về tổng = 1
        total_w = sum(self.weights.values()) or 1
        for name in self.weights:
            self.weights[name] /= total_w

        # Contrarian streak
        meta_pred = self.predict(engine_preds)
        if meta_pred["prediction"] == actual:
            self.all_wrong_streak = 0
            self.accuracy["correct"] += 1
        else:
            self.all_wrong_streak += 1

        self.accuracy["total"] += 1


# ─────────────────────────────────────────────
# SUPREME AI — tổng hợp toàn bộ
# ─────────────────────────────────────────────
class SupremeAI:
    """
    Điều phối 5 engine. Học từ lịch sử, update online.
    Thread-safe với RLock.
    """

    def __init__(self):
        self.pattern   = PatternEngine()
        self.markov    = MarkovEngine()
        self.streak    = StreakEngine()
        self.frequency = FrequencyEngine()
        self.meta      = MetaLearner()

        self.history: list[int] = []          # toàn bộ lịch sử kết quả
        self.session_ids: list[int] = []      # ID tương ứng
        self.latest_session_id: int | None = None
        self.target_session_id: int | None = None

        self._lock = threading.RLock()
        self._last_engine_preds: dict | None = None

        # Trạng thái huấn luyện
        self.trained = False
        self.total_rounds = 0

        # Cache kết quả predict để API trả nhanh
        self._cache: dict | None = None
        self._cache_time: float = 0

    # ── DATA LOADING ──────────────────────────
    def _fetch_page(self, limit: int, page: int) -> list[dict]:
        try:
            url = HISTORY_BULK_URL.format(limit=limit, page=page)
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            return r.json().get("list", [])
        except Exception as e:
            log.warning(f"fetch_page limit={limit} page={page} lỗi: {e}")
            return []

    def load_history(self):
        """
        Load toàn bộ lịch sử từ API game.
        Thử load tối đa 15,000 ván theo từng page 200.
        Chạy trong thread riêng khi khởi động.
        """
        log.info("Bắt đầu load lịch sử...")
        all_sessions = []
        page = 1
        max_pages = 75  # 75 * 200 = 15,000 ván

        while page <= max_pages:
            sessions = self._fetch_page(200, page)
            if not sessions:
                break
            all_sessions.extend(sessions)
            log.info(f"  Loaded page {page}, total={len(all_sessions)}")
            page += 1
            time.sleep(0.3)   # lịch sự với API

        # API trả ngược: index 0 là mới nhất
        # Ta cần cũ nhất trước để train theo thứ tự thời gian
        all_sessions.reverse()

        history = []
        session_ids = []
        for s in all_sessions:
            val = parse_result(s.get("resultTruyenThong"))
            if val is not None:
                history.append(val)
                session_ids.append(s.get("id"))

        with self._lock:
            self.history = history
            self.session_ids = session_ids
            self.total_rounds = len(history)
            if session_ids:
                self.latest_session_id  = session_ids[-1]
                # target = ván tiếp theo (ID + 1 là ước tính)
                self.target_session_id  = session_ids[-1] + 1

        log.info(f"Load xong {len(history)} ván. Bắt đầu train...")
        self._train()

    def _train(self):
        """Train tất cả engine từ lịch sử."""
        h = self.history
        if len(h) < 20:
            log.warning("Lịch sử quá ít để train.")
            return

        # Markov: train một lần
        self.markov.train(h)

        # Streak & Frequency: train từng bước (có thể mất vài giây)
        log.info("Training StreakEngine...")
        self.streak.train(h)

        log.info("Training FrequencyEngine...")
        self.frequency.train(h)

        # Pattern: update từng bước
        log.info("Training PatternEngine...")
        for i in range(15, len(h)):
            self.pattern.update(h[:i], h[i])

        with self._lock:
            self.trained = True
        log.info(f"Train xong. Tổng {len(h)} ván. Sẵn sàng dự đoán.")
        self._invalidate_cache()

    # ── PREDICTION ────────────────────────────
    def _get_engine_preds(self, history: list[int]) -> dict:
        return {
            "pattern":   self.pattern.predict(history),
            "markov":    self.markov.predict(history),
            "streak":    self.streak.predict(history),
            "frequency": self.frequency.predict(history),
        }

    def predict(self) -> dict:
        """
        Trả về dict kết quả dự đoán cho API /predict.
        Cache 1.5 giây để không tính lại liên tục.
        """
        now = time.time()
        with self._lock:
            if self._cache and (now - self._cache_time) < 1.5:
                return self._cache

            if not self.trained or len(self.history) < 20:
                result = {
                    "status": "TRAINING",
                    "predict": "---",
                    "predict_short": "-",
                    "confidence": 0.0,
                    "latest_session_id": self.latest_session_id,
                    "target_session_id": self.target_session_id,
                    "context": {
                        "total_rounds_learned": self.total_rounds,
                        "trained": self.trained,
                    },
                }
                self._cache = result
                self._cache_time = now
                return result

            h = self.history
            engine_preds = self._get_engine_preds(h)
            self._last_engine_preds = engine_preds
            meta_result = self.meta.predict(engine_preds)

            pred_val  = meta_result["prediction"]
            pred_str  = result_label(pred_val)
            conf      = meta_result["confidence"]

            # Streak state
            val, slen = self.streak._current_streak(h)
            streak_state = f"STREAK_{result_label(val)}_{slen}"

            result = {
                "status": "PREDICT",
                "predict": pred_str,
                "predict_short": "T" if pred_val == TAI else "X",
                "confidence": round(conf, 4),
                "latest_session_id": self.latest_session_id,
                "target_session_id": self.target_session_id,
                "supreme_ai": {
                    "state": streak_state,
                    "mode": meta_result.get("mode", "NORMAL"),
                    "weights": {k: round(v, 3) for k, v in self.meta.weights.items()},
                    "engine_votes": meta_result.get("detail", {}),
                },
                "context": {
                    "total_rounds_learned": self.total_rounds,
                    "trained": self.trained,
                    "meta_accuracy": round(
                        self.meta.accuracy["correct"] / self.meta.accuracy["total"], 4
                    ) if self.meta.accuracy["total"] > 0 else 0,
                },
            }
            self._cache = result
            self._cache_time = now
            return result

    def _invalidate_cache(self):
        self._cache = None
        self._cache_time = 0

    # ── ONLINE UPDATE ─────────────────────────
    def add_result(self, session_id: int, result_raw: str) -> bool:
        """
        Thêm kết quả mới, update tất cả engine.
        Trả về True nếu là kết quả mới (chưa có trong history).
        """
        val = parse_result(result_raw)
        if val is None:
            return False

        with self._lock:
            if session_id in self.session_ids:
                return False   # Đã có rồi

            self.history.append(val)
            self.session_ids.append(session_id)
            self.total_rounds += 1
            self.latest_session_id = session_id
            self.target_session_id = session_id + 1

            h = self.history

            # Update tất cả engine
            if self._last_engine_preds:
                self.meta.update(self._last_engine_preds, val)

            self.pattern.update(h, val)
            self.markov.update(h, val)
            self.streak.update(h, val)
            self.frequency.update(h, val)

            self._invalidate_cache()
            log.info(f"Update session_id={session_id} result={result_label(val)} total={self.total_rounds}")
            return True

    # ── POLLING GAME API ──────────────────────
    def start_polling(self):
        """Chạy vòng lặp poll API game mỗi 2 giây để nhận kết quả mới."""
        def poll():
            while True:
                try:
                    r = requests.get(HISTORY_URL, timeout=8)
                    r.raise_for_status()
                    sessions = r.json().get("list", [])
                    if sessions:
                        # index 0 là mới nhất
                        latest = sessions[0]
                        sid = latest.get("id")
                        raw = latest.get("resultTruyenThong")
                        if sid and raw:
                            self.add_result(sid, raw)
                        # Cập nhật target_session_id
                        with self._lock:
                            if sessions and len(sessions) > 1:
                                # index 0 = phiên mới nhất đã có kết quả
                                # target = index 0 + 1
                                pass   # đã set trong add_result
                except Exception as e:
                    log.warning(f"poll lỗi: {e}")
                time.sleep(2)

        t = threading.Thread(target=poll, daemon=True)
        t.start()
        log.info("Polling game API bắt đầu (interval=2s)")


# ─────────────────────────────────────────────
# FLASK API
# ─────────────────────────────────────────────
app   = Flask(__name__)
ai    = SupremeAI()

@app.route("/")
def health():
    return jsonify({"status": "ok", "trained": ai.trained, "rounds": ai.total_rounds})

@app.route("/predict")
def predict():
    return jsonify(ai.predict())

@app.route("/stats")
def stats():
    with ai._lock:
        return jsonify({
            "total_rounds":   ai.total_rounds,
            "trained":        ai.trained,
            "meta_weights":   {k: round(v, 3) for k, v in ai.meta.weights.items()},
            "meta_accuracy":  round(
                ai.meta.accuracy["correct"] / ai.meta.accuracy["total"], 4
            ) if ai.meta.accuracy["total"] > 0 else 0,
            "pattern_patterns": len(ai.pattern.pattern_stats),
            "markov_states":  {o: len(ai.markov.tables[o]) for o in ai.markov.ORDERS},
            "streak_stats":   len(ai.streak.streak_stats),
        })

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    # Load lịch sử & train trong background
    threading.Thread(target=ai.load_history, daemon=True).start()
    # Polling kết quả mới (bắt đầu ngay, trước khi train xong cũng không sao)
    ai.start_polling()
    # Chạy Flask
    log.info(f"Kano AI app.py khởi động trên port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
