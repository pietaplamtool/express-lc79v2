import requests
import logging
import random
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ===== CẤU HÌNH =====
TOKEN = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
API_URL = "https://bettv-predictor.onrender.com/predict"
logging.basicConfig(level=logging.INFO)

# ===== DỮ LIỆU =====
user_data = {}
user_sessions = {}

MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["🎮 KHU VỰC GAME", "👤 HỒ SƠ"],
    ["🔑 MUA GÓI KEY", "✅ KÍCH HOẠT KEY"],
    ["🎁 NHẬN GIFTCODE", "💰 NẠP TIỀN VÍ"],
    ["📝 GỬI ĐÓNG GÓP", "📢 KÊNH HỖ TRỢ"]
], resize_keyboard=True)

WELCOME_TEXT = (
    "🏆 TOOL KANO AI\n"
    "Chào mừng bạn!\n"
    "Bấm chọn tính năng bên dưới."
)

def get_prediction():
    try:
        r = requests.get(API_URL, timeout=5)
        if r.status_code != 200:
            return None, "Lỗi API"
        data = r.json()
        return data, None
    except:
        return None, "Lỗi kết nối"

def generate_key():
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

# ===== LỆNH /START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {"balance": 0, "used": 0, "key": None, "key_expiry": None}
    await update.message.reply_text(WELCOME_TEXT, reply_markup=MENU_KEYBOARD, parse_mode="Markdown")

# ===== XỬ LÝ MENU CHÍNH =====
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🎮 KHU VỰC GAME":
        await show_game_area(update, context)
    elif text == "👤 HỒ SƠ":
        await show_profile(update, context)
    elif text == "🔑 MUA GÓI KEY":
        await show_key_packages(update, context)
    elif text == "✅ KÍCH HOẠT KEY":
        await activate_key(update, context)
    elif text == "🎁 NHẬN GIFTCODE":
        await giftcode(update, context)
    elif text == "💰 NẠP TIỀN VÍ":
        await show_nap_tien(update, context)
    elif text == "📝 GỬI ĐÓNG GÓP":
        await contribute(update, context)
    elif text == "📢 KÊNH HỖ TRỢ":
        await support(update, context)
    else:
        await update.message.reply_text("Chọn chức năng từ menu.")

# ===== KHU VỰC GAME =====
async def show_game_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ BetVip", callback_data="game_betvip")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
    ])
    await update.message.reply_text("🎮 Chọn game:", reply_markup=keyboard)

# ===== BẮT ĐẦU DỰ ĐOÁN =====
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    user_sessions[user_id] = {
        "active": True,
        "last_session": None,
        "last_result": None,
        "message_id": None,
        "chat_id": query.message.chat_id
    }
    await send_prediction_ui(update, context, user_id, is_first=True)
    if context.job_queue:
        for job in context.job_queue.jobs():
            if job.name == f"auto_predict_{user_id}":
                job.schedule_removal()
        context.job_queue.run_repeating(auto_predict, interval=5, first=2, name=f"auto_predict_{user_id}", user_id=user_id)

async def send_prediction_ui(update, context, user_id, is_first=False):
    session = user_sessions.get(user_id)
    if not session or not session.get('active'):
        return
    data, error = get_prediction()
    if error or not data or data.get("status") != "PREDICT":
        current_session = "---"
        current_result = "Đợi..."
        confidence = 0
    else:
        current_session = data.get("target_session_id", "---")
        current_result = data.get("predict", "?")
        confidence = data.get("confidence", 0) * 100
    prev_session = session.get('last_session', '---')
    prev_result = session.get('last_result', '---')
    session['last_session'] = current_session
    session['last_result'] = current_result
    bar = "█" * int(confidence/10) + "░" * (10 - int(confidence/10))
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    text = (
        f"🏆 TOOL KANO AI\n"
        f"🎮 Game: BetVip\n\n"
        f"📊 DỰ ĐOÁN\n"
        f"Phiên: {current_session}\n"
        f"Kết quả: {current_result}\n\n"
        f"📈 ĐỘ TIN CẬY\n"
        f"`{bar}` {confidence:.1f}%\n\n"
        f"📜 PHIÊN TRƯỚC\n"
        f"Phiên: {prev_session}\n"
        f"Kết quả: {prev_result}\n\n"
        f"🕒 {now}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹ DỪNG", callback_data="stop_game")],
        [InlineKeyboardButton("🔙 BACK", callback_data="back_game")]
    ])
    if is_first:
        msg = await update.callback_query.message.reply_text(text, reply_markup=keyboard)
        session['message_id'] = msg.message_id
        session['chat_id'] = msg.chat_id
    else:
        try:
            await context.bot.edit_message_text(text, chat_id=session['chat_id'], message_id=session['message_id'], reply_markup=keyboard)
        except Exception as e:
            print("Edit error:", e)

async def auto_predict(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    session = user_sessions.get(user_id)
    if not session or not session.get('active'):
        context.job.schedule_removal()
        return
    await send_prediction_ui(None, context, user_id, is_first=False)

async def stop_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id in user_sessions:
        user_sessions[user_id]['active'] = False
    for job in context.job_queue.jobs():
        if job.name == f"auto_predict_{user_id}":
            job.schedule_removal()
    await query.edit_message_text("Đã dừng dự đoán.")

async def back_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id in user_sessions:
        user_sessions[user_id]['active'] = False
    for job in context.job_queue.jobs():
        if job.name == f"auto_predict_{user_id}":
            job.schedule_removal()
    await query.edit_message_text("Đã quay lại.", reply_markup=MENU_KEYBOARD)

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(WELCOME_TEXT, reply_markup=MENU_KEYBOARD, parse_mode="Markdown")

# ===== HỒ SƠ =====
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    data = user_data.get(user_id, {"balance": 0, "used": 0})
    await update.message.reply_text(
        f"👤 HỒ SƠ\n"
        f"ID: {user_id}\n"
        f"Tên: {user.first_name}\n"
        f"Username: @{user.username or 'Chưa có'}\n"
        f"Số dư: {data['balance']:,}đ\n"
        f"Đã dùng: {data['used']:,}đ\n"
        f"Key: {data.get('key', 'Chưa có')}"
    )

# ===== MUA KEY =====
KEY_PACKAGES = {
    "1_ngay": {"name": "1 Ngày", "price": 10000, "duration": "1 ngày"},
    "7_ngay": {"name": "7 Ngày", "price": 50000, "duration": "7 ngày"},
    "30_ngay": {"name": "30 Ngày", "price": 150000, "duration": "30 ngày"},
}
async def show_key_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for key, pkg in KEY_PACKAGES.items():
        keyboard.append([InlineKeyboardButton(f"{pkg['name']} - {pkg['price']:,}đ", callback_data=f"buykey_{key}")])
    keyboard.append([InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")])
    await update.message.reply_text("Chọn gói key:", reply_markup=InlineKeyboardMarkup(keyboard))

async def buy_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    pkg_key = query.data.split('_')[1]
    pkg = KEY_PACKAGES[pkg_key]
    new_key = generate_key()
    user_data[user_id]['key'] = new_key
    user_data[user_id]['key_expiry'] = pkg['duration']
    await query.message.reply_text(f"💎 GIAO DỊCH THÀNH CÔNG\nKey: {new_key}\nHạn: {pkg['duration']}")
    await query.edit_message_text("✅ Đã tạo key.")

async def activate_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ KÍCH HOẠT KEY\nNhập /active KEY")

async def giftcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎁 Key Free: CHUA_CO_KEY")

async def show_nap_tien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("20.000đ", callback_data="nap_20000")],
        [InlineKeyboardButton("50.000đ", callback_data="nap_50000")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
    ])
    await update.message.reply_text("💰 NẠP TIỀN", reply_markup=keyboard)

async def generate_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    amount = int(query.data.split('_')[1])
    note = f"NAPTIEN{random.randint(10000,99999)}"
    qr_url = f"https://img.vietqr.io/image/MB-0844551151-compact.png?amount={amount}&addInfo={note}"
    user_data[user_id]['balance'] += amount
    await query.message.reply_photo(qr_url, caption=f"MB Bank - PHAM THE HIEN\n0844551151\n{amount:,}đ\nGhi chú: {note}")
    await query.edit_message_text("✅ Đã tạo QR.")

async def contribute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 Chức năng đang phát triển.")

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📢 Nhóm: https://t.me/kano_ai2026")

# ===== KHỞI ĐỘNG =====
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_handler(CallbackQueryHandler(start_game, pattern=r"game_.*"))
    app.add_handler(CallbackQueryHandler(stop_game, pattern="stop_game"))
    app.add_handler(CallbackQueryHandler(back_game, pattern="back_game"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="back_main"))
    app.add_handler(CallbackQueryHandler(buy_key, pattern=r"buykey_.*"))
    app.add_handler(CallbackQueryHandler(generate_qr, pattern=r"nap_\d+"))
    print("Bot đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()