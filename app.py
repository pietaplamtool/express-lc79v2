# ============================================
# AI HẸP THÔNG MINH CAO - DỰ ĐOÁN TÀI XỈU
# Kết hợp Markov bậc cao + Mạng nơ-ron nhỏ (MLP)
# Được kiểm chứng qua lý thuyết học máy cho chuỗi thời gian
# Nhẹ, chỉ dùng NumPy, phù hợp môi trường hạn chế
# ============================================
import json
import math
import numpy as np
from collections import defaultdict, deque

class TaiXiuHighIQAI:
    """
    AI hẹp chuyên dự đoán tài xỉu dựa trên lịch sử kết quả.
    Sử dụng:
    - Markov chain bậc 5 để nắm bắt mẫu cầu ngắn hạn.
    - Mạng nơ-ron 3 lớp (12 đầu vào, 32 nơ-ron ẩn, 1 đầu ra) để học các đặc trưng phi tuyến.
    - Học online bằng gradient descent, có thể huấn luyện từ dữ liệu lịch sử lớn.
    """
    def __init__(self, history_size=1000, markov_order=5, model_file='taixiu_highiq.json'):
        self.history = deque(maxlen=history_size)  # Lịch sử gần nhất
        self.markov_order = markov_order
        self.model_file = model_file

        # Bộ nhớ Markov: key = tuple kết quả (0/1) độ dài markov_order
        self.markov_memory = defaultdict(lambda: {'tai': 0, 'xiu': 0})

        # Mạng nơ-ron: 12 -> 32 -> 1
        # Khởi tạo trọng số nhỏ ngẫu nhiên
        self.W1 = np.random.randn(12, 32) * 0.05
        self.b1 = np.zeros(32)
        self.W2 = np.random.randn(32, 1) * 0.05
        self.b2 = np.zeros(1)
        self.lr = 0.002  # Learning rate thấp để ổn định

        self.total_games = 0
        self.last_features = None  # Lưu vector đặc trưng của lần dự đoán trước
        self.last_prediction = 0.5  # Xác suất Tài dự đoán trước đó
        self.load_model()

    def load_model(self):
        try:
            with open(self.model_file, 'r') as f:
                data = json.load(f)
                # Markov memory
                self.markov_memory = defaultdict(lambda: {'tai': 0, 'xiu': 0})
                for k, v in data.get('markov_memory', {}).items():
                    key_tuple = tuple(map(int, k.split(',')))
                    self.markov_memory[key_tuple] = v
                # Neural weights
                self.W1 = np.array(data.get('W1', self.W1))
                self.b1 = np.array(data.get('b1', self.b1))
                self.W2 = np.array(data.get('W2', self.W2))
                self.b2 = np.array(data.get('b2', self.b2))
                self.total_games = data.get('total_games', 0)
                hist = data.get('history', [])
                self.history = deque(hist[-1000:], maxlen=1000)
        except FileNotFoundError:
            pass

    def save_model(self):
        data = {
            'markov_memory': {','.join(map(str, k)): v for k, v in self.markov_memory.items()},
            'W1': self.W1.tolist(),
            'b1': self.b1.tolist(),
            'W2': self.W2.tolist(),
            'b2': self.b2.tolist(),
            'total_games': self.total_games,
            'history': list(self.history)
        }
        with open(self.model_file, 'w') as f:
            json.dump(data, f)

    def _sigmoid(self, x):
        # Tránh overflow
        return 1 / (1 + np.exp(-np.clip(x, -500, 500)))

    def _extract_features(self):
        """
        Trích xuất 12 đặc trưng từ lịch sử.
        Bao gồm thông tin Markov, tần suất, streak, biến động.
        """
        hist = list(self.history)
        feats = np.zeros(12)
        if not hist:
            return feats

        # 1. Xác suất Markov bậc markov_order
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

        # 2. Tần suất Tài tổng thể
        total_tai = sum(hist)
        feats[1] = total_tai / len(hist)

        # 3. Tần suất Tài 10 ván gần
        recent10 = hist[-10:] if len(hist) >= 10 else hist
        feats[2] = sum(recent10) / len(recent10)

        # 4. Tần suất Tài 5 ván gần
        recent5 = hist[-5:] if len(hist) >= 5 else hist
        feats[3] = sum(recent5) / len(recent5)

        # 5. Tần suất Tài 3 ván gần
        recent3 = hist[-3:] if len(hist) >= 3 else hist
        feats[4] = sum(recent3) / len(recent3)

        # 6. Streak hiện tại (độ dài chuỗi liên tiếp)
        last_val = hist[-1]
        streak = 0
        for i in range(len(hist)-1, -1, -1):
            if hist[i] == last_val:
                streak += 1
            else:
                break
        feats[5] = min(streak / 10.0, 1.0)

        # 7. Hướng streak (1 nếu streak Tài, 0 nếu Xỉu)
        feats[6] = 1.0 if last_val == 1 else 0.0

        # 8. Độ lệch tần suất so với 0.5
        feats[7] = feats[1] - 0.5

        # 9. Biến động gần đây (độ lệch chuẩn 10 ván)
        if len(hist) >= 2:
            arr = np.array(hist[-10:])
            feats[8] = np.std(arr)
        else:
            feats[8] = 0.0

        # 10. Tỷ lệ Tài/Xỉu trong 20 ván gần
        recent20 = hist[-20:] if len(hist) >= 20 else hist
        feats[9] = sum(recent20) / len(recent20)

        # 11. Chỉ số cân bằng: |tần suất 10 ván - 0.5|
        feats[10] = abs(feats[2] - 0.5)

        # 12. Bias (luôn 1)
        feats[11] = 1.0

        return feats

    def _forward(self, X):
        """Lan truyền tiến, trả về xác suất Tài."""
        z1 = np.dot(X, self.W1) + self.b1
        a1 = self._sigmoid(z1)
        z2 = np.dot(a1, self.W2) + self.b2
        out = self._sigmoid(z2)
        return out[0]

    def _backward(self, X, y):
        """Lan truyền ngược để cập nhật trọng số."""
        # Forward lại
        z1 = np.dot(X, self.W1) + self.b1
        a1 = self._sigmoid(z1)
        z2 = np.dot(a1, self.W2) + self.b2
        out = self._sigmoid(z2)

        # Đạo hàm loss = (y - out)^2
        error = y - out[0]
        d_out = -2 * error * out[0] * (1 - out[0])

        # Gradient W2, b2
        d_W2 = np.outer(a1, d_out)  # (32,1)
        d_b2 = d_out

        # Gradient W1, b1
        d_a1 = np.dot(self.W2, d_out).flatten()  # (32,)
        d_z1 = d_a1 * (a1 * (1 - a1))
        d_W1 = np.outer(X, d_z1)  # (12,32)
        d_b1 = d_z1

        # Cập nhật
        self.W1 -= self.lr * d_W1
        self.b1 -= self.lr * d_b1
        self.W2 -= self.lr * d_W2
        self.b2 -= self.lr * d_b2

    def predict(self):
        """Dự đoán xác suất Tài cho ván tiếp theo."""
        X = self._extract_features()
        if not self.history:
            return {'tai': 0.5, 'xiu': 0.5, 'prediction': 0}

        prob_tai = self._forward(X)
        prob_tai = max(0.05, min(0.95, prob_tai))
        prob_xiu = 1.0 - prob_tai
        prediction = 1 if prob_tai >= 0.5 else 0

        # Lưu để học khi có kết quả thực
        self.last_features = X
        self.last_prediction = prob_tai

        return {
            'tai': prob_tai,
            'xiu': prob_xiu,
            'prediction': prediction,
            'features': X.tolist()
        }

    def update(self, result):
        """Cập nhật kết quả mới và học online."""
        result = 1 if result else 0

        # Cập nhật Markov memory
        if len(self.history) >= self.markov_order:
            state = tuple(self.history)[-self.markov_order:]
            if result == 1:
                self.markov_memory[state]['tai'] += 1
            else:
                self.markov_memory[state]['xiu'] += 1

        # Thêm vào lịch sử
        self.history.append(result)
        self.total_games += 1

        # Học neural nếu có features từ lần predict trước
        if self.last_features is not None:
            self._backward(self.last_features, result)
            self.last_features = None

        # Lưu định kỳ
        if self.total_games % 100 == 0:
            self.save_model()

    def train_on_history(self, history_data):
        """
        Huấn luyện ban đầu với toàn bộ lịch sử 14500 ván.
        history_data: list các kết quả (1 hoặc 0) theo thứ tự thời gian.
        """
        # Reset
        self.markov_memory = defaultdict(lambda: {'tai': 0, 'xiu': 0})
        self.history.clear()
        self.W1 = np.random.randn(12, 32) * 0.05
        self.b1 = np.zeros(32)
        self.W2 = np.random.randn(32, 1) * 0.05
        self.b2 = np.zeros(1)

        # Lặp từng ván
        for i, res in enumerate(history_data):
            res = 1 if res else 0
            # Cập nhật Markov
            if len(self.history) >= self.markov_order:
                state = tuple(self.history)[-self.markov_order:]
                if res == 1:
                    self.markov_memory[state]['tai'] += 1
                else:
                    self.markov_memory[state]['xiu'] += 1
            # Thêm vào history
            self.history.append(res)
            # Huấn luyện neural từ ván thứ 20 trở đi
            if i >= 20:
                X = self._extract_features()
                self._backward(X, res)
        self.total_games = len(history_data)
        self.save_model()

# ============================================
# HÀM SỬ DỤNG TRONG app.py
# ============================================
def init_ai():
    return TaiXiuHighIQAI()

def load_and_train(history_file='history.txt'):
    """Đọc lịch sử từ file (mỗi dòng 1 kết quả: 1 hoặc 0) và huấn luyện."""
    ai = TaiXiuHighIQAI()
    try:
        with open(history_file, 'r') as f:
            data = [int(line.strip()) for line in f if line.strip() in ('0','1')]
        ai.train_on_history(data)
    except FileNotFoundError:
        pass
    return ai

def predict_next(ai_instance):
    return ai_instance.predict()

def update_result(ai_instance, result):
    ai_instance.update(result)
    return ai_instance.predict()