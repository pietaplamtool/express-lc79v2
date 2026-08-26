import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ===== CẤU HÌNH =====
TOKEN = "8891039285:AAFBUBpmA8f0a7MoX8npE_LrXldSnK833ww"  # Token của anh
API_URL = "https://bettv-predictor.onrender.com/predict"

# ===== HÀM GỌI AI =====
def get_prediction():
    try:
        response = requests.get(API_URL, timeout=10)
        if response.status_code != 200:
            return None, "❌ API đang bận, thử lại sau."
        data = response.json()
        return data, None
    except Exception as e:
        return None, f"❌ Lỗi kết nối: {e}"

# ===== LỆNH /START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎯 Dự đoán ngay", callback_data="predict")],
        [InlineKeyboardButton("📊 Xem thống kê", callback_data="stats")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 *AI Tài Xỉu VIP*\n\n"
        "Tôi là AI Lão làng với 5600+ ván kinh nghiệm.\n"
        "Nhấn nút bên dưới để nhận dự đoán cho ván tiếp theo.\n\n"
        "📌 *Lệnh:* /predict - Dự đoán nhanh",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# ===== LỆNH /PREDICT =====
async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang gọi AI Lão làng...")

    data, error = get_prediction()
    if error:
        await update.message.reply_text(error)
        return

    if data.get("status") == "PREDICT":
        pred = data.get("predict", "?")
        confidence = data.get("confidence", 0)
        reason = data.get("reason", "Không có lý do cụ thể.")
        learned = data.get("learned_rounds", 0)

        msg = (
            f"🎯 *Dự đoán:* `{pred}`\n"
            f"📊 *Độ tin cậy:* `{confidence*100:.1f}%`\n"
            f"🧠 *Số ván đã học:* `{learned}`\n"
            f"📝 *Lý do:* {reason[:200]}..."
        )
    else:
        msg = "⏳ AI đang quan sát, chưa có dự đoán. Hãy chờ thêm vài ván."

    await update.message.reply_text(msg, parse_mode="Markdown")

# ===== CALLBACK QUERY (Nút bấm) =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "predict":
        await query.edit_message_text("⏳ Đang gọi AI Lão làng...")
        data, error = get_prediction()
        if error:
            await query.edit_message_text(error)
            return

        if data.get("status") == "PREDICT":
            pred = data.get("predict", "?")
            confidence = data.get("confidence", 0)
            reason = data.get("reason", "Không có lý do cụ thể.")
            learned = data.get("learned_rounds", 0)

            msg = (
                f"🎯 *Dự đoán:* `{pred}`\n"
                f"📊 *Độ tin cậy:* `{confidence*100:.1f}%`\n"
                f"🧠 *Số ván đã học:* `{learned}`\n"
                f"📝 *Lý do:* {reason[:200]}..."
            )
        else:
            msg = "⏳ AI đang quan sát, chưa có dự đoán."

        await query.edit_message_text(msg, parse_mode="Markdown")

    elif query.data == "stats":
        # Gọi API stats nếu có
        await query.edit_message_text("📊 *Thống kê*\n\nĐang phát triển...", parse_mode="Markdown")

# ===== KHỞI ĐỘNG BOT =====
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("predict", predict))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Bot đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()