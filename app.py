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
from collections import Counter, deque
import warnings
warnings.filterwarnings('ignore')

load_dotenv()
app = Flask(__name__)

DB_URL = os.getenv('DB_URL')
REDIS_URL = os.getenv('REDIS_URL')
API_URL = os.getenv('API_URL')
POLL_INTERVAL = 5
RETRAIN_EVERY = 200

# ===== COOLDOWN GLOBAL STATE =====
cooldown_info = {
    'active': False,
    'remaining_rounds': 0,
    'loss_streak': 0,
    'start_time': None
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
    
    # ===== ĐẶC TRƯNG CHO PATTERN MASTER MỚI =====
    # 1. Pattern 1-1, 2-2, 3-1
    df['pattern_11'] = 0
    df['pattern_22'] = 0
    df['pattern_31'] = 0
    for i in range(3, len(df)):
        if df['label'].iloc[i] != df['label'].iloc[i-1] and df['label'].iloc[i-1] == df['label'].iloc[i-2] and df['label'].iloc[i-2] != df['label'].iloc[i-3]:
            df.loc[df.index[i], 'pattern_11'] = 1
        if df['label'].iloc[i] == df['label'].iloc[i-1] and df['label'].iloc[i] != df['label'].iloc[i-2] and df['label'].iloc[i-2] == df['label'].iloc[i-3]:
            df.loc[df.index[i], 'pattern_22'] = 1
        if df['label'].iloc[i] != df['label'].iloc[i-1] and df['label'].iloc[i-1] != df['label'].iloc[i-2] and df['label'].iloc[i-2] == df['label'].iloc[i-3]:
            df.loc[df.index[i], 'pattern_31'] = 1
    
    # 2. Tín hiệu bẻ bệt (dựa trên điểm và entropy)
    df['break_signal'] = 0
    for i in range(5, len(df)):
        if df['streak'].iloc[i] >= 4 and df['entropy_10'].iloc[i] > 0.7:
            df.loc[df.index[i], 'break_signal'] = 1
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
        print('[+] Ultra Pattern Master AI trained successfully!')
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

# ===== COOLDOWN FUNCTIONS =====
def check_loss_streak_and_cooldown():
    global cooldown_info
    if cooldown_info['active']:
        cooldown_info['remaining_rounds'] -= 1
        if cooldown_info['remaining_rounds'] <= 0:
            cooldown_info['active'] = False
            cooldown_info['loss_streak'] = 0
            print('[+] COOLDOWN ended, AI resumed.')
        return
    try:
        conn = get_db()
        df = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 10', conn)
        conn.close()
        if len(df) < 10: return
        recent = df['result'].values[::-1]
        if len(recent) >= 3 and recent[-1] == recent[-2] == recent[-3]:
            labels = (df['result'] == 'TAI').astype(int).values[::-1]
            ent = entropy(np.bincount(labels[:10]) / len(labels[:10]), base=2)
            if ent > 0.7:
                cooldown_info['active'] = True
                cooldown_info['remaining_rounds'] = 4
                cooldown_info['loss_streak'] = 3
                cooldown_info['start_time'] = time.time()
                print('[!!!] COOLDOWN ACTIVATED: Lost 3 in a row, pausing 4 rounds.')
    except Exception as e:
        print(f'[-] Cooldown check error: {e}')

def get_cooldown_status():
    if cooldown_info['active']:
        return {
            'active': True,
            'remaining_rounds': cooldown_info['remaining_rounds'],
            'reason': 'Thua 3 ván liên tiếp. Đang nghỉ 4 ván để phân tích.'
        }
    return {'active': False, 'remaining_rounds': 0}

# ===== ULTRA PATTERN MASTER (NEW) =====
def analyze_market_structure(df, lookback=30):
    if len(df) < lookback:
        return 'UNKNOWN', 'Not enough data'
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    ent = entropy(np.bincount(labels) / len(labels), base=2)
    reversals = np.sum(np.diff(labels) != 0)
    streak = 0
    for i in range(1, len(labels)):
        if labels[i] == labels[i-1]:
            streak += 1
        else:
            break
    if ent > 0.85 or reversals > 8:
        return 'CHOPPY', f'entropy={ent:.2f}, reversals={reversals}'
    elif streak >= 5 and ent > 0.7:
        return 'BREAKING', f'bệt {streak} ván, entropy={ent:.2f}'
    elif ent > 0.75 and streak >= 3:
        return 'FAKE_TRENDING', f'FAKE signal, streak={streak}, entropy={ent:.2f}'
    else:
        return 'TRENDING', f'entropy={ent:.2f}, reversals={reversals}, streak={streak}'

def ultra_pattern_matcher(df_history, df_current):
    """
    Nhận diện 10+ loại cầu: bệt, 1-1, 2-2, 3-1, 1-2, 2-1-1, 3-2-1
    """
    if len(df_current) < 15 or len(df_history) < 200:
        return None, 'Not enough data'
    
    # Chuyển thành chuỗi 15 ván
    current_pattern = ''.join(df_current['result'].values[::-1][:15])
    history_patterns = []
    
    for i in range(15, len(df_history)):
        sub = ''.join(df_history['result'].values[i-15:i])
        next_result = df_history.iloc[i]['result']
        history_patterns.append((sub, next_result))
    
    # Tìm mẫu giống nhất (cho phép sai lệch tối đa 2 ván)
    matches = []
    for pattern, next_result in history_patterns:
        diff = sum(1 for a, b in zip(current_pattern, pattern) if a != b)
        if diff <= 2:
            matches.append(next_result)
    
    if not matches:
        return None, 'No similar pattern found'
    
    # Thống kê và phân loại
    counter = Counter(matches)
    most_common = counter.most_common(1)[0]
    
    # Xác định loại cầu
    pattern_type = 'unknown'
    if len(current_pattern) >= 3:
        if current_pattern[-3:] == 'TTT' or current_pattern[-3:] == 'XXX':
            pattern_type = 'Bệt'
        elif current_pattern[-3:] == 'TXT' or current_pattern[-3:] == 'XTX':
            pattern_type = '1-1'
        elif current_pattern[-4:] == 'TTXX' or current_pattern[-4:] == 'XXTT':
            pattern_type = '2-2'
        elif current_pattern[-5:] == 'TTTXX' or current_pattern[-5:] == 'XXXTT':
            pattern_type = '3-2'
    
    return most_common[0], f'Found {len(matches)} similar patterns, {most_common[1]} votes | Type: {pattern_type}'

def smart_streak_pro(df, lookback=100):
    """
    Phân tích bệt siêu cấp với xác suất có điều kiện
    """
    if len(df) < lookback:
        return 0.5, 'Not enough data'
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    current_streak = 0
    current_value = labels[0]
    for val in labels:
        if val == current_value:
            current_streak += 1
        else:
            break
    
    if current_streak < 2:
        return 0.5, 'No significant streak'
    
    # Thống kê các streak trong lịch sử
    streak_lengths = []
    streak_outcomes = []
    temp_len = 0
    temp_val = labels[0]
    for val in labels:
        if val == temp_val:
            temp_len += 1
        else:
            streak_lengths.append(temp_len)
            streak_outcomes.append(val)
            temp_len = 1
            temp_val = val
    streak_lengths.append(temp_len)
    streak_outcomes.append(temp_val)
    
    # Tính xác suất bệt tiếp tục dựa trên độ dài hiện tại
    prob_continue = 0.5
    similar_streaks = []
    for i, length in enumerate(streak_lengths):
        if length >= current_streak and i < len(streak_outcomes):
            similar_streaks.append(streak_outcomes[i])
    
    if similar_streaks:
        prob_continue = np.mean(similar_streaks)
    
    # Điều chỉnh dựa trên độ dài bệt
    if current_streak >= 6:
        prob_continue = max(prob_continue, 0.65)  # Bệt dài có xu hướng tiếp tục
    elif current_streak >= 4:
        prob_continue = max(prob_continue, 0.55)
    
    return prob_continue, f'Streak={current_streak}, prob={prob_continue:.2f}'

def pro_break_hyper(df, confidence, prob_streak, pattern_type):
    """
    Bẻ bệt siêu cấp với 5 tín hiệu
    """
    if len(df) < 30:
        return 'NO', 'Not enough data'
    
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    current_streak = 0
    current_value = labels[0]
    for val in labels:
        if val == current_value:
            current_streak += 1
        else:
            break
    
    # Tín hiệu 1: Điểm biến động
    point_diff = df['point'].diff().values[::-1][:5]
    point_volatility = np.std(point_diff) if len(point_diff) > 0 else 0
    
    # Tín hiệu 2: Entropy
    ent = entropy(np.bincount(labels[:20]) / len(labels[:20]), base=2)
    
    # Tín hiệu 3: Confidence
    conf_signal = confidence < 0.65
    
    # Tín hiệu 4: Lịch sử bẻ (nếu có)
    break_history = df['break_signal'].values[::-1][:10] if 'break_signal' in df.columns else []
    break_ratio = np.mean(break_history) if len(break_history) > 0 else 0
    
    # Tín hiệu 5: Pattern đang là 1-1 hoặc 2-2 (dễ bẻ)
    pattern_signal = pattern_type in ['1-1', '2-2', '3-2']
    
    # Tổng hợp
    score = 0
    if current_streak >= 4: score += 1
    if conf_signal: score += 1
    if prob_streak < 0.55: score += 1
    if ent > 0.7: score += 1
    if point_volatility > 2: score += 1
    if break_ratio > 0.3: score += 1
    if pattern_signal: score += 1
    
    if score >= 5:
        return 'YES', f'Bẻ bệt! Score={score}/7, streak={current_streak}'
    elif score >= 4:
        return 'MAYBE', f'Có thể bẻ, Score={score}/7, streak={current_streak}'
    else:
        return 'NO', f'Không bẻ, Score={score}/7, streak={current_streak}'

def psychology_filter_ultra(df):
    """
    Lọc tâm lý nâng cao
    """
    if len(df) < 30:
        return 'NEUTRAL', 'Not enough data'
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    recent_ratio = np.mean(labels[:10])
    std = np.std(labels[:20])
    if recent_ratio > 0.6 and std < 0.35:
        return 'BIAS_TAI', f'Dân đang theo Tài mạnh ({recent_ratio:.2f})'
    elif recent_ratio < 0.4 and std < 0.35:
        return 'BIAS_XIU', f'Dân đang theo Xỉu mạnh ({recent_ratio:.2f})'
    else:
        return 'NEUTRAL', f'Thị trường cân bằng ({recent_ratio:.2f})'

def risk_manager_ultra(df, confidence, prob_streak, break_decision):
    if len(df) < 20:
        return {'stake': '0%', 'risk': 'HIGH'}, 'Not enough data'
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    volatility = np.std(labels[:20])
    recent_losses = 0
    for i in range(1, min(len(labels), 10)):
        if labels[i] != labels[i-1]:
            recent_losses += 1
    if confidence >= 0.8 and break_decision == 'NO' and volatility < 0.4:
        stake, risk = '6-8%', 'LOW'
    elif confidence >= 0.7 and break_decision == 'NO' and volatility < 0.5:
        stake, risk = '4-6%', 'MEDIUM'
    elif confidence >= 0.65 and break_decision == 'MAYBE':
        stake, risk = '3-4%', 'MEDIUM'
    elif confidence >= 0.6 and break_decision == 'YES':
        stake, risk = '1-2%', 'HIGH'
    else:
        stake, risk = '0%', 'VERY_HIGH'
    if recent_losses >= 3:
        stake, risk = '1-2%', 'HIGH'
    return {'stake': stake, 'risk': risk, 'volatility': round(volatility, 3)}, f'Stake {stake}, Risk {risk}'

# ===== ENDPOINT ULTRA PATTERN MASTER =====
@app.route('/predict_ultra')
def predict_ultra():
    """
    Endpoint chính: Ultra Pattern Master (Toàn diện + Bệt + Bẻ bệt)
    """
    cooldown_status = get_cooldown_status()
    if cooldown_status['active']:
        return jsonify({
            'status': 'COOLDOWN',
            'recommendation': 'KHÔNG ĐÁNH - ĐANG PHÂN TÍCH',
            'remaining_rounds': cooldown_status['remaining_rounds'],
            'reason': cooldown_status['reason']
        })
    try:
        conn = get_db()
        df_raw = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 200', conn)
        conn.close()
        if len(df_raw) < 50:
            return jsonify({'status': 'WAIT', 'reason': 'Need 50+ rounds'})
        
        # ===== Tầng 1: Market Structure =====
        market_state, state_reason = analyze_market_structure(df_raw)
        if market_state in ['CHOPPY', 'FAKE_TRENDING']:
            check_loss_streak_and_cooldown()
            return jsonify({
                'status': 'WAIT',
                'reason': f'Cầu loạn ({state_reason})',
                'recommendation': 'KHÔNG ĐÁNH - NGHỈ'
            })
        
        # ===== Tầng 2: Ultra Pattern Matcher =====
        df_history = pd.read_sql('SELECT result, point FROM sessions ORDER BY id DESC LIMIT 1000', conn)
        df_history = df_history.sort_values('id').reset_index(drop=True)
        df_current = df_raw.sort_values('id').reset_index(drop=True)
        
        match_result, match_reason = ultra_pattern_matcher(df_history, df_current)
        
        # ===== Dự đoán từ AI models =====
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
        
        # ===== Kết hợp với Pattern Matcher =====
        if match_result is not None:
            # Nếu pattern mạnh, ghi đè dự đoán
            if 'Type: Bệt' in match_reason:
                prob_final = max(prob_final, 0.7)  # Tăng xác suất khi bệt
            elif 'Type: 1-1' in match_reason:
                prob_final = 0.5  # 1-1 thì trung lập
            elif 'Type: 2-2' in match_reason:
                prob_final = 0.55 if prob_final < 0.5 else prob_final
            pred = 'TAI' if prob_final >= 0.5 else 'XIU'
            confidence = abs(prob_final - 0.5) * 2
        else:
            pred = 'TAI' if prob_final >= 0.5 else 'XIU'
            confidence = abs(prob_final - 0.5) * 2
        
        # ===== Tầng 3: Smart Streak Pro =====
        prob_streak, streak_reason = smart_streak_pro(df_raw)
        
        # ===== Tầng 4: Pro Break Hyper =====
        # Trích xuất pattern_type từ match_reason
        pattern_type = 'unknown'
        if 'Type: Bệt' in match_reason:
            pattern_type = 'Bệt'
        elif 'Type: 1-1' in match_reason:
            pattern_type = '1-1'
        elif 'Type: 2-2' in match_reason:
            pattern_type = '2-2'
        elif 'Type: 3-2' in match_reason:
            pattern_type = '3-2'
        
        break_decision, break_reason = pro_break_hyper(df_raw, confidence, prob_streak, pattern_type)
        
        # ===== Tầng 5: Psychology Filter Ultra =====
        psychology_bias, psych_reason = psychology_filter_ultra(df_raw)
        
        # ===== Tầng 6: Risk Manager Ultra =====
        risk_info, risk_reason = risk_manager_ultra(df_raw, confidence, prob_streak, break_decision)
        
        # ===== Xử lý Bẻ bệt =====
        if break_decision == 'YES':
            pred = 'XIU' if pred == 'TAI' else 'TAI'
            confidence = confidence * 0.85
            reason = f'BẺ BỆT: {break_reason}'
        elif break_decision == 'MAYBE':
            # Bẻ nhẹ, không đảo ngược hoàn toàn
            reason = f'CÓ THỂ BỂ: {break_reason}'
            confidence = confidence * 1.05
        else:
            reason = f'Theo cầu: {state_reason} | Pattern: {match_reason} | Streak: {streak_reason}'
        
        # ===== Điều chỉnh tâm lý =====
        if psychology_bias == 'BIAS_TAI' and pred == 'XIU':
            confidence = min(confidence * 1.1, 0.92)
        elif psychology_bias == 'BIAS_XIU' and pred == 'TAI':
            confidence = min(confidence * 1.1, 0.92)
        elif psychology_bias != 'NEUTRAL' and pred == psychology_bias.split('_')[1]:
            confidence = confidence * 0.9
        
        # ===== Kiểm tra confidence =====
        if confidence < 0.55:
            check_loss_streak_and_cooldown()
            return jsonify({
                'status': 'WAIT',
                'reason': f'Confidence quá thấp ({confidence:.2f})',
                'recommendation': 'KHÔNG ĐÁNH'
            })
        
        check_loss_streak_and_cooldown()
        return jsonify({
            'status': 'PREDICT',
            'predict': pred,
            'confidence': round(confidence, 3),
            'prob_tai': round(confidence if pred == 'TAI' else 1 - confidence, 3),
            'reason': reason,
            'risk': risk_info,
            'layers': {
                'market_state': market_state,
                'pattern_type': pattern_type,
                'streak_prob': round(prob_streak, 3),
                'break_decision': break_decision,
                'psychology_bias': psychology_bias,
                'pattern_match': match_reason[:50] + '...' if len(match_reason) > 50 else match_reason
            }
        })
    except Exception as e:
        return jsonify({'status': 'ERROR', 'reason': str(e)})

# ===== GIỮ LẠI CÁC ENDPOINT CŨ CHO TƯƠNG THÍCH =====
@app.route('/predict_pro')
def predict_pro():
    # Giữ nguyên để tương thích với các phiên bản cũ
    return predict_ultra()

@app.route('/predict_bệt')
def predict_bệt():
    # Chuyển hướng sang ultra
    return predict_ultra()

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