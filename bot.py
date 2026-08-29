import requests
import logging
import random
import string
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ===== CẤU HÌNH =====
TOKEN = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
API_URL = "https://bettv-predictor.onrender.com/predict"
GROUP_LINK = "https://t.me/kano_ai2026"
FEEDBACK_LINK = "https://t.me/feedbackkanoai_2026"
ADMIN_ID = 7853432590
logging.basicConfig(level=logging.INFO)

# ===== DỮ LIỆU USER =====
user_data = {}
user_sessions = {}

# ===== MENU BÀN PHÍM =====
MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["🎮 KHU VỰC GAME", "👤 HỒ SƠ"],
    ["🔑 MUA GÓI KEY", "✅ KÍCH HOẠT KEY"],
    ["🎁 NHẬN GIFTCODE", "💰 NẠP TIỀN VÍ"],
    ["📝 FEEDBACK", "📢 KÊNH HỖ TRỢ"]
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

# ===== GÓI KEY =====
KEY_PACKAGES = {
    "1_ngay": {"name": "1 Ngày", "price": 10000, "duration": "1 ngày"},
    "7_ngay": {"name": "7 Ngày", "price": 50000, "duration": "7 ngày"},
    "30_ngay": {"name": "30 Ngày", "price": 150000, "duration": "30 ngày"},
    "90_ngay": {"name": "90 Ngày", "price": 350000, "duration": "90 ngày"},
}

# ===== HÀM TẠO KEY =====
def generate_key():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

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
    user_id = update.effective_user.id

    if user_id not in user_data:
        user_data[user_id] = {
            "balance": 0,
            "used": 0,
            "key": None,
            "key_expiry": None,
            "is_admin": (user_id == ADMIN_ID)
        }

    if user_id == ADMIN_ID:
        user_data[user_id]["balance"] = 2000000
        user_data[user_id]["key"] = "TEST_KEY_001"
        user_data[user_id]["key_expiry"] = "30 ngày"

    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=MENU_KEYBOARD)

# ===== XỬ LÝ MENU =====
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
    elif text == "📝 FEEDBACK":
        await feedback(update, context)
    elif text == "📢 KÊNH HỖ TRỢ":
        await support(update, context)
    else:
        await update.message.reply_text("⚠️ Vui lòng chọn chức năng từ menu bên dưới.")

# ===== KHU VỰC GAME =====
async def show_game_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ BetVip", callback_data="game_betvip")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
    ])
    await update.message.reply_text(
        "🎮 *KHU VỰC GAME*\n\nHiện tại chỉ hỗ trợ game BetVip.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ===== BẮT ĐẦU DỰ ĐOÁN =====
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if user_id not in user_data or not user_data[user_id].get('key'):
        await query.edit_message_text(
            "❌ *Bạn chưa có KEY VIP!*\n\n"
            "Vui lòng mua key tại mục `🔑 MUA GÓI KEY` để sử dụng tính năng dự đoán.",
            parse_mode="Markdown"
        )
        return

    # Xoá session cũ nếu có
    user_sessions[user_id] = {
        "active": True,
        # current_session: phiên đang hiển thị trên UI (phiên sắp chơi)
        "current_session": None,
        # prev_session / prev_result: phiên trước đó đã được gửi đi
        "prev_session": "---",
        "prev_result": "---",
        # message_id để edit tin nhắn
        "message_id": None,
        "chat_id": query.message.chat_id,
    }

    await send_prediction_ui(update, context, user_id, is_first=True)

    # Huỷ job cũ nếu có
    if context.job_queue:
        for job in context.job_queue.jobs():
            if job.name == f"auto_predict_{user_id}":
                job.schedule_removal()
        context.job_queue.run_repeating(
            auto_predict,
            interval=5,
            first=5,
            name=f"auto_predict_{user_id}",
            user_id=user_id
        )

# ===== GỬI / CẬP NHẬT GIAO DIỆN DỰ ĐOÁN =====
async def send_prediction_ui(update, context, user_id, is_first=False):
    session = user_sessions.get(user_id)
    if not session or not session.get('active'):
        return

    data, error = get_prediction()

    if error or not data or data.get("status") != "PREDICT":
        current_session = "---"
        current_result = "⏳ Đợi..."
        confidence = 0
    else:
        current_session = str(data.get("target_session_id", "---"))
        current_result = data.get("predict", "?")
        confidence = float(data.get("confidence", 0)) * 100

    bar_length = 10
    filled = int(confidence / 100 * bar_length)
    bar = "█" * filled + "░" * (bar_length - filled)
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    # Lấy thông tin phiên trước từ session
    prev_session = session.get("prev_session", "---")
    prev_result = session.get("prev_result", "---")

    # Kiểm tra có phiên mới không
    # Phiên mới = API trả về session_id khác với session đang hiển thị
    stored_current = session.get("current_session")
    is_new_session = (
        stored_current is not None
        and current_session != "---"
        and current_session != stored_current
    )

    if is_new_session:
        # Phiên cũ trở thành phiên trước
        session["prev_session"] = stored_current
        session["prev_result"] = session.get("current_result", "---")

    # Cập nhật phiên hiện tại
    session["current_session"] = current_session
    session["current_result"] = current_result

    # Lấy lại prev sau khi cập nhật
    prev_session = session.get("prev_session", "---")
    prev_result = session.get("prev_result", "---")

    ui_text = (
        f"🏆 *Tool Kano AI*\n"
        f"🎮 *Game: BetVip*\n\n"
        f"📊 *DỰ ĐOÁN PHIÊN TIẾP THEO*\n"
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
        # Gửi tin nhắn lần đầu
        msg = await update.callback_query.message.reply_text(
            ui_text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        session['message_id'] = msg.message_id
        session['chat_id'] = msg.chat_id
    elif is_new_session and current_session != "---":
        # Phiên mới: gửi tin nhắn MỚI để thông báo rõ ràng
        try:
            msg = await context.bot.send_message(
                chat_id=session['chat_id'],
                text=ui_text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            session['message_id'] = msg.message_id
        except Exception as e:
            logging.error(f"Lỗi gửi tin mới: {e}")
    else:
        # Cùng phiên: chỉ edit tin nhắn hiện tại
        try:
            await context.bot.edit_message_text(
                ui_text,
                chat_id=session['chat_id'],
                message_id=session['message_id'],
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Lỗi edit: {e}")

# ===== TỰ ĐỘNG DỰ ĐOÁN =====
async def auto_predict(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    session = user_sessions.get(user_id)
    if not session or not session.get('active'):
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
    await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown")

# ===== HỒ SƠ =====
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    data = user_data.get(user_id, {"balance": 0, "used": 0})
    await update.message.reply_text(
        f"👤 *HỒ SƠ CỦA BẠN*\n\n"
        f"ID: `{user_id}`\n"
        f"Tên: {user.first_name}\n"
        f"Username: @{user.username or 'Chưa có'}\n"
        f"💰 Số dư hiện có: {data['balance']:,}đ\n"
        f"💸 Số dư đã sử dụng: {data['used']:,}đ\n"
        f"🔑 KEY VIP: {data.get('key', 'Chưa có')}\n"
        f"⏰ Hạn key: {data.get('key_expiry', 'Chưa có')}",
        parse_mode="Markdown"
    )

# ===== MUA GÓI KEY =====
async def show_key_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for key, pkg in KEY_PACKAGES.items():
        keyboard.append([InlineKeyboardButton(
            f"{pkg['name']} - {pkg['price']:,}đ",
            callback_data=f"buykey_{key}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")])

    await update.message.reply_text(
        "🔑 *MUA GÓI KEY VIP*\n\nChọn gói key phù hợp với bạn:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def buy_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    pkg_key = query.data.replace("buykey_", "")
    pkg = KEY_PACKAGES.get(pkg_key)

    if not pkg:
        await query.edit_message_text("❌ Gói key không hợp lệ.")
        return

    is_admin = user_data.get(user_id, {}).get("is_admin", False)

    if not is_admin:
        if user_data[user_id]['balance'] < pkg['price']:
            await query.message.reply_text(
                f"❌ *THẤT BẠI* ❌\n\n"
                f"Số dư của bạn không đủ.\n"
                f"💰 Số dư hiện tại: {user_data[user_id]['balance']:,}đ\n"
                f"💸 Cần: {pkg['price']:,}đ\n\n"
                f"Vui lòng nạp thêm tiền để mua key.",
                parse_mode="Markdown"
            )
            await query.edit_message_text("❌ Giao dịch thất bại do số dư không đủ.")
            return

        user_data[user_id]['balance'] -= pkg['price']
        user_data[user_id]['used'] += pkg['price']

    new_key = generate_key()
    user_data[user_id]['key'] = new_key
    user_data[user_id]['key_expiry'] = pkg['duration']

    await query.message.reply_text(
        f"💎 *GIAO DỊCH THÀNH CÔNG — CẢM ƠN QUÝ KHÁCH!* 💎\n\n"
        f"Chân thành cảm ơn bạn đã lựa chọn sử dụng dịch vụ của Tool Kano AI.\n\n"
        f"🎁 Key: `{new_key}`\n"
        f"⏰ Thời Hạn: {pkg['duration']}\n\n"
        f"🎯 Kính chúc bạn sử dụng tool đạt hiệu quả cao nhất!\n\n"
        f"👑 *VUI LÒNG ẤN KÍCH HOẠT KEY ĐỂ SỬ DỤNG* 👑",
        parse_mode="Markdown"
    )
    await query.edit_message_text("✅ Đã tạo key thành công! Xem phía trên.")

# ===== KÍCH HOẠT KEY =====
async def activate_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *KÍCH HOẠT KEY*\n\n"
        "Vui lòng nhập key bạn đã mua theo cú pháp:\n"
        "`/active KEY_CUA_BAN`\n\n"
        "Ví dụ: `/active ABC123XYZ`",
        parse_mode="Markdown"
    )

# ===== LỆNH /ACTIVE =====
async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Vui lòng nhập key cần kích hoạt.\n"
            "Cú pháp: `/active KEY_CUA_BAN`",
            parse_mode="Markdown"
        )
        return

    input_key = args[0]
    if user_id not in user_data or user_data[user_id].get('key') != input_key:
        await update.message.reply_text(
            "❌ *Key không hợp lệ!*\n"
            "Vui lòng kiểm tra lại key hoặc liên hệ hỗ trợ.",
            parse_mode="Markdown"
        )
        return

    user_data[user_id]['key'] = input_key
    await update.message.reply_text(
        "✅ *KÍCH HOẠT KEY THÀNH CÔNG!*\n\n"
        f"Key: `{input_key}`\n"
        f"Hạn: {user_data[user_id].get('key_expiry', 'Chưa xác định')}\n\n"
        "🎯 Chúc bạn may mắn và thành công!",
        parse_mode="Markdown"
    )

# ===== GIFTCODE =====
async def giftcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎁 *Quà Tri Ân Khách Hàng* 🎁\n\n"
        "Hiện tại chưa có giftcode mới. Theo dõi kênh để nhận thông báo sớm nhất!",
        parse_mode="Markdown"
    )

# ===== NẠP TIỀN =====
async def show_nap_tien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("20.000đ", callback_data="nap_20000")],
        [InlineKeyboardButton("50.000đ", callback_data="nap_50000")],
        [InlineKeyboardButton("100.000đ", callback_data="nap_100000")],
        [InlineKeyboardButton("200.000đ", callback_data="nap_200000")],
        [InlineKeyboardButton("500.000đ", callback_data="nap_500000")],
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

    user_id = update.effective_user.id
    amount_str = query.data.replace("nap_", "")

    try:
        amount = int(amount_str)
    except Exception:
        await query.edit_message_text("❌ Số tiền không hợp lệ.")
        return

    # Tạo ghi chú ngẫu nhiên 4 số
    note_digits = random.randint(1000, 9999)
    note = f"NAPTIEN{note_digits}"

    # URL QR VietQR - KHÔNG tự động cộng tiền
    qr_url = (
        f"https://img.vietqr.io/image/MB-0844551151-compact.png"
        f"?amount={amount}&addInfo={note}&accountName=PHAM%20THE%20HIEN"
    )

    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    info = (
        f"💰 *THÔNG TIN NẠP TIỀN*\n\n"
        f"🏦 Tên Bank: *MBBANK*\n"
        f"👤 Họ Tên: *PHAM THE HIEN*\n"
        f"🔢 STK: *0844551151*\n"
        f"💵 Số tiền: *{amount:,}đ*\n"
        f"📝 Ghi Chú: `{note}`\n"
        f"🕒 {now}\n\n"
        f"⚠️ *Lưu ý:* Nhập đúng ghi chú `{note}` để hệ thống xác nhận tự động.\n"
        f"Sau khi chuyển khoản, số dư sẽ được cộng trong vòng 1-5 phút."
    )

    await query.message.reply_photo(
        photo=qr_url,
        caption=info,
        parse_mode="Markdown"
    )
    await query.edit_message_text("✅ Đã tạo mã QR, xem thông tin bên trên.")

# ===== FEEDBACK =====
async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("FEEDBACK", url=FEEDBACK_LINK)]
    ])
    await update.message.reply_text(
        "📝 *FEEDBACK*\n\n"
        "Mọi ý kiến đóng góp vui lòng gửi qua kênh bên dưới.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ===== KÊNH HỖ TRỢ =====
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
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_handler(CallbackQueryHandler(start_game, pattern="^game_betvip$"))
    app.add_handler(CallbackQueryHandler(stop_game, pattern="^stop_game$"))
    app.add_handler(CallbackQueryHandler(back_game, pattern="^back_game$"))
    app.add_handler(CallbackQueryHandler(back_main, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(buy_key, pattern="^buykey_"))
    app.add_handler(CallbackQueryHandler(generate_qr, pattern="^nap_"))

    print("Bot Kano AI đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
