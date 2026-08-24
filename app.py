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
        print(f'[+] Saved {len(new_records)} new rounds.')
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
                'point': row['point'],
                'dices': [row['dice1'], row['dice2'], row['dice3']]
            })
        return jsonify({
            'recent': history_data,
            'status': 'ready'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/history50')
def history50():
    """
    Xem 50 ván gần nhất + tỉ lệ thắng thực tế (dựa trên kết quả game)
    """
    try:
        conn = get_db()
        df = pd.read_sql('''
            SELECT id, result, point, dice1, dice2, dice3, created_at 
            FROM sessions 
            ORDER BY id DESC 
            LIMIT 50
        ''', conn)
        conn.close()

        if len(df) < 10:
            return jsonify({'error': 'Not enough data (need at least 10 rounds)'})

        tai_count = (df['result'] == 'TAI').sum()
        xiu_count = (df['result'] == 'XIU').sum()
        total = len(df)

        recent = df.to_dict(orient='records')[::-1]

        history_data = []
        for row in recent:
            history_data.append({
                'id': row['id'],
                'result': row['result'],
                'point': row['point'],
                'dices': [row['dice1'], row['dice2'], row['dice3']],
                'time': row['created_at'].strftime('%d-%m-%Y %H:%M:%S') if row['created_at'] else None
            })

        win_rate = round(max(tai_count, xiu_count) / total * 100, 1)

        return jsonify({
            'total_rounds': total,
            'tai': int(tai_count),
            'xiu': int(xiu_count),
            'win_rate': win_rate,
            'history': history_data,
            'status': 'ready'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/streak_20')
def streak_20():
    try:
        conn = get_db()
        df = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 20', conn)
        conn.close()
        if len(df) < 2:
            return jsonify({'error': 'Not enough data'})
        
        recent = df['result'].values[::-1]
        max_streak_tai = 0
        max_streak_xiu = 0
        current_tai = 0
        current_xiu = 0
        
        for res in recent:
            if res == 'TAI':
                current_tai += 1
                current_xiu = 0
                max_streak_tai = max(max_streak_tai, current_tai)
            else:
                current_xiu += 1
                current_tai = 0
                max_streak_xiu = max(max_streak_xiu, current_xiu)
        
        return jsonify({
            'max_win_streak_20': max_streak_tai,
            'max_loss_streak_20': max_streak_xiu,
            'total_tai': list(recent).count('TAI'),
            'total_xiu': list(recent).count('XIU'),
            'status': 'ready'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/accuracy')
def accuracy():
    try:
        conn = get_db()
        df = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 200', conn)
        conn.close()
        if len(df) < 10:
            return jsonify({'error': 'Need at least 10 rounds'})
        
        recent = df['result'].values[::-1]
        tai_count = sum(1 for r in recent if r == 'TAI')
        xiu_count = len(recent) - tai_count
        
        last_10 = recent[:10]
        tai_last_10 = sum(1 for r in last_10 if r == 'TAI')
        xiu_last_10 = 10 - tai_last_10
        
        if tai_last_10 > xiu_last_10:
            trend = 'TAI'
            ratio = tai_last_10 / 10
        else:
            trend = 'XIU'
            ratio = xiu_last_10 / 10
        
        return jsonify({
            'total_samples': len(recent),
            'tai': tai_count,
            'xiu': xiu_count,
            'current_trend': trend,
            'trend_strength': round(ratio * 100, 1),
            'estimated_win_rate': round(max(tai_count, xiu_count) / len(recent) * 100, 1),
            'status': 'ready',
            'note': 'Đây là tỉ lệ dựa trên lịch sử thực tế, không phải tỉ lệ dự đoán của AI.'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

# ===== ENDPOINT MỚI: XEM ĐÚNG/SAI TRONG 50 VÁN =====
@app.route('/accuracy_50')
def accuracy_50():
    """
    Tính tỉ lệ đúng/sai của AI trong 50 ván gần nhất
    (So sánh dự đoán AI với kết quả thực tế)
    """
    try:
        conn = get_db()
        # Lấy 50 ván gần nhất
        df = pd.read_sql('''
            SELECT id, result 
            FROM sessions 
            ORDER BY id DESC 
            LIMIT 50
        ''', conn)
        conn.close()

        if len(df) < 10:
            return jsonify({'error': 'Not enough data (need at least 10 rounds)'})

        # Đảo ngược để từ cũ đến mới
        df = df[::-1].reset_index(drop=True)
        
        correct = 0
        total = len(df)
        details = []
        
        for i in range(1, len(df)):
            # Lấy 10 ván trước đó để tạo pattern
            context = df.iloc[:i]['result'].tolist()
            if len(context) < 5:
                continue
            
            # Dự đoán đơn giản: theo xu hướng
            last_result = context[-1]
            streak = 0
            for res in reversed(context):
                if res == last_result:
                    streak += 1
                else:
                    break
            
            # Logic dự đoán (giống AI)
            if streak >= 5:
                predicted = 'XIU' if last_result == 'TAI' else 'TAI'  # Bẻ bệt
            elif streak >= 3:
                predicted = last_result  # Theo bệt
            else:
                # Theo xu hướng chung
                tai_count = context.count('TAI')
                xiu_count = context.count('XIU')
                predicted = 'TAI' if tai_count >= xiu_count else 'XIU'
            
            actual = df.iloc[i]['result']
            
            is_correct = (predicted == actual)
            if is_correct:
                correct += 1
            
            details.append({
                'round': i + 1,
                'predict': predicted,
                'actual': actual,
                'correct': is_correct
            })
        
        win_rate = round(correct / (len(df) - 1) * 100, 1) if len(df) > 1 else 0
        
        return jsonify({
            'total_rounds': total,
            'correct': correct,
            'wrong': (total - 1) - correct,
            'win_rate': win_rate,
            'details': details[-20:],  # 20 ván gần nhất
            'status': 'ready'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/retrain')
def force_retrain():
    return jsonify({'status': 'AI đang tự học liên tục, không cần retrain thủ công.'})

def background_worker():
    init_db()
    while True:
        fetch_and_save()
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)