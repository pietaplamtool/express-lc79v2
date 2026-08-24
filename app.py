import requests, psycopg2, redis, os, time, threading, json
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

# ===== "LÃO LÀNG" AI =====
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

@app.route('/predict')
def predict():
    try:
        conn = get_db()
        # Lấy 4000 ván gần nhất để làm bộ nhớ
        df_history = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 4000', conn)
        # Lấy 15 ván gần nhất để làm bối cảnh hiện tại
        df_context = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 15', conn)
        conn.close()

        if len(df_history) < 100:
            return jsonify({
                'status': 'WAIT',
                'reason': 'Lão làng đang học thêm kinh nghiệm từ các ván mới...',
                'advice': 'Hãy chờ thêm ít phút nữa.'
            })

        # === 1. Xác định bối cảnh (Context) ===
        context_series = df_context['result'].values[::-1]  # Từ cũ đến mới
        current_streak = 0
        last_result = context_series[-1]
        for res in reversed(context_series):
            if res == last_result:
                current_streak += 1
            else:
                break

        # Tạo pattern 10 ván (không cần dấu cách)
        pattern_10 = ''.join(context_series[-10:])
        
        # === 2. Tìm kiếm bằng chứng trong 4000 ván ===
        history_series = df_history['result'].values[::-1]
        history_str = ''.join(history_series)
        
        # Tìm tất cả các vị trí giống pattern hiện tại
        similar_positions = []
        for i in range(len(history_str) - 10):
            if history_str[i:i+10] == pattern_10:
                # Lấy kết quả của ván tiếp theo (sau pattern)
                next_result = history_str[i+10] if i+10 < len(history_str) else None
                if next_result:
                    similar_positions.append(next_result)

        # === 3. Lão làng đưa ra quyết định ===
        if not similar_positions:
            # Nếu chưa gặp pattern này, lão làng dựa vào kinh nghiệm bệt
            if current_streak >= 3:
                predicted = last_result
                confidence = 0.55 + min(current_streak * 0.03, 0.15)
                evidence = f"Bệt {current_streak} ván. Lão làng tin rằng cầu sẽ tiếp tục."
            else:
                # Mặc định theo xu hướng chung (Tài nhiều hơn)
                tai_count = (df_history['result'] == 'TAI').sum()
                xiu_count = len(df_history) - tai_count
                predicted = 'TAI' if tai_count >= xiu_count else 'XIU'
                confidence = max(tai_count, xiu_count) / len(df_history)
                evidence = f"Tổng quan {len(df_history)} ván: Tài {tai_count}, Xỉu {xiu_count}."
        else:
            # Đếm số lần Tài và Xỉu sau pattern
            counter = Counter(similar_positions)
            total_matches = len(similar_positions)
            tai_prob = counter.get('T', 0) / total_matches
            xiu_prob = counter.get('X', 0) / total_matches
            
            # Lão làng quyết định
            if tai_prob >= xiu_prob:
                predicted = 'TAI'
                confidence = tai_prob
                evidence = f"Trong {total_matches} lần cầu giống vậy, có {counter.get('T', 0)} lần ra Tài, {counter.get('X', 0)} lần ra Xỉu."
            else:
                predicted = 'XIU'
                confidence = xiu_prob
                evidence = f"Trong {total_matches} lần cầu giống vậy, có {counter.get('X', 0)} lần ra Xỉu, {counter.get('T', 0)} lần ra Tài."
            
            # Điều chỉnh dựa trên bệt (ưu tiên bệt)
            if current_streak >= 3 and predicted != last_result:
                # Nếu đang bệt dài mà lão làng muốn bẻ, thì cân nhắc
                if current_streak >= 6 and confidence < 0.7:
                    # Bệt quá dài (>=6) và xác suất thấp -> bẻ
                    predicted = 'XIU' if last_result == 'TAI' else 'TAI'
                    evidence += f" Nhưng bệt đã {current_streak} ván, lão làng quyết định bẻ."

        # === 4. Lão làng trả lời ===
        return jsonify({
            'status': 'PREDICT',
            'predict': predicted,
            'confidence': round(min(confidence, 0.95), 3),
            'reason': evidence,
            'context': {
                'current_streak': current_streak,
                'last_10': list(context_series[-10:]),
                'total_matches': len(similar_positions) if similar_positions else 0,
                'total_rounds_learned': len(df_history)
            }
        })
    except Exception as e:
        return jsonify({'status': 'ERROR', 'reason': str(e)})

# ===== CÁC ENDPOINT HỖ TRỢ (GIỮ NGUYÊN) =====
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

# ===== ENDPOINT MỚI: SUMMARY 50 VÁN =====
@app.route('/summary_50')
def summary_50():
    """
    Thống kê 50 ván gần nhất: tổng, thắng, thua, chuỗi kết quả rút gọn (T/X)
    """
    try:
        conn = get_db()
        df = pd.read_sql('''
            SELECT result FROM sessions ORDER BY id DESC LIMIT 50
        ''', conn)
        conn.close()

        if len(df) < 10:
            return jsonify({'error': 'Not enough data'})

        # Lấy kết quả từ ván cũ đến mới
        recent = df['result'].values[::-1]

        # Đếm số Tài và Xỉu
        tai_count = sum(1 for r in recent if r == 'TAI')
        xiu_count = len(recent) - tai_count

        # Chuỗi rút gọn: T = Tài, X = Xỉu
        history_str = ''.join('T' if r == 'TAI' else 'X' for r in recent)

        # Quy ước: win là cửa xuất hiện nhiều hơn
        if tai_count >= xiu_count:
            win = tai_count
            lose = xiu_count
        else:
            win = xiu_count
            lose = tai_count

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

def background_worker():
    init_db()
    while True:
        fetch_and_save()
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)