import os
import re
import random
import logging
from typing import Dict, List, Tuple, Optional

from aiohttp import web  # <— add this
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, filters
)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("shot-cook-bot")

# ---------- Config: deadly sticker(s) ----------
TARGET_STICKER_FILE_IDS = {
    "CAACAgUAAxkBAAMCaLrKaM8M05mcNbW1hwzrRHWRyDIAAoACAALZkE0HXDbU1x9tb6o2BA"
}
TARGET_STICKER_UNIQUE_IDS = {"AgADgAIAAtmQTQc"}  # <-- make this a SET, not a string

# ---------- Typist history per chat (compact, no consecutive duplicates) ----------
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
        await update.effective_chat.send_message(
            f"{shooter_name} fired, but there’s no one else to shoot. 🫥"
        )
        return

    _, target_name = target
    await update.effective_chat.send_message(f"🔫 {shooter_name} shot {target_name}! 💀")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Error: %s", context.error)

# ---------- Main ----------
def main():
    token = os.getenv("BOT_TOKEN")
    port = int(os.getenv("PORT", 10000))  # Render provides this
    url = (os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")  # ensure no trailing slash

    if not token:
        raise RuntimeError("Set BOT_TOKEN env var with your Telegram bot token.")
    if not url:
        raise RuntimeError("Set RENDER_EXTERNAL_URL env var to your Render app URL (e.g., https://your-app.onrender.com).")

    app = ApplicationBuilder().token(token).build()

    # Handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.Sticker.ALL & ~filters.StatusUpdate.ALL, text_handler))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_handler))
    app.add_error_handler(error_handler)

    # --- Health check web app for Render (returns 200 on "/") ---
    web_app = web.Application()
    async def health(_request):
        return web.Response(text="ok")
    web_app.router.add_get("/", health)

    # log.info("Bot starting. Binding 0.0.0.0:%s and webhook to %s/<secret>", port, url)

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=token,                 # path handler inside our app
        webhook_url=f"{url}/{token}",   # public HTTPS URL that Telegram calls
        drop_pending_updates=True,
        web_app=web_app                 # <-- serves "/" for Render health check
    )

if __name__ == "__main__":
    main()
