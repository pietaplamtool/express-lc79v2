import requests, psycopg2, redis, os, time, threading, json
import numpy as np, pandas as pd
from flask import Flask, jsonify
from dotenv import load_dotenv
from collections import Counter, deque
import warnings
warnings.filterwarnings('ignore')

load_dotenv()
app = Flask(__name__)

DB_URL = os.getenv('DB_URL')
REDIS_URL = os.getenv('REDIS_URL')
API_URL = os.getenv('API_URL')
POLL_INTERVAL = 5

# ===== BỘ NHỚ CỦA LÃO LÀNG =====
experience = {
    'last_prediction': None,
    'last_actual': None,
    'consecutive_losses': 0,
    'consecutive_wins': 0,
    'last_15': deque(maxlen=15)
}

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
        print(f'[+] Lão làng đã học thêm {len(new_records)} ván mới.')
    except Exception as e:
        print(f'[-] Lão làng gặp lỗi: {e}')

def analyze_flow(sequence):
    """
    Phân tích dòng chảy cầu dựa trên 15 ván gần nhất
    Trả về các thông số và chiến thuật khuyến nghị
    """
    if len(sequence) < 8:
        return {'status': 'learning', 'advice': 'Đang quan sát...'}
    
    # 1. Đo độ dài bệt trung bình
    streak_lengths = []
    current_streak = 1
    for i in range(1, len(sequence)):
        if sequence[i] == sequence[i-1]:
            current_streak += 1
        else:
            streak_lengths.append(current_streak)
            current_streak = 1
    streak_lengths.append(current_streak)
    avg_streak = sum(streak_lengths) / len(streak_lengths) if streak_lengths else 0
    
    # 2. Đo tần suất đảo
    reversals = 0
    for i in range(1, len(sequence)):
        if sequence[i] != sequence[i-1]:
            reversals += 1
    reversal_freq = reversals / len(sequence)
    
    # 3. Đo mức độ lệch
    tai_count = sequence.count('T')
    xiu_count = sequence.count('X')
    bias = tai_count / len(sequence)  # 0.5 là cân bằng, >0.5 là lệch Tài, <0.5 là lệch Xỉu
    
    # 4. Đo độ biến động (dựa trên độ lệch chuẩn của các streak)
    volatility = np.std(streak_lengths) if len(streak_lengths) > 1 else 0
    
    # 5. Phát hiện tín hiệu gãy
    break_signal = False
    if len(sequence) >= 3:
        last_three = sequence[-3:]
        if last_three[0] == last_three[1] != last_three[2]:
            # Kiểm tra xem streak trước đó có dài không
            prev_streak = 1
            for i in range(len(sequence)-3, 0, -1):
                if sequence[i] == sequence[i-1]:
                    prev_streak += 1
                else:
                    break
            if prev_streak >= 3:
                break_signal = True
    
    # Xác định chiến thuật
    strategy = {}
    if volatility < 0.5 and (bias > 0.65 or bias < 0.35):
        # Cầu đang bệt và có xu hướng rõ
        trend = 'TAI' if bias > 0.5 else 'XIU'
        strategy = {
            'type': 'STRONG_TREND',
            'advice': f'Theo xu hướng {trend} mạnh.',
            'confidence_boost': 0.2,
            'action': 'FOLLOW_TREND'
        }
    elif reversal_freq > 0.4 and volatility < 0.8:
        # Cầu đang đảo đều
        last = sequence[-1]
        next_pred = 'XIU' if last == 'T' else 'TAI'
        strategy = {
            'type': 'REVERSAL',
            'advice': f'Cầu đang đảo đều, dự đoán {next_pred}.',
            'confidence_boost': 0.1,
            'action': 'REVERSE'
        }
    elif break_signal:
        # Có dấu hiệu gãy
        strategy = {
            'type': 'BREAK_SIGNAL',
            'advice': 'Phát hiện tín hiệu gãy cầu. Đang điều chỉnh...',
            'confidence_boost': -0.05,
            'action': 'WAIT_OR_REVERSE'
        }
    elif volatility > 1.0:
        # Cầu đang loạn
        strategy = {
            'type': 'CHOPPY',
            'advice': 'Cầu đang rất loạn, nên quan sát và giảm cược.',
            'confidence_boost': -0.15,
            'action': 'WAIT'
        }
    else:
        # Mặc định: cân bằng
        strategy = {
            'type': 'NEUTRAL',
            'advice': 'Cầu đang trong giai đoạn trung lập.',
            'confidence_boost': 0.0,
            'action': 'FOLLOW_BIAS'
        }
    
    return {
        'avg_streak': avg_streak,
        'reversal_freq': reversal_freq,
        'bias': bias,
        'volatility': volatility,
        'break_signal': break_signal,
        'strategy': strategy,
        'last_result': sequence[-1]
    }

@app.route('/predict')
def predict():
    global experience
    try:
        conn = get_db()
        df_history = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 5600', conn)
        df_context = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 20', conn)
        conn.close()

        if len(df_history) < 100:
            return jsonify({
                'status': 'WAIT',
                'reason': 'Lão làng đang học thêm kinh nghiệm...',
                'advice': 'Hãy chờ thêm ít phút nữa.'
            })

        context_series = df_context['result'].values[::-1]  # Từ cũ đến mới
        flow_analysis = analyze_flow(context_series)
        
        # === 1. XÁC ĐỊNH BỐI CẢNH CƠ BẢN ===
        current_streak = 0
        last_result = context_series[-1]
        for res in reversed(context_series):
            if res == last_result:
                current_streak += 1
            else:
                break
        
        # === 2. TÌM BẰNG CHỨNG TRONG LỊCH SỬ ===
        pattern_10 = ''.join(context_series[-10:])
        history_series = df_history['result'].values[::-1]
        history_str = ''.join(history_series)
        
        similar_positions = []
        for i in range(len(history_str) - 10):
            if history_str[i:i+10] == pattern_10:
                next_result = history_str[i+10] if i+10 < len(history_str) else None
                if next_result:
                    similar_positions.append(next_result)

        # === 3. LÃO LÀNG RA QUYẾT ĐỊNH (LINH HOẠT) ===
        strategy = flow_analysis['strategy']
        action = strategy['action']
        confidence_boost = strategy['confidence_boost']
        
        # Dự đoán cơ bản (dựa trên bằng chứng hoặc xu hướng)
        if similar_positions:
            counter = Counter(similar_positions)
            total_matches = len(similar_positions)
            tai_prob = counter.get('T', 0) / total_matches
            xiu_prob = counter.get('X', 0) / total_matches
            base_pred = 'TAI' if tai_prob >= xiu_prob else 'XIU'
            base_confidence = max(tai_prob, xiu_prob)
            evidence = f"Trong {total_matches} lần cầu giống vậy, Tài {tai_prob*100:.1f}%, Xỉu {xiu_prob*100:.1f}%."
        else:
            # Dùng xu hướng chung
            tai_count = (df_history['result'] == 'TAI').sum()
            xiu_count = len(df_history) - tai_count
            base_pred = 'TAI' if tai_count >= xiu_count else 'XIU'
            base_confidence = max(tai_count, xiu_count) / len(df_history)
            evidence = f"Dựa trên xu hướng tổng quan (5600 ván)."
        
        # Áp dụng chiến thuật linh hoạt
        if action == 'FOLLOW_TREND':
            # The trend is your friend
            predicted = 'TAI' if flow_analysis['bias'] > 0.5 else 'XIU'
            final_confidence = base_confidence + confidence_boost
            reason = f"Chiến thuật THEO XU HƯỚNG: {strategy['advice']} {evidence}"
        elif action == 'REVERSE':
            # Đánh ngược lại ván cuối
            predicted = 'XIU' if last_result == 'TAI' else 'TAI'
            final_confidence = base_confidence + confidence_boost
            reason = f"Chiến thuật ĐẢO CẦU: {strategy['advice']} {evidence}"
        elif action == 'WAIT_OR_REVERSE':
            # Nếu có tín hiệu gãy, ưu tiên quan sát, nhưng vẫn đưa ra dự đoán thận trọng
            predicted = 'XIU' if last_result == 'TAI' else 'TAI'
            final_confidence = max(base_confidence - 0.1, 0.5)
            reason = f"TÍN HIỆU GÃY: {strategy['advice']} {evidence}"
        elif action == 'WAIT':
            # Cầu loạn, dự đoán nhưng khuyên thận trọng
            predicted = base_pred
            final_confidence = max(base_confidence - 0.15, 0.5)
            reason = f"CẦU LOẠN: {strategy['advice']} {evidence}"
        else:
            # NEUTRAL: giữ nguyên dự đoán cơ bản
            predicted = base_pred
            final_confidence = base_confidence + confidence_boost
            reason = f"TRUNG LẬP: {strategy['advice']} {evidence}"
        
        # Giới hạn confidence hợp lý
        final_confidence = max(0.5, min(0.95, final_confidence))
        
        return jsonify({
            'status': 'PREDICT',
            'predict': predicted,
            'confidence': round(final_confidence, 3),
            'reason': reason,
            'context': {
                'current_streak': current_streak,
                'flow_analysis': {
                    'avg_streak': round(flow_analysis['avg_streak'], 2),
                    'reversal_freq': round(flow_analysis['reversal_freq'], 2),
                    'bias': round(flow_analysis['bias'], 2),
                    'volatility': round(flow_analysis['volatility'], 2),
                    'strategy': strategy['type'],
                    'action': action
                },
                'total_matches': len(similar_positions),
                'total_rounds_learned': len(df_history)
            }
        })
    except Exception as e:
        return jsonify({'status': 'ERROR', 'reason': str(e)})

# ===== GIỮ NGUYÊN CÁC ENDPOINT CŨ =====
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

@app.route('/summary_50')
def summary_50():
    try:
        conn = get_db()
        df = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 50', conn)
        conn.close()
        if len(df) < 10:
            return jsonify({'error': 'Not enough data'})
        recent = df['result'].values[::-1]
        tai_count = sum(1 for r in recent if r == 'TAI')
        xiu_count = len(recent) - tai_count
        history_str = ''.join('T' if r == 'TAI' else 'X' for r in recent)
        if tai_count >= xiu_count:
            win, lose = tai_count, xiu_count
        else:
            win, lose = xiu_count, tai_count
        win_rate = round(win / len(recent) * 100, 1)
        return jsonify({
            'total_rounds': len(recent),
            'win': win,
            'lose': lose,
            'win_rate': win_rate,
            'history': history_str,
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
            trend, ratio = 'TAI', tai_last_10 / 10
        else:
            trend, ratio = 'XIU', xiu_last_10 / 10
        return jsonify({
            'total_samples': len(recent),
            'tai': tai_count,
            'xiu': xiu_count,
            'current_trend': trend,
            'trend_strength': round(ratio * 100, 1),
            'estimated_win_rate': round(max(tai_count, xiu_count) / len(recent) * 100, 1),
            'status': 'ready',
            'note': 'Đây là tỉ lệ dựa trên lịch sử thực tế.'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/history50')
def history50():
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

def background_worker():
    init_db()
    while True:
        fetch_and_save()
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)