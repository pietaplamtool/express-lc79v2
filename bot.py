import requests
import logging
import random
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ===== CẤU HÌNH =====
TOKEN = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
API_URL = "https://bettv-predictor.onrender.com/predict"
GROUP_LINK = "https://t.me/kano_ai2026"
logging.basicConfig(level=logging.INFO)

# ===== DỮ LIỆU GAME =====
GAMES = {
    "max789": {"name": "Max789", "icon": "🎰"},
    "lc79": {"name": "LC79", "icon": "🎲"},
    "betvip": {"name": "BetVip", "icon": "⭐"},
    "hitclub": {"name": "HitClub", "icon": "🔥"},
}

# ===== TRẠNG THÁI USER =====
user_sessions = {}

# ===== HÀM GỌI AI =====
def get_prediction():
    try:
        response = requests.get(API_URL, timeout=10)
        if response.status_code != 200:
            return None, f"Lỗi API: {response.status_code}"
        data = response.json()
        return data, None
    except Exception as e:
        return None, f"Lỗi kết nối: {e}"

# ===== LỆNH /START (Lời chào + menu) =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("🎮 Chơi Game", callback_data="choose_game")],
        [InlineKeyboardButton("💰 Nạp Tiền", callback_data="nap_tien")],
        [InlineKeyboardButton("👥 Nhóm", url=GROUP_LINK)],
    ]
    msg = (
        f"🏆 *Tool Kano AI*\n"
        f"Xin chào, {user.first_name}!\n"
        f"ID: `{user.id}`\n\n"
        f"🤖 *Hệ thống dự đoán Tài Xỉu MD5*\n"
        f"🔹 Tự động quét phiên 24/7\n"
        f"🔹 Hỗ trợ nhiều game: Max789, LC79, BetVip, HitClub\n"
        f"🔹 Độ chính xác cao từ AI Lão làng\n\n"
        f"📌 *Bấm nút bên dưới để bắt đầu!*"
    )
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ===== CHỌN GAME =====
async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = []
    for key, game in GAMES.items():
        keyboard.append([InlineKeyboardButton(f"{game['icon']} {game['name']}", callback_data=f"game_{key}")])
    keyboard.append([InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")])
    
    await query.edit_message_text(
        "🎮 *CHỌN GAME*\n\nChọn game bạn muốn dự đoán:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ===== BẮT ĐẦU DỰ ĐOÁN =====
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    game_key = query.data.split('_')[1]
    game_name = GAMES[game_key]['name']
    user_id = update.effective_user.id
    
    # Khởi tạo session
    user_sessions[user_id] = {
        "game": game_key,
        "game_name": game_name,
        "active": True,
        "last_session_id": None,
        "last_result": None
    }
    
    # Gửi giao diện dự đoán lần đầu
    await send_prediction_ui(update, context, user_id, is_first=True)
    
    # Tạo job tự động (mỗi 5 giây)
    if context.job_queue:
        # Xóa job cũ nếu có
        for job in context.job_queue.jobs():
            if job.name == f"auto_predict_{user_id}":
                job.schedule_removal()
        
        context.job_queue.run_repeating(
            auto_predict,
            interval=5,
            first=2,
            name=f"auto_predict_{user_id}",
            user_id=user_id
        )

# ===== GỬI GIAO DIỆN DỰ ĐOÁN =====
async def send_prediction_ui(update, context, user_id, is_first=False):
    session = user_sessions.get(user_id)
    if not session:
        return
    
    game_name = session['game_name']
    
    # Lấy dự đoán hiện tại
    data, error = get_prediction()
    if error or not data or data.get("status") != "PREDICT":
        current_session = "---"
        current_result = "⏳ Đợi..."
        confidence = 0
    else:
        current_session = data.get("target_session_id", "---")
        current_result = data.get("predict", "?")
        confidence = data.get("confidence", 0) * 100
    
    # Lấy phiên trước (nếu có)
    prev_session = session.get('last_session_id', '---')
    prev_result = session.get('last_result', '---')
    
    # Cập nhật phiên trước cho lần sau
    session['last_session_id'] = current_session
    session['last_result'] = current_result
    
    # Tạo thanh độ tin cậy
    bar_length = 10
    filled = int(confidence / 100 * bar_length)
    bar = "█" * filled + "░" * (bar_length - filled)
    
    # Ngày giờ
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    # Giao diện UI
    ui_text = (
        f"🏆 *Tool Kano AI*\n"
        f"🎮 *Game: {game_name}*\n\n"
        f"📊 *DỰ ĐOÁN*\n"
        f"🔢 Phiên: `{current_session}`\n"
        f"🎯 Kết quả: *{current_result}*\n\n"
        f"📈 *ĐỘ TIN CẬY*\n"
        f"`{bar}` {confidence:.1f}%\n\n"
        f"📜 *PHIÊN TRƯỚC*\n"
        f"🔢 Phiên: `{prev_session}`\n"
        f"🎯 Kết quả: *{prev_result}*\n\n"
        f"🕒 {now}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("⏹ DỪNG", callback_data="stop_game")],
        [InlineKeyboardButton("🔙 BACK", callback_data="choose_game")],
    ]
    
    if is_first:
        await update.callback_query.message.reply_text(
            ui_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        try:
            await update.callback_query.message.edit_text(
                ui_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except:
            pass

# ===== TỰ ĐỘNG DỰ ĐOÁN =====
async def auto_predict(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    session = user_sessions.get(user_id)
    if not session or not session.get('active', False):
        context.job.schedule_removal()
        return
    # Chỉ gửi cập nhật, không cần update callback_query
    await send_prediction_ui(None, context, user_id, is_first=False)

# ===== DỪNG DỰ ĐOÁN =====
async def stop_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if user_id in user_sessions:
        user_sessions[user_id]['active'] = False
    
    for job in context.job_queue.jobs():
        if job.name == f"auto_predict_{user_id}":
            job.schedule_removal()
    
    await query.edit_message_text(
        "⏹ *Đã dừng dự đoán.*\n\nBấm /start để quay lại menu chính.",
        parse_mode="Markdown"
    )

# ===== QUAY LẠI MENU CHÍNH =====
async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)

# ===== NẠP TIỀN =====
async def nap_tien_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("20.000đ", callback_data="nap_20000")],
        [InlineKeyboardButton("50.000đ", callback_data="nap_50000")],
        [InlineKeyboardButton("100.000đ", callback_data="nap_100000")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")],
    ]
    await query.edit_message_text(
        "💰 *CHỌN SỐ TIỀN NẠP*\n\nChọn số tiền để tạo mã QR:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def generate_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    amount = int(query.data.split('_')[1])
    note = f"NAPTIEN{random.randint(10000, 99999)}"
    qr_url = f"https://img.vietqr.io/image/MB-0844551151-compact.png?amount={amount}&addInfo={note}"
    
    info = (
        f"💳 *MB Bank*\n"
        f"👤 PHAM THE HIEN\n"
        f"🔢 0844551151\n"
        f"💵 {amount:,}đ\n"
        f"📝 Ghi chú: `{note}`\n\n"
        f"📌 Quét mã QR để chuyển khoản."
    )
    
    await query.message.reply_photo(photo=qr_url, caption=info, parse_mode="Markdown")
    await query.edit_message_text("✅ Đã tạo mã QR, xem bên trên.")

# ===== KHỞI ĐỘNG =====
def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(choose_game, pattern="choose_game"))
    app.add_handler(CallbackQueryHandler(start_game, pattern=r"game_.*"))
    app.add_handler(CallbackQueryHandler(stop_game, pattern="stop_game"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="back_main"))
    app.add_handler(CallbackQueryHandler(nap_tien_menu, pattern="nap_tien"))
    app.add_handler(CallbackQueryHandler(generate_qr, pattern=r"nap_\d+"))
    
    print("🤖 Tool Kano AI đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()