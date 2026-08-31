# ============================================
# AI TÍCH HỢP ĐA MÔ HÌNH DỰ ĐOÁN TÀI XỈU
# Gồm: Markov bậc cao, HMM, GRU, MLP, Ensemble
# Chạy nhẹ, chỉ dùng NumPy, không lỗi deploy
# ============================================
import json
import numpy as np
from collections import defaultdict, deque

class TaiXiuEnsembleAI:
    def __init__(self, markov_order=5, model_file='taixiu_ensemble.json'):
        self.history = deque(maxlen=500)
        self.markov_order = markov_order
        self.model_file = model_file

        # Markov memory
        self.markov_memory = defaultdict(lambda: {'tai': 0, 'xiu': 0})

        # HMM parameters (2 trạng thái ẩn: cân bằng, lệch)
        self.hmm = {
            'A': np.array([[0.9, 0.1], [0.1, 0.9]]),  # ma trận chuyển trạng thái
            'B': np.array([[0.5, 0.5], [0.6, 0.4]]),  # xác suất quan sát [Tài, Xỉu]
            'pi': np.array([0.5, 0.5])               # phân phối ban đầu
        }

        # GRU nhỏ: input_size=1, hidden_size=8
        self.gru = {
            'Wz': np.random.randn(1, 8) * 0.1,
            'Uz': np.random.randn(8, 8) * 0.1,
            'bz': np.zeros(8),
            'Wr': np.random.randn(1, 8) * 0.1,
            'Ur': np.random.randn(8, 8) * 0.1,
            'br': np.zeros(8),
            'Wh': np.random.randn(1, 8) * 0.1,
            'Uh': np.random.randn(8, 8) * 0.1,
            'bh': np.zeros(8),
            'W_out': np.random.randn(8, 1) * 0.1,
            'b_out': np.zeros(1)
        }

        # MLP nhỏ: input 12 -> hidden 16 -> output 1
        self.mlp = {
            'W1': np.random.randn(12, 16) * 0.1,
            'b1': np.zeros(16),
            'W2': np.random.randn(16, 1) * 0.1,
            'b2': np.zeros(1)
        }

        # Trọng số ensemble khởi tạo
        self.ensemble_weights = {
            'markov': 0.3,
            'hmm': 0.2,
            'gru': 0.25,
            'mlp': 0.25
        }

        self.total_games = 0
        self.last_features = None          # lưu vector đặc trưng lần dự đoán trước
        self.last_component_preds = None   # lưu dự đoán từng thành phần
        self.last_gru_seq = None           # lưu chuỗi 5 kết quả cho huấn luyện GRU

        self.load_model()

    def load_model(self):
        try:
            with open(self.model_file, 'r') as f:
                data = json.load(f)
                hist = data.get('history', [])
                self.history = deque(hist[-500:], maxlen=500)
                # Markov
                self.markov_memory = defaultdict(lambda: {'tai': 0, 'xiu': 0})
                for k, v in data.get('markov_memory', {}).items():
                    self.markov_memory[tuple(map(int, k.split(',')))] = v
                # HMM
                if 'hmm' in data:
                    self.hmm = {k: np.array(v) for k, v in data['hmm'].items()}
                # GRU
                if 'gru' in data:
                    self.gru = {k: np.array(v) for k, v in data['gru'].items()}
                # MLP
                if 'mlp' in data:
                    self.mlp = {k: np.array(v) for k, v in data['mlp'].items()}
                # Ensemble weights
                if 'ensemble_weights' in data:
                    self.ensemble_weights = data['ensemble_weights']
                self.total_games = data.get('total_games', 0)
        except FileNotFoundError:
            pass

    def save_model(self):
        data = {
            'history': list(self.history),
            'markov_memory': {','.join(map(str, k)): v for k, v in self.markov_memory.items()},
            'hmm': {k: v.tolist() for k, v in self.hmm.items()},
            'gru': {k: v.tolist() for k, v in self.gru.items()},
            'mlp': {k: v.tolist() for k, v in self.mlp.items()},
            'ensemble_weights': self.ensemble_weights,
            'total_games': self.total_games
        }
        with open(self.model_file, 'w') as f:
            json.dump(data, f)

    def _sigmoid(self, x):
        return 1 / (1 + np.exp(-np.clip(x, -500, 500)))

    def _extract_features(self):
        hist = list(self.history)
        feats = np.zeros(12)
        if not hist:
            return feats
        # 1. Xác suất Markov
        if len(hist) >= self.markov_order:
            state = tuple(hist[-self.markov_order:])
            counts = self.markov_memory.get(state, {'tai': 0, 'xiu': 0})
            total = counts['tai'] + counts['xiu']
            feats[0] = counts['tai'] / total if total > 0 else 0.5
        else:
            state = tuple(hist)
            counts = self.markov_memory.get(state, {'tai': 0, 'xiu': 0})
            total = counts['tai'] + counts['xiu']
            feats[0] = counts['tai'] / total if total > 0 else 0.5
        # 2. Tần suất tổng
        total_tai = sum(hist)
        feats[1] = total_tai / len(hist)
        # 3. Tần suất 10 ván
        recent10 = hist[-10:] if len(hist) >= 10 else hist
        feats[2] = sum(recent10) / len(recent10)
        # 4. Tần suất 5 ván
        recent5 = hist[-5:] if len(hist) >= 5 else hist
        feats[3] = sum(recent5) / len(recent5)
        # 5. Tần suất 3 ván
        recent3 = hist[-3:] if len(hist) >= 3 else hist
        feats[4] = sum(recent3) / len(recent3)
        # 6. Streak
        last_val = hist[-1]
        streak = 0
        for i in range(len(hist)-1, -1, -1):
            if hist[i] == last_val:
                streak += 1
            else:
                break
        feats[5] = min(streak / 10.0, 1.0)
        # 7. Hướng streak
        feats[6] = 1.0 if last_val == 1 else 0.0
        # 8. Độ lệch tần suất
        feats[7] = feats[1] - 0.5
        # 9. Độ lệch chuẩn 10 ván
        if len(hist) >= 2:
            arr = np.array(hist[-10:])
            feats[8] = np.std(arr)
        else:
            feats[8] = 0.0
        # 10. Tần suất 20 ván
        recent20 = hist[-20:] if len(hist) >= 20 else hist
        feats[9] = sum(recent20) / len(recent20)
        # 11. |tần suất 10 ván - 0.5|
        feats[10] = abs(feats[2] - 0.5)
        # 12. Bias
        feats[11] = 1.0
        return feats

    def _markov_predict(self):
        hist = list(self.history)
        if len(hist) >= self.markov_order:
            state = tuple(hist[-self.markov_order:])
            counts = self.markov_memory.get(state, {'tai': 0, 'xiu': 0})
            total = counts['tai'] + counts['xiu']
            if total > 0:
                return counts['tai'] / total
        if hist:
            return sum(hist) / len(hist)
        return 0.5

    def _hmm_predict(self):
        hist = list(self.history)
        if len(hist) < 2:
            return 0.5
        # Forward algorithm
        alpha = self.hmm['pi'] * self.hmm['B'][:, hist[0]]
        for t in range(1, len(hist)):
            alpha = (alpha @ self.hmm['A']) * self.hmm['B'][:, hist[t]]
            alpha /= alpha.sum()  # normalize
        state_probs = alpha / alpha.sum()
        prob_tai = state_probs[0] * self.hmm['B'][0, 0] + state_probs[1] * self.hmm['B'][1, 0]
        return prob_tai

    def _gru_forward(self, seq):
        """Trả về xác suất Tài và lưu các giá trị trung gian để backprop."""
        h = np.zeros(8)
        cache = []  # lưu (x, h_prev, z, r, h_tilde, h)
        for val in seq:
            x = np.array([val])  # shape (1,)
            z = self._sigmoid(x @ self.gru['Wz'] + h @ self.gru['Uz'] + self.gru['bz'])
            r = self._sigmoid(x @ self.gru['Wr'] + h @ self.gru['Ur'] + self.gru['br'])
            h_tilde = np.tanh(x @ self.gru['Wh'] + (r * h) @ self.gru['Uh'] + self.gru['bh'])
            h_new = (1 - z) * h + z * h_tilde
            cache.append((x, h.copy(), z, r, h_tilde, h_new.copy()))
            h = h_new
        out = self._sigmoid(h @ self.gru['W_out'] + self.gru['b_out'])
        return out[0], cache

    def _gru_predict(self):
        if len(self.history) < 5:
            return 0.5
        seq = list(self.history)[-5:]
        prob, _ = self._gru_forward(seq)
        return prob

    def _mlp_predict(self):
        feats = self._extract_features()
        z1 = feats @ self.mlp['W1'] + self.mlp['b1']
        a1 = self._sigmoid(z1)
        z2 = a1 @ self.mlp['W2'] + self.mlp['b2']
        out = self._sigmoid(z2)
        return out[0]

    def predict(self):
        if not self.history:
            return {'tai': 0.5, 'xiu': 0.5, 'prediction': 0}
        p_markov = self._markov_predict()
        p_hmm = self._hmm_predict()
        p_gru = self._gru_predict()
        p_mlp = self._mlp_predict()

        # Ensemble có trọng số
        w = self.ensemble_weights
        total = sum(w.values())
        prob_tai = (p_markov * w['markov'] + p_hmm * w['hmm'] +
                    p_gru * w['gru'] + p_mlp * w['mlp']) / total
        prob_tai = max(0.05, min(0.95, prob_tai))
        prob_xiu = 1.0 - prob_tai
        prediction = 1 if prob_tai >= 0.5 else 0

        # Lưu dự đoán thành phần và đặc trưng cho việc học
        self.last_component_preds = {
            'markov': p_markov,
            'hmm': p_hmm,
            'gru': p_gru,
            'mlp': p_mlp
        }
        self.last_features = self._extract_features()
        self.last_gru_seq = list(self.history)[-5:] if len(self.history) >= 5 else None

        return {
            'tai': prob_tai,
            'xiu': prob_xiu,
            'prediction': prediction
        }

    def _train_mlp(self, feats, y):
        lr = 0.001
        z1 = feats @ self.mlp['W1'] + self.mlp['b1']
        a1 = self._sigmoid(z1)
        z2 = a1 @ self.mlp['W2'] + self.mlp['b2']
        out = self._sigmoid(z2)[0]
        error = y - out
        d_out = -2 * error * out * (1 - out)
        d_W2 = np.outer(a1, d_out)
        d_b2 = d_out
        d_a1 = self.mlp['W2'] @ d_out
        d_z1 = d_a1 * (a1 * (1 - a1))
        d_W1 = np.outer(feats, d_z1)
        d_b1 = d_z1
        self.mlp['W1'] -= lr * d_W1
        self.mlp['b1'] -= lr * d_b1
        self.mlp['W2'] -= lr * d_W2
        self.mlp['b2'] -= lr * d_b2

    def _train_gru(self, seq, y):
        """Huấn luyện GRU với chuỗi seq và nhãn y (0/1)."""
        lr = 0.001
        prob, cache = self._gru_forward(seq)
        error = y - prob
        d_out = -2 * error * prob * (1 - prob)
        # Gradient wrt W_out, b_out
        h_final = cache[-1][5]  # hidden state cuối
        d_W_out = np.outer(h_final, d_out)
        d_b_out = d_out
        d_h = self.gru['W_out'] @ d_out
        # Backprop qua từng bước ngược
        for t in reversed(range(len(cache))):
            x, h_prev, z, r, h_tilde, h = cache[t]
            # Đạo hàm qua h_new
            # h = (1-z)*h_prev + z*h_tilde
            d_h_new = d_h
            d_z = d_h_new * (h_tilde - h_prev) * z * (1 - z)
            d_h_tilde = d_h_new * z * (1 - h_tilde**2)
            d_r = (d_h_new * z * (1 - h_tilde**2)) @ self.gru['Uh'].T * h_prev * r * (1 - r)
            # Đạo hàm qua các ma trận
            d_Wh = np.outer(x, d_h_tilde)
            d_Uh = np.outer(r * h_prev, d_h_tilde)
            d_bh = d_h_tilde
            d_Wz = np.outer(x, d_z)
            d_Uz = np.outer(h_prev, d_z)
            d_bz = d_z
            d_Wr = np.outer(x, d_r)
            d_Ur = np.outer(h_prev, d_r)
            d_br = d_r
            # Cập nhật
            self.gru['Wz'] -= lr * d_Wz
            self.gru['Uz'] -= lr * d_Uz
            self.gru['bz'] -= lr * d_bz
            self.gru['Wr'] -= lr * d_Wr
            self.gru['Ur'] -= lr * d_Ur
            self.gru['br'] -= lr * d_br
            self.gru['Wh'] -= lr * d_Wh
            self.gru['Uh'] -= lr * d_Uh
            self.gru['bh'] -= lr * d_bh
            # Gradient cho h_prev để truyền ngược
            d_h = (d_h_new * (1 - z)) + (d_h_new * z * (1 - h_tilde**2)) @ self.gru['Uh'].T * r
            # Có thể thêm các thành phần khác nhưng đơn giản hóa
        self.gru['W_out'] -= lr * d_W_out
        self.gru['b_out'] -= lr * d_b_out

    def update(self, result):
        result = 1 if result else 0
        # Cập nhật Markov memory
        if len(self.history) >= self.markov_order:
            state = tuple(self.history)[-self.markov_order:]
            if result == 1:
                self.markov_memory[state]['tai'] += 1
            else:
                self.markov_memory[state]['xiu'] += 1

        # Huấn luyện các mô hình nếu có dữ liệu từ lần predict trước
        if self.last_features is not None:
            self._train_mlp(self.last_features, result)
        if self.last_gru_seq is not None:
            self._train_gru(self.last_gru_seq, result)

        # Cập nhật trọng số ensemble dựa trên sai số từng thành phần
        if self.last_component_preds is not None:
            for key in self.last_component_preds:
                pred = self.last_component_preds[key]
                error = abs(result - pred)
                self.ensemble_weights[key] *= (1 - error * 0.1)
            total = sum(self.ensemble_weights.values())
            for k in self.ensemble_weights:
                self.ensemble_weights[k] /= total

        # Thêm kết quả vào lịch sử
        self.history.append(result)
        self.total_games += 1

        # Reset lưu trữ tạm
        self.last_features = None
        self.last_gru_seq = None
        self.last_component_preds = None

        # Lưu model định kỳ
        if self.total_games % 100 == 0:
            self.save_model()

    def train_on_history(self, history_data):
        """Huấn luyện ban đầu từ lịch sử có sẵn (list 0/1)."""
        self.markov_memory = defaultdict(lambda: {'tai': 0, 'xiu': 0})
        self.history.clear()
        # Reset trọng số (tùy chọn)
        self.hmm = {
            'A': np.array([[0.9, 0.1], [0.1, 0.9]]),
            'B': np.array([[0.5, 0.5], [0.6, 0.4]]),
            'pi': np.array([0.5, 0.5])
        }
        self.gru = {
            'Wz': np.random.randn(1, 8) * 0.1,
            'Uz': np.random.randn(8, 8) * 0.1,
            'bz': np.zeros(8),
            'Wr': np.random.randn(1, 8) * 0.1,
            'Ur': np.random.randn(8, 8) * 0.1,
            'br': np.zeros(8),
            'Wh': np.random.randn(1, 8) * 0.1,
            'Uh': np.random.randn(8, 8) * 0.1,
            'bh': np.zeros(8),
            'W_out': np.random.randn(8, 1) * 0.1,
            'b_out': np.zeros(1)
        }
        self.mlp = {
            'W1': np.random.randn(12, 16) * 0.1,
            'b1': np.zeros(16),
            'W2': np.random.randn(16, 1) * 0.1,
            'b2': np.zeros(1)
        }
        self.ensemble_weights = {'markov': 0.3, 'hmm': 0.2, 'gru': 0.25, 'mlp': 0.25}

        for i, res in enumerate(history_data):
            res = 1 if res else 0
            # Nếu có đủ dữ liệu, dự đoán trước khi thêm res để học
            if len(self.history) >= 5:
                # Lưu đặc trưng trước khi thêm
                self.last_features = self._extract_features()
                self.last_gru_seq = list(self.history)[-5:]
                # Dự đoán thành phần
                self.last_component_preds = {
                    'markov': self._markov_predict(),
                    'hmm': self._hmm_predict(),
                    'gru': self._gru_predict(),
                    'mlp': self._mlp_predict()
                }
            # Cập nhật Markov
            if len(self.history) >= self.markov_order:
                state = tuple(self.history)[-self.markov_order:]
                if res == 1:
                    self.markov_memory[state]['tai'] += 1
                else:
                    self.markov_memory[state]['xiu'] += 1
            # Thêm res vào lịch sử
            self.history.append(res)
            # Huấn luyện sau khi có nhãn
            if len(self.history) >= 5:
                # Lưu ý: sau khi thêm res, last_features đã được tính trước khi thêm, đúng
                self._train_mlp(self.last_features, res)
                self._train_gru(self.last_gru_seq, res)
                # Cập nhật ensemble weights
                for key in self.last_component_preds:
                    error = abs(res - self.last_component_preds[key])
                    self.ensemble_weights[key] *= (1 - error * 0.1)
                total = sum(self.ensemble_weights.values())
                for k in self.ensemble_weights:
                    self.ensemble_weights[k] /= total
        self.total_games = len(history_data)
        self.save_model()