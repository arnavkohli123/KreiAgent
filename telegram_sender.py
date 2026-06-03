"""
Telegram sender for Krei Deal Pipeline.

Uses the Telegram Bot API directly via httpx — no extra library needed.
The Bot API is just a set of HTTPS endpoints:
  https://api.telegram.org/bot{TOKEN}/methodName

Two things you need:
  TELEGRAM_BOT_TOKEN  — from BotFather when you created the bot
  TELEGRAM_CHAT_ID    — your personal chat ID (run get_my_chat_id() to find it)
                        or a group chat ID (add bot to group, send a message, run again)
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BASE  = f"https://api.telegram.org/bot{TOKEN}"


# ---------------------------------------------------------------------------
# UTILITY: find your chat ID
# ---------------------------------------------------------------------------
def get_my_chat_id() -> None:
    """
    Prints all recent messages sent to your bot so you can find your chat ID.

    How to use:
      1. Send any message to your bot in Telegram (e.g. "/start")
      2. Run: python telegram_sender.py
      3. Look for "chat_id" in the output — that's your number
    """
    resp = httpx.get(f"{BASE}/getUpdates")
    data = resp.json()

    if not data.get("ok"):
        print(f"[error] Telegram API error: {data}")
        return

    updates = data.get("result", [])
    if not updates:
        print("[info] No messages found. Make sure you sent a message to your bot first.")
        return

    print(f"\nFound {len(updates)} update(s):\n")
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        print(f"  chat_id : {chat.get('id')}")
        print(f"  type    : {chat.get('type')}")
        print(f"  title   : {chat.get('title') or chat.get('first_name')}")
        print(f"  text    : {msg.get('text', '')[:60]}")
        print()


# ---------------------------------------------------------------------------
# FORMAT a single listing as a Telegram message
# ---------------------------------------------------------------------------
def format_listing(listing: dict) -> str:
    """
    Builds a nicely formatted Telegram message for one listing.

    Telegram supports a limited subset of HTML:
      <b>bold</b>   <i>italic</i>   <a href="...">link text</a>
    We use parse_mode="HTML" when sending.

    The em dash (—) and bullet (•) are just Unicode characters — they render
    fine in Telegram without any special escaping.
    """
    name   = listing.get("name") or "Unknown"
    price  = listing.get("price") or "Price N/A"
    cap    = listing.get("cap_rate") or "Cap N/A"
    sqft   = listing.get("size_sqft") or "Size N/A"
    addr   = listing.get("address") or "Address N/A"
    types  = ", ".join(listing.get("property_types") or []) or "N/A"
    source = listing.get("source", "Crexi").capitalize()
    url    = listing.get("url") or ""

    link_line = f'🔗 <a href="{url}">View on {source}</a>' if url else f"Source: {source}"

    return (
        f"<b>{name}</b>\n"
        f"💰 {price}  •  Cap: {cap}  •  {sqft} sqft\n"
        f"🏢 {types}\n"
        f"📍 {addr}\n"
        f"{link_line}"
    )


# ---------------------------------------------------------------------------
# SEND a single message
# ---------------------------------------------------------------------------
def send_message(chat_id: str | int, text: str) -> bool:
    """
    Sends one HTML-formatted message to a Telegram chat.
    Returns True on success, False on failure.

    chat_id can be:
      - Your personal ID (a positive integer like 123456789)
      - A group ID (a negative integer like -987654321)
    """
    resp = httpx.post(
        f"{BASE}/sendMessage",
        json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
            # preview=False means Telegram won't generate a link preview card,
            # which would look cluttered with 10 listings in a row.
            "link_preview_options": {"is_disabled": True},
        },
        timeout=15,
    )
    result = resp.json()
    if not result.get("ok"):
        print(f"[telegram] Send failed: {result.get('description')}")
        return False
    return True


# ---------------------------------------------------------------------------
# SEND a batch of listings
# ---------------------------------------------------------------------------
def send_listings(listings: list[dict], chat_id: str | int | None = None) -> int:
    """
    Sends each listing as its own Telegram message.
    Sends a header first ("N new listings found"), then one message per listing.
    Returns the number of messages successfully sent.
    """
    if not TOKEN or TOKEN == "your-token-here":
        print("[telegram] TELEGRAM_BOT_TOKEN not set in .env")
        return 0

    target = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not target or target == "your-chat-id-here":
        print("[telegram] TELEGRAM_CHAT_ID not set in .env")
        return 0

    if not listings:
        print("[telegram] No listings to send.")
        return 0

    # Header message
    send_message(target, f"🏠 <b>{len(listings)} new listing(s)</b> — Palm Beach County\n(Retail / Office | $5M–$20M | ≥6% cap or unlisted)")

    sent = 0
    for listing in listings:
        text = format_listing(listing)
        if send_message(target, text):
            sent += 1
            print(f"[telegram] Sent: {listing.get('name')}")
        else:
            print(f"[telegram] Failed: {listing.get('name')}")

    print(f"[telegram] {sent}/{len(listings)} messages sent.")
    return sent


# ---------------------------------------------------------------------------
# STANDALONE: run this file directly to find your chat ID or send a test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Send a test message to the chat ID in .env
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not chat_id or chat_id == "your-chat-id-here":
            print("[error] Set TELEGRAM_CHAT_ID in .env first (run without args to find it).")
        else:
            fake_listing = {
                "crexi_id":       "TEST",
                "name":           "Test Property — Krei Pipeline Working ✅",
                "price":          "$10,000,000",
                "cap_rate":       "6.50%",
                "property_types": ["Retail"],
                "address":        "123 Test St, West Palm Beach, Palm Beach County, FL 33401",
                "size_sqft":      "15,000",
                "url":            "https://www.crexi.com",
                "source":         "crexi",
            }
            send_listings([fake_listing], chat_id)
    else:
        # Default: print recent updates so you can find your chat ID
        get_my_chat_id()
