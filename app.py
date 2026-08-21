import requests, psycopg2, redis, os, time, threading, json
import numpy as np, pandas as pd
from flask import Flask, jsonify
from dotenv import load_dotenv
from datetime import datetime
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier
from hmmlearn import hmm
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
import joblib

load_dotenv()
app = Flask(__name__)

DB_URL = os.getenv('DB_URL')
REDIS_URL = os.getenv('REDIS_URL')
API_URL = os.getenv('API_URL')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 5))
RETRAIN_EVERY = int(os.getenv('RETRAIN_EVERY', 200))

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
        print(f'[-] Error: {e}')

def train_models():
    try:
        conn = get_db()
        df = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 5000', conn)
        conn.close()
        if len(df) < 100:
            print('[-] Not enough data to train')
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
        # Train XGB
        xgb = XGBClassifier(n_estimators=50, max_depth=5, use_label_encoder=False)
        xgb.fit(X, y)
        # Train HMM
        hmm_model = hmm.GaussianHMM(n_components=2, covariance_type='full', n_iter=50)
        hmm_model.fit(X)
        # Save models
        os.makedirs('models', exist_ok=True)
        joblib.dump(hmm_model, 'models/hmm.pkl')
        joblib.dump(xgb, 'models/xgb.pkl')
        print('[+] Models trained successfully')
    except Exception as e:
        print(f'[-] Train error: {e}')

def load_models():
    global hmm_model, xgb_model
    try:
        hmm_model = joblib.load('models/hmm.pkl')
        xgb_model = joblib.load('models/xgb.pkl')
        return True
    except:
        return False

def predict():
    if not load_models():
        return {'predict': 'XIU', 'confidence': 0.5, 'prob_tai': 0.5}
    try:
        conn = get_db()
        df = pd.read_sql('SELECT * FROM sessions ORDER BY id DESC LIMIT 35', conn)
        conn.close()
        if len(df) < 10:
            return {'predict': 'XIU', 'confidence': 0.5, 'prob_tai': 0.5}
        df = df.sort_values('id').reset_index(drop=True)
        df['label'] = (df['result'] == 'TAI').astype(int)
        for lag in range(1, 6):
            df[f'lag_{lag}'] = df['label'].shift(lag).fillna(0)
        df['rolling_mean'] = df['label'].rolling(10).mean().fillna(0.5)
        df['point_diff'] = df['point'].diff().fillna(0)
        last = df.iloc[-1:][['point', 'rolling_mean', 'point_diff'] + [f'lag_{i}' for i in range(1,6)]].values
        prob_hmm = hmm_model.predict_proba(last)[0][1]
        prob_xgb = xgb_model.predict_proba(last)[0][1]
        prob_final = 0.3*prob_hmm + 0.7*prob_xgb
        pred = 'TAI' if prob_final >= 0.5 else 'XIU'
        conf = abs(prob_final - 0.5) * 2
        return {'predict': pred, 'confidence': round(conf, 3), 'prob_tai': round(prob_final, 3)}
    except Exception as e:
        return {'predict': 'XIU', 'confidence': 0.5, 'prob_tai': 0.5}

@app.route('/predict')
def api_predict():
    return jsonify(predict())

def background_worker():
    init_db()
    while True:
        fetch_and_save()
        conn = get_db(); cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM sessions')
        count = cur.fetchone()[0]; conn.close()
        if count % RETRAIN_EVERY == 0 and count > 200:
            train_models()
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    threading.Thread(target=background_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)