#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fshare.vn Telegram Bot for Synology Download Station
Features: add link, dashboard, task management, push notification
"""

import asyncio
import json
import logging
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand, MenuButtonCommands
)
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

USERAGENT        = "pyLoad-B1RS5N"
POLL_INTERVAL    = 30   # seconds — check task status
DISK_WARN_GB     = 50   # warn when free space below this

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
user_sessions  = {}   # { chat_id: { links, waiting_select } }
ds_sid         = None
prev_tasks     = {}   # { task_id: status } for push notification

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

def format_speed(bps):
    try:
        return format_size(int(bps)) + "/s"
    except Exception:
        return "0 B/s"

def progress_bar(pct, width=10):
    filled = int(width * pct / 100)
    return "[" + "#" * filled + "-" * (width - filled) + "]"

# ── DS API ────────────────────────────────────────────────────────────────────

def _ds_get(path, params=None):
    global ds_sid
    if params is None:
        params = {}
    if ds_sid:
        params["_sid"] = ds_sid
    qs  = urllib.parse.urlencode(params)
    url = f"{DS_HOST}/webapi/{path}?{qs}"
    req  = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())

def ds_login():
    global ds_sid
    data = _ds_get("auth.cgi", {
        "api":     "SYNO.API.Auth",
        "version": "3",
        "method":  "login",
        "account": DS_USER,
        "passwd":  DS_PASS,
        "session": "DownloadStation",
        "format":  "sid",
    })
    if data.get("success"):
        ds_sid = data["data"]["sid"]
        return True
    return False

def ds_ensure_login():
    global ds_sid
    if not ds_sid:
        ds_login()

def ds_request(path, params, retry=True):
    global ds_sid
    ds_ensure_login()
    data = _ds_get(path, params)
    if not data.get("success") and data.get("error", {}).get("code") in (105, 106, 107):
        # Session expired — re-login once
        if retry:
            ds_sid = None
            ds_login()
            return ds_request(path, params, retry=False)
    return data

def ds_task_list():
    data = ds_request("DownloadStation/task.cgi", {
        "api":        "SYNO.DownloadStation.Task",
        "version":    "1",
        "method":     "list",
        "additional": "transfer,detail",
    })
    if data.get("success"):
        return data["data"].get("tasks", [])
    return []

def ds_task_action(action, task_ids):
    ids = ",".join(task_ids)
    return ds_request("DownloadStation/task.cgi", {
        "api":     "SYNO.DownloadStation.Task",
        "version": "1",
        "method":  action,
        "id":      ids,
    })

def ds_add_task(url):
    data = ds_request("DownloadStation/task.cgi", {
        "api":     "SYNO.DownloadStation.Task",
        "version": "1",
        "method":  "create",
        "uri":     url,
    })
    return data.get("success", False)

def ds_statistic():
    data = ds_request("DownloadStation/statistic.cgi", {
        "api":     "SYNO.DownloadStation.Statistic",
        "version": "1",
        "method":  "getinfo",
    })
    if data.get("success"):
        return data["data"]
    return {}

def ds_disk_info():
    """Lay thong tin disk qua SYNO.FileStation.Info"""
    data = ds_request("FileStation/info.cgi", {
        "api":     "SYNO.FileStation.Info",
        "version": "2",
        "method":  "getinfo",
    })
    if data.get("success"):
        volumes = data["data"].get("items", [])
        total_free = sum(int(v.get("free_space", 0)) for v in volumes if v.get("free_space"))
        total_size = sum(int(v.get("total_space", 0)) for v in volumes if v.get("total_space"))
        return total_free, total_size
    return 0, 0

# ── Fshare folder ─────────────────────────────────────────────────────────────

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

# ── Keyboards ─────────────────────────────────────────────────────────────────

def reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("/add"), KeyboardButton("/info")],
            [KeyboardButton("/tasks"), KeyboardButton("/clear")],
        ],
        resize_keyboard=True,
        persistent=True,
        is_persistent=True,
    )

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Quay lai", callback_data="back_main")]])

# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("Ban khong co quyen su dung bot nay.")
        return
    await update.message.reply_text(
        "*Fshare Bot*\n\nChon lenh tu menu hoac gui link Fshare truc tiep.",
        parse_mode="Markdown",
        reply_markup=reply_kb()
    )

# ── /info — Dashboard ─────────────────────────────────────────────────────────

async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    msg = await update.message.reply_text("Dang lay thong tin...")
    await _show_dashboard(msg)

async def _show_dashboard(message):
    tasks  = ds_task_list()
    stats  = ds_statistic()
    free, total = ds_disk_info()

    downloading = [t for t in tasks if t["status"] == "downloading"]
    paused      = [t for t in tasks if t["status"] == "paused"]
    error       = [t for t in tasks if t["status"] == "error"]
    finished    = [t for t in tasks if t["status"] == "finished"]

    dl_speed = stats.get("speed_download", 0)
    ul_speed = stats.get("speed_upload", 0)

    disk_text = f"{format_size(free)} / {format_size(total)}" if total else "N/A"
    warn      = " [!] Sap day" if free and free < DISK_WARN_GB * 1024 ** 3 else ""

    text = (
        "*Dashboard*\n\n"
        f"Dang tai  : `{len(downloading)}` task\n"
        f"Tam dung  : `{len(paused)}` task\n"
        f"Loi       : `{len(error)}` task\n"
        f"Hoan tat  : `{len(finished)}` task\n\n"
        f"Toc do tai: `{format_speed(dl_speed)}`\n"
        f"Toc do up : `{format_speed(ul_speed)}`\n\n"
        f"Dung luong: `{disk_text}`{warn}"
    )

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Lam moi", callback_data="refresh_info")]])
    try:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

# ── /tasks — Task management ──────────────────────────────────────────────────

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    msg = await update.message.reply_text("Dang lay danh sach task...")
    await _show_tasks(msg)

async def _show_tasks(message):
    tasks = ds_task_list()
    if not tasks:
        await message.edit_text("Khong co task nao.", reply_markup=back_kb())
        return

    lines = []
    for i, t in enumerate(tasks, 1):
        status = t["status"]
        size   = int(t.get("size", 0))
        dl     = int(t.get("additional", {}).get("transfer", {}).get("size_downloaded", 0))
        pct    = int(dl / size * 100) if size > 0 else 0
        speed  = int(t.get("additional", {}).get("transfer", {}).get("speed_download", 0))
        name   = t["title"][:30] + "..." if len(t["title"]) > 30 else t["title"]

        if status == "downloading":
            line = f"`{i}.` {name}\n    {progress_bar(pct)} {pct}% - {format_speed(speed)}"
        elif status == "finished":
            line = f"`{i}.` {name}\n    [Hoan tat] - {format_size(size)}"
        elif status == "paused":
            line = f"`{i}.` {name}\n    [Tam dung] {pct}%"
        elif status == "error":
            line = f"`{i}.` {name}\n    [Loi]"
        else:
            line = f"`{i}.` {name}\n    [{status}]"
        lines.append(line)

    text = "*Danh sach task:*\n\n" + "\n\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(con nua)"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Tam dung tat ca", callback_data="task_pause_all"),
            InlineKeyboardButton("Tiep tuc tat ca", callback_data="task_resume_all"),
        ],
        [
            InlineKeyboardButton("Xoa da xong", callback_data="task_clear_done"),
            InlineKeyboardButton("Restart loi", callback_data="task_restart_error"),
        ],
        [InlineKeyboardButton("Lam moi", callback_data="refresh_tasks")],
    ])

    try:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

# ── /add ──────────────────────────────────────────────────────────────────────

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Gui link Fshare (ho tro nhieu link, moi link 1 dong):\n\n"
        "`https://www.fshare.vn/folder/XXXXXX`\n"
        "`https://www.fshare.vn/file/XXXXXX`",
        parse_mode="Markdown"
    )

# ── /clear ────────────────────────────────────────────────────────────────────

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    tasks    = ds_task_list()
    finished = [t["id"] for t in tasks if t["status"] == "finished"]
    if not finished:
        await update.message.reply_text("Khong co task nao da hoan tat.")
        return
    ds_task_action("delete", finished)
    await update.message.reply_text(f"Da xoa {len(finished)} task hoan tat.")

# ── /done ─────────────────────────────────────────────────────────────────────

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    tasks    = ds_task_list()
    finished = [t for t in tasks if t["status"] == "finished"]
    if not finished:
        await update.message.reply_text("Chua co file nao hoan tat.")
        return
    lines = [f"- {t['title']} ({format_size(t.get('size', 0))})" for t in finished]
    text  = f"*Da tai xong ({len(finished)} file):*\n\n" + "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown")

# ── Message handler ───────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    text    = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Chon theo muc
    if chat_id in user_sessions and user_sessions[chat_id].get("waiting_select"):
        indices  = [int(x.strip()) - 1 for x in re.split(r"[,\s]+", text) if x.strip().isdigit()]
        links    = user_sessions[chat_id]["links"]
        selected = [links[i] for i in indices if 0 <= i < len(links)]
        if not selected:
            await update.message.reply_text("Khong co file nao hop le. Vui long nhap lai.")
            return
        user_sessions[chat_id]["waiting_select"] = False
        await _do_download(update.message, selected)
        return

    # Quet tat ca link Fshare trong tin nhan
    folder_ids = re.findall(r"fshare\.vn/folder/(\w+)", text)
    file_urls  = re.findall(r"https?://(?:www\.)?fshare\.vn/file/\w+", text)

    if not folder_ids and not file_urls:
        await update.message.reply_text(
            "Khong tim thay link Fshare. Gui link file hoac folder Fshare."
        )
        return

    # Xu ly folder
    if folder_ids:
        msg = await update.message.reply_text("Dang lay danh sach file...")
        all_links = []
        for fid in folder_ids:
            try:
                all_links.extend(fshare_get_folder(fid))
            except Exception as e:
                await update.message.reply_text(f"Loi folder {fid}: {e}")

        if not all_links and not file_urls:
            await msg.edit_text("Khong tim thay file nao.")
            return

        # Them file don neu co
        for u in file_urls:
            m = re.search(r"fshare\.vn/file/(\w+)", u)
            if m:
                all_links.append({"name": m.group(1), "size": "?", "url": u})

        user_sessions[chat_id] = {"links": all_links}

        lines = [f"`{i}.` {item['name']} - {item['size']}" for i, item in enumerate(all_links, 1)]
        list_text = f"*Tim thay {len(all_links)} file:*\n\n" + "\n".join(lines)
        if len(list_text) > 4000:
            list_text = list_text[:4000] + "\n...(con nua)"

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Tai tat ca", callback_data="dl_all"),
                InlineKeyboardButton("Tai theo muc", callback_data="dl_select"),
            ],
            [InlineKeyboardButton("Huy", callback_data="dl_cancel")],
        ])
        await msg.edit_text(list_text, parse_mode="Markdown", reply_markup=kb)
        return

    # Chi co file don
    if file_urls:
        if len(file_urls) == 1:
            msg = await update.message.reply_text("Dang them vao Download Station...")
            ok = ds_add_task(file_urls[0])
            status = "Da them vao Download Station." if ok else "Them that bai."
            await msg.edit_text(status)
        else:
            links = [{"name": u.split("/")[-1], "size": "?", "url": u} for u in file_urls]
            user_sessions[chat_id] = {"links": links}
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"Tai tat ca ({len(links)} file)", callback_data="dl_all"),
                    InlineKeyboardButton("Tai theo muc", callback_data="dl_select"),
                ],
                [InlineKeyboardButton("Huy", callback_data="dl_cancel")],
            ])
            await update.message.reply_text(
                f"Tim thay {len(links)} link file. Ban muon lam gi?",
                reply_markup=kb
            )

# ── Callback handler ──────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    query   = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()
    data    = query.data

    # ── Dashboard ─────────────────────────────────────────────────────────────
    if data == "refresh_info":
        await _show_dashboard(query.message)

    # ── Tasks ─────────────────────────────────────────────────────────────────
    elif data == "refresh_tasks":
        await _show_tasks(query.message)

    elif data == "task_pause_all":
        tasks = ds_task_list()
        ids   = [t["id"] for t in tasks if t["status"] == "downloading"]
        if ids:
            ds_task_action("pause", ids)
            await query.message.reply_text(f"Da tam dung {len(ids)} task.")
        else:
            await query.message.reply_text("Khong co task nao dang tai.")

    elif data == "task_resume_all":
        tasks = ds_task_list()
        ids   = [t["id"] for t in tasks if t["status"] == "paused"]
        if ids:
            ds_task_action("resume", ids)
            await query.message.reply_text(f"Da tiep tuc {len(ids)} task.")
        else:
            await query.message.reply_text("Khong co task nao dang tam dung.")

    elif data == "task_clear_done":
        tasks = ds_task_list()
        ids   = [t["id"] for t in tasks if t["status"] == "finished"]
        if ids:
            ds_task_action("delete", ids)
            await query.message.reply_text(f"Da xoa {len(ids)} task hoan tat.")
            await _show_tasks(query.message)
        else:
            await query.message.reply_text("Khong co task nao hoan tat.")

    elif data == "task_restart_error":
        tasks = ds_task_list()
        ids   = [t["id"] for t in tasks if t["status"] == "error"]
        if ids:
            ds_task_action("resume", ids)
            await query.message.reply_text(f"Da restart {len(ids)} task loi.")
            await _show_tasks(query.message)
        else:
            await query.message.reply_text("Khong co task nao bi loi.")

    # ── Download ───────────────────────────────────────────────────────────────
    elif data == "dl_all":
        links = user_sessions.get(chat_id, {}).get("links", [])
        await query.edit_message_reply_markup(None)
        await _do_download(query.message, links)

    elif data == "dl_select":
        user_sessions[chat_id]["waiting_select"] = True
        await query.edit_message_reply_markup(None)
        total = len(user_sessions[chat_id]["links"])
        await query.message.reply_text(
            f"Nhap so thu tu cac file muon tai (1-{total}), cach nhau bang dau cach hoac phay.\n"
            f"Vi du: `1 3 5` hoac `1, 3, 5`",
            parse_mode="Markdown"
        )

    elif data == "dl_cancel":
        user_sessions.pop(chat_id, None)
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("Da huy.")

    elif data == "back_main":
        await query.edit_message_reply_markup(None)

# ── Download helper ───────────────────────────────────────────────────────────

async def _do_download(message, links: list):
    msg = await message.reply_text(f"Dang them {len(links)} file vao Download Station...")
    success = failed = 0
    for item in links:
        if ds_add_task(item["url"]):
            success += 1
        else:
            failed += 1

    result = f"Da them *{success}* file vao Download Station."
    if failed:
        result += f"\nThat bai: *{failed}* file."
    await msg.edit_text(result, parse_mode="Markdown")

# ── Push notification (job queue) ─────────────────────────────────────────────

async def check_tasks(context: ContextTypes.DEFAULT_TYPE):
    global prev_tasks
    chat_id = ALLOWED_ID

    try:
        tasks   = ds_task_list()
        current = {t["id"]: t for t in tasks}

        for tid, task in current.items():
            prev_status = prev_tasks.get(tid, {}).get("status")
            curr_status = task["status"]

            if prev_status and prev_status != curr_status:
                name = task["title"][:50]
                size = format_size(task.get("size", 0))

                if curr_status == "finished":
                    await context.bot.send_message(
                        chat_id,
                        f"[Hoan tat] {name} ({size})"
                    )
                elif curr_status == "error":
                    await context.bot.send_message(
                        chat_id,
                        f"[Loi] {name} - Kiem tra Download Station."
                    )

        # Canh bao disk
        free, total = ds_disk_info()
        if free and free < DISK_WARN_GB * 1024 ** 3:
            free_gb = free / 1024 ** 3
            if not context.bot_data.get("disk_warned"):
                await context.bot.send_message(
                    chat_id,
                    f"[Canh bao] NAS chi con {free_gb:.1f} GB. Vui long don dep."
                )
                context.bot_data["disk_warned"] = True
        else:
            context.bot_data["disk_warned"] = False

        prev_tasks = current

    except Exception as e:
        logger.error(f"check_tasks error: {e}")

# ── Bot setup ─────────────────────────────────────────────────────────────────

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",  "Khoi dong bot"),
        BotCommand("add",    "Them link tai"),
        BotCommand("info",   "Dashboard tong quan"),
        BotCommand("tasks",  "Quan ly task"),
        BotCommand("done",   "File da hoan tat"),
        BotCommand("clear",  "Xoa task da xong"),
    ])
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Bot initialized.")

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("info",  cmd_info))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("add",   cmd_add))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("done",  cmd_done))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Push notification job
    app.job_queue.run_repeating(check_tasks, interval=POLL_INTERVAL, first=10)

    logger.info("Fshare Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
