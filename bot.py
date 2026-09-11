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
    # FIX: dùng PORT+1 để tránh conflict với gunicorn trên cùng PORT
    # Bot chạy bằng `python bot.py` nên gunicorn không can thiệp.
    # PORT vẫn dùng được vì bot.py tự chạy Flask, không qua gunicorn.
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def self_ping():
    """
    Ping cả 2 service mỗi 4 phút để Render Free không spin down.
    Render Free spin down sau 15 phút không có request.
    """
    import time as _t
    _t.sleep(30)
    bot_url      = BOT_URL or "https://bettv-telegram-bot.onrender.com"
    predictor_url = "https://bettv-predictor.onrender.com/ping"
    while True:
        for url in [bot_url, predictor_url]:
            try:
                requests.get(url, timeout=10)
                log.info(f"Self-ping OK: {url}")
            except Exception as e:
                log.warning(f"Self-ping lỗi {url}: {e}")
        _t.sleep(240)

# ===== CẤU HÌNH =====
# FIX: Token mới — token cũ đã bị lộ, revoke ngay trên BotFather
TOKEN          = os.environ.get("BOT_TOKEN", "PASTE_TOKEN_MOI_VAO_ENV_RENDER")
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
user_data     = {}
user_sessions = {}

# ===== KEYBOARDS =====
MENU_KB = ReplyKeyboardMarkup([
    ["🎮 KHU VỰC GAME",  "👤 HỒ SƠ"],
    ["🔑 MUA GÓI KEY",   "✅ KÍCH HOẠT KEY"],
    ["🎁 NHẬN GIFTCODE", "💰 NẠP TIỀN VÍ"],
    ["📝 FEEDBACK",      "📢 KÊNH THÔNG BÁO"],
], resize_keyboard=True)

GAME_KB = ReplyKeyboardMarkup([
    ["⏹ DỪNG DỰ ĐOÁN"],
    ["🤖 BẬT AUTO DỰ ĐOÁN"],
    ["🔙 QUAY LẠI MENU"],
], resize_keyboard=True)

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

    # FIX: engine mới trả label trực tiếp, không dùng status==PREDICT
    has_prediction = (
        predict_data is not None
        and predict_data.get("label") in ("T", "X")
        and predict_data.get("history_len", 0) > 0
    )
    if has_prediction:
        # Phiên tiếp theo = phiên trước + 1
        prev = session.get("prev_session", "---")
        try:
            target_id = str(int(prev) + 1)
        except (ValueError, TypeError):
            target_id = "---"
        pred_label, pred_emoji = label_result(predict_data.get("label", ""))
        conf                   = predict_data.get("confidence_pct", 0.0)
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
        f"📡 *DỰ ĐOÁN*\n"
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

    if text == "⏹ DỪNG DỰ ĐOÁN":
        await do_stop(update, context, uid)
        return
    if text == "🤖 BẬT AUTO DỰ ĐOÁN":
        await do_start_auto(update, context, uid)
        return
    if text == "⏹ DỪNG AUTO":
        await do_stop_auto(update, context, uid)
        return
    # Baccarat buttons
    if text == "⏹ DỪNG DỰ ĐOÁN BAC":
        await do_stop_bac(update, context, uid)
        return
    if text == "🤖 BẬT AUTO BAC":
        await do_start_auto_bac(update, context, uid)
        return
    if text == "⏹ DỪNG AUTO BAC":
        await do_stop_auto_bac(update, context, uid)
        return
    if text == "🔙 QUAY LẠI MENU":
        _deactivate(uid)
        _cancel_job(context, uid)
        await update.message.reply_text(
            WELCOME_TEXT, parse_mode="Markdown", reply_markup=MENU_KB
        )
        return

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
        "🎮 *KHU VỰC GAME*\n\nChọn game để bắt đầu:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ BetVip Tài/Xỉu", callback_data="game_betvip")],
            [InlineKeyboardButton("🃏 Baccarat", callback_data="game_baccarat")],
            [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")],
        ])
    )

# ===== KHỞI ĐỘNG DỰ ĐOÁN =====
async def _launch(uid, chat_id, context, send_fn, auto_mode=False):
    _cancel_job(context, uid)
    session = new_session(chat_id, auto_mode=auto_mode)
    user_sessions[uid] = session

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

# ===== BẬT AUTO =====
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

# ===== DỪNG =====
async def do_stop(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    _deactivate(uid)
    _cancel_job(context, uid)
    await update.message.reply_text(
        "⏹ *Đã dừng dự đoán.*\n\nBấm 🤖 BẬT AUTO DỰ ĐOÁN để tiếp tục hoặc 🔙 QUAY LẠI MENU.",
        parse_mode="Markdown",
        reply_markup=GAME_KB
    )

async def do_stop_auto(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    _deactivate(uid)
    _cancel_job(context, uid)
    await update.message.reply_text(
        "⏹ *Đã dừng AUTO dự đoán.*\n\nBấm 🤖 BẬT AUTO DỰ ĐOÁN để bật lại.",
        parse_mode="Markdown",
        reply_markup=GAME_KB
    )

# ===== AUTO JOB =====
async def auto_predict_job(context: ContextTypes.DEFAULT_TYPE):
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
        session["prev_session"] = str(current_latest)
        session["prev_result"]  = finished.get("resultTruyenThong") or "---"
        session["prev_dices"]   = finished.get("dices")
        session["prev_point"]   = finished.get("point")
        session["known_latest"] = current_latest

        predict_data = fetch_predict()
        # Engine mới trả label trực tiếp, không có status field
        if not predict_data:
            log.info(f"uid={uid} phiên mới={current_latest} nhưng predict API lỗi")
            return
        # Chuyển confidence sang % nếu chưa có confidence_pct
        if "confidence_pct" not in predict_data and "confidence" in predict_data:
            c = float(predict_data.get("confidence", 0))
            predict_data["confidence_pct"] = round(c * 100 if c <= 1.0 else c, 1)

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

    if pkg.get("one_time") and user_data[uid].get("tan_thu_used") and not admin:
        await query.answer("Bạn đã sử dụng gói Tân Thủ rồi!", show_alert=True)
        return

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

# ===== LỆNH NẠP TIỀN ADMIN =====
async def cmd_naptien(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        log.warning(f"Không thể gửi thông báo cho uid={target_uid}: {e}")

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


# ═══════════════════════════════════════════════════════════════════════════════
# BACCARAT MODULE
# ═══════════════════════════════════════════════════════════════════════════════

BACCARAT_API_URL = "https://kkvgvbcrj.onrender.com/api/fullban"

# ── Baccarat AI Engine ────────────────────────────────────────────────────────

def _bac_safe(v):
    if v is None: return None
    s = str(v).strip().upper()
    return s if s in ("P","B","T") else None

def _bac_entropy(counts):
    total = sum(counts.values())
    if total == 0: return 0.0
    import math
    return -sum((c/total)*math.log2(c/total) for c in counts.values() if c > 0)

def _bac_clamp(v, lo, hi): return max(lo, min(hi, v))

def _bac_bat_nhip(history):
    """AI Bắt nhịp: phát hiện pattern lặp PBPB, BBPP..."""
    seq = [x for x in history if x != "T"]
    if len(seq) < 4: return "B", 0.5
    best_label = seq[-1]; best_score = 0.0
    for plen in [2, 3, 4]:
        if len(seq) < plen * 2: continue
        pattern = tuple(seq[-plen:])
        count = sum(1 for i in range(len(seq)-plen) if tuple(seq[i:i+plen]) == pattern)
        freq = count / max(1, len(seq) - plen)
        if freq > best_score:
            best_score = freq
            best_label = pattern[len(seq) % plen] if freq > 0.3 else seq[-1]
    return best_label, _bac_clamp(best_score * 1.5, 0.3, 0.85)

def _bac_theo_bet(history):
    """AI Theo bệt: phát hiện streak và theo."""
    seq = [x for x in history if x != "T"]
    if not seq: return "B", 0.5
    current = seq[-1]; streak = 0
    for x in reversed(seq):
        if x == current: streak += 1
        else: break
    return current, _bac_clamp(0.45 + streak * 0.06, 0.45, 0.82)

def _bac_be_bet(history):
    """AI Bẻ bệt: khi streak >= 4 dự đoán sẽ bẻ."""
    seq = [x for x in history if x != "T"]
    if len(seq) < 6: return "B", 0.5
    window = seq[-10:]; current = window[-1]; streak = 0
    for x in reversed(window):
        if x == current: streak += 1
        else: break
    if streak >= 4:
        opposite = "P" if current == "B" else "B"
        return opposite, _bac_clamp(0.5 + (streak-3)*0.08, 0.5, 0.85)
    return current, 0.48

def _bac_tie_prob(history):
    """AI Tie: dự đoán xác suất Hòa."""
    if len(history) < 10: return 9.5
    window = list(history)[-30:]
    n = len(window)
    base = window.count("T") / n
    dist = 0
    for x in reversed(window):
        if x != "T": dist += 1
        else: break
    tie_p = _bac_clamp(base * 0.6 + 0.04 + min(dist/20, 0.5)*0.03, 0.03, 0.25)
    return round(tie_p * 100, 1)

def baccarat_predict_local(history_str, api_du_doan, api_tin_cay):
    """
    Fuse: API signal (40%) + AI Bắt nhịp (20%) + AI Theo bệt (20%) + AI Bẻ bệt (20%).
    Trả về dict label/confidence_pct/tie_prob_pct/signals.
    """
    history = [_bac_safe(c) for c in history_str]
    history = [x for x in history if x is not None]

    tie_prob_pct = _bac_tie_prob(history)

    l1, c1 = _bac_bat_nhip(history)
    l2, c2 = _bac_theo_bet(history)
    l3, c3 = _bac_be_bet(history)

    api_label = _bac_safe(api_du_doan) or "B"
    api_conf  = _bac_clamp(float(api_tin_cay or 0) / 100.0, 0.0, 1.0)

    weights  = {"api": 0.40, "bat_nhip": 0.20, "theo_bet": 0.20, "be_bet": 0.20}
    signals  = {"api": (api_label, api_conf), "bat_nhip": (l1,c1), "theo_bet": (l2,c2), "be_bet": (l3,c3)}

    score_P = score_B = 0.0
    for name, (lbl, conf) in signals.items():
        w = weights[name]
        if lbl == "P": score_P += w * conf
        elif lbl == "B": score_B += w * conf

    total = score_P + score_B
    if total == 0: final_label = "B"; final_conf = 0.5
    elif score_P >= score_B: final_label = "P"; final_conf = score_P / total
    else: final_label = "B"; final_conf = score_B / total

    if final_conf > 0.88:
        final_label = "P" if final_label == "B" else "B"

    return {
        "label":          final_label,
        "confidence_pct": round(final_conf * 100, 1),
        "tie_prob_pct":   tie_prob_pct,
        "signals":        {k: {"label": v[0], "conf_pct": round(v[1]*100,1)} for k,v in signals.items()},
    }

# ── Baccarat API ──────────────────────────────────────────────────────────────

def fetch_baccarat_all():
    """Lấy toàn bộ dữ liệu tất cả bàn."""
    try:
        r = requests.get(BACCARAT_API_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("danh_sach", {})
    except Exception as e:
        log.warning(f"fetch_baccarat_all lỗi: {e}")
        return {}

def fetch_baccarat_ban(ban_id):
    """Lấy dữ liệu 1 bàn cụ thể."""
    try:
        r = requests.get(BACCARAT_API_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data.get("danh_sach", {}).get(ban_id)
    except Exception as e:
        log.warning(f"fetch_baccarat_ban {ban_id} lỗi: {e}")
        return None

# ── Baccarat Keyboards ────────────────────────────────────────────────────────

BAC_GAME_KB = ReplyKeyboardMarkup([
    ["⏹ DỪNG DỰ ĐOÁN BAC"],
    ["🤖 BẬT AUTO BAC"],
    ["🔙 QUAY LẠI MENU"],
], resize_keyboard=True)

BAC_AUTO_KB = ReplyKeyboardMarkup([
    ["⏹ DỪNG AUTO BAC"],
    ["🔙 QUAY LẠI MENU"],
], resize_keyboard=True)

# ── Baccarat build_ui ─────────────────────────────────────────────────────────

def label_bac(raw):
    raw = (raw or "").upper()
    if raw == "P": return "PLAYER", "🔵"
    if raw == "B": return "BANKER", "🔴"
    if raw == "T": return "HÒA", "🟢"
    return raw or "---", "➖"

def build_bac_ui(session, predict):
    now    = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    sep    = "━" * 22
    ban_id = session.get("bac_ban", "---")
    auto_tag = "🤖 AUTO · " if session.get("auto_mode") else ""

    if predict and predict.get("label") in ("P","B","T"):
        pred_label, pred_emoji = label_bac(predict["label"])
        conf         = predict.get("confidence_pct", 0.0)
        tie_pct      = predict.get("tie_prob_pct", 9.5)
        is_ready     = True
    else:
        pred_label = "Đang chờ dữ liệu"; pred_emoji = "⏳"
        conf = 0.0; tie_pct = 0.0; is_ready = False

    bar     = "▰" * int(conf / 100 * 12) + "▱" * (12 - int(conf / 100 * 12))
    tie_bar = "▰" * int(tie_pct / 25 * 12) + "▱" * (12 - int(tie_pct / 25 * 12))

    prev_label, prev_emoji = label_bac(session.get("bac_prev_result",""))
    prev_seq = session.get("bac_prev_seq","")
    seq_display = " ".join(list(prev_seq[-10:])) if prev_seq else "---"

    sigs = predict.get("signals", {}) if predict else {}
    sig_lines = ""
    sig_names = {"api":"📡 API","bat_nhip":"🎵 Bắt nhịp","theo_bet":"📈 Theo bệt","be_bet":"✂️ Bẻ bệt"}
    for k, v in sigs.items():
        lbl, _ = label_bac(v.get("label",""))
        sig_lines += f"  {sig_names.get(k,k)}: *{lbl}* {v.get('conf_pct',0):.0f}%\n"

    return (
        f"╔══════════════════════╗\n"
        f"   {auto_tag}🃏 *KANO AI* · Baccarat\n"
        f"╚══════════════════════╝\n\n"
        f"{sep}\n"
        f"🎰 *BÀN: {ban_id}*\n"
        f"{sep}\n"
        f"📡 *DỰ ĐOÁN*\n"
        f"{pred_emoji} Kết quả: *{pred_label}*\n\n"
        f"📊 *ĐỘ TIN CẬY*\n"
        f"`{bar}` *{conf:.1f}%*\n\n"
        f"🟢 *TỈ LỆ HÒA (AI Tie)*\n"
        f"`{tie_bar}` *{tie_pct:.1f}%*\n\n"
        f"{sep}\n"
        f"🤖 *TÍN HIỆU AI*\n"
        f"{sig_lines}"
        f"{sep}\n"
        f"📜 *PHIÊN TRƯỚC*\n"
        f"{prev_emoji} Kết quả: *{prev_label}*\n"
        f"🎴 Cầu gần: `{seq_display}`\n\n"
        f"{sep}\n"
        f"🕒 {now}\n"
        f"{'🟢 *AI ĐANG HOẠT ĐỘNG*' if is_ready else '🔴 *ĐANG CHỜ DỮ LIỆU*'}"
    )

def new_bac_session(chat_id, ban_id, auto_mode=False):
    return {
        "active":         True,
        "auto_mode":      auto_mode,
        "game":           "baccarat",
        "chat_id":        chat_id,
        "message_id":     None,
        "bac_ban":        ban_id,
        "bac_prev_result": "",
        "bac_prev_seq":   "",
        "bac_known_seq":  None,
        "last_predict":   None,
    }

# ── Show game area (updated) ──────────────────────────────────────────────────

async def show_baccarat_tables(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách tất cả bàn Baccarat."""
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    ensure_user(uid, uname)
    if not user_data[uid].get("key"):
        if hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.edit_message_text(
                "❌ *Bạn chưa có KEY VIP!*\n\nMua key tại mục `🔑 MUA GÓI KEY`.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ *Bạn chưa có KEY VIP!*\n\nMua key tại mục `🔑 MUA GÓI KEY`.",
                parse_mode="Markdown"
            )
        return

    tables = fetch_baccarat_all()
    if not tables:
        query = getattr(update, "callback_query", None)
        txt = "❌ Không lấy được danh sách bàn. Thử lại sau."
        if query: await query.edit_message_text(txt)
        else: await update.message.reply_text(txt)
        return

    rows = []
    sorted_tables = sorted(tables.keys())
    for i in range(0, len(sorted_tables), 3):
        row = []
        for ban_id in sorted_tables[i:i+3]:
            info = tables[ban_id]
            status = "✅" if info.get("trang_thai") == "Thành công" else "⚠️"
            row.append(InlineKeyboardButton(
                f"{status} {ban_id}",
                callback_data=f"bac_ban_{ban_id}"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")])

    text = f"🃏 *BACCARAT — CHỌN BÀN*\n\n✅ Đang hoạt động  ⚠️ Chưa có dữ liệu\n\nTổng: *{len(tables)} bàn*"
    query = getattr(update, "callback_query", None)
    if query:
        await query.edit_message_text(text, parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(rows))
    else:
        await update.message.reply_text(text, parse_mode="Markdown",
                                         reply_markup=InlineKeyboardMarkup(rows))

async def cb_bac_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User chọn 1 bàn cụ thể."""
    query  = update.callback_query
    await query.answer()
    uid    = update.effective_user.id
    uname  = update.effective_user.username or ""
    ensure_user(uid, uname)
    ban_id = query.data.replace("bac_ban_", "")

    if not user_data[uid].get("key"):
        await query.edit_message_text(
            "❌ *Bạn chưa có KEY VIP!*", parse_mode="Markdown"
        )
        return

    await query.edit_message_text(f"✅ Đang khởi động bàn *{ban_id}*...", parse_mode="Markdown")

    async def send_fn(text, kb):
        return await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text, reply_markup=kb, parse_mode="Markdown"
        )
    await _launch_baccarat(uid, query.message.chat_id, context, send_fn, ban_id, auto_mode=False)

async def _launch_baccarat(uid, chat_id, context, send_fn, ban_id, auto_mode=False):
    """Khởi động session Baccarat cho 1 bàn."""
    _cancel_job(context, uid)
    session = new_bac_session(chat_id, ban_id, auto_mode=auto_mode)
    user_sessions[uid] = session

    ban_data = fetch_baccarat_ban(ban_id)
    predict  = None
    if ban_data:
        ket_qua = ban_data.get("ket_qua","")
        session["bac_prev_seq"]    = ket_qua
        session["bac_known_seq"]   = ket_qua
        session["bac_prev_result"] = ket_qua[-1] if ket_qua else ""
        predict = baccarat_predict_local(
            ket_qua,
            ban_data.get("du_doan","B"),
            ban_data.get("do_tin_cay", 50)
        )
    session["last_predict"] = predict

    kb   = BAC_AUTO_KB if auto_mode else BAC_GAME_KB
    text = build_bac_ui(session, predict)
    msg  = await send_fn(text, kb)
    session["message_id"] = msg.message_id
    session["chat_id"]    = msg.chat_id

    if context.job_queue:
        context.job_queue.run_repeating(
            bac_auto_job,
            interval=2,
            first=2,
            name=f"auto_{uid}",
            user_id=uid,
        )

async def bac_auto_job(context: ContextTypes.DEFAULT_TYPE):
    """Poll API mỗi 2s, gửi tin mới khi cầu thay đổi."""
    uid     = context.job.user_id
    session = user_sessions.get(uid)
    if not session or not session.get("active") or session.get("game") != "baccarat":
        context.job.schedule_removal()
        return

    ban_id    = session.get("bac_ban")
    auto_mode = session.get("auto_mode", False)
    kb        = BAC_AUTO_KB if auto_mode else BAC_GAME_KB

    ban_data = fetch_baccarat_ban(ban_id)
    if not ban_data:
        return

    ket_qua     = ban_data.get("ket_qua","")
    known_seq   = session.get("bac_known_seq")
    is_new      = (known_seq is not None and ket_qua != known_seq and len(ket_qua) > len(known_seq or ""))

    if is_new:
        session["bac_prev_result"] = ket_qua[-1] if ket_qua else ""
        session["bac_prev_seq"]    = ket_qua
        session["bac_known_seq"]   = ket_qua

        predict = baccarat_predict_local(
            ket_qua,
            ban_data.get("du_doan","B"),
            ban_data.get("do_tin_cay", 50)
        )
        session["last_predict"] = predict
        text = build_bac_ui(session, predict)
        try:
            msg = await context.bot.send_message(
                chat_id=session["chat_id"],
                text=text, reply_markup=kb, parse_mode="Markdown"
            )
            session["message_id"] = msg.message_id
            log.info(f"Baccarat uid={uid} ban={ban_id} gửi dự đoán mới")
        except Exception as e:
            log.error(f"bac_auto_job send lỗi uid={uid}: {e}")
    else:
        if known_seq is None:
            session["bac_known_seq"] = ket_qua
        text = build_bac_ui(session, session.get("last_predict"))
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
                log.error(f"bac_auto_job edit lỗi uid={uid}: {e}")

async def do_start_auto_bac(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    session = user_sessions.get(uid)
    if not session or session.get("game") != "baccarat":
        await update.message.reply_text("⚠️ Vui lòng chọn bàn Baccarat trước.", parse_mode="Markdown")
        return
    ban_id = session.get("bac_ban","---")
    await update.message.reply_text(
        f"🤖 *AUTO BAC đã bật!*\n\nBàn: *{ban_id}*\nBot tự gửi dự đoán mỗi khi có kết quả mới.",
        parse_mode="Markdown", reply_markup=BAC_AUTO_KB
    )
    async def send_fn(text, kb):
        return await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    await _launch_baccarat(uid, update.message.chat_id, context, send_fn, ban_id, auto_mode=True)

async def do_stop_bac(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    _deactivate(uid); _cancel_job(context, uid)
    await update.message.reply_text(
        "⏹ *Đã dừng dự đoán Baccarat.*",
        parse_mode="Markdown", reply_markup=BAC_GAME_KB
    )

async def do_stop_auto_bac(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    _deactivate(uid); _cancel_job(context, uid)
    await update.message.reply_text(
        "⏹ *Đã dừng AUTO Baccarat.*",
        parse_mode="Markdown", reply_markup=BAC_GAME_KB
    )

# ===== BROADCAST =====
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /broadcast <nội dung> — gửi thông báo đến tất cả user."""
    uid   = update.effective_user.id
    uname = update.effective_user.username or ""
    if not is_admin(uid, uname):
        await update.message.reply_text("❌ Chỉ admin mới dùng được lệnh này.")
        return

    if not context.args:
        await update.message.reply_text(
            "📋 *Cú pháp:* /broadcast Nội dung thông báo\n"
            "Ví dụ: /broadcast Hệ thống bảo trì 30 phút, vui lòng chờ!",
            parse_mode="Markdown"
        )
        return

    content = " ".join(context.args)
    now     = datetime.now().strftime("%d/%m/%Y %H:%M")
    sep     = "\u2501" * 22
    text    = (
        f"\U0001f4e2 *THONG BAO TU KANO AI*\n"
        f"{sep}\n\n"
        f"{content}\n\n"
        f"{sep}\n"
        f"\U0001f551 {now}"
    )

    # Gửi đến tất cả user đã từng dùng bot
    all_uids = list(user_data.keys())
    sent = 0; failed = 0
    status_msg = await update.message.reply_text(
        f"📤 Đang gửi đến {len(all_uids)} user...", parse_mode="Markdown"
    )
    for target_uid in all_uids:
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=text,
                parse_mode="Markdown"
            )
            sent += 1
        except Exception as e:
            log.warning(f"Broadcast fail uid={target_uid}: {e}")
            failed += 1

    await status_msg.edit_text(
        f"Broadcast xong!\nDa gui: {sent}\nThat bai: {failed}\nTong: {len(all_uids)}",
        parse_mode="Markdown"
    )

# ===== MAIN =====
def main():
    # Validate token trước khi chạy
    token = os.environ.get("BOT_TOKEN", "")
    if not token or token == "PASTE_TOKEN_MOI_VAO_ENV_RENDER":
        raise RuntimeError(
            "BOT_TOKEN chưa được set! "
            "Vào Render → Environment → thêm BOT_TOKEN = <token mới từ BotFather>"
        )

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("active",  cmd_active))
    app.add_handler(CommandHandler("naptien",    cmd_naptien))
    app.add_handler(CommandHandler("broadcast",  cmd_broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))

    app.add_handler(CallbackQueryHandler(cb_game_betvip,      pattern="^game_betvip$"))
    app.add_handler(CallbackQueryHandler(back_main,           pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(buy_key,             pattern="^buykey_"))
    app.add_handler(CallbackQueryHandler(tan_thu_used_notice, pattern="^tan_thu_used$"))
    app.add_handler(CallbackQueryHandler(generate_qr,         pattern="^nap_"))
    # Baccarat handlers
    app.add_handler(CallbackQueryHandler(show_baccarat_tables, pattern="^game_baccarat$"))
    app.add_handler(CallbackQueryHandler(cb_bac_ban,           pattern="^bac_ban_"))

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()

    log.info("Bot Kano AI đang chạy...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
