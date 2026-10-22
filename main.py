import os
import re
import asyncio
import tempfile
import logging
import threading
import traceback
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# إعدادات السيرفر الوهمي لـ Render
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is active and running 24/7! ✅"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

BOT_TOKEN ="8746342226:AAFyiCo5Dd7ZIdy78rni0NKnmMC0r6NI_bs"

MAX_FILE_MB = 50
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "almuneer_downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("almuneer")

USERS = set()
BLOCKED = set()

URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

def extract_url(text: str):
    match = URL_PATTERN.search(text or "")
    if not match:
        return None
    url = match.group(0).rstrip(".,!?)])}")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".local") or host in {"127.0.0.1", "0.0.0.0", "::1"}:
        return None
    return url

def site_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    host = host.removeprefix("www.")
    names = {
        "youtube.com": "YouTube", "youtu.be": "YouTube",
        "tiktok.com": "TikTok", "instagram.com": "Instagram",
        "facebook.com": "Facebook", "fb.watch": "Facebook",
        "x.com": "X", "twitter.com": "X",
        "snapchat.com": "Snapchat",
    }
    for domain, name in names.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "موقع غير معروف"

def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name or "media")
    return name[:100].strip() or "media"

def make_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 فيديو", callback_data="video"),
            InlineKeyboardButton("🎵 صوت MP3", callback_data="audio"),
        ],
        [
            InlineKeyboardButton("ℹ️ معلومات الرابط", callback_data="info"),
        ],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    USERS.add(user.id)
    await update.message.reply_text(
        "أهلاً بك في بوت التحميل \n\n"
        "أرسل رابطًا عامًا لمحتوى تريد تنزيله.\n"
        "سأحاول تحديد الموقع والخيارات المتاحة."
    )

async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text:
        return
    if user.id in BLOCKED:
        await message.reply_text("لا يمكنك استخدام البوت حاليًا.")
        return
    USERS.add(user.id)
    url = extract_url(message.text)
    if not url:
        await message.reply_text("أرسل رابطًا صحيحًا يبدأ بـ https:// أو http://")
        return
    context.user_data["url"] = url
    await message.reply_text(
        f"🔗 الموقع: {site_name(url)}\n\n"
        "اختر نوع المحتوى الذي تريد تجربته:",
        reply_markup=make_keyboard(),
    )

def ydl_options(download: bool, audio: bool = False):
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "restrictfilenames": True,
        "outtmpl": str(DOWNLOAD_DIR / "%(title).80s-%(id)s.%(ext)s"),
        "max_filesize": MAX_FILE_BYTES,
        "skip_download": not download,
    }
    if audio:
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        options["format"] = "best[filesize<45M]/best"
    return options

def run_extract(url: str, download: bool, audio: bool = False):
    with yt_dlp.YoutubeDL(ydl_options(download, audio)) as ydl:
        info = ydl.extract_info(url, download=download)
        if not info:
            raise RuntimeError("لم يرجع الموقع معلومات عن المحتوى.")
        if "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        if not info:
            raise RuntimeError("لم يتم العثور على محتوى قابل للمعالجة.")
        return info

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if not user or user.id in BLOCKED:
        await query.edit_message_text("لا يمكنك استخدام البوت حاليًا.")
        return
    url = context.user_data.get("url")
    if not url:
        await query.edit_message_text("أرسل الرابط من جديد.")
        return
    
    action = query.data
    if action == "info":
        status = await query.edit_message_text("🔎 أفحص الرابط...")
        try:
            info = await asyncio.to_thread(run_extract, url, False, False)
            title = safe_filename(info.get("title", "بدون عنوان"))
            await status.edit_text(f"📌 العنوان: {title}\n🌐 الموقع: {site_name(url)}")
        except Exception:
            await status.edit_text("تعذر قراءة معلومات الرابط.")
        return

    audio = action == "audio"
    status_message = await query.edit_message_text("⏳ جارٍ التنزيل...")
    file_path = None
    try:
        info = await asyncio.to_thread(run_extract, url, True, audio)
        title = safe_filename(info.get("title", "media"))
        file_id = info.get("id")
        candidates = [p for p in DOWNLOAD_DIR.glob("*") if p.is_file()]
        if file_id:
            matches = [p for p in candidates if str(file_id) in p.name]
            if matches:
                candidates = matches
        if not candidates:
            raise RuntimeError("لم يُعثر على الملف.")
        file_path = max(candidates, key=lambda p: p.stat().st_mtime)

        with file_path.open("rb") as media:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=media,
                filename=file_path.name,
                caption=f"✅ {title}\n🌐 {site_name(url)}"[:1000],
            )
        await status_message.edit_text("✅ تم إرسال الملف.")
    except Exception as exc:
        logger.exception("Download failed")
        await status_message.edit_text("❌ حدث خطأ أثناء التنزيل أو أن الملف كبير جداً.")
    finally:
        try:
            if file_path and file_path.exists():
                file_path.unlink()
        except OSError:
            pass

def main():
    try:
        if not BOT_TOKEN:
            print("خطأ حرج: لم يتم تعيين BOT_TOKEN في المتغيرات البيئية.")
            return

        # تشغيل سيرفر Flask في الخلفية
        threading.Thread(target=run_flask, daemon=True).start()

        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(button_handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url))

        print("البوت والسيرفر يعملان بنجاح ✅")
        app.run_polling(drop_pending_updates=False)
    except Exception as e:
        print(f"حدث استثناء أدى لتوقف التطبيق: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
