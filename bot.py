import os
import requests
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ===== CẤU HÌNH =====
TOKEN = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
API_URL = "https://bettv-predictor.onrender.com/predict"

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

# ===== LỆNH /START (GIAO DIỆN CHÍNH) =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or "Chưa có"

    # Lấy dự đoán AI để hiển thị luôn
    data, error = get_prediction()
    if data and data.get("status") == "PREDICT":
        pred = data.get("predict", "?")
        confidence = data.get("confidence", 0) * 100
        state = data.get("supreme_ai", {}).get("state", "Chưa xác định")
        learned = data.get("learned_rounds", 0)
        ai_text = f"🎯 *Dự đoán:* {pred}\n📊 *Độ tin cậy:* {confidence:.1f}%\n🧠 *Trạng thái cầu:* {state}\n📚 *Số ván đã học:* {learned}"
    else:
        ai_text = "⏳ AI đang quan sát và học hỏi. Hãy gửi lại /start sau vài phút."

    # Giao diện chính
    keyboard = [
        [InlineKeyboardButton("🎮 VÀO GAME", url="https://game.betvip.fit/?utm_source=taibetvip.org&utm_campaign=taibetvip.org&utm_medium=taibetvip.org&utm_term=taibetvip.org")],
        [InlineKeyboardButton("🔑 MUA KEY", callback_data="buy_key")],
        [InlineKeyboardButton("💰 NẠP TIỀN", callback_data="nap_tien")],
        [InlineKeyboardButton("📢 Kênh thông báo", url="https://t.me/thongbaos1")],
        [InlineKeyboardButton("💬 Nhóm chat", url="https://t.me/nhomchats1")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = (
        f"🏆 *Tool Kano AI* 🏆\n"
        f"*VIP PREDICT SYSTEM* – SOI CẦU AI ONLINE\n\n"
        f"Xin chào, *{username}!*\n"
        f"ID: `{user_id}`\n"
        f"Nick: @{username}\n"
        f"Số dư ví: 0đ\n"
        f"KEY VIP: Chưa kích hoạt\n"
        f"Cộng đồng: 1858 thành viên\n\n"
        f"--- *DỰ ĐOÁN HIỆN TẠI* ---\n"
        f"{ai_text}\n\n"
        f"--- *HƯỚNG DẪN* ---\n"
        f"1️⃣ MUA GÓI KEY (chỉ từ 30k)\n"
        f"2️⃣ KÍCH HOẠT KEY\n"
        f"3️⃣ VÀO KHU VỰC GAME\n"
        f"4️⃣ BẬT AUTO – nhận kết quả tự động\n\n"
        f"📌 *Bấm nút bên dưới để bắt đầu!*"
    )

    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")

# ===== XỬ LÝ NÚT "MUA KEY" =====
async def buy_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 *HƯỚNG DẪN MUA KEY VIP*\n\n"
        "1. Liên hệ hỗ trợ: @thehpie9\n"
        "2. Chuyển khoản 30.000đ theo thông tin bên dưới:\n"
        "   - Ngân hàng: ...\n"
        "   - Số TK: ...\n"
        "   - Chủ TK: ...\n"
        "3. Nhận key qua tin nhắn\n"
        "4. Nhập key vào ô bên dưới (chức năng đang phát triển)\n\n"
        "📌 *Bấm /start để quay lại menu chính.*",
        parse_mode="Markdown"
    )

# ===== XỬ LÝ NÚT "NẠP TIỀN" =====
async def nap_tien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💰 *HƯỚNG DẪN NẠP TIỀN*\n\n"
        "Liên hệ hỗ trợ: @thehpie9\n\n"
        "📌 *Bấm /start để quay lại menu chính.*",
        parse_mode="Markdown"
    )

# ===== KHỞI ĐỘNG BOT =====
def main():
    app_bot = Application.builder().token(TOKEN).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CallbackQueryHandler(buy_key, pattern="buy_key"))
    app_bot.add_handler(CallbackQueryHandler(nap_tien, pattern="nap_tien"))

    print("🤖 Tool Kano AI đang chạy...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()