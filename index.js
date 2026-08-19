const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

// =============================================
// CẤU HÌNH API LC79
// =============================================
const API_URL = 'https://wtxmd52.tele68.com/v1/txmd5/lite-sessions?cp=R&cl=R&pf=web&at=2f9251283c3748d5e0f0528c1eeac6de';
const HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Origin': 'https://lc79.com',
    'Referer': 'https://lc79.com/'
};

// =============================================
// HÀM LẤY DỮ LIỆU TỪ API LC79
// =============================================
async function fetchLC79History() {
    try {
        const response = await fetch(API_URL, { headers: HEADERS });
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        
        // Chuyển đổi dữ liệu từ API sang định dạng history
        const history = data.list.map(item => ({
            result: item.resultTruyenThong === 'TAI' ? 'T' : 'X',
            dices: item.dices,
            point: item.point,
            id: item.id
        }));
        
        return history;
    } catch (error) {
        console.error('Lỗi fetch API:', error.message);
        return null;
    }
}

// =============================================
// THUẬT TOÁN LC79 PREDICTOR
// =============================================
class LC79Predictor {
    constructor() {
        this.patterns = new Map();
        this.totalPred = 0;
        this.correctPred = 0;
        this.consecutiveLosses = 0;
    }

    learn(results) {
        for (let len = 3; len <= 8; len++) {
            for (let i = 0; i <= results.length - len - 1; i++) {
                let pattern = results.slice(i, i + len).join('');
                let next = results[i + len];
                if (!this.patterns.has(pattern)) {
                    this.patterns.set(pattern, { T: 0, X: 0, total: 0 });
                }
                let stats = this.patterns.get(pattern);
                stats[next]++;
                stats.total++;
            }
        }
    }

    predict(history) {
        try {
            let clean = history.filter(h => h && (h.result === 'T' || h.result === 'X'));
            if (clean.length < 8) {
                return { result: null, confidence: 0, status: 'SKIP', message: 'Cần ít nhất 8 phiên' };
            }
            
            let results = clean.map(h => h.result);
            let len = results.length;
            let last = results[len - 1];
            
            this.learn(results);
            
            let scores = { T: 0, X: 0 };
            let signals = [];

            for (let patternLen = 8; patternLen >= 3; patternLen--) {
                let lastPattern = results.slice(-patternLen).join('');
                let stats = this.patterns.get(lastPattern);
                if (stats && stats.total >= 2) {
                    let probT = stats.T / stats.total;
                    let probX = stats.X / stats.total;
                    let weight = stats.total * (patternLen / 8);
                    scores.T += probT * weight;
                    scores.X += probX * weight;
                    if (stats.total >= 3) {
                        signals.push(`P${lastPattern}:${probT > probX ? 'T' : 'X'}(${stats.total})`);
                    }
                }
            }

            for (let order = 1; order <= 3; order++) {
                if (results.length < order + 1) continue;
                let transitions = {};
                for (let i = order; i < results.length; i++) {
                    let prev = results.slice(i - order, i).join('');
                    let current = results[i];
                    if (!transitions[prev]) transitions[prev] = { T: 0, X: 0 };
                    transitions[prev][current]++;
                }
                let lastState = results.slice(-order).join('');
                if (transitions[lastState]) {
                    let counts = transitions[lastState];
                    let total = counts.T + counts.X;
                    if (total > 0) {
                        scores.T += (counts.T / total) * (order / 3);
                        scores.X += (counts.X / total) * (order / 3);
                        signals.push(`M${order}:${counts.T > counts.X ? 'T' : 'X'}`);
                    }
                }
            }

            let streak = 1;
            for (let i = len - 2; i >= 0; i--) {
                if (results[i] === last) streak++;
                else break;
            }
            if (streak >= 7) {
                scores[last === 'T' ? 'X' : 'T'] += 3;
                signals.push(`Bệt${streak}:Bẻ`);
            } else if (streak >= 5) {
                scores[last === 'T' ? 'X' : 'T'] += 2;
                signals.push(`Bệt${streak}:Bẻ nhẹ`);
            } else if (streak >= 3) {
                scores[last] += 1;
                signals.push(`Bệt${streak}:Theo`);
            }

            let recent10 = results.slice(-10);
            let countT = recent10.filter(r => r === 'T').length;
            let countX = recent10.filter(r => r === 'X').length;
            if (Math.abs(countT - countX) >= 4) {
                let prediction = countT < countX ? 'T' : 'X';
                scores[prediction] += 2;
                signals.push(`Lệch${Math.abs(countT - countX)}:${prediction}`);
            }

            let alternating = true;
            for (let i = len - 3; i < len - 1; i++) {
                if (results[i] === results[i + 1]) {
                    alternating = false;
                    break;
                }
            }
            if (alternating) {
                scores[last === 'T' ? 'X' : 'T'] += 2;
                signals.push('Xen kẽ:Đảo');
            }

            let last4 = results.slice(-4).join('');
            if (last4 === 'TXTX' || last4 === 'XTXT') {
                scores[last === 'T' ? 'X' : 'T'] += 2;
                signals.push('Cầu 1-1:Đảo');
            }

            let last8 = results.slice(-8).join('');
            if (last8 === 'TTXXTTXX' || last8 === 'XXTTXXTT') {
                scores[last === 'T' ? 'X' : 'T'] += 3;
                signals.push('Cầu 2-2:Đảo');
            }

            let totalScore = scores.T + scores.X;
            if (totalScore === 0) {
                return { result: last === 'T' ? 'X' : 'T', confidence: 50, status: 'RANDOM' };
            }
            
            let prediction = scores.T > scores.X ? 'T' : 'X';
            let confidence = Math.round((Math.max(scores.T, scores.X) / totalScore) * 100);
            
            if (signals.length >= 5) confidence = Math.min(confidence + 10, 95);
            else if (signals.length >= 3) confidence = Math.min(confidence + 5, 90);
            
            return {
                result: prediction,
                confidence,
                status: 'OK',
                taiPercent: Math.round((scores.T / totalScore) * 100),
                xiuPercent: Math.round((scores.X / totalScore) * 100),
                streak,
                shouldBet: confidence >= 60 && this.consecutiveLosses < 3,
                signals,
                pattern: signals.slice(0, 5).join(' | '),
                reason: signals.join(' | ')
            };
        } catch (error) {
            return { result: null, confidence: 0, status: 'ERROR', message: error.message };
        }
    }

    updateActual(prediction, actual) {
        this.totalPred++;
        if (prediction === actual) {
            this.correctPred++;
            this.consecutiveLosses = 0;
        } else {
            this.consecutiveLosses++;
        }
    }

    getAccuracy() {
        if (this.totalPred === 0) return 0;
        return (this.correctPred / this.totalPred) * 100;
    }
}

// =============================================
// KHỞI TẠO
// =============================================
const predictor = new LC79Predictor();
let lastPrediction = null;
let lc79History = [];

// =============================================
// API ENDPOINTS
// =============================================

// Trang chủ
app.get('/', (req, res) => {
    res.json({
        status: 'OK',
        message: '🚀 LC79 Predictor Server đang chạy!',
        endpoints: {
            health: '/api/health',
            history: '/api/lc79/history',
            predict: '/api/lc79/predict',
            update: '/api/lc79/update?actual=T',
            accuracy: '/api/lc79/accuracy'
        }
    });
});

// Health check (cho UptimeRobot)
app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// Lấy lịch sử từ API LC79
app.get('/api/lc79/history', async (req, res) => {
    const history = await fetchLC79History();
    if (history) {
        lc79History = history;
        res.json({ status: 'OK', data: history, count: history.length });
    } else {
        res.status(500).json({ status: 'ERROR', message: 'Không thể lấy dữ liệu từ API LC79' });
    }
});

// Dự đoán dựa trên lịch sử LC79
app.get('/api/lc79/predict', async (req, res) => {
    try {
        // Nếu chưa có history hoặc muốn lấy mới
        const forceRefresh = req.query.refresh === 'true';
        if (lc79History.length === 0 || forceRefresh) {
            const history = await fetchLC79History();
            if (history) lc79History = history;
            else return res.status(500).json({ status: 'ERROR', message: 'Không thể lấy dữ liệu từ API LC79' });
        }

        const result = predictor.predict(lc79History);
        lastPrediction = result.result;
        res.json({ 
            ...result, 
            historyCount: lc79History.length,
            lastId: lc79History[0]?.id 
        });
    } catch (error) {
        res.status(500).json({ status: 'ERROR', message: error.message });
    }
});

// Cập nhật kết quả thực tế
app.get('/api/lc79/update', (req, res) => {
    const actual = req.query.actual;
    const predicted = req.query.predicted || lastPrediction;
    
    if (actual === 'T' || actual === 'X') {
        predictor.updateActual(predicted, actual);
        res.json({
            status: 'OK',
            accuracy: predictor.getAccuracy().toFixed(2) + '%',
            totalPredictions: predictor.totalPred,
            correctPredictions: predictor.correctPred,
            consecutiveLosses: predictor.consecutiveLosses
        });
    } else {
        res.json({ status: 'ERROR', message: 'actual phải là T hoặc X' });
    }
});

// Xem thống kê
app.get('/api/lc79/accuracy', (req, res) => {
    res.json({
        accuracy: predictor.getAccuracy().toFixed(2) + '%',
        totalPredictions: predictor.totalPred,
        correctPredictions: predictor.correctPred,
        consecutiveLosses: predictor.consecutiveLosses
    });
});

// =============================================
// KHỞI ĐỘNG SERVER
// =============================================
app.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 LC79 Server chạy trên port ${PORT}`);
    console.log(`📊 Test: http://localhost:${PORT}/api/lc79/predict`);
});