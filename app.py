import requests, psycopg2, redis, os, time, threading, json, joblib
import numpy as np, pandas as pd
from flask import Flask, jsonify
from dotenv import load_dotenv
from collections import Counter
import warnings
warnings.filterwarnings('ignore')

load_dotenv()
app = Flask(__name__)

DB_URL = os.getenv('DB_URL')
REDIS_URL = os.getenv('REDIS_URL')
API_URL = os.getenv('API_URL')
POLL_INTERVAL = 5
RETRAIN_EVERY = 200

# ===== HỆ THỐNG CHÍNH =====
def get_db():
    return psycopg2.connect(DB_URL)

def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id BIGINT PRIMARY KEY,
            result TEXT,
            dice1 INT, dice2 INT, dice3 INT,
            point INT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            id SERIAL PRIMARY KEY,
            session_id BIGINT,
            predict TEXT,
            confidence FLOAT,
            actual TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    conn.commit(); conn.close()

def fetch_and_save():
    r = get_redis()
    last_id = r.get('last_id') or 0
    try:
        resp = requests.get(API_URL, timeout=10).json()
        data = resp.get('list', [])
        if not data: return
        latest_id = data[0]['id']
        if latest_id <= int(last_id): return
        new_records = [d for d in data if d['id'] > int(last_id)]
        if not new_records: return
        conn = get_db(); cur = conn.cursor()
        for d in new_records:
            cur.execute('''
                INSERT INTO sessions (id, result, dice1, dice2, dice3, point)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            ''', (d['id'], d['resultTruyenThong'], d['dices'][0], d['dices'][1], d['dices'][2], d['point']))
        conn.commit(); conn.close()
        r.set('last_id', latest_id)
        print(f'[+] Saved {len(new_records)} new rounds. Total: {r.get("total_count") or 0}')
    except Exception as e:
        print(f'[-] Fetch error: {e}')

def analyze_pattern(df_context, df_search, n_rounds=10):
    """
    Phân tích và tìm pattern tương tự. Học liên tục từ dữ liệu mới.
    """
    if len(df_search) < 30:
        return None, 0.5, 'Not enough data to learn'
    
    search_series = df_search['result'].values[::-1]
    context_series = df_context['result'].values[::-1]
    
    # Xác định chuỗi bệt hiện tại
    last_result = context_series[-1]
    current_streak = 0
    for res in reversed(context_series):
        if res == last_result:
            current_streak += 1
        else:
            break
    
    # Tạo pattern
    pattern_length = min(n_rounds, len(context_series))
    current_pattern = ''.join(context_series[-pattern_length:])
    
    # Tìm kiếm trong lịch sử
    similar_patterns = []
    search_history = ''.join(search_series)
    
    for i in range(pattern_length, len(search_history) - 1):
        if search_history[i-pattern_length:i] == current_pattern:
            similar_patterns.append(search_history[i])
    
    if similar_patterns:
        counter = Counter(similar_patterns)
        total_similar = len(similar_patterns)
        tai_prob = counter.get('T', 0) / total_similar
        xiu_prob = counter.get('X', 0) / total_similar
        
        # Quyết định dựa trên bối cảnh
        if current_streak >= 5:
            predicted = 'XIU' if last_result == 'T' else 'TAI'
            confidence = max(tai_prob, xiu_prob) * 0.9
            reason = f'SIÊU BỆT {current_streak} ván! Bẻ bệt với {confidence*100:.1f}% tự tin.'
        elif current_streak >= 3:
            predicted = last_result
            confidence = max(tai_prob, xiu_prob) * 1.1
            reason = f'THEO BỆT {current_streak} ván. Tỉ lệ thắng kỳ vọng: {confidence*100:.1f}%'
        else:
            predicted = 'TAI' if tai_prob >= xiu_prob else 'XIU'
            confidence = max(tai_prob, xiu_prob)
            reason = f'AI nhận diện pattern, chọn {predicted} với {confidence*100:.1f}% tự tin.'
        
        return predicted, min(confidence, 0.95), reason, current_streak
    else:
        # Nếu không có pattern cũ, đưa ra dự đoán dựa trên tỉ lệ chung
        global_tai = (df_search['result'] == 'TAI').sum() / len(df_search)
        predicted = 'TAI' if global_tai >= 0.5 else 'XIU'
        confidence = max(global_tai, 1 - global_tai)
        return predicted, confidence, 'Pattern mới! Đang học hỏi, dựa trên xác suất chung.', current_streak

@app.route('/predict')
def predict():
    try:
        conn = get_db()
        # Lấy dữ liệu
        df_context = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 30', conn)
        df_search = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 1000', conn)
        conn.close()

        if len(df_search) < 50:
            return jsonify({'status': 'WAIT', 'reason': 'AI đang huấn luyện với 3000 ván...'})

        # Phân tích và học từ dữ liệu
        predicted, confidence, reason, streak = analyze_pattern(df_context, df_search)
        
        return jsonify({
            'status': 'PREDICT',
            'predict': predicted,
            'confidence': round(confidence, 3),
            'reason': reason,
            'context': {
                'current_streak': streak,
                'total_learned': len(df_search)
            }
        })
    except Exception as e:
        return jsonify({'status': 'ERROR', 'reason': str(e)})

@app.route('/stats')
def stats():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM sessions')
        total = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM sessions WHERE result = \'TAI\'')
        tai = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM sessions WHERE result = \'XIU\'')
        xiu = cur.fetchone()[0]
        conn.close()
        return jsonify({
            'total_rounds_learned': total,
            'tai': tai,
            'xiu': xiu,
            'status': 'running' if total > 0 else 'waiting_for_data'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/history')
def history():
    try:
        conn = get_db()
        df = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 20', conn)
        conn.close()
        if len(df) < 10:
            return jsonify({'error': 'Not enough data'})
        recent = df.head(20).to_dict(orient='records')[::-1]
        history_data = []
        for row in recent:
            history_data.append({
                'id': row['id'],
                'result': row['result'],
                'point': row['point']
            })
        return jsonify({
            'recent': history_data,
            'status': 'ready'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

def background_worker():
    init_db()
    while True:
        fetch_and_save()
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)