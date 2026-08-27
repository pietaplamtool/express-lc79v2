import requests
import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Cấu hình
TOKEN = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
API_URL = "https://bettv-predictor.onrender.com/predict"
logging.basicConfig(level=logging.INFO)

# Hàm gọi AI
def get_ai():
    try:
        r = requests.get(API_URL, timeout=8)
        if r.status_code != 200:
            return "⚠️ AI đang bận"
        data = r.json()
        if data.get("status") == "PREDICT":
            return f"🎯 {data['predict']} (độ tin cậy {data['confidence']*100:.1f}%)"
        return "⏳ AI đang quan sát"
    except:
        return "❌ Mất kết nối AI"

# Lệnh Start
async def start(update: Update, context):
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("🎯 Dự Đoán", callback_data="predict")],
        [InlineKeyboardButton("💰 Nạp Tiền", callback_data="nap")],
        [InlineKeyboardButton("📢 Kênh", url="https://t.me/thongbaos1")],
    ]
    msg = f"🏆 *Tool Kano AI*\nChào {user.first_name}!\nID: `{user.id}`\n\nDùng nút bên dưới nhé."
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# Nút Dự đoán
async def do_predict(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("⏳ Đang gọi AI...")
    res = get_ai()
    await q.edit_message_text(res, parse_mode="Markdown")

# Nút Nạp tiền
async def show_nap(update, context):
    q = update.callback_query
    await q.answer()
    keyboard = [
        [InlineKeyboardButton("20.000đ", callback_data="nap_20000")],
        [InlineKeyboardButton("50.000đ", callback_data="nap_50000")],
        [InlineKeyboardButton("100.000đ", callback_data="nap_100000")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back")],
    ]
    await q.edit_message_text("💰 Chọn số tiền:", reply_markup=InlineKeyboardMarkup(keyboard))

# Tạo mã QR (có ghi chú ngẫu nhiên)
async def gen_qr(update, context):
    q = update.callback_query
    await q.answer()
    amount = int(q.data.split('_')[1])
    note = f"NAPTIEN{random.randint(10000,99999)}"
    qr = f"https://img.vietqr.io/image/MB-0844551151-compact.png?amount={amount}&addInfo={note}"
    info = f"💳 MB Bank - PHAM THE HIEN\n🔢 0844551151\n💵 {amount:,}đ\n📝 Ghi chú: `{note}`"
    await q.message.reply_photo(qr, caption=info, parse_mode="Markdown")
    await q.edit_message_text("✅ Đã tạo QR, xem bên trên.")

# Quay lại menu
async def go_back(update, context):
    q = update.callback_query
    await q.answer()
    await start(update, context)

# Khởi chạy
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(do_predict, pattern="predict"))
    app.add_handler(CallbackQueryHandler(show_nap, pattern="nap"))
    app.add_handler(CallbackQueryHandler(gen_qr, pattern=r"nap_\d+"))
    app.add_handler(CallbackQueryHandler(go_back, pattern="back"))
    print("Bot Kano AI đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()