#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fshare.vn Telegram Bot for Synology Download Station
Chạy trực tiếp trên NAS
"""

import json
import logging
import re
import urllib.request
import urllib.parse
import urllib.error
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# ── Cấu hình ─────────────────────────────────────────────────────────────────
import os

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

_cfg = load_config()

BOT_TOKEN  = _cfg["BOT_TOKEN"]
ALLOWED_ID = int(_cfg["ALLOWED_ID"])
DS_HOST    = _cfg["DS_HOST"]
DS_USER    = _cfg["DS_USER"]
DS_PASS    = _cfg["DS_PASS"]

FSHARE_API  = "https://api.fshare.vn/api/"
FSHARE_KEY  = "dMnqMMZMUnN5YpvKENaEhdQQ5jxDqddt"
USERAGENT   = "pyLoad-B1RS5N"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Session store (in-memory) ─────────────────────────────────────────────────
user_sessions = {}   # { chat_id: { "folder_links": [...], "folder_name": "" } }
ds_sid        = None # DS session ID

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_allowed(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_ID

def format_size(size_bytes):
    try:
        size = int(size_bytes)
    except (ValueError, TypeError):
        return "N/A"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

# ── Fshare API ────────────────────────────────────────────────────────────────

def fshare_get_folder(folder_id):
    """Lấy toàn bộ link file trong folder (không cần đăng nhập)"""
    links = []
    page  = 1

    while True:
        url = (f"https://www.fshare.vn/api/v3/files/folder"
               f"?linkcode={folder_id}&page={page}&per-page=50&sort=type,name")
        req  = urllib.request.Request(url, headers={"User-Agent": USERAGENT})
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode("utf-8"))

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            if item["type"] == 1:
                links.append({
                    "name": item.get("realname") or item.get("name", ""),
                    "size": format_size(item.get("size", 0)),
                    "url":  "https://www.fshare.vn/file/" + item["linkcode"],
                })
            else:
                # Subfolder — đệ quy
                sub = fshare_get_folder(item["linkcode"])
                links.extend(sub)

        last_link = data.get("_links", {}).get("last", "")
        m         = re.search(r"page=(\d+)", last_link)
        last_page = int(m.group(1)) if m else 1
        if page >= last_page:
            break
        page += 1

    return links

# ── DS API ────────────────────────────────────────────────────────────────────

def ds_login():
    global ds_sid
    url  = (f"{DS_HOST}/webapi/auth.cgi"
            f"?api=SYNO.API.Auth&version=3&method=login"
            f"&account={urllib.parse.quote(DS_USER)}"
            f"&passwd={urllib.parse.quote(DS_PASS)}"
            f"&session=DownloadStation&format=sid")
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    if data.get("success"):
        ds_sid = data["data"]["sid"]
        return True
    return False

def ds_add_task(url):
    global ds_sid
    if not ds_sid:
        if not ds_login():
            return False
    api_url = (f"{DS_HOST}/webapi/DownloadStation/task.cgi"
               f"?api=SYNO.DownloadStation.Task&version=1&method=create"
               f"&uri={urllib.parse.quote(url)}&_sid={ds_sid}")
    resp = urllib.request.urlopen(api_url, timeout=10)
    data = json.loads(resp.read())
    return data.get("success", False)

def ds_get_tasks():
    global ds_sid
    if not ds_sid:
        if not ds_login():
            return []
    api_url = (f"{DS_HOST}/webapi/DownloadStation/task.cgi"
               f"?api=SYNO.DownloadStation.Task&version=1&method=list"
               f"&additional=transfer&_sid={ds_sid}")
    resp  = urllib.request.urlopen(api_url, timeout=10)
    data  = json.loads(resp.read())
    if data.get("success"):
        return data["data"]["tasks"]
    return []

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "👋 Fshare Bot sẵn sàng!\n\n"
        "Gửi link folder Fshare để bắt đầu:\n"
        "`https://www.fshare.vn/folder/XXXXXX`\n\n"
        "Các lệnh:\n"
        "/status — Xem task đang tải\n"
        "/done — Xem file đã tải xong",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    text    = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Nhận link folder
    m = re.search(r"fshare\.vn/folder/(\w+)", text)
    if m:
        folder_id = m.group(1)
        await update.message.reply_text("⏳ Đang lấy danh sách file...")

        try:
            links = fshare_get_folder(folder_id)
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi: {e}")
            return

        if not links:
            await update.message.reply_text("❌ Không tìm thấy file nào trong folder.")
            return

        # Lưu vào session
        user_sessions[chat_id] = {"links": links}

        # Hiện danh sách
        text_list = "📁 Danh sách file:\n\n"
        for i, item in enumerate(links, 1):
            text_list += f"`{i}.` {item['name']} — {item['size']}\n"

        # Nếu quá dài thì chia nhỏ
        if len(text_list) > 4000:
            text_list = text_list[:4000] + "\n...(còn nữa)"

        keyboard = [
            [
                InlineKeyboardButton("✅ Tải tất cả", callback_data="dl_all"),
                InlineKeyboardButton("📋 Tải theo mục", callback_data="dl_select"),
            ],
            [InlineKeyboardButton("❌ Huỷ", callback_data="dl_cancel")],
        ]
        await update.message.reply_text(
            text_list,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Nhận số thứ tự để tải theo mục
    if chat_id in user_sessions and user_sessions[chat_id].get("waiting_select"):
        try:
            indices = [int(x.strip()) - 1 for x in re.split(r"[,\s]+", text) if x.strip().isdigit()]
            links   = user_sessions[chat_id]["links"]
            selected = [links[i] for i in indices if 0 <= i < len(links)]

            if not selected:
                await update.message.reply_text("❌ Không có file nào hợp lệ.")
                return

            user_sessions[chat_id]["waiting_select"] = False
            await _download_files(update, selected)
        except Exception as e:
            await update.message.reply_text(f"❌ Lỗi: {e}")
        return

    await update.message.reply_text("Gửi link folder Fshare để bắt đầu.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    query   = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()

    if query.data == "dl_all":
        links = user_sessions.get(chat_id, {}).get("links", [])
        await query.edit_message_reply_markup(None)
        await _download_files(update, links)

    elif query.data == "dl_select":
        user_sessions[chat_id]["waiting_select"] = True
        await query.edit_message_reply_markup(None)
        await query.message.reply_text(
            "📋 Nhập số thứ tự các file muốn tải, cách nhau bằng dấu cách hoặc dấu phẩy.\n"
            "Ví dụ: `1 3 5` hoặc `1, 3, 5`",
            parse_mode="Markdown"
        )

    elif query.data == "dl_cancel":
        user_sessions.pop(chat_id, None)
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("❌ Đã huỷ.")

async def _download_files(update: Update, links: list):
    msg = await update.effective_message.reply_text(f"⏳ Đang thêm {len(links)} file vào Download Station...")

    success = 0
    failed  = 0
    for item in links:
        if ds_add_task(item["url"]):
            success += 1
        else:
            failed += 1

    result = f"✅ Đã thêm {success} file vào Download Station."
    if failed:
        result += f"\n❌ {failed} file thất bại."

    await msg.edit_text(result)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    tasks = ds_get_tasks()
    if not tasks:
        await update.message.reply_text("📭 Không có task nào đang chạy.")
        return

    downloading = [t for t in tasks if t["status"] == "downloading"]
    if not downloading:
        await update.message.reply_text("📭 Không có file nào đang tải.")
        return

    text = "📥 Đang tải:\n\n"
    for t in downloading:
        speed    = t.get("additional", {}).get("transfer", {}).get("speed_download", 0)
        progress = ""
        size     = t.get("size", 0)
        dl       = t.get("additional", {}).get("transfer", {}).get("size_downloaded", 0)
        if size and size > 0:
            pct     = int(dl / size * 100)
            progress = f" ({pct}%)"
        text += f"• {t['title']}{progress} — {format_size(speed)}/s\n"

    await update.message.reply_text(text)

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    tasks = ds_get_tasks()
    finished = [t for t in tasks if t["status"] == "finished"]

    if not finished:
        await update.message.reply_text("📭 Chưa có file nào hoàn tất.")
        return

    text = "✅ Đã tải xong:\n\n"
    for t in finished:
        text += f"• {t['title']} — {format_size(t.get('size', 0))}\n"

    await update.message.reply_text(text)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
