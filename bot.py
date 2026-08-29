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
FEEDBACK_LINK = "https://t.me/feedbackkanoai_2026"
THONGBAO_LINK = "https://t.me/thongbaokanoai_2026"
ADMIN_ID = 7853432590
ADMIN_USERNAME = "thehpie9"
logging.basicConfig(level=logging.INFO)

# ===== DỮ LIỆU USER =====
user_data = {}
user_sessions = {}

# ===== MENU BÀN PHÍM =====
# "Gửi Đóng Góp" -> "FEEDBACK" | "Kênh Hỗ Trợ" -> "Kênh Thông Báo"
MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["🎮 KHU VỰC GAME", "👤 HỒ SƠ"],
    ["🔑 MUA GÓI KEY", "✅ KÍCH HOẠT KEY"],
    ["🎁 NHẬN GIFTCODE", "💰 NẠP TIỀN VÍ"],
    ["📝 FEEDBACK", "📢 KÊNH THÔNG BÁO"]
], resize_keyboard=True)

# ===== LỜI CHÀO =====
WELCOME_TEXT = (
    "🏆 *𝐓𝐎𝐎𝐋 𝐊𝐀𝐍𝐎 𝐀𝐈 — ĐẲNG CẤP DỰ ĐOÁN TÀI XỈU* 🏆\n\n"
    "🎉 Chào mừng bạn đã đến với trợ lý AI dự đoán đỉnh cao nhất hiện nay!\n\n"
    "💥 *ĐẶC QUYỀN DÀNH CHO BẠN:*\n"
    "⚡ Dự đoán chuẩn xác: Bắt nhịp Tài Xỉu cực mượt với công nghệ AI thế hệ mới.\n"
    "⚡ Nạp tiền chớp mắt: Hệ thống chuyển khoản siêu tốc.\n"
    "⚡ Menu tiện lợi: Dễ sử dụng cho cả người mới bắt đầu.\n\n"
    "🎁 Bạn đã sẵn sàng làm chủ cuộc chơi? Bấm chọn tính năng bên dưới để chiến ngay!"
)

# ===== GÓI KEY =====
KEY_PACKAGES = {
    "1_ngay":  {"name": "1 Ngày",  "price": 10000,  "duration": "1 ngày"},
    "7_ngay":  {"name": "7 Ngày",  "price": 50000,  "duration": "7 ngày"},
    "30_ngay": {"name": "30 Ngày", "price": 150000, "duration": "30 ngày"},
    "90_ngay": {"name": "90 Ngày", "price": 350000, "duration": "90 ngày"},
}

# ===== HELPERS =====
def generate_key():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

def check_admin(user_id, username=None):
    if user_id == ADMIN_ID:
        return True
    if username and username.lower() == ADMIN_USERNAME.lower():
        return True
    return False

def get_prediction():
    """
    Gọi API render, trả về (data, error).
    data chứa đầy đủ JSON kể cả latest_session_id (phiên vừa xong)
    và target_session_id (phiên sắp đoán).
    """
    try:
        resp = requests.get(API_URL, timeout=10)
        if resp.status_code != 200:
            return None, f"Lỗi API: {resp.status_code}"
        return resp.json(), None
    except Exception as e:
        return None, str(e)

def ensure_user(user_id, username=""):
    if user_id not in user_data:
        user_data[user_id] = {
            "balance": 0,
            "used": 0,
            "key": None,
            "key_expiry": None,
            "is_admin": check_admin(user_id, username)
        }
    else:
        user_data[user_id]["is_admin"] = check_admin(user_id, username)

    if check_admin(user_id, username):
        user_data[user_id]["balance"]    = 999999999
        user_data[user_id]["key"]        = "ADMIN_UNLIMITED"
        user_data[user_id]["key_expiry"] = "Vĩnh viễn"

# ===== /START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=MENU_KEYBOARD)

# ===== MENU ROUTER =====
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    routes = {
        "🎮 KHU VỰC GAME":  show_game_area,
        "👤 HỒ SƠ":         show_profile,
        "🔑 MUA GÓI KEY":   show_key_packages,
        "✅ KÍCH HOẠT KEY": activate_key,
        "🎁 NHẬN GIFTCODE": giftcode,
        "💰 NẠP TIỀN VÍ":  show_nap_tien,
        "📝 FEEDBACK":      feedback,
        "📢 KÊNH THÔNG BÁO": support,
    }
    handler = routes.get(text)
    if handler:
        await handler(update, context)
    else:
        await update.message.reply_text("⚠️ Vui lòng chọn chức năng từ menu bên dưới.")

# ===== KHU VỰC GAME =====
async def show_game_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ BetVip", callback_data="game_betvip")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
    ])
    await update.message.reply_text(
        "🎮 *KHU VỰC GAME*\n\nHiện tại chỉ hỗ trợ game BetVip.",
        reply_markup=kb, parse_mode="Markdown"
    )

# ===== BẮT ĐẦU DỰ ĐOÁN =====
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id

    if uid not in user_data or not user_data[uid].get('key'):
        await query.edit_message_text(
            "❌ *Bạn chưa có KEY VIP!*\n\nVui lòng mua key tại mục `🔑 MUA GÓI KEY`.",
            parse_mode="Markdown"
        )
        return

    # Reset session hoàn toàn
    user_sessions[uid] = {
        "active": True,
        "chat_id": query.message.chat_id,
        "message_id": None,
        # target_session_id đang hiển thị — để phát hiện phiên mới
        "shown_target": None,
        # latest_session_id lần trước — chính là "phiên trước" hiển thị
        "prev_session": "---",
        "prev_result": "---",
    }

    # Huỷ job cũ
    if context.job_queue:
        for job in context.job_queue.jobs():
            if job.name == f"auto_{uid}":
                job.schedule_removal()

    await send_ui(update, context, uid, is_first=True)

    if context.job_queue:
        context.job_queue.run_repeating(
            auto_predict,
            interval=5,
            first=5,
            name=f"auto_{uid}",
            user_id=uid
        )

# ===== CORE: XÂY GIAO DIỆN VÀ GỬI =====
async def send_ui(update, context, uid, is_first=False):
    session = user_sessions.get(uid)
    if not session or not session["active"]:
        return

    data, err = get_prediction()
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    if err or not data or data.get("status") != "PREDICT":
        # API chưa sẵn sàng
        target_id    = "---"
        predict_val  = "⏳ Đang chờ..."
        confidence   = 0.0
        latest_id    = session["prev_session"]
        latest_res   = session["prev_result"]
    else:
        # target_session_id = phiên BOT đang dự đoán (người chơi chọn phiên này)
        target_id   = str(data["target_session_id"])
        predict_val = data.get("predict", "?")
        confidence  = float(data.get("confidence", 0)) * 100
        # latest_session_id = phiên vừa kết thúc trên render
        # predict của phiên đó = kết quả API đưa ra cho phiên trước
        latest_id   = str(data["latest_session_id"])
        # predict_short là kết quả dự đoán của phiên latest (phiên trước)
        latest_res  = data.get("predict_short", data.get("predict", "?"))

    # Phát hiện phiên mới: target_id đã đổi
    shown = session["shown_target"]
    is_new = (shown is not None) and (target_id != "---") and (target_id != shown)

    if is_new or session["shown_target"] is None:
        # Cập nhật phiên trước chỉ khi API trả dữ liệu thật
        if data and data.get("status") == "PREDICT":
            session["prev_session"] = latest_id
            session["prev_result"]  = latest_res
        session["shown_target"] = target_id

    prev_session = session["prev_session"]
    prev_result  = session["prev_result"]

    bar_len = 10
    filled  = int(confidence / 100 * bar_len)
    bar     = "█" * filled + "░" * (bar_len - filled)

    text = (
        f"🏆 *Tool Kano AI*\n"
        f"🎮 *Game: BetVip*\n\n"
        f"📊 *DỰ ĐOÁN PHIÊN TIẾP THEO*\n"
        f"🔢 Phiên: `{target_id}`\n"
        f"🎯 Kết quả: *{predict_val}*\n\n"
        f"📈 *ĐỘ TIN CẬY*\n"
        f"`{bar}` {confidence:.1f}%\n\n"
        f"📜 *PHIÊN TRƯỚC (Render)*\n"
        f"🔢 Phiên: `{prev_session}`\n"
        f"🎯 Kết quả: *{prev_result}*\n\n"
        f"🕒 {now_str}"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹ DỪNG", callback_data="stop_game")],
        [InlineKeyboardButton("🔙 BACK", callback_data="back_game")]
    ])

    if is_first:
        msg = await update.callback_query.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
        session["message_id"] = msg.message_id
        session["chat_id"]    = msg.chat_id
    elif is_new:
        # Phiên mới -> gửi tin MỚI để người dùng thấy rõ
        try:
            msg = await context.bot.send_message(
                chat_id=session["chat_id"], text=text, reply_markup=kb, parse_mode="Markdown"
            )
            session["message_id"] = msg.message_id
        except Exception as e:
            logging.error(f"send_message lỗi: {e}")
    else:
        # Cùng phiên -> edit tin cũ (cập nhật giờ + độ tin cậy)
        try:
            await context.bot.edit_message_text(
                text,
                chat_id=session["chat_id"],
                message_id=session["message_id"],
                reply_markup=kb,
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"edit_message lỗi: {e}")

# ===== AUTO JOB =====
async def auto_predict(context: ContextTypes.DEFAULT_TYPE):
    uid = context.job.user_id
    session = user_sessions.get(uid)
    if not session or not session["active"]:
        context.job.schedule_removal()
        return
    await send_ui(None, context, uid, is_first=False)

# ===== DỪNG =====
async def stop_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if uid in user_sessions:
        user_sessions[uid]["active"] = False
    for job in context.job_queue.jobs():
        if job.name == f"auto_{uid}":
            job.schedule_removal()
    await query.edit_message_text("⏹ *Đã dừng dự đoán.*\n\nBấm /start để quay lại.", parse_mode="Markdown")

async def back_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if uid in user_sessions:
        user_sessions[uid]["active"] = False
    for job in context.job_queue.jobs():
        if job.name == f"auto_{uid}":
            job.schedule_removal()
    await query.edit_message_text("🔙 *Đã quay lại.*\n\nChọn menu bên dưới.", parse_mode="Markdown")

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown")

# ===== HỒ SƠ =====
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    uid   = user.id
    uname = user.username or ""
    ensure_user(uid, uname)
    d     = user_data[uid]
    admin = check_admin(uid, uname)

    badge   = "👑 *ADMIN — ĐẶC QUYỀN VÔ HẠN*" if admin else "👤 *HỒ SƠ CỦA BẠN*"
    balance = "Không giới hạn" if admin else f"{d['balance']:,}đ"

    await update.message.reply_text(
        f"{badge}\n\n"
        f"🆔 ID: `{uid}`\n"
        f"👤 Tên: {user.first_name}\n"
        f"🔗 Username: @{uname or 'Chưa có'}\n"
        f"💰 Số dư: {balance}\n"
        f"💸 Đã sử dụng: {d.get('used', 0):,}đ\n"
        f"🔑 KEY VIP: `{d.get('key', 'Chưa có')}`\n"
        f"⏰ Hạn key: {d.get('key_expiry', 'Chưa có')}",
        parse_mode="Markdown"
    )

# ===== MUA KEY =====
async def show_key_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"{p['name']} - {p['price']:,}đ", callback_data=f"buykey_{k}")]
          for k, p in KEY_PACKAGES.items()]
    kb.append([InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")])
    await update.message.reply_text(
        "🔑 *MUA GÓI KEY VIP*\n\nChọn gói key phù hợp với bạn:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

async def buy_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    pkg_k = query.data.replace("buykey_", "")
    pkg   = KEY_PACKAGES.get(pkg_k)
    if not pkg:
        await query.edit_message_text("❌ Gói key không hợp lệ.")
        return

    ensure_user(uid, uname)
    admin = check_admin(uid, uname)

    if not admin:
        if user_data[uid]['balance'] < pkg['price']:
            await query.message.reply_text(
                f"❌ *Số dư không đủ*\n\n"
                f"💰 Hiện có: {user_data[uid]['balance']:,}đ\n"
                f"💸 Cần: {pkg['price']:,}đ\n\n"
                f"Vui lòng nạp thêm tiền để mua key.",
                parse_mode="Markdown"
            )
            await query.edit_message_text("❌ Giao dịch thất bại do số dư không đủ.")
            return
        user_data[uid]['balance'] -= pkg['price']
        user_data[uid]['used']    += pkg['price']

    new_key  = "ADMIN_UNLIMITED" if admin else generate_key()
    duration = "Vĩnh viễn"       if admin else pkg['duration']
    user_data[uid]['key']        = new_key
    user_data[uid]['key_expiry'] = duration

    await query.message.reply_text(
        f"💎 *GIAO DỊCH THÀNH CÔNG* 💎\n\n"
        f"🎁 Key: `{new_key}`\n"
        f"⏰ Thời Hạn: {duration}\n\n"
        f"👑 *VUI LÒNG ẤN KÍCH HOẠT KEY ĐỂ SỬ DỤNG* 👑",
        parse_mode="Markdown"
    )
    await query.edit_message_text("✅ Đã tạo key thành công! Xem phía trên.")

# ===== KÍCH HOẠT KEY =====
async def activate_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *KÍCH HOẠT KEY*\n\n"
        "Nhập key theo cú pháp:\n`/active KEY_CUA_BAN`\n\n"
        "Ví dụ: `/active ABC123XYZ`",
        parse_mode="Markdown"
    )

async def active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Cú pháp: `/active KEY_CUA_BAN`", parse_mode="Markdown"
        )
        return
    input_key = args[0]
    if uid not in user_data or user_data[uid].get('key') != input_key:
        await update.message.reply_text(
            "❌ *Key không hợp lệ!* Kiểm tra lại hoặc liên hệ hỗ trợ.", parse_mode="Markdown"
        )
        return
    await update.message.reply_text(
        f"✅ *KÍCH HOẠT THÀNH CÔNG!*\n\n"
        f"Key: `{input_key}`\n"
        f"Hạn: {user_data[uid].get('key_expiry', 'Chưa xác định')}\n\n"
        f"🎯 Chúc bạn may mắn!",
        parse_mode="Markdown"
    )

# ===== GIFTCODE =====
async def giftcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎁 *Quà Tri Ân Khách Hàng* 🎁\n\n"
        "Hiện tại chưa có giftcode mới. Theo dõi kênh thông báo để nhận sớm nhất!",
        parse_mode="Markdown"
    )

# ===== NẠP TIỀN =====
async def show_nap_tien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("20.000đ",  callback_data="nap_20000")],
        [InlineKeyboardButton("50.000đ",  callback_data="nap_50000")],
        [InlineKeyboardButton("100.000đ", callback_data="nap_100000")],
        [InlineKeyboardButton("200.000đ", callback_data="nap_200000")],
        [InlineKeyboardButton("500.000đ", callback_data="nap_500000")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
    ])
    await update.message.reply_text(
        "💰 *NẠP TIỀN VÍ*\n\nChọn số tiền bạn muốn nạp:",
        reply_markup=kb, parse_mode="Markdown"
    )

async def generate_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount_str = query.data.replace("nap_", "")
    try:
        amount = int(amount_str)
    except Exception:
        await query.edit_message_text("❌ Số tiền không hợp lệ.")
        return

    note      = f"NAPTIEN{random.randint(1000, 9999)}"
    qr_url    = (
        f"https://img.vietqr.io/image/MB-0844551151-compact.png"
        f"?amount={amount}&addInfo={note}&accountName=PHAM%20THE%20HIEN"
    )
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    caption = (
        f"💰 *THÔNG TIN NẠP TIỀN*\n\n"
        f"🏦 Tên Bank: *MBBANK*\n"
        f"👤 Họ Tên: *PHAM THE HIEN*\n"
        f"🔢 STK: *0844551151*\n"
        f"💵 Số tiền: *{amount:,}đ*\n"
        f"📝 Ghi Chú: `{note}`\n"
        f"🕒 {now_str}\n\n"
        f"⚠️ Nhập đúng ghi chú `{note}` để hệ thống xác nhận.\n"
        f"Số dư sẽ được cộng sau khi admin xác nhận chuyển khoản."
    )

    await query.message.reply_photo(photo=qr_url, caption=caption, parse_mode="Markdown")
    await query.edit_message_text("✅ Đã tạo mã QR, xem thông tin bên trên.")

# ===== FEEDBACK =====
async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("FEEDBACK", url=FEEDBACK_LINK)]
    ])
    await update.message.reply_text(
        "📝 *FEEDBACK*\n\nMọi ý kiến đóng góp vui lòng gửi qua kênh bên dưới.",
        reply_markup=kb, parse_mode="Markdown"
    )

# ===== KÊNH THÔNG BÁO =====
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Kênh Thông Báo", url=THONGBAO_LINK)]
    ])
    await update.message.reply_text(
        "📢 *KÊNH THÔNG BÁO*\n\n"
        "Theo dõi kênh để nhận thông báo mới nhất về tool và giftcode!",
        reply_markup=kb, parse_mode="Markdown"
    )

# ===== MAIN =====
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("active", active_key))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_handler(CallbackQueryHandler(start_game,   pattern="^game_betvip$"))
    app.add_handler(CallbackQueryHandler(stop_game,    pattern="^stop_game$"))
    app.add_handler(CallbackQueryHandler(back_game,    pattern="^back_game$"))
    app.add_handler(CallbackQueryHandler(back_main,    pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(buy_key,      pattern="^buykey_"))
    app.add_handler(CallbackQueryHandler(generate_qr,  pattern="^nap_"))

    print("Bot Kano AI v3 đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
