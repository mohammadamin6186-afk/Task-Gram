import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
from decimal import Decimal, InvalidOperation
from functools import wraps
from threading import Thread

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "Task_Gram_with_ads_bot").strip().lstrip("@")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "task_gram_game").strip().lstrip("@")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/") + "/"

ADSGRAM_BLOCK_ID = os.getenv("ADSGRAM_BLOCK_ID", "").strip()

REWARD_PER_AD = Decimal(os.getenv("REWARD_PER_AD", "0.0005"))
MIN_WITHDRAWAL = Decimal(os.getenv("MIN_WITHDRAWAL", "0.5"))
REFERRAL_POINTS_PER_REF = int(os.getenv("REFERRAL_POINTS_PER_REF", "1"))

PORT = int(os.getenv("PORT", "8080"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "task_gram.sqlite3")

app = Flask(__name__, static_folder="web", static_url_path="")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def db():
    con = sqlite3.connect(DATABASE_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            gram_address TEXT NOT NULL DEFAULT '',
            balance TEXT NOT NULL DEFAULT '0',
            referral_code TEXT NOT NULL UNIQUE,
            referred_by INTEGER,
            referral_points INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ad_sessions (
            id TEXT PRIMARY KEY,
            telegram_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            claimed_at INTEGER,
            status TEXT NOT NULL DEFAULT 'open'
        );

        CREATE TABLE IF NOT EXISTS ad_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            session_id TEXT NOT NULL UNIQUE,
            amount TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL,
            amount TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL
        );
        """
    )
    con.commit()
    con.close()


def now():
    return int(time.time())


def get_user(user_id):
    con = db()
    row = con.execute(
        "SELECT * FROM users WHERE telegram_id=?", (int(user_id),)
    ).fetchone()
    con.close()
    return row


def create_or_update_user(user_id, username="", first_name=""):
    con = db()
    row = con.execute(
        "SELECT * FROM users WHERE telegram_id=?", (int(user_id),)
    ).fetchone()
    if row is None:
        referral_code = str(user_id)
        con.execute(
            """
            INSERT INTO users
            (telegram_id, username, first_name, referral_code, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(user_id), username or "", first_name or "", referral_code, now(), now()),
        )
    else:
        con.execute(
            """
            UPDATE users
            SET username=?, first_name=?, updated_at=?
            WHERE telegram_id=?
            """,
            (username or "", first_name or "", now(), int(user_id)),
        )
    con.commit()
    con.close()


def decimal_string(value):
    return format(Decimal(value).quantize(Decimal("0.00000001")), "f")


# ---------------------------------------------------------------------------
# Telegram channel membership
# ---------------------------------------------------------------------------

async def is_channel_member(user_id: int) -> bool:
    if not BOT_TOKEN:
        return False

    try:
        bot = Bot(BOT_TOKEN)
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", int(user_id))
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        }
    except Exception:
        return False


def join_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📢 Join Channel",
                    url=f"https://t.me/{CHANNEL_USERNAME}",
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Check Membership",
                    callback_data="check_membership",
                )
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["💸 Withdraw", "👥 Referral"],
            ["🎁 Earn More", "💰 Balance"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


async def show_main_menu(message):
    await message.reply_text(
        "🔥 Task GRAM\n\nChoose an option:",
        reply_markup=main_keyboard(),
    )


# ---------------------------------------------------------------------------
# Referral
# ---------------------------------------------------------------------------

async def process_referral(user_id: int, referral_code: str):
    referral_code = (referral_code or "").strip()
    if not referral_code:
        return

    con = db()
    try:
        current = con.execute(
            "SELECT referred_by FROM users WHERE telegram_id=?", (int(user_id),)
        ).fetchone()

        # A user can only be credited once.
        if current is None or current["referred_by"] is not None:
            return

        if referral_code == str(user_id):
            return

        referrer = con.execute(
            "SELECT telegram_id FROM users WHERE referral_code=?",
            (referral_code,),
        ).fetchone()

        if not referrer:
            return

        con.execute(
            "UPDATE users SET referred_by=?, updated_at=? WHERE telegram_id=?",
            (int(referrer["telegram_id"]), now(), int(user_id)),
        )
        con.execute(
            """
            UPDATE users
            SET referral_points=referral_points+?, updated_at=?
            WHERE telegram_id=?
            """,
            (REFERRAL_POINTS_PER_REF, now(), int(referrer["telegram_id"])),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Bot handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    create_or_update_user(user.id, user.username, user.first_name)

    # /start ref_123
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            await process_referral(user.id, arg[4:])

    # Every /start asks for the address again, but never resets balance/points.
    member = await is_channel_member(user.id)
    if not member:
        await update.message.reply_text(
            "🔒 Please join our channel first.",
            reply_markup=join_keyboard(),
        )
        context.user_data["awaiting_address"] = False
        return

    context.user_data["awaiting_address"] = True
    await update.message.reply_text("send your Ton(Gram) address")


async def check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    member = await is_channel_member(query.from_user.id)
    if not member:
        await query.edit_message_text(
            "🔒 Please join our channel first.",
            reply_markup=join_keyboard(),
        )
        return

    context.user_data["awaiting_address"] = True
    await query.edit_message_text("send your Ton(Gram) address")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()

    create_or_update_user(user.id, user.username, user.first_name)

    # Address collection has priority.
    if context.user_data.get("awaiting_address"):
        if not looks_like_gram_address(text):
            await update.message.reply_text(
                "❌ Please send a valid Ton(Gram) address."
            )
            return

        con = db()
        # IMPORTANT: only address is changed. Balance and referral points remain.
        con.execute(
            """
            UPDATE users
            SET gram_address=?, updated_at=?
            WHERE telegram_id=?
            """,
            (text, now(), int(user.id)),
        )
        con.commit()
        con.close()

        context.user_data["awaiting_address"] = False
        await show_main_menu(update.message)
        return

    row = get_user(user.id)
    if row is None:
        await update.message.reply_text("Please use /start first.")
        return

    # Balance
    if text == "💰 Balance":
        await update.message.reply_text(
            f"🤴User : {row['first_name']}\n\n"
            f"💰Your Balance : {decimal_string(row['balance'])} Gram\n\n"
            "📝If you submitted wrong data then you can restart the bot & "
            "resubmit the data again by clicking on /start"
        )
        return

    # Referral
    if text == "👥 Referral":
        referral_link = (
            f"https://t.me/{BOT_USERNAME}?start=ref_{row['referral_code']}"
        )
        await update.message.reply_text(
            "🔥 Task GRAM\n\n"
            "👨‍👨‍👦 Per Refer : 1 point 🪙\n\n"
            "📝(The person with the most points at the end of each week wins GRAM)\n"
            "https://t.me/weekend_point\n\n"
            f"🔗 Your Refer Link = {referral_link}"
        )
        return

    # Withdraw
    if text == "💸 Withdraw":
        balance = Decimal(row["balance"])

        if balance < MIN_WITHDRAWAL:
            await update.message.reply_text(
                "⚠ Minimum Withdrawal Is 0.5 GRAM"
            )
            return

        if not row["gram_address"]:
            await update.message.reply_text(
                "send your Ton(Gram) address"
            )
            context.user_data["awaiting_address"] = True
            return

        con = db()
        try:
            con.execute(
                """
                INSERT INTO withdrawals
                (telegram_id, username, first_name, address, amount, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    int(user.id),
                    user.username or "",
                    user.first_name or "",
                    row["gram_address"],
                    decimal_string(balance),
                    now(),
                ),
            )

            # Reserve/clear balance after creating the request.
            con.execute(
                "UPDATE users SET balance='0', updated_at=? WHERE telegram_id=?",
                (now(), int(user.id)),
            )
            con.commit()
        finally:
            con.close()

        await notify_admin_withdrawal(
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
            address=row["gram_address"],
            amount=balance,
        )

        await update.message.reply_text(
            "✅ Withdrawal Request Submitted\n\n"
            "⏰ Time: 1 _ 24 h"
        )
        return

    # Earn More
    if text == "🎁 Earn More":
        if not WEBAPP_URL.startswith("https://"):
            await update.message.reply_text(
                "Mini App is not configured yet. Please try again later."
            )
            return

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "open tasks🚀",
                        web_app=WebAppInfo(url=WEBAPP_URL),
                    )
                ]
            ]
        )

        await update.message.reply_text(
            "🎯 Complete tasks & earn GRAM\n\n"
            "Watch ADs and get rewarded instantly!",
            reply_markup=keyboard,
        )
        return

    await show_main_menu(update.message)


async def notify_admin_withdrawal(
    user_id, username, first_name, address, amount
):
    if not ADMIN_CHAT_ID or not BOT_TOKEN:
        return

    text = (
        "💸 NEW GRAM WITHDRAWAL\n\n"
        f"👤 User: {first_name}\n"
        f"🔗 Username: @{username}" if username else f"👤 User: {first_name}"
    )
    text += (
        f"\n🆔 Telegram ID: {user_id}\n"
        f"📍 GRAM Address: {address}\n"
        f"💰 Amount: {decimal_string(amount)} GRAM\n"
        "⏰ Time: 1 _ 24 h"
    )

    try:
        bot = Bot(BOT_TOKEN)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    except Exception as exc:
        print("Admin notification failed:", exc)


def looks_like_gram_address(address: str) -> bool:
    # TON/GRAM addresses commonly use EQ/UQ user-friendly forms or raw numeric forms.
    # This deliberately accepts a broad set so legitimate TON address variants are not
    # rejected by the bot.
    if not address:
        return False
    if len(address) < 20 or len(address) > 120:
        return False
    allowed = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789:_-"
    )
    return all(ch in allowed for ch in address)


# ---------------------------------------------------------------------------
# Telegram Mini App initData validation
# ---------------------------------------------------------------------------

def validate_telegram_init_data(init_data: str):
    """
    Telegram requires the Mini App's initData to be validated on the server
    before trusting the user identity.
    """
    if not init_data or not BOT_TOKEN:
        return None

    try:
        pairs = urllib.parse.parse_qsl(
            init_data, keep_blank_values=True
        )
        data = dict(pairs)
        received_hash = data.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(data.items())
        )

        # Telegram WebApp validation:
        # secret_key = HMAC_SHA256(key="WebAppData", message=bot_token)
        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        auth_date = int(data.get("auth_date", "0"))
        # 24h maximum age.
        if abs(time.time() - auth_date) > 86400:
            return None

        raw_user = data.get("user")
        if not raw_user:
            return None

        return json.loads(raw_user)

    except Exception:
        return None


def require_mini_app_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        tg_user = validate_telegram_init_data(init_data)
        if not tg_user or "id" not in tg_user:
            return jsonify(
                {"ok": False, "error": "Invalid Telegram session"}
            ), 401

        request.tg_user = tg_user
        create_or_update_user(
            tg_user["id"],
            tg_user.get("username", ""),
            tg_user.get("first_name", ""),
        )
        return fn(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Mini App API
# ---------------------------------------------------------------------------

@app.get("/")
def mini_app():
    path = os.path.join("web", "index.html")
    with open(path, "r", encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace("__ADSGRAM_BLOCK_ID__", ADSGRAM_BLOCK_ID)
    return html


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory("web", path)


@app.post("/api/me")
@require_mini_app_user
def api_me():
    row = get_user(request.tg_user["id"])
    referral_link = (
        f"https://t.me/{BOT_USERNAME}?start=ref_{row['referral_code']}"
    )
    return jsonify(
        {
            "ok": True,
            "user": {
                "id": row["telegram_id"],
                "first_name": row["first_name"],
                "username": row["username"],
                "address": row["gram_address"],
                "balance": decimal_string(row["balance"]),
                "referral_points": row["referral_points"],
                "referral_link": referral_link,
            },
        }
    )


@app.post("/api/join-check")
@require_mini_app_user
def api_join_check():
    joined = asyncio.run(
        is_channel_member(request.tg_user["id"])
    )
    return jsonify({"ok": True, "joined": joined})


@app.post("/api/ad/session")
@require_mini_app_user
def create_ad_session():
    """
    Creates a one-time reward session.
    The client sends this ID only when AdsGram reports a completed Rewarded ad.
    """
    user_id = request.tg_user["id"]
    session_id = secrets.token_urlsafe(32)

    con = db()
    con.execute(
        """
        INSERT INTO ad_sessions (id, telegram_id, created_at, status)
        VALUES (?, ?, ?, 'open')
        """,
        (session_id, int(user_id), now()),
    )
    con.commit()
    con.close()

    return jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "reward": decimal_string(REWARD_PER_AD),
        }
    )


@app.post("/api/ad/claim")
@require_mini_app_user
def claim_ad_reward():
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("session_id", "")).strip()

    if not session_id:
        return jsonify({"ok": False, "error": "Missing ad session"}), 400

    con = db()
    try:
        session = con.execute(
            """
            SELECT * FROM ad_sessions
            WHERE id=? AND telegram_id=?
            """,
            (session_id, int(request.tg_user["id"])),
        ).fetchone()

        if session is None:
            return jsonify({"ok": False, "error": "Invalid ad session"}), 400

        if session["status"] != "open" or session["claimed_at"] is not None:
            return jsonify({"ok": False, "error": "Reward already claimed"}), 400

        # Session expiry: 10 minutes.
        if now() - int(session["created_at"]) > 600:
            con.execute(
                "UPDATE ad_sessions SET status='expired' WHERE id=?",
                (session_id,),
            )
            con.commit()
            return jsonify({"ok": False, "error": "Ad session expired"}), 400

        # One claim per session, transactionally.
        row = con.execute(
            "SELECT balance FROM users WHERE telegram_id=?",
            (int(request.tg_user["id"]),),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "User not found"}), 404

        current_balance = Decimal(row["balance"])
        new_balance = current_balance + REWARD_PER_AD

        con.execute(
            """
            UPDATE users
            SET balance=?, updated_at=?
            WHERE telegram_id=?
            """,
            (
                decimal_string(new_balance),
                now(),
                int(request.tg_user["id"]),
            ),
        )

        con.execute(
            """
            INSERT INTO ad_rewards
            (telegram_id, session_id, amount, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(request.tg_user["id"]),
                session_id,
                decimal_string(REWARD_PER_AD),
                now(),
            ),
        )

        con.execute(
            """
            UPDATE ad_sessions
            SET status='claimed', claimed_at=?
            WHERE id=?
            """,
            (now(), session_id),
        )

        con.commit()

        return jsonify(
            {
                "ok": True,
                "reward": decimal_string(REWARD_PER_AD),
                "balance": decimal_string(new_balance),
            }
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Bot runner
# ---------------------------------------------------------------------------

def run_telegram_bot():
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing; Telegram bot polling is disabled.")
        return

    async def runner():
        telegram_app = (
            Application.builder()
            .token(BOT_TOKEN)
            .build()
        )

        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(
            CallbackQueryHandler(
                check_membership,
                pattern="^check_membership$",
            )
        )
        telegram_app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                handle_text,
            )
        )

        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES
        )

        print("Telegram bot polling started.")

        while True:
            await asyncio.sleep(3600)

    asyncio.run(runner())


if __name__ == "__main__":
    init_db()

    # Bot polling runs in a separate thread while Flask serves the Mini App.
    Thread(
        target=run_telegram_bot,
        daemon=True,
    ).start()

    print(f"Task Gram web server listening on :{PORT}")
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        threaded=True,
    )
