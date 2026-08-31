import requests
import logging
import random
import string
import threading
import os
from datetime import datetime
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ===== FLASK KEEPALIVE =====
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "Kano AI Bot is running.", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ===== CẤU HÌNH =====
TOKEN        = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
PREDICT_URL  = "https://bettv-predictor.onrender.com/predict"
HISTORY_URL  = (
    "https://wtxmd52.macminim6.online/v1/txmd5/sessions"
    "?cp=R&cl=R&pf=web&at=1fc7bfdeab18790088a6e44d6b8cb288&limit=10"
)
FEEDBACK_LINK  = "https://t.me/feedbackkanoai_2026"
THONGBAO_LINK  = "https://t.me/thongbaokanoai_2026"
ADMIN_ID       = 7853432590
ADMIN_USERNAME = "thehpie9"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ===== BỘ NHỚ =====
user_data     = {}
user_sessions = {}

# ===== UI CONSTANTS =====
MENU_KEYBOARD = ReplyKeyboardMarkup([
    ["🎮 KHU VỰC GAME", "👤 HỒ SƠ"],
    ["🔑 MUA GÓI KEY",  "✅ KÍCH HOẠT KEY"],
    ["🎁 NHẬN GIFTCODE", "💰 NẠP TIỀN VÍ"],
    ["📝 FEEDBACK",     "📢 KÊNH THÔNG BÁO"],
], resize_keyboard=True)

WELCOME_TEXT = (
    "🏆 *𝐓𝐎𝐎𝐋 𝐊𝐀𝐍𝐎 𝐀𝐈 — ĐẲNG CẤP DỰ ĐOÁN TÀI XỈU* 🏆\n\n"
    "🎉 Chào mừng bạn đến với trợ lý AI dự đoán đỉnh cao nhất!\n\n"
    "💥 *ĐẶC QUYỀN DÀNH CHO BẠN:*\n"
    "⚡ Dự đoán chuẩn xác với công nghệ AI thế hệ mới.\n"
    "⚡ Nạp tiền chớp mắt, hệ thống xử lý siêu tốc.\n"
    "⚡ Menu tiện lợi, dễ dùng cho cả người mới.\n\n"
    "🎁 Sẵn sàng chiến chưa? Chọn tính năng bên dưới!"
)

KEY_PACKAGES = {
    "1_ngay":  {"name": "1 Ngày",  "price": 10000,  "duration": "1 ngày"},
    "7_ngay":  {"name": "7 Ngày",  "price": 50000,  "duration": "7 ngày"},
    "30_ngay": {"name": "30 Ngày", "price": 150000, "duration": "30 ngày"},
    "90_ngay": {"name": "90 Ngày", "price": 350000, "duration": "90 ngày"},
}

def action_kb(paused=False):
    """Keyboard dưới tin dự đoán. paused=True thêm nút Tiếp Tục."""
    rows = []
    if paused:
        rows.append([InlineKeyboardButton("▶️ TIẾP TỤC DỰ ĐOÁN", callback_data="resume_game")])
    else:
        rows.append([InlineKeyboardButton("⏹ DỪNG DỰ ĐOÁN", callback_data="stop_game")])
    rows.append([InlineKeyboardButton("🔙 QUAY LẠI", callback_data="back_game")])
    return InlineKeyboardMarkup(rows)

# ===== HELPERS =====
def generate_key():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

def is_admin(uid, username=""):
    return uid == ADMIN_ID or (
        bool(username) and username.lower() == ADMIN_USERNAME.lower()
    )

def ensure_user(uid, username=""):
    if uid not in user_data:
        user_data[uid] = {"balance": 0, "used": 0, "key": None, "key_expiry": None}
    if is_admin(uid, username):
        user_data[uid].update({
            "balance": 999_999_999,
            "key": "ADMIN_UNLIMITED",
            "key_expiry": "Vĩnh viễn",
        })

# ===== API =====
def fetch_predict():
    """
    Gọi API predict.
    Trả về dict hoặc None.
    Nếu status=TRAINING thì trả về nhưng caller phải bỏ qua logic phiên mới.
    """
    try:
        r = requests.get(PREDICT_URL, timeout=12)
        r.raise_for_status()
        data = r.json()
        c = float(data.get("confidence", 0))
        data["confidence_pct"] = round(c * 100 if c <= 1.0 else c, 1)
        return data
    except Exception as e:
        log.warning(f"fetch_predict lỗi: {e}")
        return None

def fetch_game_sessions():
    """
    Lấy lịch sử phiên từ API game.
    index 0 = phiên mới nhất (vừa có kết quả).
    """
    try:
        r = requests.get(HISTORY_URL, timeout=8)
        r.raise_for_status()
        return r.json().get("list", [])
    except Exception as e:
        log.warning(f"fetch_game_sessions lỗi: {e}")
        return []

def find_result(session_id, sessions):
    """Tìm kết quả thật của session_id trong list sessions."""
    try:
        sid = int(session_id)
        for s in sessions:
            if s.get("id") == sid:
                return s.get("resultTruyenThong"), s.get("dices"), s.get("point")
    except Exception:
        pass
    return None, None, None

# ===== BUILD UI =====
def label_result(raw):
    if raw in ("TAI", "T", "TÀI"):
        return "TÀI", "🔴"
    if raw in ("XIU", "X", "XỈU"):
        return "XỈU", "🔵"
    return (raw or "---"), "➖"

def build_ui(session, predict_data):
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    sep = "━" * 22

    if predict_data and predict_data.get("status") == "PREDICT":
        target_id            = str(predict_data["target_session_id"])
        pred_label, pred_emoji = label_result(predict_data.get("predict", "").upper())
        conf                 = predict_data["confidence_pct"]
        is_ready             = True
    else:
        target_id   = "---"
        pred_label  = "Đang chờ dữ liệu"
        pred_emoji  = "⏳"
        conf        = 0.0
        is_ready    = False

    bar = "▰" * int(conf / 100 * 12) + "▱" * (12 - int(conf / 100 * 12))

    prev_label, prev_emoji = label_result(session["prev_result"])
    prev_dices = session.get("prev_dices")
    prev_point = session.get("prev_point")

    dice_line = ""
    if prev_dices and len(prev_dices) == 3:
        dice_line = f"\n🎲 {prev_dices[0]} · {prev_dices[1]} · {prev_dices[2]}   Tổng: *{prev_point}*"

    return (
        f"╔══════════════════════╗\n"
        f"      🏆 *KANO AI* · BetVip\n"
        f"╚══════════════════════╝\n\n"
        f"{sep}\n"
        f"📡 *DỰ ĐOÁN PHIÊN TIẾP THEO*\n"
        f"{sep}\n"
        f"🔢 Phiên:   `#{target_id}`\n"
        f"{pred_emoji} Kết quả:  *{pred_label}*\n\n"
        f"📊 *ĐỘ TIN CẬY*\n"
        f"`{bar}` *{conf:.1f}%*\n\n"
        f"{sep}\n"
        f"📜 *PHIÊN TRƯỚC*\n"
        f"{sep}\n"
        f"🔢 Phiên:   `#{session['prev_session']}`\n"
        f"{prev_emoji} Kết quả:  *{prev_label}*"
        f"{dice_line}\n\n"
        f"{sep}\n"
        f"🕒 {now}\n"
        f"{'🟢 *AI ĐANG HOẠT ĐỘNG*' if is_ready else '🔴 *ĐANG CHỜ DỮ LIỆU*'}"
    ), target_id

# ===== SESSION =====
def new_session(chat_id):
    return {
        "active":       True,
        "chat_id":      chat_id,
        "message_id":   None,
        "prev_session": "---",
        "prev_result":  "---",
        "prev_dices":   None,
        "prev_point":   None,
        # latest_session_id từ API predict — khi tăng => phiên mới
        "known_latest": None,
        # cache dự đoán hiện tại để không gọi lại API predict khi cùng phiên
        "last_predict": None,
    }

# ===== /START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=MENU_KEYBOARD)

# ===== MENU ROUTER =====
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)
    routes = {
        "🎮 KHU VỰC GAME":   show_game_area,
        "👤 HỒ SƠ":          show_profile,
        "🔑 MUA GÓI KEY":    show_key_packages,
        "✅ KÍCH HOẠT KEY":  activate_key_prompt,
        "🎁 NHẬN GIFTCODE":  giftcode,
        "💰 NẠP TIỀN VÍ":   show_nap_tien,
        "📝 FEEDBACK":       feedback,
        "📢 KÊNH THÔNG BÁO": thongbao,
    }
    fn = routes.get(update.message.text)
    if fn:
        await fn(update, context)
    else:
        await update.message.reply_text("⚠️ Vui lòng chọn chức năng từ menu bên dưới.")

# ===== KHU VỰC GAME =====
async def show_game_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 *KHU VỰC GAME*\n\nHiện tại hỗ trợ game BetVip.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ BetVip", callback_data="game_betvip")],
            [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")],
        ])
    )

# ===== CORE: KHỞI ĐỘNG DỰ ĐOÁN =====
async def _launch_predict(uid, chat_id, context, reply_fn):
    """
    Dùng chung cho start_game và resume_game.
    reply_fn: async function(text, reply_markup, parse_mode) -> Message
    """
    _cancel_job(context, uid)
    session = new_session(chat_id)
    user_sessions[uid] = session

    # Lấy trạng thái hiện tại từ API game (nguồn sự thật về phiên)
    game_sessions = fetch_game_sessions()
    if game_sessions:
        latest_id = game_sessions[0].get("id")   # phiên mới nhất đã có kết quả
        session["known_latest"] = latest_id
        if latest_id:
            res, dices, point = find_result(latest_id, game_sessions)
            session["prev_session"] = str(latest_id)
            session["prev_result"]  = res or "---"
            session["prev_dices"]   = dices
            session["prev_point"]   = point

    # Fetch predict (chỉ 1 lần khi khởi động)
    predict_data = fetch_predict()
    session["last_predict"] = predict_data

    text, _ = build_ui(session, predict_data)
    msg = await reply_fn(text, action_kb(), "Markdown")
    session["message_id"] = msg.message_id
    session["chat_id"]    = msg.chat_id

    if context.job_queue:
        context.job_queue.run_repeating(
            auto_predict,
            interval=2,
            first=2,
            name=f"auto_{uid}",
            user_id=uid,
        )

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)

    if not user_data[uid].get("key"):
        await query.edit_message_text(
            "❌ *Bạn chưa có KEY VIP!*\n\nMua key tại mục `🔑 MUA GÓI KEY`.",
            parse_mode="Markdown"
        )
        return

    async def reply_fn(text, kb, pm):
        return await query.message.reply_text(text, reply_markup=kb, parse_mode=pm)

    await _launch_predict(uid, query.message.chat_id, context, reply_fn)

async def resume_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tiếp tục dự đoán sau khi dừng."""
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)

    if not user_data[uid].get("key"):
        await query.edit_message_text(
            "❌ *Bạn chưa có KEY VIP!*", parse_mode="Markdown"
        )
        return

    async def reply_fn(text, kb, pm):
        return await query.message.reply_text(text, reply_markup=kb, parse_mode=pm)

    await query.edit_message_reply_markup(reply_markup=None)
    await _launch_predict(uid, query.message.chat_id, context, reply_fn)

# ===== AUTO JOB =====
async def auto_predict(context: ContextTypes.DEFAULT_TYPE):
    """
    Chạy mỗi 2 giây. Dùng latest_session_id từ API predict làm mốc.

    latest_session_id = ID phiên VỪA CÓ KẾT QUẢ (phiên trước target_session_id).
    Khi latest_session_id tăng => có kết quả mới => gửi dự đoán ngay.
    Cùng latest_session_id => cùng phiên => chỉ edit cập nhật giờ.
    """
    uid     = context.job.user_id
    session = user_sessions.get(uid)
    if not session or not session["active"]:
        context.job.schedule_removal()
        return

    # ── Bước 1: Poll API game để phát hiện phiên mới (nhẹ hơn gọi predict)
    game_sessions = fetch_game_sessions()
    if not game_sessions:
        return

    current_latest = game_sessions[0].get("id")   # phiên vừa có kết quả
    known_latest   = session.get("known_latest")

    is_new_session = (
        current_latest is not None
        and known_latest is not None
        and current_latest != known_latest
    )

    if is_new_session:
        # ── Bước 2: Phiên mới — lấy kết quả thật + fetch predict mới
        res, dices, point = find_result(current_latest, game_sessions)
        session["prev_session"] = str(current_latest)
        session["prev_result"]  = res or "---"
        session["prev_dices"]   = dices
        session["prev_point"]   = point
        session["known_latest"] = current_latest

        # Chỉ fetch predict khi phiên mới — tránh spam API
        predict_data = fetch_predict()

        # Nếu app.py đang training thì chờ, không gửi tin
        if not predict_data or predict_data.get("status") == "TRAINING":
            log.info(f"uid={uid} phiên mới nhưng AI đang training, chờ...")
            session["last_predict"] = predict_data
            # Vẫn update known_latest để không trigger lại
            return

        session["last_predict"] = predict_data
        text, _ = build_ui(session, predict_data)
        try:
            msg = await context.bot.send_message(
                chat_id=session["chat_id"],
                text=text,
                reply_markup=action_kb(),
                parse_mode="Markdown",
            )
            session["message_id"] = msg.message_id
            log.info(f"uid={uid} phiên mới latest={current_latest} => gửi dự đoán")
        except Exception as e:
            log.error(f"auto_predict send_message lỗi uid={uid}: {e}")

    else:
        # ── Cùng phiên — chỉ edit cập nhật giờ, không gọi predict
        if known_latest is None and current_latest is not None:
            session["known_latest"] = current_latest
        text, _ = build_ui(session, session["last_predict"])
        try:
            await context.bot.edit_message_text(
                text,
                chat_id=session["chat_id"],
                message_id=session["message_id"],
                reply_markup=action_kb(),
                parse_mode="Markdown",
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                log.error(f"auto_predict edit_message lỗi uid={uid}: {e}")

# ===== DỪNG / QUAY LẠI =====
def _cancel_job(context, uid):
    if context.job_queue:
        for job in context.job_queue.get_jobs_by_name(f"auto_{uid}"):
            job.schedule_removal()

def _deactivate(uid):
    if uid in user_sessions:
        user_sessions[uid]["active"] = False

async def stop_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    _deactivate(uid)
    _cancel_job(context, uid)
    await query.edit_message_reply_markup(reply_markup=action_kb(paused=True))

async def back_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    _deactivate(uid)
    _cancel_job(context, uid)
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
    admin = is_admin(uid, uname)
    badge   = "👑 *ADMIN — ĐẶC QUYỀN VÔ HẠN*" if admin else "👤 *HỒ SƠ CỦA BẠN*"
    balance = "Không giới hạn" if admin else f"{d['balance']:,}đ"
    await update.message.reply_text(
        f"{badge}\n\n"
        f"🆔 ID: `{uid}`\n"
        f"👤 Tên: {user.first_name}\n"
        f"🔗 Username: @{uname or 'Chưa có'}\n"
        f"💰 Số dư: {balance}\n"
        f"💸 Đã dùng: {d.get('used', 0):,}đ\n"
        f"🔑 KEY VIP: `{d.get('key') or 'Chưa có'}`\n"
        f"⏰ Hạn key: {d.get('key_expiry') or 'Chưa có'}",
        parse_mode="Markdown"
    )

# ===== MUA KEY =====
async def show_key_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [[InlineKeyboardButton(
        f"{p['name']} — {p['price']:,}đ", callback_data=f"buykey_{k}"
    )] for k, p in KEY_PACKAGES.items()]
    rows.append([InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")])
    await update.message.reply_text(
        "🔑 *MUA GÓI KEY VIP*\n\nChọn gói phù hợp:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def buy_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)

    pkg_k = query.data.replace("buykey_", "")
    pkg   = KEY_PACKAGES.get(pkg_k)
    if not pkg:
        await query.edit_message_text("❌ Gói key không hợp lệ.")
        return

    admin = is_admin(uid, uname)
    if not admin:
        if user_data[uid]["balance"] < pkg["price"]:
            await query.answer(
                f"Số dư không đủ! Cần {pkg['price']:,}đ, hiện có {user_data[uid]['balance']:,}đ.",
                show_alert=True
            )
            return
        user_data[uid]["balance"] -= pkg["price"]
        user_data[uid]["used"]    += pkg["price"]

    new_key  = "ADMIN_UNLIMITED" if admin else generate_key()
    duration = "Vĩnh viễn"       if admin else pkg["duration"]
    user_data[uid]["key"]        = new_key
    user_data[uid]["key_expiry"] = duration

    await query.edit_message_text(
        f"💎 *GIAO DỊCH THÀNH CÔNG* 💎\n\n"
        f"🎁 Key: `{new_key}`\n"
        f"⏰ Hạn: {duration}\n\n"
        f"Dùng lệnh `/active {new_key}` để kích hoạt.",
        parse_mode="Markdown"
    )

# ===== KÍCH HOẠT KEY =====
async def activate_key_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *KÍCH HOẠT KEY*\n\nNhập lệnh:\n`/active KEY_CUA_BAN`",
        parse_mode="Markdown"
    )

async def cmd_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = context.args
    if not args:
        await update.message.reply_text("❌ Cú pháp: `/active KEY_CUA_BAN`", parse_mode="Markdown")
        return
    key_input = args[0]
    if uid not in user_data or user_data[uid].get("key") != key_input:
        await update.message.reply_text("❌ *Key không hợp lệ!*", parse_mode="Markdown")
        return
    await update.message.reply_text(
        f"✅ *KÍCH HOẠT THÀNH CÔNG!*\n\n"
        f"Key: `{key_input}`\n"
        f"Hạn: {user_data[uid].get('key_expiry', '---')}\n\n"
        f"🎯 Chúc bạn may mắn!",
        parse_mode="Markdown"
    )

# ===== GIFTCODE =====
async def giftcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎁 *QUÀ TRI ÂN* 🎁\n\nHiện chưa có giftcode mới.\nTheo dõi kênh thông báo để nhận sớm nhất!",
        parse_mode="Markdown"
    )

# ===== NẠP TIỀN =====
async def show_nap_tien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💰 *NẠP TIỀN VÍ*\n\nChọn số tiền:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("20.000đ",  callback_data="nap_20000")],
            [InlineKeyboardButton("50.000đ",  callback_data="nap_50000")],
            [InlineKeyboardButton("100.000đ", callback_data="nap_100000")],
            [InlineKeyboardButton("200.000đ", callback_data="nap_200000")],
            [InlineKeyboardButton("500.000đ", callback_data="nap_500000")],
            [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")],
        ])
    )

async def generate_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        amount = int(query.data.replace("nap_", ""))
    except ValueError:
        await query.edit_message_text("❌ Số tiền không hợp lệ.")
        return

    note   = f"NAPTIEN{random.randint(10000, 99999)}"
    qr_url = (
        f"https://img.vietqr.io/image/MB-0844551151-compact.png"
        f"?amount={amount}&addInfo={note}&accountName=PHAM%20THE%20HIEN"
    )
    caption = (
        f"💰 *THÔNG TIN NẠP TIỀN*\n\n"
        f"🏦 Bank: *MBBANK*\n"
        f"👤 Tên: *PHAM THE HIEN*\n"
        f"🔢 STK: *0844551151*\n"
        f"💵 Số tiền: *{amount:,}đ*\n"
        f"📝 Ghi chú: `{note}`\n\n"
        f"⚠️ Nhập đúng ghi chú `{note}` để hệ thống xác nhận.\n"
        f"Số dư cộng sau khi admin xác nhận."
    )
    await query.message.reply_photo(photo=qr_url, caption=caption, parse_mode="Markdown")
    await query.edit_message_text("✅ Đã tạo mã QR bên trên.")

# ===== FEEDBACK & THÔNG BÁO =====
async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 *FEEDBACK*\n\nMọi ý kiến đóng góp vui lòng gửi qua kênh bên dưới.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Gửi Feedback", url=FEEDBACK_LINK)]
        ])
    )

async def thongbao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📢 *KÊNH THÔNG BÁO*\n\nTheo dõi để nhận thông báo và giftcode mới nhất!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Kênh Thông Báo", url=THONGBAO_LINK)]
        ])
    )

# ===== MAIN =====
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("active", cmd_active))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    app.add_handler(CallbackQueryHandler(start_game,  pattern="^game_betvip$"))
    app.add_handler(CallbackQueryHandler(resume_game, pattern="^resume_game$"))
    app.add_handler(CallbackQueryHandler(stop_game,   pattern="^stop_game$"))
    app.add_handler(CallbackQueryHandler(back_game,   pattern="^back_game$"))
    app.add_handler(CallbackQueryHandler(back_main,   pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(buy_key,     pattern="^buykey_"))
    app.add_handler(CallbackQueryHandler(generate_qr, pattern="^nap_"))

    threading.Thread(target=run_flask, daemon=True).start()

    log.info("Bot Kano AI v6 đang chạy...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
