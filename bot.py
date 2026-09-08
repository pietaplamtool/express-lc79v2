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
BOT_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

@flask_app.route("/")
def health():
    return "Kano AI Bot is running.", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def self_ping():
    import time as _t
    _t.sleep(30)
    while True:
        try:
            url = BOT_URL or "https://bettv-telegram-bot.onrender.com"
            requests.get(url, timeout=10)
            log.info("Self-ping OK")
        except Exception as e:
            log.warning(f"Self-ping lỗi: {e}")
        _t.sleep(600)

# ===== CẤU HÌNH =====
TOKEN          = "8891039285:AAGuzG0fdsycHSsIhogbth3dvnzE16PTziw"
PREDICT_URL    = "https://bettv-predictor.onrender.com/predict"
HISTORY_URL    = (
    "https://wtxmd52.macminim6.online/v1/txmd5/sessions"
    "?cp=R&cl=R&pf=web&at=1fc7bfdeab18790088a6e44d6b8cb288&limit=10"
)
FEEDBACK_LINK  = "https://t.me/feedbackkanoai_2026"
THONGBAO_LINK  = "https://t.me/thongbaokanoai_2026"

# Danh sách admin
ADMINS = {
    7853432590: {"username": "thehpie9",  "balance": 10_000_000},
    8953969016: {"username": "pie900k",   "balance": 10_000_000},
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ===== BỘ NHỚ =====
user_data     = {}   # uid -> dict
user_sessions = {}   # uid -> dict

# ===== KEYBOARDS =====
# Menu chính — luôn hiển thị
MENU_KB = ReplyKeyboardMarkup([
    ["🎮 KHU VỰC GAME",  "👤 HỒ SƠ"],
    ["🔑 MUA GÓI KEY",   "✅ KÍCH HOẠT KEY"],
    ["🎁 NHẬN GIFTCODE", "💰 NẠP TIỀN VÍ"],
    ["📝 FEEDBACK",      "📢 KÊNH THÔNG BÁO"],
], resize_keyboard=True)

# Keyboard sau khi chọn game — có nút AUTO
GAME_KB = ReplyKeyboardMarkup([
    ["⏹ DỪNG DỰ ĐOÁN"],
    ["🤖 BẬT AUTO DỰ ĐOÁN"],
    ["🔙 QUAY LẠI MENU"],
], resize_keyboard=True)

# Keyboard khi AUTO đang chạy
AUTO_KB = ReplyKeyboardMarkup([
    ["⏹ DỪNG AUTO"],
    ["🔙 QUAY LẠI MENU"],
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

# ===== GÓI KEY =====
KEY_PACKAGES = {
    "tan_thu": {
        "name":     "🎁 Tân Thủ Trải Nghiệm",
        "price":    0,
        "duration": "2 ngày",
        "one_time": True,
    },
    "1_ngay": {
        "name":     "1 Ngày",
        "price":    10_000,
        "duration": "1 ngày",
        "one_time": False,
    },
    "7_ngay": {
        "name":     "7 Ngày",
        "price":    50_000,
        "duration": "7 ngày",
        "one_time": False,
    },
    "30_ngay": {
        "name":     "30 Ngày",
        "price":    150_000,
        "duration": "30 ngày",
        "one_time": False,
    },
    "90_ngay": {
        "name":     "90 Ngày",
        "price":    350_000,
        "duration": "90 ngày",
        "one_time": False,
    },
}

# ===== HELPERS =====
def generate_key():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

def is_admin(uid, username=""):
    if uid in ADMINS:
        return True
    if username:
        uname_clean = username.lstrip("@").lower()
        for info in ADMINS.values():
            if info["username"].lstrip("@").lower() == uname_clean:
                return True
    return False

def ensure_user(uid, username=""):
    if uid not in user_data:
        user_data[uid] = {
            "balance":      0,
            "used":         0,
            "key":          None,
            "key_expiry":   None,
            "tan_thu_used": False,
        }
    if is_admin(uid, username):
        admin_balance = ADMINS.get(uid, {}).get("balance", 10_000_000)
        # Chỉ set balance nếu chưa set hoặc thấp hơn mức admin
        if user_data[uid]["balance"] < admin_balance:
            user_data[uid]["balance"] = admin_balance
        user_data[uid]["key"]        = "ADMIN_UNLIMITED"
        user_data[uid]["key_expiry"] = "Vĩnh viễn"

def _cancel_job(context, uid):
    if context.job_queue:
        for job in context.job_queue.get_jobs_by_name(f"auto_{uid}"):
            job.schedule_removal()

def _deactivate(uid):
    if uid in user_sessions:
        user_sessions[uid]["active"] = False

# ===== API =====
def fetch_predict():
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
    try:
        r = requests.get(HISTORY_URL, timeout=8)
        r.raise_for_status()
        return r.json().get("list", [])
    except Exception as e:
        log.warning(f"fetch_game_sessions lỗi: {e}")
        return []

def get_latest_finished(sessions):
    """Phiên mới nhất đã có kết quả thật."""
    for s in sessions:
        if s.get("resultTruyenThong"):
            return s
    return None

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
    auto_tag = "🤖 AUTO · " if session.get("auto_mode") else ""

    if predict_data and predict_data.get("status") == "PREDICT":
        target_id              = str(predict_data["target_session_id"])
        pred_label, pred_emoji = label_result(predict_data.get("predict", "").upper())
        conf                   = predict_data["confidence_pct"]
        is_ready               = True
    else:
        target_id  = "---"
        pred_label = "Đang chờ dữ liệu"
        pred_emoji = "⏳"
        conf       = 0.0
        is_ready   = False

    bar = "▰" * int(conf / 100 * 12) + "▱" * (12 - int(conf / 100 * 12))
    prev_label, prev_emoji = label_result(session["prev_result"])
    prev_dices = session.get("prev_dices")
    prev_point = session.get("prev_point")
    dice_line  = ""
    if prev_dices and len(prev_dices) == 3:
        dice_line = f"\n🎲 {prev_dices[0]} · {prev_dices[1]} · {prev_dices[2]}   Tổng: *{prev_point}*"

    return (
        f"╔══════════════════════╗\n"
        f"   {auto_tag}🏆 *KANO AI* · BetVip\n"
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
    )

# ===== SESSION =====
def new_session(chat_id, auto_mode=False):
    return {
        "active":       True,
        "auto_mode":    auto_mode,
        "chat_id":      chat_id,
        "message_id":   None,
        "prev_session": "---",
        "prev_result":  "---",
        "prev_dices":   None,
        "prev_point":   None,
        "known_latest": None,
        "last_predict": None,
    }

# ===== /START =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)
    await update.message.reply_text(
        WELCOME_TEXT, parse_mode="Markdown", reply_markup=MENU_KB
    )

# ===== MENU ROUTER =====
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)
    text  = update.message.text

    # Nút trên GAME_KB / AUTO_KB
    if text == "⏹ DỪNG DỰ ĐOÁN":
        await do_stop(update, context, uid)
        return
    if text == "🤖 BẬT AUTO DỰ ĐOÁN":
        await do_start_auto(update, context, uid)
        return
    if text == "⏹ DỪNG AUTO":
        await do_stop_auto(update, context, uid)
        return
    if text == "🔙 QUAY LẠI MENU":
        _deactivate(uid)
        _cancel_job(context, uid)
        await update.message.reply_text(
            WELCOME_TEXT, parse_mode="Markdown", reply_markup=MENU_KB
        )
        return

    # Menu chính
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
    fn = routes.get(text)
    if fn:
        await fn(update, context)
    else:
        await update.message.reply_text("⚠️ Vui lòng chọn chức năng từ menu bên dưới.")

# ===== KHU VỰC GAME =====
async def show_game_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 *KHU VỰC GAME*\n\nHiện tại hỗ trợ game BetVip.\nChọn game để bắt đầu:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ BetVip", callback_data="game_betvip")],
            [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")],
        ])
    )

# ===== KHỞI ĐỘNG DỰ ĐOÁN (core) =====
async def _launch(uid, chat_id, context, send_fn, auto_mode=False):
    """
    Khởi động session dự đoán.
    send_fn: async (text, reply_markup) -> Message
    """
    _cancel_job(context, uid)
    session = new_session(chat_id, auto_mode=auto_mode)
    user_sessions[uid] = session

    # Lấy trạng thái game hiện tại
    game_sessions = fetch_game_sessions()
    if game_sessions:
        finished = get_latest_finished(game_sessions)
        if finished:
            session["known_latest"] = finished.get("id")
            session["prev_session"] = str(finished.get("id", "---"))
            session["prev_result"]  = finished.get("resultTruyenThong") or "---"
            session["prev_dices"]   = finished.get("dices")
            session["prev_point"]   = finished.get("point")

    predict_data = fetch_predict()
    session["last_predict"] = predict_data

    kb   = AUTO_KB if auto_mode else GAME_KB
    text = build_ui(session, predict_data)
    msg  = await send_fn(text, kb)
    session["message_id"] = msg.message_id
    session["chat_id"]    = msg.chat_id

    if context.job_queue:
        context.job_queue.run_repeating(
            auto_predict_job,
            interval=2,
            first=2,
            name=f"auto_{uid}",
            user_id=uid,
        )

# ===== CALLBACK: CHỌN GAME =====
async def cb_game_betvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await query.edit_message_text("✅ Đang khởi động BetVip...")

    async def send_fn(text, kb):
        return await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text, reply_markup=kb, parse_mode="Markdown"
        )

    await _launch(uid, query.message.chat_id, context, send_fn, auto_mode=False)

# ===== BẬT AUTO (từ nút keyboard) =====
async def do_start_auto(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)

    if not user_data[uid].get("key"):
        await update.message.reply_text(
            "❌ *Bạn chưa có KEY VIP!*\n\nMua key tại mục `🔑 MUA GÓI KEY`.",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        "🤖 *AUTO DỰ ĐOÁN đã bật!*\n\nBot sẽ tự động gửi dự đoán mỗi khi có kết quả mới.",
        parse_mode="Markdown",
        reply_markup=AUTO_KB
    )

    async def send_fn(text, kb):
        return await update.message.reply_text(
            text, reply_markup=kb, parse_mode="Markdown"
        )

    await _launch(uid, update.message.chat_id, context, send_fn, auto_mode=True)

# ===== DỪNG DỰ ĐOÁN THƯỜNG =====
async def do_stop(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    _deactivate(uid)
    _cancel_job(context, uid)
    await update.message.reply_text(
        "⏹ *Đã dừng dự đoán.*\n\nBấm 🤖 BẬT AUTO DỰ ĐOÁN để tiếp tục hoặc 🔙 QUAY LẠI MENU.",
        parse_mode="Markdown",
        reply_markup=GAME_KB
    )

# ===== DỪNG AUTO =====
async def do_stop_auto(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    _deactivate(uid)
    _cancel_job(context, uid)
    await update.message.reply_text(
        "⏹ *Đã dừng AUTO dự đoán.*\n\nBấm 🤖 BẬT AUTO DỰ ĐOÁN để bật lại.",
        parse_mode="Markdown",
        reply_markup=GAME_KB
    )

# ===== AUTO JOB (2 giây/lần) =====
async def auto_predict_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Mỗi 2 giây:
    - Poll API game, lấy phiên mới nhất đã có kết quả.
    - Nếu ID thay đổi → phiên mới vừa ra kết quả
      → fetch dự đoán mới và gửi tin ngay.
    - Nếu cùng phiên → chỉ edit cập nhật giờ (không gọi predict API).
    """
    uid     = context.job.user_id
    session = user_sessions.get(uid)
    if not session or not session["active"]:
        context.job.schedule_removal()
        return

    auto_mode     = session.get("auto_mode", False)
    kb            = AUTO_KB if auto_mode else GAME_KB

    game_sessions = fetch_game_sessions()
    if not game_sessions:
        return

    finished = get_latest_finished(game_sessions)
    if not finished:
        return

    current_latest = finished.get("id")
    known_latest   = session.get("known_latest")

    is_new = (
        current_latest is not None
        and known_latest is not None
        and current_latest != known_latest
    )

    if is_new:
        # Kết quả mới vừa ra — cập nhật phiên trước
        session["prev_session"] = str(current_latest)
        session["prev_result"]  = finished.get("resultTruyenThong") or "---"
        session["prev_dices"]   = finished.get("dices")
        session["prev_point"]   = finished.get("point")
        session["known_latest"] = current_latest

        # Fetch dự đoán phiên tiếp theo
        predict_data = fetch_predict()
        if not predict_data or predict_data.get("status") == "TRAINING":
            session["last_predict"] = predict_data
            log.info(f"uid={uid} phiên mới={current_latest} nhưng AI đang training")
            return

        session["last_predict"] = predict_data
        text = build_ui(session, predict_data)

        try:
            msg = await context.bot.send_message(
                chat_id=session["chat_id"],
                text=text,
                reply_markup=kb,
                parse_mode="Markdown",
            )
            session["message_id"] = msg.message_id
            log.info(f"uid={uid} gửi dự đoán phiên mới latest={current_latest}")
        except Exception as e:
            log.error(f"auto_predict_job send lỗi uid={uid}: {e}")

    else:
        # Cùng phiên — chỉ edit cập nhật giờ
        if known_latest is None and current_latest is not None:
            session["known_latest"] = current_latest

        text = build_ui(session, session["last_predict"])
        try:
            await context.bot.edit_message_text(
                text,
                chat_id=session["chat_id"],
                message_id=session["message_id"],
                reply_markup=kb,
                parse_mode="Markdown",
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                log.error(f"auto_predict_job edit lỗi uid={uid}: {e}")

# ===== INLINE CALLBACKS =====
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
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)
    rows  = []
    for k, p in KEY_PACKAGES.items():
        if p.get("one_time") and user_data[uid].get("tan_thu_used"):
            label = f"{p['name']} — Đã dùng ✗"
            rows.append([InlineKeyboardButton(label, callback_data="tan_thu_used")])
        else:
            price_str = "FREE" if p["price"] == 0 else f"{p['price']:,}đ"
            label = f"{p['name']} — {price_str}"
            rows.append([InlineKeyboardButton(label, callback_data=f"buykey_{k}")])
    rows.append([InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")])
    await update.message.reply_text(
        "🔑 *MUA GÓI KEY VIP*\n\nChọn gói phù hợp:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def tan_thu_used_notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Bạn đã sử dụng gói Tân Thủ rồi!", show_alert=True)

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

    # Kiểm tra tân thủ đã dùng
    if pkg.get("one_time") and user_data[uid].get("tan_thu_used") and not admin:
        await query.answer("Bạn đã sử dụng gói Tân Thủ rồi!", show_alert=True)
        return

    # Kiểm tra số dư (gói free thì bỏ qua)
    if not admin and pkg["price"] > 0:
        if user_data[uid]["balance"] < pkg["price"]:
            await query.answer(
                f"Số dư không đủ! Cần {pkg['price']:,}đ, hiện có {user_data[uid]['balance']:,}đ.",
                show_alert=True
            )
            return
        user_data[uid]["balance"] -= pkg["price"]
        user_data[uid]["used"]    += pkg["price"]

    if pkg.get("one_time") and not admin:
        user_data[uid]["tan_thu_used"] = True

    new_key  = "ADMIN_UNLIMITED" if admin else generate_key()
    duration = "Vĩnh viễn"       if admin else pkg["duration"]
    user_data[uid]["key"]        = new_key
    user_data[uid]["key_expiry"] = duration

    price_str    = "Miễn phí" if pkg["price"] == 0 else f"{pkg['price']:,}đ"
    one_time_note = "\n⚠️ *Gói này chỉ dùng được 1 lần.*" if pkg.get("one_time") and not admin else ""

    await query.edit_message_text(
        f"╔══════════════════════╗\n"
        f"   💎 *GIAO DỊCH THÀNH CÔNG*\n"
        f"╚══════════════════════╝\n\n"
        f"📦 Gói: *{pkg['name']}*\n"
        f"🔑 Key: `{new_key}`\n"
        f"⏰ Hạn: *{duration}*\n"
        f"💰 Chi phí: *{price_str}*"
        f"{one_time_note}\n\n"
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
        f"╔══════════════════════╗\n"
        f"   ✅ *KÍCH HOẠT THÀNH CÔNG*\n"
        f"╚══════════════════════╝\n\n"
        f"🔑 Key: `{key_input}`\n"
        f"⏰ Hạn: *{user_data[uid].get('key_expiry', '---')}*\n\n"
        f"🎯 Chúc bạn may mắn và thắng lớn!",
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
            [InlineKeyboardButton("1.000đ",   callback_data="nap_1000")],
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

    note   = f"KANO{random.randint(10000, 99999)}"
    qr_url = (
        f"https://img.vietqr.io/image/MB-0844551151-compact.png"
        f"?amount={amount}&addInfo={note}&accountName=PHAM%20THE%20HIEN"
    )
    caption = (
        f"╔══════════════════════╗\n"
        f"   💰 *THÔNG TIN NẠP TIỀN*\n"
        f"╚══════════════════════╝\n\n"
        f"🏦 Bank:     *MBBANK*\n"
        f"👤 Tên:      *PHAM THE HIEN*\n"
        f"🔢 STK:      *0844551151*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Số tiền:  *{amount:,}đ*\n"
        f"📝 Nội dung: `{note}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Nhập đúng nội dung `{note}`\n"
        f"để hệ thống xác nhận giao dịch.\n\n"
        f"✅ Số dư cộng ngay sau khi\n"
        f"admin xác nhận chuyển khoản."
    )
    await query.message.reply_photo(photo=qr_url, caption=caption, parse_mode="Markdown")
    await query.edit_message_text(
        f"✅ *Đã tạo lệnh nạp tiền!*\n\n"
        f"Quét mã QR bên trên hoặc chuyển khoản thủ công.\n"
        f"💡 Mã giao dịch: `{note}`",
        parse_mode="Markdown"
    )

# ===== LỆNH NẠP TIỀN CHO ADMIN =====
async def cmd_naptien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /naptien <user_id> <so_tien>"""
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    if not is_admin(uid, uname):
        await update.message.reply_text("❌ Chỉ admin mới dùng được lệnh này.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "📋 *Cú pháp:* `/naptien <user_id> <so_tien>`\n"
            "Ví dụ: `/naptien 123456789 50000`",
            parse_mode="Markdown"
        )
        return

    try:
        target_uid = int(args[0])
        amount     = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ user_id và số tiền phải là số.")
        return

    if target_uid not in user_data:
        await update.message.reply_text(
            f"❌ Không tìm thấy user ID `{target_uid}`.", parse_mode="Markdown"
        )
        return

    user_data[target_uid]["balance"] += amount
    new_balance = user_data[target_uid]["balance"]

    await update.message.reply_text(
        f"╔══════════════════════╗\n"
        f"   ✅ *NẠP TIỀN THÀNH CÔNG*\n"
        f"╚══════════════════════╝\n\n"
        f"👤 User ID: `{target_uid}`\n"
        f"💵 Nạp: *+{amount:,}đ*\n"
        f"💰 Số dư mới: *{new_balance:,}đ*",
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=(
                f"╔══════════════════════╗\n"
                f"   🎉 *TÀI KHOẢN ĐƯỢC NẠP TIỀN*\n"
                f"╚══════════════════════╝\n\n"
                f"💵 Số tiền: *+{amount:,}đ*\n"
                f"💰 Số dư hiện tại: *{new_balance:,}đ*\n\n"
                f"✅ Giao dịch đã được xác nhận!\n"
                f"Cảm ơn bạn đã nạp tiền vào Kano AI. 🙏"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        log.warning(f"Không thể gửi thông báo nạp tiền cho uid={target_uid}: {e}")

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

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("active",  cmd_active))
    app.add_handler(CommandHandler("naptien", cmd_naptien))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))

    app.add_handler(CallbackQueryHandler(cb_game_betvip,      pattern="^game_betvip$"))
    app.add_handler(CallbackQueryHandler(back_main,           pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(buy_key,             pattern="^buykey_"))
    app.add_handler(CallbackQueryHandler(tan_thu_used_notice, pattern="^tan_thu_used$"))
    app.add_handler(CallbackQueryHandler(generate_qr,         pattern="^nap_"))

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()

    log.info("Bot Kano AI v8 đang chạy...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
