import os
import json
import logging
import requests
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)

# ── Cấu hình ──────────────────────────────────────────────────────────────────
TOKEN = "8891039285:AAEDqy69JNgeQqqMo7q2yTAs4yUX6CP0eZ4"
KANO_API_URL = os.getenv("KANO_API_URL", "https://kano-ai-predictor.onrender.com")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ── Menu chính ──────────────────────────────────────────────────────────────
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📊 Dự đoán T/X", callback_data="predict")],
    [InlineKeyboardButton("📈 Thống kê", callback_data="stats")],
    [InlineKeyboardButton("📜 Lịch sử 50", callback_data="history")],
    [InlineKeyboardButton("📊 Độ chính xác", callback_data="accuracy")],
    [InlineKeyboardButton("🔄 Cập nhật kết quả", callback_data="update")],
    [InlineKeyboardButton("ℹ️ Thông tin bot", callback_data="info")],
])

# ── Xử lý lệnh ──────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lệnh /start - Chạy trực tiếp không qua kiểm tra nhóm"""
    user = update.effective_user
    welcome = f"""
🎯 Chào {user.first_name}!

Tôi là Kano AI Bot - Công cụ dự đoán T/X thông minh.

📌 Sử dụng các nút bên dưới để điều hướng:
• Dự đoán - Xem dự đoán tiếp theo
• Thống kê - Xem hiệu suất bot
• Lịch sử - 50 kết quả gần nhất
• Cập nhật - Nhập kết quả thực tế

Bot hoạt động hoàn toàn miễn phí! 🚀
"""
    await update.message.reply_text(welcome, reply_markup=MAIN_MENU)


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị menu chính"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📋 **MENU CHÍNH**\n\nChọn chức năng bạn muốn sử dụng:",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )


async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gọi API dự đoán"""
    query = update.callback_query
    await query.answer()
    
    try:
        response = requests.get(f"{KANO_API_URL}/predict", timeout=10)
        data = response.json()
        
        label = data.get("label", "T")
        confidence = data.get("confidence", 0.5)
        reason = data.get("reason", "Không có lý do")
        votes = data.get("module_votes", {})
        
        vote_text = ""
        for module, info in votes.items():
            vote_text += f"• {module}: {info['label']} ({info['confidence']:.2%})\n"
        
        message = f"""
🔮 **DỰ ĐOÁN TIẾP THEO**

📌 **Kết quả:** `{label}`
🎯 **Độ tin cậy:** {confidence:.2%}

📊 **Bỏ phiếu module:**
{vote_text}

💡 **Lý do:** {reason}
"""
        await query.edit_message_text(message, parse_mode="Markdown", reply_markup=MAIN_MENU)
    except Exception as e:
        logger.error(f"Predict error: {e}")
        await query.edit_message_text(
            "❌ Lỗi kết nối đến server dự đoán!",
            reply_markup=MAIN_MENU
        )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem thống kê"""
    query = update.callback_query
    await query.answer()
    
    try:
        response = requests.get(f"{KANO_API_URL}/stats", timeout=10)
        data = response.json()
        
        message = f"""
📊 **THỐNG KÊ BOT**

📌 **Tổng dự đoán:** {data.get('total_predictions', 0)}
✅ **Dự đoán đúng:** {data.get('correct_predictions', 0)}
🎯 **Độ chính xác:** {data.get('accuracy', 0):.2%}
📜 **Lịch sử:** {data.get('history_len', 0)}/{data.get('history_limit', 500)}
⏱️ **Uptime:** {data.get('uptime_seconds', 0):.0f}s

**Trọng số module:**
"""
        weights = data.get('module_weights', {})
        for module, weight in weights.items():
            message += f"• {module}: {weight:.2%}\n"
            
        await query.edit_message_text(message, parse_mode="Markdown", reply_markup=MAIN_MENU)
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await query.edit_message_text(
            "❌ Lỗi lấy thống kê!",
            reply_markup=MAIN_MENU
        )


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem lịch sử 50 kết quả"""
    query = update.callback_query
    await query.answer()
    
    try:
        response = requests.get(f"{KANO_API_URL}/history", timeout=10)
        data = response.json()
        history_list = data.get("history", [])
        
        if not history_list:
            await query.edit_message_text(
                "📜 Chưa có lịch sử dự đoán!",
                reply_markup=MAIN_MENU
            )
            return
            
        t_count = history_list.count("T")
        x_count = history_list.count("X")
        total = len(history_list)
        
        message = f"""
📜 **50 KẾT QUẢ GẦN NHẤT**

{' '.join(history_list)}

📊 **Thống kê:**
• T: {t_count} ({t_count/total:.1%})
• X: {x_count} ({x_count/total:.1%})
• Tổng: {total}
"""
        await query.edit_message_text(message, reply_markup=MAIN_MENU)
    except Exception as e:
        logger.error(f"History error: {e}")
        await query.edit_message_text(
            "❌ Lỗi lấy lịch sử!",
            reply_markup=MAIN_MENU
        )


async def accuracy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem độ chính xác"""
    query = update.callback_query
    await query.answer()
    
    try:
        response = requests.get(f"{KANO_API_URL}/accuracy", timeout=10)
        data = response.json()
        acc = data.get("accuracy", 0)
        
        if acc >= 0.8:
            rating = "🌟 Xuất sắc!"
        elif acc >= 0.7:
            rating = "👍 Tốt"
        elif acc >= 0.6:
            rating = "📊 Trung bình"
        else:
            rating = "🔄 Cần cải thiện"
        
        message = f"""
🎯 **ĐỘ CHÍNH XÁC**

📊 **Accuracy:** {acc:.2%}

📝 **Đánh giá:** {rating}

💡 Bot đang học từ dữ liệu của bạn!
Hãy cập nhật kết quả thực tế để bot thông minh hơn.
"""
        await query.edit_message_text(message, parse_mode="Markdown", reply_markup=MAIN_MENU)
    except Exception as e:
        logger.error(f"Accuracy error: {e}")
        await query.edit_message_text(
            "❌ Lỗi lấy độ chính xác!",
            reply_markup=MAIN_MENU
        )


async def update_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hướng dẫn cập nhật kết quả"""
    query = update.callback_query
    await query.answer()
    
    message = """
🔄 **CẬP NHẬT KẾT QUẢ**

Gửi tin nhắn theo định dạng:

`/update T` hoặc `/update X`

Ví dụ:
• Kết quả thực tế là T → gửi `/update T`
• Kết quả thực tế là X → gửi `/update X`

⚠️ Bot sẽ học từ kết quả bạn gửi để cải thiện độ chính xác!
"""
    await query.edit_message_text(message, parse_mode="Markdown", reply_markup=MAIN_MENU)


async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lệnh /update T/X"""
    text = update.message.text.strip()
    parts = text.split()
    
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ Sai định dạng! Hãy gửi: `/update T` hoặc `/update X`",
            parse_mode="Markdown"
        )
        return
        
    label = parts[1].upper()
    if label not in ["T", "X"]:
        await update.message.reply_text(
            "❌ Label phải là T hoặc X!",
            parse_mode="Markdown"
        )
        return
    
    try:
        payload = {"label": label}
        response = requests.post(
            f"{KANO_API_URL}/update",
            json=payload,
            timeout=10
        )
        data = response.json()
        
        if data.get("ok"):
            await update.message.reply_text(
                f"✅ Đã cập nhật kết quả: `{label}`\n\n"
                f"📊 Accuracy mới: {data['stats']['accuracy']:.2%}",
                parse_mode="Markdown",
                reply_markup=MAIN_MENU
            )
        else:
            await update.message.reply_text(
                f"❌ Lỗi: {data.get('error', 'Không xác định')}",
                reply_markup=MAIN_MENU
            )
    except Exception as e:
        logger.error(f"Update error: {e}")
        await update.message.reply_text(
            "❌ Lỗi kết nối đến server!",
            reply_markup=MAIN_MENU
        )


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thông tin bot"""
    query = update.callback_query
    await query.answer()
    
    message = """
ℹ️ **THÔNG TIN BOT**

🤖 **Tên:** Kano AI Prediction Bot
📌 **Phiên bản:** v4.1
🧠 **AI Engine:** GradientBoosting + MetaLearner

**Các module dự đoán:**
• Pattern Engine - Nhận diện mẫu
• Markov Engine - Chuỗi Markov
• Streak Engine - Phân tích chuỗi
• Frequency Engine - Tần suất
• GradientBoost - AI học sâu

⚡ **Tính năng:**
✓ Dự đoán T/X
✓ Học từ kết quả
✓ Thống kê chi tiết
✓ Miễn phí 100%
"""
    await query.edit_message_text(message, parse_mode="Markdown", reply_markup=MAIN_MENU)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn văn bản"""
    text = update.message.text
    if text.startswith("/update"):
        await handle_update(update, context)
    else:
        await update.message.reply_text(
            "❓ Không hiểu lệnh! Hãy dùng /start để xem menu.",
            reply_markup=MAIN_MENU
        )


# ── Khởi tạo bot ────────────────────────────────────────────────────────────

def main():
    """Khởi chạy bot"""
    application = Application.builder().token(TOKEN).build()
    
    # Đăng ký handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    application.add_handler(CallbackQueryHandler(predict, pattern="^predict$"))
    application.add_handler(CallbackQueryHandler(stats, pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(history, pattern="^history$"))
    application.add_handler(CallbackQueryHandler(accuracy, pattern="^accuracy$"))
    application.add_handler(CallbackQueryHandler(update_result, pattern="^update$"))
    application.add_handler(CallbackQueryHandler(info, pattern="^info$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 Bot đang chạy...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
