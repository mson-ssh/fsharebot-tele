#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fshare.vn Telegram Bot for Synology Download Station
"""

import json
import logging
import os
import re
import urllib.request
import urllib.parse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

_cfg       = load_config()
BOT_TOKEN  = _cfg["BOT_TOKEN"]
ALLOWED_ID = int(_cfg["ALLOWED_ID"])
DS_HOST    = _cfg["DS_HOST"]
DS_USER    = _cfg["DS_USER"]
DS_PASS    = _cfg["DS_PASS"]

USERAGENT = "pyLoad-B1RS5N"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Session store ─────────────────────────────────────────────────────────────
user_sessions = {}
ds_sid        = None

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
                links.extend(fshare_get_folder(item["linkcode"]))

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
    if not data.get("success") and data.get("error", {}).get("code") == 105:
        # Session hết hạn, login lại
        if ds_login():
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
    resp = urllib.request.urlopen(api_url, timeout=10)
    data = json.loads(resp.read())
    if data.get("success"):
        return data["data"]["tasks"]
    return []

# ── Menu chính ────────────────────────────────────────────────────────────────

def main_menu():
    keyboard = [
        [InlineKeyboardButton("Them link tai", callback_data="menu_add")],
        [InlineKeyboardButton("Trang thai tai", callback_data="menu_status")],
        [InlineKeyboardButton("File da xong", callback_data="menu_done")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("Ban khong co quyen su dung bot nay.")
        return
    await update.message.reply_text(
        "*Fshare Bot*\n\nChọn chức năng bên dưới hoặc gửi link folder/file Fshare trực tiếp.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    query   = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()

    # ── Menu chính ────────────────────────────────────────────────────────────
    if query.data == "menu_add":
        await query.message.reply_text(
            "Gửi link folder hoặc file Fshare:\n\n"
            "`https://www.fshare.vn/folder/XXXXXX`\n"
            "`https://www.fshare.vn/file/XXXXXX`",
            parse_mode="Markdown"
        )

    elif query.data == "menu_status":
        await _show_status(query.message)

    elif query.data == "menu_done":
        await _show_done(query.message)

    # ── Download actions ──────────────────────────────────────────────────────
    elif query.data == "dl_all":
        links = user_sessions.get(chat_id, {}).get("links", [])
        await query.edit_message_reply_markup(None)
        await _download_files(query.message, links)

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
        await query.message.reply_text(
            "Da huy.",
            reply_markup=main_menu()
        )

    # ── Back to menu ──────────────────────────────────────────────────────────
    elif query.data == "back_menu":
        await query.edit_message_reply_markup(None)
        await query.message.reply_text(
            "Chọn chức năng:",
            reply_markup=main_menu()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    text    = update.message.text.strip()
    chat_id = update.effective_chat.id

    # ── Link folder ───────────────────────────────────────────────────────────
    m = re.search(r"fshare\.vn/folder/(\w+)", text)
    if m:
        folder_id = m.group(1)
        msg = await update.message.reply_text("Dang lay danh sach file...")

        try:
            links = fshare_get_folder(folder_id)
        except Exception as e:
            await msg.edit_text(f"Loi: {e}")
            return

        if not links:
            await msg.edit_text(
                "Khong tim thay file nao.",
                reply_markup=main_menu()
            )
            return

        user_sessions[chat_id] = {"links": links}

        text_list = f"*Tim thay {len(links)} file:*\n\n"
        for i, item in enumerate(links, 1):
            text_list += f"`{i}.` {item['name']} — _{item['size']}_\n"

        if len(text_list) > 4000:
            text_list = text_list[:4000] + "\n_...(còn nữa)_"

        keyboard = [
            [
                InlineKeyboardButton("Tai tat ca", callback_data="dl_all"),
                InlineKeyboardButton("Tai theo muc", callback_data="dl_select"),
            ],
            [InlineKeyboardButton("Huy", callback_data="dl_cancel")],
        ]
        await msg.edit_text(
            text_list,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ── Link file đơn ─────────────────────────────────────────────────────────
    m = re.search(r"fshare\.vn/file/(\w+)", text)
    if m:
        url = f"https://www.fshare.vn/file/{m.group(1)}"
        msg = await update.message.reply_text("Dang them vao Download Station...")
        if ds_add_task(url):
            await msg.edit_text(
                "Da them vao Download Station!",
                reply_markup=main_menu()
            )
        else:
            await msg.edit_text(
                "Them vao DS that bai.",
                reply_markup=main_menu()
            )
        return

    # ── Chọn theo mục ─────────────────────────────────────────────────────────
    if chat_id in user_sessions and user_sessions[chat_id].get("waiting_select"):
        try:
            indices  = [int(x.strip()) - 1 for x in re.split(r"[,\s]+", text) if x.strip().isdigit()]
            links    = user_sessions[chat_id]["links"]
            selected = [links[i] for i in indices if 0 <= i < len(links)]

            if not selected:
                await update.message.reply_text("Khong co file nao hop le.")
                return

            user_sessions[chat_id]["waiting_select"] = False
            await _download_files(update.message, selected)
        except Exception as e:
            await update.message.reply_text(f"Loi: {e}")
        return

    await update.message.reply_text(
        "Gửi link folder hoặc file Fshare để bắt đầu.",
        reply_markup=main_menu()
    )

# ── Download helper ───────────────────────────────────────────────────────────

async def _download_files(message, links: list):
    msg = await message.reply_text(f"Dang them {len(links)} file vào Download Station...")

    success = 0
    failed  = 0
    for item in links:
        if ds_add_task(item["url"]):
            success += 1
        else:
            failed += 1

    result = f"Da them *{success}* file vào Download Station."
    if failed:
        result += f"\nLoi: *{failed}* file thất bại."

    await msg.edit_text(
        result,
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ── Status helpers ────────────────────────────────────────────────────────────

async def _show_status(message):
    tasks = ds_get_tasks()
    downloading = [t for t in tasks if t["status"] == "downloading"]

    if not downloading:
        await message.reply_text(
            "Khong co file nao dang tai.",
            reply_markup=main_menu()
        )
        return

    text = f"*Dang tai ({len(downloading)} file):*\n\n"
    for t in downloading:
        speed = t.get("additional", {}).get("transfer", {}).get("speed_download", 0)
        size  = t.get("size", 0)
        dl    = t.get("additional", {}).get("transfer", {}).get("size_downloaded", 0)
        pct   = f" ({int(dl/size*100)}%)" if size and size > 0 else ""
        text += f"• _{t['title']}{pct}_ — {format_size(speed)}/s\n"

    await message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def _show_done(message):
    tasks    = ds_get_tasks()
    finished = [t for t in tasks if t["status"] == "finished"]

    if not finished:
        await message.reply_text(
            "Chua co file nao hoan tat.",
            reply_markup=main_menu()
        )
        return

    text = f"*Da tai xong ({len(finished)} file):*\n\n"
    for t in finished:
        text += f"• _{t['title']}_ — {format_size(t.get('size', 0))}\n"

    await message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await _show_status(update.message)

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await _show_done(update.message)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Fshare Bot dang chay...")
    app.run_polling()

if __name__ == "__main__":
    main()
