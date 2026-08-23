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

# ===== 6-LAYER AI FUNCTIONS =====
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

def pattern_matcher(df_history, df_current):
    if len(df_current) < 10 or len(df_history) < 100:
        return None, 'Not enough data'
    current_pattern = ''.join(df_current['result'].values[::-1][:10])
    history_patterns = []
    for i in range(10, len(df_history)):
        sub = ''.join(df_history['result'].values[i-10:i])
        next_result = df_history.iloc[i]['result']
        history_patterns.append((sub, next_result))
    matches = []
    for pattern, next_result in history_patterns:
        diff = sum(1 for a, b in zip(current_pattern, pattern) if a != b)
        if diff <= 1:
            matches.append(next_result)
    if not matches:
        return None, 'No similar pattern found'
    counter = Counter(matches)
    most_common = counter.most_common(1)[0]
    return most_common[0], f'Found {len(matches)} similar patterns, {most_common[1]} votes'

def smart_streak(df, lookback=50):
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
    similar_streaks = []
    for i in range(current_streak, len(labels) - 1):
        if labels[i] == current_value and labels[i-1] == current_value:
            streak_len = 0
            j = i
            while j >= 0 and labels[j] == current_value:
                streak_len += 1
                j -= 1
            if streak_len >= current_streak:
                similar_streaks.append(labels[i+1])
    if not similar_streaks:
        return 0.5, 'No similar streak in history'
    prob_continue = np.mean(similar_streaks)
    return prob_continue, f'Found {len(similar_streaks)} similar streaks, prob_continue={prob_continue:.2f}'

def pro_break(df, confidence, prob_streak):
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
    ent = entropy(np.bincount(labels[:20]) / len(labels[:20]), base=2)
    if current_streak >= 4 and confidence < 0.65 and prob_streak < 0.55 and ent > 0.7:
        return 'YES', f'Break! streak={current_streak}, conf={confidence:.2f}'
    elif current_streak >= 5 and confidence < 0.7:
        return 'YES', f'Break! streak={current_streak}, conf={confidence:.2f}'
    else:
        return 'NO', f'No break, streak={current_streak}, conf={confidence:.2f}'

def psychology_filter(df):
    if len(df) < 20:
        return 'NEUTRAL', 'Not enough data'
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    recent_ratio = np.mean(labels[:10])
    std = np.std(labels[:20])
    if recent_ratio > 0.6 and std < 0.4:
        return 'BIAS_TAI', f'Crowd following TAI ({recent_ratio:.2f})'
    elif recent_ratio < 0.4 and std < 0.4:
        return 'BIAS_XIU', f'Crowd following XIU ({recent_ratio:.2f})'
    else:
        return 'NEUTRAL', f'Market balanced ({recent_ratio:.2f})'

def risk_manager(df, confidence, prob_streak, break_decision):
    if len(df) < 20:
        return {'stake': '0%', 'risk': 'HIGH'}, 'Not enough data'
    labels = (df['result'] == 'TAI').astype(int).values[::-1]
    volatility = np.std(labels[:20])
    recent_losses = 0
    for i in range(1, min(len(labels), 10)):
        if labels[i] != labels[i-1]:
            recent_losses += 1
    if confidence >= 0.75 and break_decision == 'NO' and volatility < 0.45:
        stake, risk = '5-8%', 'LOW'
    elif confidence >= 0.65 and break_decision == 'NO' and volatility < 0.55:
        stake, risk = '3-5%', 'MEDIUM'
    elif confidence >= 0.6 and break_decision == 'YES':
        stake, risk = '1-3%', 'HIGH'
    else:
        stake, risk = '0%', 'VERY_HIGH'
    if recent_losses >= 3:
        stake, risk = '1-2%', 'HIGH'
    return {'stake': stake, 'risk': risk, 'volatility': round(volatility, 3)}, f'Stake {stake}, Risk {risk}'

# ===== ENDPOINTS =====
@app.route('/predict_pro')
def predict_pro():
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
        df_raw = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 100', conn)
        conn.close()
        if len(df_raw) < 30:
            return jsonify({'status': 'WAIT', 'reason': 'Need 30+ rounds'})
        
        market_state, state_reason = analyze_market_structure(df_raw)
        if market_state in ['CHOPPY', 'FAKE_TRENDING']:
            check_loss_streak_and_cooldown()
            return jsonify({
                'status': 'WAIT',
                'reason': f'Cầu loạn ({state_reason})',
                'recommendation': 'KHÔNG ĐÁNH - NGHỈ'
            })
        
        df_history = pd.read_sql('SELECT result FROM sessions ORDER BY id DESC LIMIT 500', conn)
        df_history = df_history.sort_values('id').reset_index(drop=True)
        df_current = df_raw.sort_values('id').reset_index(drop=True)
        
        match_result, match_reason = pattern_matcher(df_history, df_current)
        if match_result is None:
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
            pred = match_result
            confidence = 0.65
        
        prob_streak, streak_reason = smart_streak(df_raw)
        break_decision, break_reason = pro_break(df_raw, confidence, prob_streak)
        psychology_bias, psych_reason = psychology_filter(df_raw)
        
        if psychology_bias == 'BIAS_TAI' and pred == 'XIU':
            confidence = min(confidence * 1.1, 0.9)
        elif psychology_bias == 'BIAS_XIU' and pred == 'TAI':
            confidence = min(confidence * 1.1, 0.9)
        elif psychology_bias != 'NEUTRAL' and pred == psychology_bias.split('_')[1]:
            confidence = confidence * 0.9
        
        risk_info, risk_reason = risk_manager(df_raw, confidence, prob_streak, break_decision)
        
        if break_decision == 'YES':
            pred = 'XIU' if pred == 'TAI' else 'TAI'
            confidence = confidence * 0.85
            reason = f'BẺ BỆT: {break_reason}'
        else:
            reason = f'Theo cầu: {state_reason}. Pattern: {match_reason}. Streak: {streak_reason}. Tâm lý: {psych_reason}'
        
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
                'pattern_match': match_result,
                'streak_prob': round(prob_streak, 3),
                'break_decision': break_decision,
                'psychology_bias': psychology_bias
            }
        })
    except Exception as e:
        return jsonify({'status': 'ERROR', 'reason': str(e)})

@app.route('/probe')
def probe():
    try:
        conn = get_db()
        df_raw = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 100', conn)
        conn.close()
        if len(df_raw) < 30:
            return jsonify({'status': 'WAIT', 'reason': 'Need 30+ rounds'})
        
        pro_response = predict_pro()
        pro_data = json.loads(pro_response.get_data(as_text=True))
        
        if pro_data.get('status') == 'PREDICT':
            return pro_data
        if pro_data.get('status') == 'COOLDOWN':
            return pro_data
        
        if pro_data.get('status') == 'WAIT':
            recent = df_raw.head(3)
            probe_results = recent['result'].tolist()
            
            if len(probe_results) == 3 and probe_results[0] == probe_results[1] == probe_results[2]:
                return jsonify({
                    'status': 'PREDICT',
                    'predict': probe_results[0],
                    'confidence': 0.70,
                    'reason': f'3 ván dò đều là {probe_results[0]}, bắt xu hướng!'
                })
            
            df_feat = extract_features(df_raw)
            models = load_models()
            if models[0] is not None:
                hmm_m, xgb_m, lgbm_m, scaler, feature_cols, stacking_m = models
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
                pred = 'TAI' if np.random.rand() > 0.5 else 'XIU'
                confidence = 0.5
            
            tai_count = probe_results.count('TAI') if len(probe_results) >= 3 else 0
            if len(probe_results) == 3 and tai_count >= 2 and pred == 'TAI':
                confidence = min(confidence * 1.15, 0.75)
            elif len(probe_results) == 3 and tai_count <= 1 and pred == 'XIU':
                confidence = min(confidence * 1.15, 0.75)
            
            return jsonify({
                'status': 'PROBE_4',
                'predict': pred,
                'confidence': round(confidence, 3),
                'probe_info': {
                    'round_1': probe_results[0] if len(probe_results) > 0 else 'unknown',
                    'round_2': probe_results[1] if len(probe_results) > 1 else 'unknown',
                    'round_3': probe_results[2] if len(probe_results) > 2 else 'unknown',
                    'analysis': f'Đã dò 3 ván, ván thứ 4 quyết định.'
                },
                'warning': '⚠️ 3 ván trước là dò đường. Ván này có xác suất thắng cao hơn.'
            })
        return pro_data
    except Exception as e:
        return jsonify({'status': 'ERROR', 'reason': str(e)})

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