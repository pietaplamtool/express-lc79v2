const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

// =============================================
// CẤU HÌNH API BETVIP + PROXY
// =============================================
const API_URL = 'https://wtxmd52.macminim6.online/v1/txmd5/sessions?cp=R&cl=R&pf=web&at=1fc7bfdeab18790088a6e44d6b8cb288&limit=200';

// Proxy dự phòng (nếu API chặn IP Render)
const PROXY_URLS = [
    null, // Thử trực tiếp trước
    'https://cors-anywhere.herokuapp.com/',
    'https://api.allorigins.win/raw?url=',
    'https://corsproxy.io/?'
];

const HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Origin': 'https://betvip.com',
    'Referer': 'https://betvip.com/',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Cache-Control': 'no-cache',
    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
    'Pragma': 'no-cache'
};

// =============================================
// HÀM FETCH VỚI PROXY + RETRY
// =============================================
async function fetchBetVipData(retries = 3) {
    let lastError = null;
    
    for (let attempt = 1; attempt <= retries; attempt++) {
        for (const proxy of PROXY_URLS) {
            try {
                const url = proxy ? proxy + encodeURIComponent(API_URL) : API_URL;
                console.log(`🔄 Thử ${proxy ? 'Proxy' : 'Direct'} (lần ${attempt})...`);
                
                const controller = new AbortController();
                const timeout = setTimeout(() => controller.abort(), 15000);
                
                const response = await fetch(url, {
                    headers: HEADERS,
                    signal: controller.signal
                });
                clearTimeout(timeout);
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                
                if (data && data.list && data.list.length > 0) {
                    console.log(`✅ Thành công! (${data.list.length} records)`);
                    return data;
                } else {
                    throw new Error('Dữ liệu trống hoặc không hợp lệ');
                }
                
            } catch (error) {
                console.warn(`⚠️ ${proxy ? 'Proxy' : 'Direct'} thất bại: ${error.message}`);
                lastError = error;
                // Chờ 1s trước khi thử proxy khác
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        }
        // Đợi 2s trước khi thử lại vòng mới
        await new Promise(resolve => setTimeout(resolve, 2000));
    }
    
    throw new Error(`Không thể lấy dữ liệu sau ${retries} lần thử. Lỗi cuối: ${lastError ? lastError.message : 'Unknown'}`);
}

// =============================================
// THUẬT TOÁN PREDICTOR
// =============================================
class BetVipPredictor {
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
const predictor = new BetVipPredictor();
let lastPrediction = null;
let betVipHistory = [];

// =============================================
// API ENDPOINTS
// =============================================

app.get('/', (req, res) => {
    res.json({
        status: 'OK',
        message: '🚀 BetVip Predictor Server đang chạy!',
        endpoints: {
            health: '/api/health',
            history: '/api/betvip/history',
            predict: '/api/betvip/predict',
            update: '/api/betvip/update?actual=T',
            accuracy: '/api/betvip/accuracy'
        }
    });
});

app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.get('/api/betvip/history', async (req, res) => {
    try {
        const data = await fetchBetVipData();
        const history = data.list.map(item => ({
            result: item.resultTruyenThong === 'TAI' ? 'T' : 'X',
            dices: item.dices,
            point: item.point,
            id: item.id
        }));
        betVipHistory = history;
        res.json({ status: 'OK', data: history, count: history.length });
    } catch (error) {
        console.error('❌ Lỗi:', error.message);
        res.status(503).json({ status: 'ERROR', message: error.message });
    }
});

app.get('/api/betvip/predict', async (req, res) => {
    try {
        const forceRefresh = req.query.refresh === 'true';
        if (betVipHistory.length === 0 || forceRefresh) {
            const data = await fetchBetVipData();
            betVipHistory = data.list.map(item => ({
                result: item.resultTruyenThong === 'TAI' ? 'T' : 'X',
                dices: item.dices,
                point: item.point,
                id: item.id
            }));
        }
        const result = predictor.predict(betVipHistory);
        lastPrediction = result.result;
        res.json({ ...result, historyCount: betVipHistory.length });
    } catch (error) {
        console.error('❌ Lỗi dự đoán:', error.message);
        res.status(503).json({ status: 'ERROR', message: error.message });
    }
});

app.get('/api/betvip/update', (req, res) => {
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

app.get('/api/betvip/accuracy', (req, res) => {
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
    console.log(`🚀 BetVip Predictor Server chạy trên port ${PORT}`);
});