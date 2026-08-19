const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

// =============================================
// ULTIMATE AI PREDICTOR - BETVIP
// =============================================

class UltimatePredictor {
    constructor() {
        this.apiConfig = {
            baseUrl: 'https://wtxmd52.macminim6.online/v1/txmd5/sessions',
            token: '1fc7bfdeab18790088a6e44d6b8cb288',
            params: 'cp=R&cl=R&pf=web&at=',
            pollInterval: 4000
        };
        this.history = [];
        this.maxHistory = 200;
        this.stats = {
            totalPredictions: 0,
            correctPredictions: 0,
            accuracy: 0,
            streaks: { current: 0, best: 0 }
        };
        this.lastPrediction = null;
        this.weights = {
            bệt: 1.0, đảo: 0.8, cầu21: 0.7, cầu31: 0.6,
            tầnSuất: 0.5, fibonacci: 0.4, martingale: 0.3,
            ml: 0.6, neural: 0.7, pattern: 0.5
        };
        this.predictionHistory = [];
    }

    // =============================================
    // FETCH DỮ LIỆU TỪ BETVIP
    // =============================================
    async fetchData() {
        try {
            const url = `${this.apiConfig.baseUrl}?${this.apiConfig.params}${this.apiConfig.token}`;
            console.log('📡 Đang lấy dữ liệu từ BetVip...');
            
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'application/json',
                    'Origin': 'https://betvip.com',
                    'Referer': 'https://betvip.com/'
                }
            });
            
            if (!response.ok) throw new Error(`HTTP Error: ${response.status}`);
            const data = await response.json();
            
            // Xử lý dữ liệu (hỗ trợ cả list và sessions)
            const sessions = data.list || data.sessions || [];
            if (sessions.length > 0) {
                this.processData(sessions);
                return sessions;
            }
            return null;
        } catch (error) {
            console.error('❌ Lỗi fetch:', error.message);
            return null;
        }
    }

    processData(sessions) {
        let count = 0;
        sessions.forEach(session => {
            // Lấy kết quả từ resultTruyenThong (BetVip) hoặc result (LC79)
            const raw = session.resultTruyenThong || session.result;
            if (!raw) return;
            
            let result = raw;
            if (raw === 'TAI') result = 'T';
            else if (raw === 'XIU') result = 'X';
            else if (raw !== 'T' && raw !== 'X') return;
            
            if (this.history.length === 0 || this.history[this.history.length - 1] !== result) {
                this.history.push(result);
                if (this.history.length > this.maxHistory) this.history.shift();
                count++;
            }
        });
        console.log(`✅ Đã cập nhật ${count} kết quả mới`);
    }

    // =============================================
    // CÁC THUẬT TOÁN PHÂN TÍCH
    // =============================================
    detectBệt() {
        if (this.history.length < 3) return null;
        const last3 = this.history.slice(-3);
        const last5 = this.history.slice(-5);
        if (last3.every(r => r === 'T')) {
            const strength = 0.9 + (last5.filter(r => r === 'T').length * 0.02);
            return { result: 'T', pattern: 'Bệt Tài', strength: Math.min(strength, 1) };
        }
        if (last3.every(r => r === 'X')) {
            const strength = 0.9 + (last5.filter(r => r === 'X').length * 0.02);
            return { result: 'X', pattern: 'Bệt Xỉu', strength: Math.min(strength, 1) };
        }
        return null;
    }

    detectCầuĐảo() {
        if (this.history.length < 4) return null;
        const last4 = this.history.slice(-4);
        const last6 = this.history.slice(-6);
        const isAlternating = last4.every((r, i) => i === 0 || r !== last4[i - 1]);
        if (isAlternating) {
            const nextResult = last4[3] === 'T' ? 'X' : 'T';
            const strength = 0.75 + (last6.filter((r, i) => i > 0 && r !== last6[i - 1]).length * 0.05);
            return { result: nextResult, pattern: 'Cầu đảo', strength: Math.min(strength, 0.95) };
        }
        return null;
    }

    detectCầu21() {
        if (this.history.length < 6) return null;
        const pattern = this.history.slice(-6).join('');
        const maps = {
            'TTXTTX': { result: 'T', strength: 0.7 },
            'XXTXXT': { result: 'X', strength: 0.7 },
            'TXXTXX': { result: 'T', strength: 0.65 },
            'XTTXTT': { result: 'X', strength: 0.65 }
        };
        if (maps[pattern]) {
            return { result: maps[pattern].result, pattern: `Cầu 2-1 (${pattern})`, strength: maps[pattern].strength };
        }
        return null;
    }

    detectCầu31() {
        if (this.history.length < 8) return null;
        const pattern = this.history.slice(-8).join('');
        const maps = {
            'TTTXTTTX': { result: 'T', strength: 0.6 },
            'XXXTXXXT': { result: 'X', strength: 0.6 },
            'TXTTTXTT': { result: 'T', strength: 0.55 },
            'XTXXXTXX': { result: 'X', strength: 0.55 }
        };
        if (maps[pattern]) {
            return { result: maps[pattern].result, pattern: `Cầu 3-1 (${pattern})`, strength: maps[pattern].strength };
        }
        return null;
    }

    analyzeTầnSuất() {
        if (this.history.length < 20) return null;
        const last20 = this.history.slice(-20);
        const last50 = this.history.slice(-50);
        const t20 = last20.filter(r => r === 'T').length;
        const x20 = 20 - t20;
        const diff = Math.abs(t20 - x20);
        if (diff > 4) {
            const result = t20 > x20 ? 'X' : 'T';
            return { result, pattern: 'Cân bằng tần suất', strength: Math.min(diff / 20, 0.7) };
        }
        const t50 = last50.filter(r => r === 'T').length;
        const p50 = t50 / last50.length;
        if (p50 > 0.55) return { result: 'T', pattern: 'Xu hướng Tài', strength: 0.4 };
        if (p50 < 0.45) return { result: 'X', pattern: 'Xu hướng Xỉu', strength: 0.4 };
        return null;
    }

    analyzeFibonacci() {
        if (this.history.length < 13) return null;
        const pos = [2, 3, 5, 8, 13];
        const last = this.history[this.history.length - 1];
        let matches = 0;
        pos.forEach(p => { if (this.history[this.history.length - p] === last) matches++; });
        if (matches / pos.length > 0.6) {
            return { result: last === 'T' ? 'X' : 'T', pattern: 'Fibonacci', strength: (matches / pos.length) * 0.5 };
        }
        return null;
    }

    analyzeMartingale() {
        if (this.history.length < 5) return null;
        const last5 = this.history.slice(-5);
        const t = last5.filter(r => r === 'T').length;
        if (t >= 4) return { result: 'X', pattern: 'Martingale ngược', strength: 0.35 };
        if (t <= 1) return { result: 'T', pattern: 'Martingale ngược', strength: 0.35 };
        return null;
    }

    simpleML() {
        if (this.history.length < 10) return null;
        const last10 = this.history.slice(-10);
        const last30 = this.history.slice(-30);
        const p = (last10.filter(r => r === 'T').length / 10) * 0.6 + (last30.filter(r => r === 'T').length / 30) * 0.4;
        if (p > 0.55) return { result: 'T', pattern: 'ML xu hướng', strength: p * 0.8 };
        if (p < 0.45) return { result: 'X', pattern: 'ML xu hướng', strength: (1 - p) * 0.8 };
        return null;
    }

    neuralNetwork() {
        if (this.history.length < 20) return null;
        const input = this.history.slice(-20).map(r => r === 'T' ? 1 : 0);
        const sum = input.reduce((s, x, i) => s + x * (Math.sin(i * 0.5) * 0.3 + 0.5), 0);
        const avg = sum / input.length;
        if (avg > 0.55) return { result: 'T', pattern: 'Neural', strength: avg * 0.7 };
        if (avg < 0.45) return { result: 'X', pattern: 'Neural', strength: (1 - avg) * 0.7 };
        return null;
    }

    patternMatching() {
        if (this.history.length < 15) return null;
        const pattern = this.history.slice(-15).join('');
        let lastMatch = -1;
        for (let i = 0; i <= this.history.length - 16; i++) {
            if (this.history.slice(i, i + 15).join('') === pattern) lastMatch = i;
        }
        if (lastMatch >= 0 && this.history[lastMatch + 15]) {
            return { result: this.history[lastMatch + 15], pattern: 'Pattern match', strength: 0.5 };
        }
        return null;
    }

    // =============================================
    // TỔNG HỢP DỰ ĐOÁN
    // =============================================
    predict() {
        if (this.history.length < 5) {
            return { 
                prediction: null, 
                message: `Cần ít nhất 5 phiên (hiện có ${this.history.length})`, 
                confidence: 0 
            };
        }

        const methods = [
            this.detectBệt(), this.detectCầuĐảo(), this.detectCầu21(), this.detectCầu31(),
            this.analyzeTầnSuất(), this.analyzeFibonacci(), this.analyzeMartingale(),
            this.simpleML(), this.neuralNetwork(), this.patternMatching()
        ];
        const valid = methods.filter(m => m);
        
        if (valid.length < 2) {
            const last = this.history[this.history.length - 1];
            const pred = last === 'T' ? 'X' : 'T';
            this.lastPrediction = pred;
            this.stats.totalPredictions++;
            return { 
                prediction: pred, 
                confidence: 50, 
                patterns: ['Random - thiếu pattern'],
                message: 'Ít pattern, dự đoán ngược'
            };
        }

        let tScore = 0, xScore = 0;
        const patterns = [];
        valid.forEach(m => {
            patterns.push(m.pattern);
            const score = m.strength * (this.weights[m.pattern.split(' ')[0].toLowerCase()] || 0.5);
            if (m.result === 'T') tScore += score;
            else xScore += score;
        });

        const totalScore = tScore + xScore;
        const confidence = totalScore > 0 ? (Math.abs(tScore - xScore) / totalScore) * 100 : 0;
        const finalResult = tScore > xScore ? 'T' : 'X';

        if (confidence < 25) {
            return { 
                prediction: null, 
                message: 'Độ tin cậy thấp (<25%), bỏ qua phiên này', 
                confidence, 
                patterns,
                scores: { T: tScore, X: xScore }
            };
        }

        const result = {
            prediction: finalResult,
            confidence: Math.min(confidence, 95),
            patterns,
            scores: { T: tScore.toFixed(2), X: xScore.toFixed(2) },
            history: this.history.slice(-10),
            timestamp: new Date().toISOString()
        };

        this.lastPrediction = finalResult;
        this.stats.totalPredictions++;
        this.predictionHistory.push(result);
        if (this.predictionHistory.length > 100) this.predictionHistory.shift();

        return result;
    }

    updateActualResult(actualResult) {
        if (this.lastPrediction) {
            if (this.lastPrediction === actualResult) {
                this.stats.correctPredictions++;
                this.stats.streaks.current++;
                if (this.stats.streaks.current > this.stats.streaks.best) {
                    this.stats.streaks.best = this.stats.streaks.current;
                }
            } else {
                this.stats.streaks.current = 0;
            }
            this.stats.accuracy = (this.stats.correctPredictions / this.stats.totalPredictions) * 100;
        }
    }

    getStats() {
        return {
            totalPredictions: this.stats.totalPredictions,
            correctPredictions: this.stats.correctPredictions,
            accuracy: this.stats.accuracy.toFixed(2) + '%',
            currentStreak: this.stats.streaks.current,
            bestStreak: this.stats.streaks.best
        };
    }

    getHistory(limit = 20) {
        return this.predictionHistory.slice(-limit);
    }
}

// =============================================
// KHỞI TẠO
// =============================================
const predictor = new UltimatePredictor();

// =============================================
// API ENDPOINTS
// =============================================

app.get('/', (req, res) => {
    res.json({
        status: 'OK',
        message: '🚀 Ultimate AI Predictor',
        algorithms: ['Bệt', 'Đảo', 'Cầu 2-1', 'Cầu 3-1', 'Tần suất', 'Fibonacci', 'Martingale', 'ML', 'Neural', 'Pattern'],
        endpoints: {
            predict: '/api/predict',
            fetch: '/api/fetch',
            stats: '/api/stats',
            history: '/api/history',
            update: '/api/update?actual=T'
        }
    });
});

app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.get('/api/fetch', async (req, res) => {
    try {
        const data = await predictor.fetchData();
        res.json({ 
            status: 'OK', 
            message: 'Đã fetch dữ liệu', 
            count: predictor.history.length,
            history: predictor.history.slice(-10)
        });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/predict', (req, res) => {
    const result = predictor.predict();
    res.json({
        timestamp: new Date().toISOString(),
        ...result,
        stats: predictor.getStats()
    });
});

app.get('/api/stats', (req, res) => {
    res.json(predictor.getStats());
});

app.get('/api/history', (req, res) => {
    const limit = parseInt(req.query.limit) || 20;
    res.json({
        history: predictor.getHistory(limit),
        count: predictor.predictionHistory.length
    });
});

app.get('/api/update', (req, res) => {
    const actual = req.query.actual;
    if (actual !== 'T' && actual !== 'X') {
        return res.status(400).json({ error: 'actual phải là T hoặc X' });
    }
    predictor.updateActualResult(actual);
    res.json({
        status: 'OK',
        actual: actual,
        stats: predictor.getStats()
    });
});

// =============================================
// KHỞI ĐỘNG SERVER
// =============================================
app.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 Ultimate AI Predictor chạy trên port ${PORT}`);
    console.log('📡 Đang fetch dữ liệu ban đầu...');
    predictor.fetchData().then(() => {
        console.log(`✅ Dữ liệu sẵn sàng! (${predictor.history.length} records)`);
    });
});