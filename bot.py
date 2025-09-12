import os
import re
import random
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Tuple, Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, filters
)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("shot-cook-bot")

# ---------- Config: sticker IDs ----------
TARGET_STICKER_FILE_IDS = {
    "CAACAgUAAxkBAAMCaLrKaM8M05mcNbW1hwzrRHWRyDIAAoACAALZkE0HXDbU1x9tb6o2BA"
}
TARGET_STICKER_UNIQUE_IDS = {"AgADgAIAAtmQTQc"}  # MUST be a set, not a str

# Add your BLEACH sticker IDs here after you log them once
BLEACH_STICKER_FILE_IDS = {"PUT_BLEACH_FILE_ID_HERE"}
BLEACH_STICKER_UNIQUE_IDS = {"PUT_BLEACH_UNIQUE_ID_HERE"}

# ---------- Typist history ----------
typist_history: Dict[int, List[Tuple[int, str]]] = {}

# ---------- Kitchen keywords ----------
COOK_VARIANTS = re.compile(r"\b(cook|cooked|cooks|cooking)\b", re.IGNORECASE)
COOK_RESPONSES = [
    "🔥 Someone’s about to burn down the kitchen!",
    "👨‍🍳 Is it hot in here, or is someone cooking up trouble?",
    "🍳 Uh oh… I smell something burning.",
    "🥘 The stove’s on and the fire alarm’s about to go off!",
    "🔥🔥🔥 Gordon Ramsay is shaking.",
    "🍔 Who gave them the spatula?!",
    "🧯 Quick, get the fire extinguisher!",
    "🍜 Someone’s cooking… and it’s getting *spicy*!",
]

# ---------- Helpers ----------
def display_name(u) -> str:
    if u.username:
        return f"@{u.username}"
    name = (u.first_name or "").strip()
    if u.last_name:
        name = f"{name} {u.last_name}".strip()
    return name or "Someone"

def push_typist(chat_id: int, entry: Tuple[int, str], limit: int = 50) -> None:
    hist = typist_history.setdefault(chat_id, [])
    if not hist or hist[-1][0] != entry[0]:
        hist.append(entry)
        if len(hist) > limit:
            del hist[:-limit]

def is_target_sticker(update: Update) -> bool:
    if not update.message or not update.message.sticker:
        return False
    s = update.message.sticker
    fid = s.file_id
    fuid = s.file_unique_id
    log.info("Sticker seen: file_id=%s file_unique_id=%s", fid, fuid)
    return (fid in TARGET_STICKER_FILE_IDS) or (fuid in TARGET_STICKER_UNIQUE_IDS)

def is_bleach_sticker(update: Update) -> bool:
    if not update.message or not update.message.sticker:
        return False
    s = update.message.sticker
    fid = s.file_id
    fuid = s.file_unique_id
    log.info("Sticker seen: file_id=%s file_unique_id=%s", fid, fuid)
    return (fid in BLEACH_STICKER_FILE_IDS) or (fuid in BLEACH_STICKER_UNIQUE_IDS)

# ---------- Handlers ----------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None or not update.message:
        return
    user = update.effective_user
    if user.is_bot:
        return
    if update.message.text:
        push_typist(update.effective_chat.id, (user.id, display_name(user)))
        if COOK_VARIANTS.search(update.message.text):
            await update.effective_chat.send_message(random.choice(COOK_RESPONSES))

async def sticker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Bleach reaction first
    if is_bleach_sticker(update):
        await update.effective_chat.send_message("🚫 Stop, he is drinking bleach!!!")
        return

    # Gun/shot logic
    if not is_target_sticker(update):
        return
    if update.effective_chat is None or update.effective_user is None:
        return

    chat_id = update.effective_chat.id
    shooter = update.effective_user
    shooter_name = display_name(shooter)

    hist = typist_history.get(chat_id, [])
    if not hist:
        await update.effective_chat.send_message("No targets yet—nobody typed before this sticker. 👀")
        return

    target: Optional[Tuple[int, str]] = None
    for uid, name in reversed(hist):
        if uid != shooter.id:
            target = (uid, name)
            break

    if target is None:
        await update.effective_chat.send_message(f"{shooter_name} fired, but there’s no one else to shoot. 🫥")
        return

    _, target_name = target
    await update.effective_chat.send_message(f"🔫 {shooter_name} shot {target_name}! 💀")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Error: %s", context.error)

# ---------- Tiny health HTTP server (so Render sees an open port) ----------
def start_health_server(port: int):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args):  # silence default logs
            return
    srv = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Health server listening on 0.0.0.0:%s", port)

# ---------- Main ----------
def main():
    token = os.getenv("BOT_TOKEN")
    port = int(os.getenv("PORT", 10000))  # Render provides PORT

    if not token:
        raise RuntimeError("Set BOT_TOKEN env var with your Telegram bot token.")

    # Bind an HTTP port so Render is happy
    start_health_server(port)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.Sticker.ALL & ~filters.StatusUpdate.ALL, text_handler))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_handler))
    app.add_error_handler(error_handler)

    log.info("Bot started (polling). Health check at GET / on port %s", port)
    app.run_polling()

if __name__ == "__main__":
    main()
