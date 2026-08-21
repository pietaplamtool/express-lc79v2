import requests, psycopg2, redis, os, time, threading, json
import numpy as np, pandas as pd
from flask import Flask, jsonify
from dotenv import load_dotenv
from hmmlearn import hmm
from xgboost import XGBClassifier
import joblib, pickle

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

def train_models():
    try:
        conn = get_db()
        df = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 3000', conn)
        conn.close()
        if len(df) < 50:
            print('[-] Not enough data')
            return
        df = df.sort_values('id').reset_index(drop=True)
        df['label'] = (df['result'] == 'TAI').astype(int)
        for lag in range(1, 6):
            df[f'lag_{lag}'] = df['label'].shift(lag).fillna(0)
        df['rolling_mean'] = df['label'].rolling(10).mean().fillna(0.5)
        df['point_diff'] = df['point'].diff().fillna(0)
        df = df.dropna().reset_index(drop=True)
        X = df[['point', 'rolling_mean', 'point_diff'] + [f'lag_{i}' for i in range(1,6)]].values
        y = df['label'].values
        xgb = XGBClassifier(n_estimators=30, max_depth=3, use_label_encoder=False)
        xgb.fit(X, y)
        hmm_model = hmm.GaussianHMM(n_components=2, covariance_type='full', n_iter=30)
        hmm_model.fit(X)
        os.makedirs('models', exist_ok=True)
        joblib.dump(hmm_model, 'models/hmm.pkl')
        joblib.dump(xgb, 'models/xgb.pkl')
        print('[+] Models trained')
    except Exception as e:
        print(f'[-] Train error: {e}')

def load_models():
    try:
        hmm_model = joblib.load('models/hmm.pkl')
        xgb_model = joblib.load('models/xgb.pkl')
        return hmm_model, xgb_model
    except:
        return None, None

@app.route('/predict')
def predict():
    hmm_model, xgb_model = load_models()
    if hmm_model is None:
        return jsonify({'predict': 'XIU', 'confidence': 0.5})
    try:
        conn = get_db()
        df = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 35', conn)
        conn.close()
        if len(df) < 10:
            return jsonify({'predict': 'XIU', 'confidence': 0.5})
        df = df.sort_values('id').reset_index(drop=True)
        df['label'] = (df['result'] == 'TAI').astype(int)
        for lag in range(1, 6):
            df[f'lag_{lag}'] = df['label'].shift(lag).fillna(0)
        df['rolling_mean'] = df['label'].rolling(10).mean().fillna(0.5)
        df['point_diff'] = df['point'].diff().fillna(0)
        last = df.iloc[-1:][['point', 'rolling_mean', 'point_diff'] + [f'lag_{i}' for i in range(1,6)]].values
        prob_hmm = hmm_model.predict_proba(last)[0][1]
        prob_xgb = xgb_model.predict_proba(last)[0][1]
        prob_final = 0.4*prob_hmm + 0.6*prob_xgb
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
            joblib.load('models/xgb.pkl')
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

# ================== ENDPOINT MỚI: TỈ LỆ THẮNG ==================
@app.route('/accuracy')
def accuracy():
    try:
        conn = get_db()
        df = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 500', conn)
        conn.close()
        
        if len(df) < 10:
            return jsonify({'error': 'Not enough data', 'total': len(df)})
        
        df = df.sort_values('id').reset_index(drop=True)
        df['label'] = (df['result'] == 'TAI').astype(int)
        for lag in range(1, 6):
            df[f'lag_{lag}'] = df['label'].shift(lag).fillna(0)
        df['rolling_mean'] = df['label'].rolling(10).mean().fillna(0.5)
        df['point_diff'] = df['point'].diff().fillna(0)
        df = df.dropna().reset_index(drop=True)
        
        hmm_model, xgb_model = load_models()
        if hmm_model is None:
            return jsonify({'error': 'Model not ready'})
        
        correct = 0
        total = 0
        correct_high = 0
        high_count = 0
        
        for i in range(len(df) - 1):
            X = df.iloc[i][['point', 'rolling_mean', 'point_diff'] + [f'lag_{i}' for i in range(1,6)]].values.reshape(1, -1)
            actual = df.iloc[i+1]['label']
            
            prob_hmm = hmm_model.predict_proba(X)[0][1]
            prob_xgb = xgb_model.predict_proba(X)[0][1]
            prob_final = 0.4*prob_hmm + 0.6*prob_xgb
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
            'accuracy': round(correct / total * 100, 2) if total > 0 else 0,
            'high_conf_accuracy': round(correct_high / high_count * 100, 2) if high_count > 0 else 0,
            'high_conf_count': high_count,
            'status': 'ready'
        })
    except Exception as e:
        return jsonify({'error': str(e)})
# ==========================================================

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