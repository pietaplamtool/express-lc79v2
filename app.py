import os
from flask import Flask, request, jsonify
from taixiu_ai import TaiXiuEnsembleAI

app = Flask(__name__)
ai = TaiXiuEnsembleAI()

@app.route('/')
def home():
    return 'Tai Xiu AI is running'

@app.route('/predict', methods=['GET'])
def predict():
    try:
        result = ai.predict()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/update', methods=['POST'])
def update():
    try:
        data = request.get_json()
        result = data.get('result')
        if result not in (0, 1):
            return jsonify({'error': 'result must be 0 or 1'}), 400
        ai.update(result)
        return jsonify({'status': 'updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)