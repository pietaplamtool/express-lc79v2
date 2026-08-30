import numpy as np
import json
import os
from collections import deque

class TaiXiuPredictorV3:
    """
    Phiên bản tối ưu: SGD với Adam, learning rate giảm dần, threshold động,
    cửa sổ trượt cho Markov, cơ chế quên hợp lý, phát hiện drift.
    """
    def __init__(self, feature_size=28, learning_rate=0.01, reg_strength=0.001,
                 model_path="model_weights_v3.json", forget_rate=0.001,
                 window_size=200):
        self.feature_size = feature_size
        self.lr = learning_rate
        self.reg = reg_strength
        self.model_path = model_path
        self.forget_rate = forget_rate
        self.window_size = window_size
        self.iteration = 0
        self.m = np.zeros(feature_size + 1)
        self.v = np.zeros(feature_size + 1)
        self.beta1 = 0.9
        self.beta2 = 0.999
        self.epsilon = 1e-8
        self.recent_acc = deque(maxlen=50)
        self.recent_probs = deque(maxlen=100)

        if os.path.exists(self.model_path):
            self.load_model()
        else:
            self.weights = np.random.randn(feature_size + 1) * 0.01
            self.save_model()

    def _ewma(self, data, alpha=0.1):
        if len(data) == 0:
            return 0.5
        ewma = data[0]
        for x in data[1:]:
            ewma = alpha * x + (1 - alpha) * ewma
        return ewma

    def _markov_features(self, history, order=2):
        window_hist = list(history)[-self.window_size:]
        if len(window_hist) < order + 1:
            return [0.5] * (2**order)
        counts = {}
        for i in range(len(window_hist) - order):
            seq = tuple(window_hist[i:i+order])
            next_val = window_hist[i+order]
            if seq not in counts:
                counts[seq] = [0, 0]
            counts[seq][next_val] += 1
        features = []
        for seq_val in range(2**order):
            seq = tuple((seq_val >> (order-1-j)) & 1 for j in range(order))
            if seq in counts:
                total = counts[seq][0] + counts[seq][1]
                prob_tai = counts[seq][1] / total if total > 0 else 0.5
            else:
                prob_tai = 0.5
            features.append(prob_tai)
        return features

    def _dynamic_threshold(self):
        if len(self.recent_probs) < 20:
            return 0.5
        return float(np.median(self.recent_probs))

    def extract_features(self, history):
        features = []
        recent = list(history)[-12:] if len(history) >= 12 else list(history) + [0]*(12-len(history))
        features.extend(recent)
        for window in [10, 20, 50, 100]:
            window_hist = list(history)[-window:]
            freq_tai = self._ewma(window_hist, alpha=0.1)
            features.append(freq_tai)
        if len(history) == 0:
            current_streak = 0
        else:
            last_val = history[-1]
            streak = 1
            for i in range(len(history)-2, -1, -1):
                if history[i] == last_val:
                    streak += 1
                else:
                    break
            current_streak = streak
        features.append(current_streak)
        recent12 = list(history)[-12:]
        changes = sum(1 for i in range(1, len(recent12)) if recent12[i] != recent12[i-1])
        features.append(changes / 11.0 if len(recent12) > 1 else 0.0)
        avg_tai = self._ewma(history, alpha=0.01)
        features.append(avg_tai)
        markov = self._markov_features(history, order=2)
        features.extend(markov)
        markov3 = self._markov_features(history, order=3)
        features.extend(markov3)
        while len(features) < self.feature_size:
            features.append(0.0)
        features = features[:self.feature_size]
        features_with_bias = np.array([1.0] + features)
        return features_with_bias

    def sigmoid(self, z):
        z = np.clip(z, -500, 500)
        return 1.0 / (1.0 + np.exp(-z))

    def predict_proba(self, history):
        x = self.extract_features(history)
        z = np.dot(self.weights, x)
        prob = self.sigmoid(z)
        self.recent_probs.append(prob)
        return prob

    def predict(self, history, threshold=None):
        prob = self.predict_proba(history)
        if threshold is None:
            threshold = self._dynamic_threshold()
        return 1 if prob >= threshold else 0

    def update(self, history, result):
        self.iteration += 1
        x = self.extract_features(history)
        prob = self.sigmoid(np.dot(self.weights, x))
        error = prob - result
        gradient = error * x + self.reg * self.weights
        gradient *= (1 - self.forget_rate)
        lr_t = self.lr / (1 + 0.001 * self.iteration)
        self.m = self.beta1 * self.m + (1 - self.beta1) * gradient
        self.v = self.beta2 * self.v + (1 - self.beta2) * (gradient ** 2)
        m_hat = self.m / (1 - self.beta1 ** self.iteration)
        v_hat = self.v / (1 - self.beta2 ** self.iteration)
        self.weights -= lr_t * m_hat / (np.sqrt(v_hat) + self.epsilon)
        pred = 1 if prob >= 0.5 else 0
        self.recent_acc.append(1 if pred == result else 0)
        if len(self.recent_acc) == 50 and np.mean(self.recent_acc) < 0.45:
            self.lr = min(self.lr * 2, 0.1)
        self.save_model()

    def save_model(self):
        with open(self.model_path, 'w') as f:
            json.dump({'weights': self.weights.tolist(),
                       'm': self.m.tolist(),
                       'v': self.v.tolist(),
                       'iteration': self.iteration,
                       'lr': self.lr}, f)

    def load_model(self):
        with open(self.model_path, 'r') as f:
            data = json.load(f)
            self.weights = np.array(data['weights'])
            self.m = np.array(data.get('m', [0]*(self.feature_size+1)))
            self.v = np.array(data.get('v', [0]*(self.feature_size+1)))
            self.iteration = data.get('iteration', 0)
            self.lr = data.get('lr', self.lr)