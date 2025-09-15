import os
import re
import random
import logging
from typing import Dict, List, Tuple, Optional

from telegram.constants import MessageEntityType
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

SLUT_VARIANT = re.compile(r"\bslut\b", re.IGNORECASE)

# chat_id -> { mention_key -> count }
slut_counts: Dict[int, Dict[str, int]] = {}


# ----- Logging -----
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bangbangchef-bot")

# ====== CONFIG ======
BOT_TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "https://bangbangchef.onrender.com").rstrip("/")
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "telegram")  # Render health check path should match this.
SECRET_TOKEN = os.environ.get("SECRET_TOKEN")  # optional but recommended

# 1) Kitchen keywords -> funny replies
COOK_VARIANTS = re.compile(r"\b(cook|cooking|cooked|cooks)\b", re.IGNORECASE)
COOK_RESPONSES = [
    "🔥 Someone’s about to burn down the kitchen!",
    "🍔 Who gave them the spatula?!",
    "👨‍🍳 Is it hot in here, or is someone cooking up trouble?",
    "🍳 Uh oh… I smell something burning.",
    "🥘 The stove’s on and the fire alarm’s about to go off!",
    "🔥🔥🔥 Gordon Ramsay is shaking.",
    "🧯 Quick, get the fire extinguisher!",
    "🍜 Someone’s cooking… and it’s getting *spicy*!",
]

# 2) Bleach sticker triggers
BLEACH_STICKER_FILE_IDS = {
    "CAACAgUAAyEFAASUR62oAAIS3GjENB-aYb1fXqy1iO94Ky_6DVvTAALZDAACgmaBVc-OOmpJBG-sNgQ"
}
BLEACH_STICKER_UNIQUE_IDS = {"AgAD2QwAAoJmgVU"}

# 3) Gun/shot trigger — fill these with YOUR target sticker IDs
TARGET_STICKER_FILE_IDS = {"CAACAgUAAxkBAAMhaMRIECrBlF8NJmL1Qie4-FR3m1AAAoACAALZkE0HXDbU1x9tb6o2BA"}       # e.g. {"<your_gun_sticker_file_id>"}
TARGET_STICKER_UNIQUE_IDS = {"AgADgAIAAtmQTQc"}    # e.g. {"<your_gun_sticker_unique_id>"}

# Track the last typists per chat (for target selection)
typist_history: Dict[int, List[Tuple[int, str]]] = {}

# ----- Helper funcs -----
def display_name(u) -> str:
    if getattr(u, "username", None):
        return f"@{u.username}"
    name = (u.first_name or "").strip()
    if getattr(u, "last_name", None):
        name = f"{name} {u.last_name}".strip()
    return name or "Someone"

def push_typist(chat_id: int, entry: Tuple[int, str], limit: int = 50) -> None:
    hist = typist_history.setdefault(chat_id, [])
    if not hist or hist[-1][0] != entry[0]:
        hist.append(entry)
        if len(hist) > limit:
            del hist[:-limit]

def is_bleach_sticker(update: Update) -> bool:
    if not update.message or not update.message.sticker:
        return False
    s = update.message.sticker
    fid, fuid = s.file_id, s.file_unique_id
    log.info("Sticker seen (bleach-check): file_id=%s file_unique_id=%s", fid, fuid)
    return (fid in BLEACH_STICKER_FILE_IDS) or (fuid in BLEACH_STICKER_UNIQUE_IDS)

def is_target_sticker(update: Update) -> bool:
    if not update.message or not update.message.sticker:
        return False
    s = update.message.sticker
    fid, fuid = s.file_id, s.file_unique_id
    log.info("Sticker seen (gun-check): file_id=%s file_unique_id=%s", fid, fuid)
    return (fid in TARGET_STICKER_FILE_IDS) or (fuid in TARGET_STICKER_UNIQUE_IDS)

# ----- Handlers -----
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None or not update.message:
        return
    user = update.effective_user
    if user.is_bot:
        return

    if update.message.text:
        # record typist for shot logic
        push_typist(update.effective_chat.id, (user.id, display_name(user)))

        # 1) Kitchen replies
        if COOK_VARIANTS.search(update.message.text):
            await update.effective_chat.send_message(random.choice(COOK_RESPONSES))
    
    # 1a) "@someone" + "slut" -> increment their 'classpart slut' and report total
    if SLUT_VARIANT.search(update.message.text):
        targets = mention_key_and_label_from_entities(update.message)
        if targets:  # only count if someone was @mentioned
            lines = []
            for key, label in targets:
                total = inc_slut_count(update.effective_chat.id, key, 1)
                lines.append(f"+1 to classpart slut for {label} — total: {total}")
            await update.effective_chat.send_message("\n".join(lines))


async def sticker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    # 2) Bleach reaction
    if is_bleach_sticker(update):
        await update.effective_chat.send_message("🚫 Stop, he is drinking bleach!!!")
        return

    # 3) Gun/shot logic
    if not is_target_sticker(update):
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

def mention_key_and_label_from_entities(msg) -> List[Tuple[str, str]]:
    """
    Returns list of (key, label) for each mentioned user in the message.
    key is stable across messages; label is for display.
    - TEXT_MENTION: we key by user_id
    - MENTION (@username): we key by lowercased username
    """
    out: List[Tuple[str, str]] = []
    if not msg or not msg.entities:
        return out

    for ent in msg.entities:
        if ent.type == MessageEntityType.TEXT_MENTION and getattr(ent, "user", None):
            u = ent.user
            key = f"u:{u.id}"
            label = f"@{u.username}" if u.username else display_name(u)
            out.append((key, label))
        elif ent.type == MessageEntityType.MENTION:
            # slice the raw text for @username
            raw = msg.text[ent.offset: ent.offset + ent.length]
            username = raw.lstrip("@")
            key = f"n:{username.lower()}"
            label = f"@{username}"
            out.append((key, label))
    return out

def inc_slut_count(chat_id: int, key: str, inc: int = 1) -> int:
    per_chat = slut_counts.setdefault(chat_id, {})
    per_chat[key] = per_chat.get(key, 0) + inc
    return per_chat[key]


# ----- Webhook runner -----
def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(HTTPXRequest())
        .build()
    )

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_handler))
    app.add_error_handler(error_handler)

    port = int(os.environ.get("PORT", "10000"))  # Render sets PORT
    webhook_url = f"{BASE_URL}/{WEBHOOK_PATH}"

    log.info("Starting webhook on %s (listening 0.0.0.0:%s, path=/%s)", webhook_url, port, WEBHOOK_PATH)

    kwargs = {}
    if SECRET_TOKEN:
        kwargs["secret_token"] = SECRET_TOKEN

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=WEBHOOK_PATH,
        webhook_url=webhook_url,          # sets Telegram webhook automatically
        drop_pending_updates=True,
        **kwargs,
    )

if __name__ == "__main__":
    main()
