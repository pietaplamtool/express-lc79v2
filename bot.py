import requests
import logging
import random
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ===== CẤU HÌNH =====
TOKEN = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
API_URL = "https://bettv-predictor.onrender.com/predict"
GROUP_LINK = "https://t.me/kano_ai2026"
logging.basicConfig(level=logging.INFO)

# ===== TRẠNG THÁI USER =====
user_sessions = {}

# ===== MENU BÀN PHÍM (Reply Keyboard) =====
MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["🎮 KHU VỰC GAME", "👤 HỒ SƠ"],
    ["🔑 MUA GÓI KEY", "✅ KÍCH HOẠT KEY"],
    ["🎁 NHẬN GIFTCODE", "💰 NẠP TIỀN VÍ"],
    ["📝 GỬI ĐÓNG GÓP", "📢 KÊNH HỖ TRỢ"]
], resize_keyboard=True)

# ===== LỜI CHÀO /START =====
WELCOME_TEXT = (
    "🏆 *𝐓𝐎𝐎𝐋 𝐊𝐀𝐍𝐎 𝐀𝐈 — ĐẲNG CẤP DỰ ĐOÁN TÀI XỈU* 🏆\n\n"
    "🎉 Chào mừng bạn đã đến với trợ lý AI dự đoán đỉnh cao nhất hiện nay!\n\n"
    "💥 *ĐẶC QUYỀN DÀNH CHO BẠN:*\n"
    "⚡ Dự đoán chuẩn xác: Bắt nhịp Tài Xỉu cực mượt với công nghệ AI thế hệ mới.\n"
    "⚡ Nạp tiền chớp mắt: Hệ thống gạch thẻ/chuyển khoản siêu tốc trong 3 giây.\n"
    "⚡ Menu tiện lợi: Dễ sử dụng cho cả người mới bắt đầu.\n\n"
    "🎁 Bạn đã sẵn sàng làm chủ cuộc chơi? Bấm chọn tính năng bên dưới để chiến ngay!"
)

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

# ===== LỆNH /START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=MENU_KEYBOARD)

# ===== XỬ LÝ MENU =====
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "🎮 KHU VỰC GAME":
        await show_game_area(update, context)
    elif text == "👤 HỒ SƠ":
        await show_profile(update, context)
    elif text == "🔑 MUA GÓI KEY":
        await buy_key(update, context)
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

# ===== KHU VỰC GAME =====
async def show_game_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ BetVip", callback_data="game_betvip")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
    ])
    await update.message.reply_text(
        "🎮 *KHU VỰC GAME*\n\nChọn game bạn muốn dự đoán:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ===== BẮT ĐẦU DỰ ĐOÁN =====
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    game_key = query.data.split('_')[1]
    game_name = "BetVip"  # Hiện chỉ có BetVip
    user_id = update.effective_user.id
    
    user_sessions[user_id] = {
        "game": game_key,
        "game_name": game_name,
        "active": True,
        "last_session_id": None,
        "last_result": None,
        "message_id": None,
        "chat_id": query.message.chat_id
    }
    
    await send_prediction_ui(update, context, user_id, is_first=True)
    
    # Tạo job tự động
    if context.job_queue:
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
    
    data, error = get_prediction()
    if error or not data or data.get("status") != "PREDICT":
        current_session = "---"
        current_result = "⏳ Đợi..."
        confidence = 0
    else:
        current_session = data.get("target_session_id", "---")
        current_result = data.get("predict", "?")
        confidence = data.get("confidence", 0) * 100
    
    prev_session = session.get('last_session_id', '---')
    prev_result = session.get('last_result', '---')
    
    session['last_session_id'] = current_session
    session['last_result'] = current_result
    
    bar_length = 10
    filled = int(confidence / 100 * bar_length)
    bar = "█" * filled + "░" * (bar_length - filled)
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
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
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹ DỪNG", callback_data="stop_game")],
        [InlineKeyboardButton("🔙 BACK", callback_data="back_game")]
    ])
    
    if is_first:
        msg = await update.callback_query.message.reply_text(
            ui_text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        session['message_id'] = msg.message_id
        session['chat_id'] = msg.chat_id
    else:
        try:
            await context.bot.edit_message_text(
                ui_text,
                chat_id=session['chat_id'],
                message_id=session['message_id'],
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Lỗi edit: {e}")

# ===== TỰ ĐỘNG DỰ ĐOÁN =====
async def auto_predict(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    session = user_sessions.get(user_id)
    if not session or not session.get('active', False):
        context.job.schedule_removal()
        return
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
    await query.edit_message_text("⏹ *Đã dừng dự đoán.*\n\nBấm /start để quay lại.", parse_mode="Markdown")

# ===== QUAY LẠI =====
async def back_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id in user_sessions:
        user_sessions[user_id]['active'] = False
    for job in context.job_queue.jobs():
        if job.name == f"auto_predict_{user_id}":
            job.schedule_removal()
    await query.edit_message_text("🔙 *Đã quay lại.*\n\nChọn menu bên dưới.", parse_mode="Markdown")

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=MENU_KEYBOARD)

# ===== HỒ SƠ =====
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👤 *HỒ SƠ CỦA BẠN*\n\n"
        f"ID: `{user.id}`\n"
        f"Tên: {user.first_name}\n"
        f"Username: @{user.username or 'Chưa có'}\n"
        f"Số dư: 0đ\n"
        f"KEY VIP: Chưa kích hoạt",
        parse_mode="Markdown"
    )

# ===== CÁC CHỨC NĂNG KHÁC =====
async def buy_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔑 *MUA GÓI KEY VIP*\n\n"
        "Liên hệ hỗ trợ: @thehpie9\n"
        "Giá: 30.000đ\n"
        "Sau khi chuyển khoản, bạn sẽ nhận được key kích hoạt.",
        parse_mode="Markdown"
    )

async def activate_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *KÍCH HOẠT KEY*\n\n"
        "Vui lòng nhập key bạn đã mua theo cú pháp:\n"
        "`/active KEY_CUA_BAN`",
        parse_mode="Markdown"
    )

async def giftcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎁 *NHẬN GIFTCODE*\n\n"
        "Hiện tại chưa có giftcode mới.\n"
        "Theo dõi kênh để nhận thông báo sớm nhất!",
        parse_mode="Markdown"
    )

async def show_nap_tien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("20.000đ", callback_data="nap_20000")],
        [InlineKeyboardButton("50.000đ", callback_data="nap_50000")],
        [InlineKeyboardButton("100.000đ", callback_data="nap_100000")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
    ])
    await update.message.reply_text(
        "💰 *NẠP TIỀN VÍ*\n\nChọn số tiền bạn muốn nạp:",
        reply_markup=keyboard,
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

async def contribute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 *GỬI ĐÓNG GÓP*\n\n"
        "Mọi đóng góp ý kiến vui lòng gửi về:\n"
        "📩 @thehpie9\n\n"
        "Chân thành cảm ơn bạn!",
        parse_mode="Markdown"
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📢 *KÊNH HỖ TRỢ*\n\n"
        "👥 Nhóm: https://t.me/kano_ai2026\n"
        "📩 Hỗ trợ: @thehpie9",
        parse_mode="Markdown"
    )

# ===== KHỞI ĐỘNG =====
def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Text(MENU_KEYBOARD), handle_menu))
    app.add_handler(CallbackQueryHandler(start_game, pattern=r"game_.*"))
    app.add_handler(CallbackQueryHandler(stop_game, pattern="stop_game"))
    app.add_handler(CallbackQueryHandler(back_game, pattern="back_game"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="back_main"))
    app.add_handler(CallbackQueryHandler(generate_qr, pattern=r"nap_\d+"))
    
    print("🤖 Tool Kano AI đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()