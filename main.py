import os
import re
import asyncio
import tempfile
import logging
from pathlib import Path
from urllib.parse import urlparse

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

# =========================
# إعدادات البوت
# =========================

BOT_TOKEN =  "8746342226:AAFyiCo5Dd7ZIdy78rni0NKnmMC0r6NI_bs"
ADMIN_ID = 5119721137

# الحد الأقصى التقريبي للملف بالميجابايت
MAX_FILE_MB = 45
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

# مجلد الملفات المؤقتة
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "almuneer_downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("almuneer")

# عدد المستخدمين الذين استخدموا البوت خلال تشغيله الحالي
USERS = set()
BLOCKED = set()

# الروابط المسموح بمحاولة معالجتها عبر yt-dlp
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def extract_url(text: str):
    """استخراج أول رابط من الرسالة."""
    match = URL_PATTERN.search(text or "")
    if not match:
        return None

    url = match.group(0).rstrip(".,!?)\]}")
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return None

    if not parsed.hostname:
        return None

    # حماية أساسية من عناوين الشبكات الداخلية.
    host = parsed.hostname.lower()
    if (
        host == "localhost"
        or host.endswith(".local")
        or host in {"127.0.0.1", "0.0.0.0", "::1"}
    ):
        return None

    return url


def site_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    host = host.removeprefix("www.")

    names = {
        "youtube.com": "YouTube",
        "youtu.be": "YouTube",
        "tiktok.com": "TikTok",
        "instagram.com": "Instagram",
        "facebook.com": "Facebook",
        "fb.watch": "Facebook",
        "x.com": "X",
        "twitter.com": "X",
        "likee.video": "Likee",
        "discord.com": "Discord",
        "discordapp.com": "Discord",
        "snapchat.com": "Snapchat",
        "shahid.mbc.net": "Shahid",
        "whatsapp.com": "WhatsApp",
        "wa.me": "WhatsApp",
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
        "سأحاول تحديد الموقع والخيارات المتاحة.\n\n"
        "اضغط /help لعرض التعليمات."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📥 طريقة الاستخدام:\n"
        "1. أرسل رابط المحتوى.\n"
        "2. اختر فيديو أو صوت إذا كان الخيار متاحًا.\n"
        "3. انتظر تجهيز الملف.\n\n"
        "ملاحظات:\n"
        f"• الحد التقريبي للملف: {MAX_FILE_MB} ميجابايت.\n"
        "• الروابط الخاصة أو التي تتطلب تسجيل دخول قد لا تعمل.\n"
        "• بعض المواقع لا يدعمها محرك التنزيل.\n"
        "• لا ترسل روابط تتطلب تجاوز حماية أو صلاحيات وصول."
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("هذا الأمر للمشرف فقط.")
        return

    await update.message.reply_text(
        f"🛠 لوحة المشرف\n"
        f"المستخدمون خلال التشغيل الحالي: {len(USERS)}\n"
        f"المستخدمون المحظورون: {len(BLOCKED)}\n"
        f"حد الملف: {MAX_FILE_MB} MB"
    )


async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم: /block USER_ID")
        return

    user_id = int(context.args[0])
    BLOCKED.add(user_id)
    await update.message.reply_text(f"تم حظر المستخدم {user_id} خلال التشغيل الحالي.")


async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم: /unblock USER_ID")
        return

    user_id = int(context.args[0])
    BLOCKED.discard(user_id)
    await update.message.reply_text(f"تم رفع الحظر عن {user_id} خلال التشغيل الحالي.")


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
    """تشغيل yt-dlp خارج حلقة الأحداث."""
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
            duration = info.get("duration")
            duration_text = f"{int(duration)} ثانية" if duration else "غير معروف"
            await status.edit_text(
                f"📌 العنوان: {title}\n"
                f"🌐 الموقع: {site_name(url)}\n"
                f"⏱ المدة: {duration_text}\n"
                "يمكنك إرسال الرابط مجددًا لاختيار نوع التنزيل."
            )
        except Exception as exc:
            logger.warning("Info failed: %s", exc)
            await status.edit_text(
                "تعذر قراءة معلومات الرابط.\n"
                "قد يكون الموقع غير مدعوم أو الرابط خاصًا أو يتطلب تسجيل الدخول."
            )
        return

    audio = action == "audio"
    status_message = await query.edit_message_text(
        "⏳ جارٍ تجهيز الصوت..." if audio else "⏳ جارٍ تنزيل المحتوى..."
    )

    file_path = None
    try:
        await context.bot.send_chat_action(
            chat_id=query.message.chat_id,
            action=ChatAction.UPLOAD_DOCUMENT,
        )

        info = await asyncio.to_thread(run_extract, url, True, audio)

        title = safe_filename(info.get("title", "media"))
        file_id = info.get("id")

        candidates = [
            p for p in DOWNLOAD_DIR.glob("*")
            if p.is_file()
        ]
        if file_id:
            matches = [p for p in candidates if str(file_id) in p.name]
            if matches:
                candidates = matches

        if not candidates:
            raise RuntimeError("لم يُعثر على الملف بعد التنزيل.")

        file_path = max(candidates, key=lambda p: p.stat().st_mtime)

        if file_path.stat().st_size > MAX_FILE_BYTES:
            raise RuntimeError(f"الملف أكبر من الحد التقريبي {MAX_FILE_MB} MB.")

        caption = f"✅ {title}\n🌐 {site_name(url)}"

        with file_path.open("rb") as media:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=media,
                filename=file_path.name,
                caption=caption[:1000],
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )

        await status_message.edit_text("✅ تم إرسال الملف.")

    except Exception as exc:
        error = str(exc)
        logger.exception("Download failed")

        if "ffmpeg" in error.lower():
            message = (
                "تعذر تحويل الصوت إلى MP3 لأن FFmpeg غير مثبت أو غير متاح.\n"
                "جرّب خيار الفيديو أو ثبّت FFmpeg على بيئة التشغيل."
            )
        elif "Unsupported URL" in error:
            message = (
                "هذا الرابط غير مدعوم حاليًا من محرك التنزيل."
            )
        elif "Sign in" in error or "login" in error.lower():
            message = (
                "الموقع يطلب تسجيل الدخول أو الوصول المصرّح به."
            )
        elif "Unable to download webpage" in error or "resolve" in error.lower():
            message = (
                "تعذر الوصول إلى الموقع. تحقق من الإنترنت والرابط، "
                "أو جرّب لاحقًا."
            )
        elif "max-filesize" in error.lower() or "larger than" in error.lower():
            message = f"الملف أكبر من الحد المسموح به ({MAX_FILE_MB} MB)."
        else:
            message = (
                "فشل التنزيل. قد يكون الرابط غير مدعوم أو خاصًا "
                "أو أن الموقع غيّر طريقة الوصول."
            )

        await status_message.edit_text("❌ " + message)

    finally:
        # حذف الملفات المؤقتة بعد محاولة الإرسال
        try:
            if file_path and file_path.exists():
                file_path.unlink()
        except OSError:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled bot error: %s", context.error)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise SystemExit(
            "ضع توكن البوت في BOT_TOKEN داخل الكود، "
            "أو عرّف متغير البيئة BOT_TOKEN."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("block", block_command))
    app.add_handler(CommandHandler("unblock", unblock_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url))
    app.add_error_handler(error_handler)

    print("البوت يعمل ✅")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
