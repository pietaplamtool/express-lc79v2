import requests, psycopg2, redis, os, time, threading, json, joblib, pickle
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
    """Trích xuất 40+ features từ dữ liệu thô"""
    df = df.sort_values('id').reset_index(drop=True)
    df['label'] = (df['result'] == 'TAI').astype(int)
    df['total'] = df['point']
    df['sum_dice'] = df['dice1'] + df['dice2'] + df['dice3']
    df['even'] = (df['total'] % 2 == 0).astype(int)
    
    # Lag features (1-10)
    for lag in range(1, 11):
        df[f'lag_{lag}'] = df['label'].shift(lag).fillna(0)
    
    # Rolling statistics (5, 10, 20)
    for window in [5, 10, 20]:
        df[f'rolling_mean_{window}'] = df['label'].rolling(window).mean().fillna(0.5)
        df[f'rolling_std_{window}'] = df['label'].rolling(window).std().fillna(0)
        df[f'rolling_sum_{window}'] = df['label'].rolling(window).sum().fillna(0)
    
    # Cầu đặc biệt
    df['streak'] = 0
    streak = 0
    for i in range(1, len(df)):
        if df['label'].iloc[i] == df['label'].iloc[i-1]:
            streak += 1
        else:
            streak = 0
        df.loc[df.index[i], 'streak'] = streak
    
    df['bệt'] = (df['streak'] >= 3).astype(int)
    df['đảo'] = (df['streak'] == 0).astype(int)
    df['cầu_1_1'] = ((df['streak'] == 0) & (df['streak'].shift(1) == 0)).astype(int)
    df['cầu_2_2'] = ((df['streak'] == 1) & (df['streak'].shift(2) == 1)).astype(int)
    df['cầu_3_1'] = ((df['streak'] == 2) & (df['streak'].shift(3) == 2)).astype(int)
    
    # Entropy (độ hỗn loạn) của 10 ván gần nhất
    def rolling_entropy(x):
        if len(x) < 2: return 0
        p = np.bincount(x.astype(int)) / len(x)
        return entropy(p)
    
    df['entropy_10'] = df['label'].rolling(10).apply(rolling_entropy).fillna(0)
    df['entropy_20'] = df['label'].rolling(20).apply(rolling_entropy).fillna(0)
    
    # Feature tương quan
    df['point_diff'] = df['total'].diff().fillna(0)
    df['point_lag1'] = df['total'].shift(1).fillna(0)
    df['point_lag2'] = df['total'].shift(2).fillna(0)
    df['dice1_mean'] = df['dice1'].rolling(5).mean().fillna(3.5)
    df['dice2_mean'] = df['dice2'].rolling(5).mean().fillna(3.5)
    df['dice3_mean'] = df['dice3'].rolling(5).mean().fillna(3.5)
    
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
        
        # Standardize
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        # 1. XGBoost siêu mạnh
        xgb = XGBClassifier(
            n_estimators=300,
            max_depth=7,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            use_label_encoder=False,
            random_state=42
        )
        xgb.fit(X_scaled, y)
        
        # 2. LightGBM (nhanh và mạnh không kém XGBoost)
        lgbm = LGBMClassifier(
            n_estimators=200,
            max_depth=7,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            random_state=42,
            verbose=-1
        )
        lgbm.fit(X_scaled, y)
        
        # 3. HMM (chuỗi Markov bậc 3)
        hmm_model = hmm.GaussianHMM(n_components=3, covariance_type='full', n_iter=100)
        hmm_model.fit(X_scaled)
        
        # 4. Stacking (Meta-learner)
        stacking_model = StackingClassifier(
            estimators=[
                ('xgb', xgb),
                ('lgbm', lgbm)
            ],
            final_estimator=LogisticRegression(),
            cv=5
        )
        stacking_model.fit(X_scaled, y)
        
        # Lưu toàn bộ
        os.makedirs('models', exist_ok=True)
        joblib.dump(hmm_model, 'models/hmm.pkl')
        joblib.dump(xgb, 'models/xgb.pkl')
        joblib.dump(lgbm, 'models/lgbm.pkl')
        joblib.dump(scaler, 'models/scaler.pkl')
        joblib.dump(feature_cols, 'models/feature_cols.pkl')
        joblib.dump(stacking_model, 'models/stacking.pkl')
        print('[+] SUPER AI (XGBoost + LightGBM + HMM + Stacking) trained successfully!')
    except Exception as e:
        print(f'[-] Train error: {e}')

def load_models():
    try:
        hmm_model = joblib.load('models/hmm.pkl')
        xgb_model = joblib.load('models/xgb.pkl')
        lgbm_model = joblib.load('models/lgbm.pkl')
        scaler = joblib.load('models/scaler.pkl')
        feature_cols = joblib.load('models/feature_cols.pkl')
        stacking_model = joblib.load('models/stacking.pkl')
        return hmm_model, xgb_model, lgbm_model, scaler, feature_cols, stacking_model
    except:
        return None, None, None, None, None, None

@app.route('/predict')
def predict():
    models = load_models()
    if models[0] is None:
        return jsonify({'predict': 'XIU', 'confidence': 0.5})
    hmm_model, xgb_model, lgbm_model, scaler, feature_cols, stacking_model = models
    try:
        conn = get_db()
        df_raw = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 100', conn)
        conn.close()
        if len(df_raw) < 20:
            return jsonify({'predict': 'XIU', 'confidence': 0.5})
        df = extract_features(df_raw)
        last = df.iloc[-1:][feature_cols].values
        last_scaled = scaler.transform(last)
        
        # XGBoost
        prob_xgb = xgb_model.predict_proba(last_scaled)[0][1]
        
        # LightGBM
        prob_lgbm = lgbm_model.predict_proba(last_scaled)[0][1]
        
        # HMM
        prob_hmm = hmm_model.predict_proba(last_scaled)[0][1]
        
        # Stacking
        prob_stack = stacking_model.predict_proba(last_scaled)[0][1]
        
        # Ensemble (weighted)
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
        hmm_model, xgb_model, lgbm_model, scaler, feature_cols, stacking_model = models
        
        correct, total, correct_high, high_count = 0, 0, 0, 0
        for i in range(10, len(df)-1):
            X = df.iloc[i:i+1][feature_cols].values
            X_scaled = scaler.transform(X)
            actual = df.iloc[i+1]['label']
            
            prob_hmm = hmm_model.predict_proba(X_scaled)[0][1]
            prob_xgb = xgb_model.predict_proba(X_scaled)[0][1]
            prob_lgbm = lgbm_model.predict_proba(X_scaled)[0][1]
            prob_stack = stacking_model.predict_proba(X_scaled)[0][1]
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