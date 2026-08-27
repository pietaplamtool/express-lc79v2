import os
import requests
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ===== CẤU HÌNH =====
TOKEN = "8891039285:AAFBUBpmA8f0a7MoX8npE_LrXldSnK833ww"
API_URL = "https://bettv-predictor.onrender.com/predict"

# Bật chế độ ghi log để dễ dàng kiểm tra lỗi
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ===== HÀM GỌI AI =====
def get_prediction():
    try:
        response = requests.get(API_URL, timeout=15)
        if response.status_code != 200:
            return None, f"❌ API trả về lỗi: {response.status_code}"
        data = response.json()
        return data, None
    except Exception as e:
        return None, f"❌ Lỗi kết nối: {e}"

# ===== LỆNH /START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎯 Dự đoán ngay", callback_data="predict")],
        [InlineKeyboardButton("📊 Xem trạng thái AI", callback_data="stats")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 *AI Tài Xỉu VIP*\n\n"
        "Tôi là AI Lão làng với 5600+ ván kinh nghiệm.\n"
        "Nhấn nút hoặc gõ lệnh /predict để nhận dự đoán.\n\n"
        "📌 *Lệnh:* /predict - Dự đoán ván tiếp theo",
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
        confidence = data.get("confidence", 0) * 100
        reason = data.get("reason", "Không có lý do cụ thể.")
        learned = data.get("learned_rounds", 0)
        state = data.get("supreme_ai", {}).get("state", "Chưa xác định")

        msg = (
            f"🎯 *Dự đoán:* `{pred}`\n"
            f"📊 *Độ tin cậy:* `{confidence:.1f}%`\n"
            f"🧠 *Trạng thái cầu:* `{state}`\n"
            f"📚 *Số ván đã học:* `{learned}`\n"
            f"📝 *Lý do:* {reason[:200]}..."
        )
    else:
        msg = "⏳ AI đang quan sát và học hỏi. Hãy chờ thêm vài phút hoặc gửi lại lệnh /predict."

    await update.message.reply_text(msg, parse_mode="Markdown")

# ===== XỬ LÝ NÚT BẤM =====
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
            confidence = data.get("confidence", 0) * 100
            reason = data.get("reason", "Không có lý do cụ thể.")
            learned = data.get("learned_rounds", 0)
            state = data.get("supreme_ai", {}).get("state", "Chưa xác định")

            msg = (
                f"🎯 *Dự đoán:* `{pred}`\n"
                f"📊 *Độ tin cậy:* `{confidence:.1f}%`\n"
                f"🧠 *Trạng thái cầu:* `{state}`\n"
                f"📚 *Số ván đã học:* `{learned}`\n"
                f"📝 *Lý do:* {reason[:200]}..."
            )
        else:
            msg = "⏳ AI đang quan sát, chưa có dự đoán. Hãy đợi thêm vài ván."

        await query.edit_message_text(msg, parse_mode="Markdown")

    elif query.data == "stats":
        await query.edit_message_text(
            "📊 *Thông tin AI*\n\n"
            "• AI đang chạy trên Render\n"
            "• Đang học từ dữ liệu Tài Xỉu\n"
            "• Gửi /predict để nhận dự đoán",
            parse_mode="Markdown"
        )

# ===== KHỞI ĐỘNG BOT =====
def main():
    app_bot = Application.builder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("predict", predict))
    app_bot.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Bot đang chạy và lắng nghe lệnh...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()