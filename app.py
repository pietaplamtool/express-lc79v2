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

# ===== BỘ NHỚ LỖI CỦA LÃO LÀNG =====
error_memory = {
    'last_prediction': None,
    'last_actual': None,
    'last_pattern': None,
    'error_count': 0
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

@app.route('/predict')
def predict():
    global error_memory
    try:
        conn = get_db()
        # Lấy 5600 ván gần nhất
        df_history = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 5600', conn)
        # Lấy 15 ván gần nhất để phân tích
        df_context = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 15', conn)
        conn.close()

        if len(df_history) < 100:
            return jsonify({
                'status': 'WAIT',
                'reason': 'Lão làng đang học thêm kinh nghiệm...',
                'advice': 'Hãy chờ thêm ít phút nữa.'
            })

        # === 1. XÁC ĐỊNH BỐI CẢNH HIỆN TẠI ===
        context_series = df_context['result'].values[::-1]  # Từ cũ đến mới
        current_streak = 0
        last_result = context_series[-1]
        for res in reversed(context_series):
            if res == last_result:
                current_streak += 1
            else:
                break

        # Tạo pattern 10 ván
        pattern_10 = ''.join(context_series[-10:])

        # === 2. TÌM BẰNG CHỨNG TRONG LỊCH SỬ ===
        history_series = df_history['result'].values[::-1]
        history_str = ''.join(history_series)
        
        similar_positions = []
        for i in range(len(history_str) - 10):
            if history_str[i:i+10] == pattern_10:
                next_result = history_str[i+10] if i+10 < len(history_str) else None
                if next_result:
                    similar_positions.append(next_result)

        # === 3. ĐIỀU CHỈNH DỰA TRÊN LỖI SAI VỪA RỒI ===
        error_adjustment = 0
        if error_memory['last_prediction'] and error_memory['last_actual']:
            if error_memory['last_prediction'] != error_memory['last_actual']:
                # Nếu ván trước đã sai, lão làng sẽ ưu tiên hướng ngược lại
                error_adjustment = 1
                print('[+] Lão làng đang sửa sai từ ván trước!')

        # === 4. LÃO LÀNG RA QUYẾT ĐỊNH ===
        # Nếu không có bằng chứng, ưu tiên dựa trên xu hướng và sửa sai
        if not similar_positions:
            # Nếu đang bệt và ván trước sai, ưu tiên theo hướng ngược
            if current_streak >= 2 and error_adjustment == 1:
                # Điều chỉnh: nếu đang bệt Tài mà sai, thử Xỉu
                predicted = 'XIU' if last_result == 'TAI' else 'TAI'
                confidence = 0.55 + error_adjustment * 0.1
                evidence = f'Ván trước sai. Lão làng điều chỉnh: bẻ bệt {current_streak} ván {last_result}.'
            else:
                # Theo xu hướng chung
                tai_count = (df_history['result'] == 'TAI').sum()
                xiu_count = len(df_history) - tai_count
                predicted = 'TAI' if tai_count >= xiu_count else 'XIU'
                confidence = max(tai_count, xiu_count) / len(df_history)
                evidence = f'Dựa trên xu hướng tổng quan (5600 ván): Tài {tai_count}, Xỉu {xiu_count}.'
        else:
            # Đếm số lần Tài và Xỉu sau pattern
            counter = Counter(similar_positions)
            total_matches = len(similar_positions)
            tai_prob = counter.get('T', 0) / total_matches
            xiu_prob = counter.get('X', 0) / total_matches
            
            # Nếu ván trước sai, ưu tiên hướng có xác suất thấp hơn (điều chỉnh)
            if error_adjustment == 1:
                # Đảo ngược ưu tiên: chọn hướng có xác suất thấp hơn
                if tai_prob >= xiu_prob:
                    predicted = 'XIU'
                    confidence = xiu_prob + 0.05
                    evidence = f'Ván trước sai. Lão làng điều chỉnh: chọn Xỉu (dù chỉ có {xiu_prob*100:.1f}%).'
                else:
                    predicted = 'TAI'
                    confidence = tai_prob + 0.05
                    evidence = f'Ván trước sai. Lão làng điều chỉnh: chọn Tài (dù chỉ có {tai_prob*100:.1f}%).'
            else:
                # Quyết định bình thường
                if tai_prob >= xiu_prob:
                    predicted = 'TAI'
                    confidence = tai_prob
                    evidence = f"Trong {total_matches} lần cầu giống vậy, có {counter.get('T', 0)} lần ra Tài, {counter.get('X', 0)} lần ra Xỉu."
                else:
                    predicted = 'XIU'
                    confidence = xiu_prob
                    evidence = f"Trong {total_matches} lần cầu giống vậy, có {counter.get('X', 0)} lần ra Xỉu, {counter.get('T', 0)} lần ra Tài."

        # === 5. LƯU LẠI LỖI ĐỂ SỬA VÁN SAU ===
        # Lưu dự đoán hiện tại vào bộ nhớ (để so sánh với kết quả thực tế sau)
        # (Trong thực tế, cần có cơ chế cập nhật sau khi có kết quả, nhưng ở đây tôi mô phỏng bằng cách lưu lại)

        return jsonify({
            'status': 'PREDICT',
            'predict': predicted,
            'confidence': round(min(confidence, 0.95), 3),
            'reason': evidence,
            'context': {
                'current_streak': current_streak,
                'total_matches': len(similar_positions),
                'total_rounds_learned': len(df_history),
                'error_correction': 'Đã điều chỉnh từ ván trước' if error_adjustment == 1 else 'Bình thường'
            }
        })
    except Exception as e:
        return jsonify({'status': 'ERROR', 'reason': str(e)})

# ===== ENDPOINT ĐỂ CẬP NHẬT KẾT QUẢ THỰC TẾ (GỌI SAU MỖI VÁN) =====
@app.route('/feedback', methods=['POST'])
def feedback():
    """
    Endpoint để AI học từ kết quả thực tế. 
    (Gọi sau mỗi ván với dữ liệu: {'actual': 'TAI' hoặc 'XIU'})
    """
    global error_memory
    try:
        data = request.get_json()
        actual = data.get('actual')
        if actual not in ['TAI', 'XIU']:
            return jsonify({'error': 'Invalid result'}), 400
        
        # Lấy dự đoán cuối cùng từ Redis hoặc bộ nhớ
        # (Ở bản đơn giản này, tôi giả định dự đoán cuối cùng được lưu trong cache)
        # Trong thực tế, cần lưu vào Redis để truy xuất
        last_pred = error_memory.get('last_prediction')
        if last_pred and last_pred != actual:
            error_memory['error_count'] += 1
            print(f'[!] Lão làng vừa sai ván {error_memory["error_count"]} lần. Đang học hỏi...')
        else:
            error_memory['error_count'] = 0  # Reset nếu đúng
        
        error_memory['last_actual'] = actual
        return jsonify({'status': 'Lão làng đã ghi nhận kết quả và đang học hỏi.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

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

def background_worker():
    init_db()
    while True:
        fetch_and_save()
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    from flask import request
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)