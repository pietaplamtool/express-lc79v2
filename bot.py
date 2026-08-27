import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
API_URL = "https://bettv-predictor.onrender.com/predict"

async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = requests.get(API_URL, timeout=10)
        data = response.json()
        if data.get("status") == "PREDICT":
            msg = f"🎯 {data.get('predict')} - {data.get('confidence', 0)*100:.1f}%"
        else:
            msg = "⏳ AI đang học..."
    except:
        msg = "❌ Lỗi kết nối"
    await update.message.reply_text(msg)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("predict", predict))
    print("Bot chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()