const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

// =============================================
// CẤU HÌNH API LC79 - REAL-TIME
// =============================================
const API_URL = 'https://wtxmd52.tele68.com/v1/txmd5/lite-sessions?cp=R&cl=R&pf=web&at=2f9251283c3748d5e0f0528c1eeac6de';
// Cập nhật token từ web LC79 nếu cần

const HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Origin': 'https://lc79.com',
    'Referer': 'https://lc79.com/',
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
    'Sec-Fetch-Site': 'cross-site'
};

// =============================================
// HÀM FETCH API REAL-TIME (CHỈ LẤY DỮ LIỆU THẬT)
// =============================================
async function fetchLC79History(retries = 3) {
    let lastError = null;
    for (let attempt = 1; attempt <= retries; attempt++) {
        try {
            console.log(`🔄 Lần thử ${attempt}/${retries}...`);
            
            const response = await fetch(API_URL, { 
                headers: HEADERS,
                timeout: 10000 // 10 giây timeout
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            if (data && data.list && data.list.length > 0) {
                console.log(`✅ Lấy dữ liệu real-time thành công! (${data.list.length} records)`);
                return data;
            } else {
                throw new Error('Dữ liệu trống hoặc không hợp lệ');
            }
            
        } catch (error) {
            console.warn(`⚠️ Lần ${attempt} thất bại: ${error.message}`);
            lastError = error;
            
            if (attempt === retries) {
                // Lần cuối thất bại -> trả về lỗi rõ ràng
                throw new Error(`Không thể lấy dữ liệu real-time sau ${retries} lần thử. Lỗi cuối: ${lastError.message}`);
            }
            
            // Đợi 2 giây trước khi thử lại
            await new Promise(resolve => setTimeout(resolve, 2000));
        }
    }
}

// =============================================
// THUẬT TOÁN LC79 PREDICTOR
// =============================================
class LC79Predictor {
    // ... (giữ nguyên class LC79Predictor từ code trước)
    // (Bạn hãy copy class này từ code cũ để đảm bảo đầy đủ)
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

app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.get('/api/lc79/history', async (req, res) => {
    try {
        const data = await fetchLC79History();
        const history = data.list.map(item => ({
            result: item.resultTruyenThong === 'TAI' ? 'T' : 'X',
            dices: item.dices,
            point: item.point,
            id: item.id
        }));
        lc79History = history;
        res.json({ 
            status: 'OK', 
            data: history, 
            count: history.length
        });
    } catch (error) {
        console.error('❌ Lỗi fetch API real-time:', error.message);
        res.status(503).json({ 
            status: 'ERROR', 
            message: 'Không thể lấy dữ liệu real-time từ API LC79. Vui lòng thử lại sau.',
            detail: error.message
        });
    }
});

app.get('/api/lc79/predict', async (req, res) => {
    try {
        const forceRefresh = req.query.refresh === 'true';
        if (lc79History.length === 0 || forceRefresh) {
            const data = await fetchLC79History();
            lc79History = data.list.map(item => ({
                result: item.resultTruyenThong === 'TAI' ? 'T' : 'X',
                dices: item.dices,
                point: item.point,
                id: item.id
            }));
        }

        const result = predictor.predict(lc79History);
        lastPrediction = result.result;
        res.json({ 
            ...result, 
            historyCount: lc79History.length,
            lastId: lc79History[0]?.id 
        });
    } catch (error) {
        console.error('❌ Lỗi dự đoán:', error.message);
        res.status(503).json({ 
            status: 'ERROR', 
            message: 'Không thể lấy dữ liệu real-time để dự đoán. Vui lòng thử lại sau.',
            detail: error.message
        });
    }
});

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