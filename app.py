import requests, psycopg2, redis, os, time, threading, json, joblib
import numpy as np, pandas as pd
from flask import Flask, jsonify
from dotenv import load_dotenv
from hmmlearn import hmm
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import StackingClassifier
from sklearn.linear_model import LogisticRegression
from scipy.stats import entropy
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
        print(f'[+] Saved {len(new_records)} records')
    except Exception as e:
        print(f'[-] Fetch error: {e}')

def extract_features(df):
    df = df.sort_values('id').reset_index(drop=True)
    df['label'] = (df['result'] == 'TAI').astype(int)
    df['total'] = df['point']
    for lag in range(1, 11):
        df[f'lag_{lag}'] = df['label'].shift(lag).fillna(0)
    for window in [5, 10, 20]:
        df[f'rolling_mean_{window}'] = df['label'].rolling(window).mean().fillna(0.5)
        df[f'rolling_std_{window}'] = df['label'].rolling(window).std().fillna(0)
    df['streak'] = 0
    streak = 0
    for i in range(1, len(df)):
        if df['label'].iloc[i] == df['label'].iloc[i-1]:
            streak += 1
        else:
            streak = 0
        df.loc[df.index[i], 'streak'] = streak
    df['point_diff'] = df['total'].diff().fillna(0)
    def rolling_entropy(x):
        if len(x) < 2: return 0
        p = np.bincount(x.astype(int)) / len(x)
        return entropy(p)
    df['entropy_10'] = df['label'].rolling(10).apply(rolling_entropy).fillna(0)
    df['entropy_20'] = df['label'].rolling(20).apply(rolling_entropy).fillna(0)
    df['reversal_10'] = df['label'].rolling(10).apply(lambda x: np.sum(np.diff(x) != 0)).fillna(0)
    return df.dropna().reset_index(drop=True)

def train_models():
    try:
        conn = get_db()
        df_raw = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 10000', conn)
        conn.close()
        if len(df_raw) < 100:
            print('[-] Not enough data')
            return
        df = extract_features(df_raw)
        if len(df) < 50:
            print('[-] Not enough features')
            return
        feature_cols = [col for col in df.columns if col not in ['id', 'result', 'created_at', 'label']]
        X = df[feature_cols].values
        y = df['label'].values
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        xgb = XGBClassifier(n_estimators=200, max_depth=5, learning_rate=0.05, use_label_encoder=False, random_state=42)
        xgb.fit(X_scaled, y)
        lgbm = LGBMClassifier(n_estimators=150, max_depth=5, learning_rate=0.05, verbose=-1, random_state=42)
        lgbm.fit(X_scaled, y)
        hmm_model = hmm.GaussianHMM(n_components=3, covariance_type='full', n_iter=100)
        hmm_model.fit(X_scaled)
        stacking = StackingClassifier(
            estimators=[('xgb', xgb), ('lgbm', lgbm)],
            final_estimator=LogisticRegression(),
            cv=5
        )
        stacking.fit(X_scaled, y)
        os.makedirs('models', exist_ok=True)
        joblib.dump(hmm_model, 'models/hmm.pkl')
        joblib.dump(xgb, 'models/xgb.pkl')
        joblib.dump(lgbm, 'models/lgbm.pkl')
        joblib.dump(scaler, 'models/scaler.pkl')
        joblib.dump(feature_cols, 'models/feature_cols.pkl')
        joblib.dump(stacking, 'models/stacking.pkl')
        print('[+] Super AI trained successfully!')
    except Exception as e:
        print(f'[-] Train error: {e}')

def load_models():
    try:
        hmm_m = joblib.load('models/hmm.pkl')
        xgb_m = joblib.load('models/xgb.pkl')
        lgbm_m = joblib.load('models/lgbm.pkl')
        scaler = joblib.load('models/scaler.pkl')
        features = joblib.load('models/feature_cols.pkl')
        stacking_m = joblib.load('models/stacking.pkl')
        return hmm_m, xgb_m, lgbm_m, scaler, features, stacking_m
    except:
        return None, None, None, None, None, None

# ================== TẦNG 1: MARKET STRUCTURE ==================
def analyze_market_structure(df, lookback=30):
    """
    Phân tích cấu trúc thị trường (loạn, xu hướng, giả)
    Trả về: TRENDING, CHOPPY, FAKE_TRENDING
    """
    if len(df) < lookback:
        return 'UNKNOWN', 'Not enough data'
    
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    ent = entropy(np.bincount(labels) / len(labels), base=2)
    reversals = np.sum(np.diff(labels) != 0)
    
    # Kiểm tra streak hiện tại
    streak = 0
    for i in range(1, len(labels)):
        if labels[i] == labels[i-1]:
            streak += 1
        else:
            break
    
    # Phân loại
    if ent > 0.85 or reversals > 8:
        return 'CHOPPY', f'entropy={ent:.2f}, reversals={reversals}'
    elif streak >= 5 and ent > 0.7:
        return 'BREAKING', f'bệt {streak} ván, entropy={ent:.2f}'
    elif ent > 0.75 and streak >= 3:
        return 'FAKE_TRENDING', f'Có dấu hiệu FAKE, streak={streak}, entropy={ent:.2f}'
    else:
        return 'TRENDING', f'entropy={ent:.2f}, reversals={reversals}, streak={streak}'

# ================== TẦNG 2: PATTERN MATCHER ==================
def pattern_matcher(df_history, df_current, top_k=5):
    """
    So khớp mẫu cầu hiện tại với lịch sử (trả về dự đoán từ các mẫu giống nhất)
    """
    if len(df_current) < 10 or len(df_history) < 100:
        return None, 'Not enough data'
    
    # Chuyển thành chuỗi T/X
    current_pattern = ''.join(df_current['result'].values[::-1][:10])  # 10 ván gần nhất
    history_patterns = []
    
    for i in range(10, len(df_history)):
        sub = ''.join(df_history['result'].values[i-10:i])
        next_result = df_history.iloc[i]['result']
        history_patterns.append((sub, next_result))
    
    # Tìm các mẫu giống nhất (cho phép sai lệch 1 ván)
    matches = []
    for pattern, next_result in history_patterns:
        diff = sum(1 for a, b in zip(current_pattern, pattern) if a != b)
        if diff <= 1:
            matches.append(next_result)
    
    if not matches:
        return None, 'No similar pattern found'
    
    # Thống kê kết quả từ các mẫu giống nhất
    counter = Counter(matches)
    most_common = counter.most_common(1)[0]
    return most_common[0], f'Found {len(matches)} similar patterns, {most_common[1]} votes'

# ================== TẦNG 3: SMART STREAK ==================
def smart_streak(df, lookback=50):
    """
    Phân tích bệt thông minh: Dự đoán xác suất bệt tiếp tục dựa trên lịch sử
    """
    if len(df) < lookback:
        return 0.5, 'Not enough data'
    
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    
    # Đếm streak hiện tại
    current_streak = 0
    current_value = labels[0]
    for val in labels:
        if val == current_value:
            current_streak += 1
        else:
            break
    
    if current_streak < 2:
        return 0.5, 'No significant streak'
    
    # Tìm các streak tương tự trong lịch sử
    similar_streaks = []
    for i in range(current_streak, len(labels) - 1):
        if labels[i] == current_value and labels[i-1] == current_value:
            # Kiểm tra xem streak trước đó có cùng độ dài không
            streak_len = 0
            j = i
            while j >= 0 and labels[j] == current_value:
                streak_len += 1
                j -= 1
            if streak_len >= current_streak:
                similar_streaks.append(labels[i+1])  # Kết quả sau streak
    
    if not similar_streaks:
        return 0.5, 'No similar streak in history'
    
    # Xác suất tiếp tục bệt
    prob_continue = np.mean(similar_streaks)
    return prob_continue, f'Found {len(similar_streaks)} similar streaks, prob_continue={prob_continue:.2f}'

# ================== TẦNG 4: PRO BREAK ==================
def pro_break(df, confidence, prob_streak):
    """
    Quyết định bẻ bệt chuyên nghiệp
    """
    if len(df) < 20:
        return 'NO', 'Not enough data'
    
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    current_streak = 0
    current_value = labels[0]
    for val in labels:
        if val == current_value:
            current_streak += 1
        else:
            break
    
    # Điều kiện bẻ bệt:
    # 1. Streak đủ dài (≥ 4)
    # 2. Confidence của model thấp (< 0.65)
    # 3. Xác suất tiếp tục bệt thấp (< 0.55)
    # 4. Entropy cao (cầu đang nhiễu)
    
    ent = entropy(np.bincount(labels[:20]) / len(labels[:20]), base=2)
    
    if current_streak >= 4 and confidence < 0.65 and prob_streak < 0.55 and ent > 0.7:
        return 'YES', f'Bẻ bệt! Streak={current_streak}, conf={confidence:.2f}, prob_streak={prob_streak:.2f}'
    elif current_streak >= 5 and confidence < 0.7:
        return 'YES', f'Bẻ bệt mạnh! Streak={current_streak}, conf={confidence:.2f}'
    else:
        return 'NO', f'Không bẻ, streak={current_streak}, conf={confidence:.2f}'

# ================== TẦNG 5: PSYCHOLOGY FILTER ==================
def psychology_filter(df):
    """
    Phân tích tâm lý đám đông dựa trên biến động và độ lệch
    Trả về: BIAS_TAI, BIAS_XIU, NEUTRAL
    """
    if len(df) < 20:
        return 'NEUTRAL', 'Not enough data'
    
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    
    # Tính tỉ lệ Tài/Xỉu trong 10 ván gần nhất
    recent_ratio = np.mean(labels[:10])
    
    # Tính độ lệch chuẩn
    std = np.std(labels[:20])
    
    if recent_ratio > 0.6 and std < 0.4:
        return 'BIAS_TAI', f'Dân đang theo Tài mạnh ({recent_ratio:.2f})'
    elif recent_ratio < 0.4 and std < 0.4:
        return 'BIAS_XIU', f'Dân đang theo Xỉu mạnh ({recent_ratio:.2f})'
    else:
        return 'NEUTRAL', f'Thị trường cân bằng ({recent_ratio:.2f})'

# ================== TẦNG 6: RISK MANAGER ==================
def risk_manager(df, confidence, prob_streak, break_decision):
    """
    Tính toán mức cược khuyến nghị và đánh giá rủi ro
    """
    if len(df) < 20:
        return {'stake': '0%', 'risk': 'HIGH'}, 'Not enough data'
    
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    
    # Tính volatility (độ biến động) của 20 ván gần nhất
    volatility = np.std(labels[:20])
    
    # Tính tỉ lệ thua gần đây (để điều chỉnh risk)
    recent_losses = 0
    for i in range(1, min(len(labels), 10)):
        if labels[i] != labels[i-1]:
            recent_losses += 1
    
    # Xác định mức cược
    if confidence >= 0.75 and break_decision == 'NO' and volatility < 0.45:
        stake = '5-8%'
        risk = 'LOW'
    elif confidence >= 0.65 and break_decision == 'NO' and volatility < 0.55:
        stake = '3-5%'
        risk = 'MEDIUM'
    elif confidence >= 0.6 and break_decision == 'YES':
        stake = '1-3%'
        risk = 'HIGH'
    else:
        stake = '0%'
        risk = 'VERY_HIGH'
    
    # Nếu đang thua liên tục, giảm cược
    if recent_losses >= 3:
        stake = '1-2%'
        risk = 'HIGH'
    
    return {'stake': stake, 'risk': risk, 'volatility': round(volatility, 3)}, f'Cược {stake}, Rủi ro {risk}'

# ================== ENDPOINT AI 6 TẦNG ==================
@app.route('/predict_pro')
def predict_pro():
    try:
        conn = get_db()
        df_raw = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 100', conn)
        conn.close()
        if len(df_raw) < 30:
            return jsonify({'status': 'WAIT', 'reason': 'Not enough data (need 30+ rounds)'})
        
        # TẦNG 1: Phân tích cấu trúc thị trường
        market_state, state_reason = analyze_market_structure(df_raw)
        if market_state == 'CHOPPY':
            return jsonify({
                'status': 'WAIT',
                'reason': f'Cầu loạn ({state_reason})',
                'recommendation': 'KHÔNG ĐÁNH - NGHỈ VÁN NÀY'
            })
        if market_state == 'FAKE_TRENDING':
            return jsonify({
                'status': 'WAIT',
                'reason': f'Có dấu hiệu FAKE ({state_reason})',
                'recommendation': 'KHÔNG ĐÁNH - NGHỈ, CẦU ĐANG LỪA'
            })
        
        # TẦNG 2: Pattern Matcher
        df_history = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 500', conn)
        df_history = df_history.sort_values('id').reset_index(drop=True)
        df_current = df_raw.sort_values('id').reset_index(drop=True)
        
        match_result, match_reason = pattern_matcher(df_history, df_current)
        if match_result is None:
            # Nếu không có pattern, dùng model để dự đoán
            models = load_models()
            if models[0] is None:
                return jsonify({'status': 'ERROR', 'reason': 'Model not ready'})
            hmm_m, xgb_m, lgbm_m, scaler, feature_cols, stacking_m = models
            df_feat = extract_features(df_raw)
            last = df_feat.iloc[-1:][feature_cols].values
            last_scaled = scaler.transform(last)
            prob_xgb = xgb_m.predict_proba(last_scaled)[0][1]
            prob_lgbm = lgbm_m.predict_proba(last_scaled)[0][1]
            prob_hmm = hmm_m.predict_proba(last_scaled)[0][1]
            prob_stack = stacking_m.predict_proba(last_scaled)[0][1]
            prob_final = 0.20*prob_hmm + 0.30*prob_xgb + 0.25*prob_lgbm + 0.25*prob_stack
            pred = 'TAI' if prob_final >= 0.5 else 'XIU'
            confidence = abs(prob_final - 0.5) * 2
        else:
            # Nếu có pattern, dùng pattern để dự đoán
            pred = match_result
            confidence = 0.65  # Mặc định khi dùng pattern (vì có ít nhất 5 mẫu tương tự)
        
        # TẦNG 3: Smart Streak
        prob_streak, streak_reason = smart_streak(df_raw)
        
        # TẦNG 4: Pro Break
        break_decision, break_reason = pro_break(df_raw, confidence, prob_streak)
        
        # TẦNG 5: Psychology Filter
        psychology_bias, psych_reason = psychology_filter(df_raw)
        
        # Điều chỉnh confidence dựa trên tâm lý
        if psychology_bias == 'BIAS_TAI' and pred == 'XIU':
            confidence = confidence * 1.1  # Tăng tự tin nếu đi ngược đám đông
        elif psychology_bias == 'BIAS_XIU' and pred == 'TAI':
            confidence = confidence * 1.1
        elif psychology_bias != 'NEUTRAL' and pred == psychology_bias.split('_')[1]:
            confidence = confidence * 0.9  # Giảm tự tin nếu theo đám đông
        
        # TẦNG 6: Risk Manager
        risk_info, risk_reason = risk_manager(df_raw, confidence, prob_streak, break_decision)
        
        # QUYẾT ĐỊNH CUỐI CÙNG
        if break_decision == 'YES':
            pred = 'XIU' if pred == 'TAI' else 'TAI'  # Bẻ bệt (đảo ngược)
            confidence = confidence * 0.85  # Giảm confidence khi bẻ bệt
            reason = f'BẺ BỆT: {break_reason}'
        else:
            reason = f'Theo cầu: {state_reason}. Pattern: {match_reason}. Streak: {streak_reason}. Tâm lý: {psych_reason}'
        
        # Nếu confidence quá thấp, khuyên không đánh
        if confidence < 0.55:
            return jsonify({
                'status': 'WAIT',
                'reason': f'Confidence quá thấp ({confidence:.2f})',
                'recommendation': 'KHÔNG ĐÁNH - AI KHÔNG TỰ TIN'
            })
        
        return jsonify({
            'status': 'PREDICT',
            'predict': pred,
            'confidence': round(confidence, 3),
            'prob_tai': round(confidence if pred == 'TAI' else 1 - confidence, 3),
            'reason': reason,
            'risk': risk_info,
            'layers': {
                'market_state': market_state,
                'pattern_match': match_result,
                'streak_prob': round(prob_streak, 3),
                'break_decision': break_decision,
                'psychology_bias': psychology_bias
            }
        })
    except Exception as e:
        return jsonify({'status': 'ERROR', 'reason': str(e)})

# ========== GIỮ NGUYÊN CÁC ENDPOINT CŨ ==========
@app.route('/predict')
def predict():
    models = load_models()
    if models[0] is None:
        return jsonify({'predict': 'XIU', 'confidence': 0.5})
    hmm_m, xgb_m, lgbm_m, scaler, feature_cols, stacking_m = models
    try:
        conn = get_db()
        df_raw = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 100', conn)
        conn.close()
        if len(df_raw) < 20:
            return jsonify({'predict': 'XIU', 'confidence': 0.5})
        df = extract_features(df_raw)
        last = df.iloc[-1:][feature_cols].values
        last_scaled = scaler.transform(last)
        prob_xgb = xgb_m.predict_proba(last_scaled)[0][1]
        prob_lgbm = lgbm_m.predict_proba(last_scaled)[0][1]
        prob_hmm = hmm_m.predict_proba(last_scaled)[0][1]
        prob_stack = stacking_m.predict_proba(last_scaled)[0][1]
        prob_final = 0.20*prob_hmm + 0.30*prob_xgb + 0.25*prob_lgbm + 0.25*prob_stack
        pred = 'TAI' if prob_final >= 0.5 else 'XIU'
        conf = abs(prob_final - 0.5) * 2
        return jsonify({'predict': pred, 'confidence': round(conf, 3), 'prob_tai': round(prob_final, 3)})
    except Exception as e:
        return jsonify({'predict': 'XIU', 'confidence': 0.5})

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
        model_ready = False
        try:
            joblib.load('models/hmm.pkl')
            model_ready = True
        except:
            pass
        return jsonify({
            'total_records': total,
            'tai': tai,
            'xiu': xiu,
            'model_ready': model_ready,
            'status': 'running' if total > 0 else 'waiting_for_data'
        })
    except Exception as e:
        return jsonify({'error': str(e), 'status': 'db_error'})

@app.route('/accuracy')
def accuracy():
    try:
        conn = get_db()
        df_raw = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 500', conn)
        conn.close()
        if len(df_raw) < 20:
            return jsonify({'error': 'Not enough data'})
        df = extract_features(df_raw)
        models = load_models()
        if models[0] is None:
            return jsonify({'error': 'Model not ready'})
        hmm_m, xgb_m, lgbm_m, scaler, feature_cols, stacking_m = models
        
        correct, total = 0, 0
        correct_high, high_count = 0, 0
        for i in range(10, len(df)-1):
            X = df.iloc[i:i+1][feature_cols].values
            X_scaled = scaler.transform(X)
            actual = df.iloc[i+1]['label']
            prob_xgb = xgb_m.predict_proba(X_scaled)[0][1]
            prob_lgbm = lgbm_m.predict_proba(X_scaled)[0][1]
            prob_hmm = hmm_m.predict_proba(X_scaled)[0][1]
            prob_stack = stacking_m.predict_proba(X_scaled)[0][1]
            prob_final = 0.20*prob_hmm + 0.30*prob_xgb + 0.25*prob_lgbm + 0.25*prob_stack
            confidence = abs(prob_final - 0.5) * 2
            pred = 1 if prob_final >= 0.5 else 0
            total += 1
            if pred == actual:
                correct += 1
            if confidence >= 0.7:
                high_count += 1
                if pred == actual:
                    correct_high += 1
        
        return jsonify({
            'total_samples': total,
            'accuracy': round(correct/total*100, 2),
            'high_conf_accuracy': round(correct_high/high_count*100, 2) if high_count > 0 else 0,
            'high_conf_count': high_count,
            'status': 'ready'
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/history')
def history():
    try:
        conn = get_db()
        df = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 100', conn)
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
        labels = (df['result'] == 'TAI').astype(int).values
        return jsonify({
            'total': len(df),
            'tai': int(np.sum(labels)),
            'xiu': int(len(labels) - np.sum(labels)),
            'recent': history_data
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/retrain')
def force_retrain():
    try:
        train_models()
        return jsonify({'status': 'Super AI retraining started!'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

def background_worker():
    init_db()
    while True:
        fetch_and_save()
        conn = get_db(); cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM sessions')
        count = cur.fetchone()[0]; conn.close()
        if count % RETRAIN_EVERY == 0 and count > 100:
            train_models()
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)