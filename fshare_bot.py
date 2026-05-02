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
    import base64
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    decoded = {}
    for k, v in raw.items():
        try:
            decoded[k] = base64.b64decode(v.encode()).decode("utf-8")
        except Exception:
            decoded[k] = v  # fallback neu chua ma hoa
    return decoded

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
user_sessions    = {}   # { chat_id: { links, waiting_select } }
ds_sid           = None
prev_tasks       = {}   # { task_id: status }
download_sessions = {}  # { session_id: { task_ids, names, chat_id, done_ids, error_ids } }
session_counter   = 0

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
    try:
        req  = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            # Re-login va thu lai
            ds_sid = None
            if ds_login():
                params["_sid"] = ds_sid
                qs  = urllib.parse.urlencode(params)
                url = f"{DS_HOST}/webapi/{path}?{qs}"
                req  = urllib.request.Request(url)
                resp = urllib.request.urlopen(req, timeout=10)
                return json.loads(resp.read())
        raise

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
    """Lay thong tin disk qua SYNO.DownloadStation.Info"""
    try:
        data = ds_request("DownloadStation/info.cgi", {
            "api":     "SYNO.DownloadStation.Info",
            "version": "1",
            "method":  "getinfo",
        })
        if data.get("success"):
            info = data["data"]
            # DS Info tra ve thong tin volume
            free = int(info.get("free_space", 0))
            total = 0
            return free, total
    except Exception:
        pass
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
            [KeyboardButton("/add"), KeyboardButton("/info")"],
            [KeyboardButton("/tasks"), KeyboardButton("/clear")],
        ],
        resize_keyboard=True,
        persistent=True,
        is_persistent=True,
    )

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Quay lại", callback_data="back_main")]])

# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("Bạn không có quyền sử dụng bot này.")
        return
    await update.message.reply_text(
        "*Fshare Bot*\n\nChọn lệnh từ menu hoặc gửi link Fshare trực tiếp.",
        parse_mode="Markdown",
        reply_markup=reply_kb()
    )

# ── /info — Dashboard ─────────────────────────────────────────────────────────

async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    msg = await update.message.reply_text("Đang lấy thông tin...")
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
    warn      = " [!] Sắp đầy" if free and free < DISK_WARN_GB * 1024 ** 3 else ""

    text = (
        "*Dashboard*\n"
        "```\n"
        f"+------------------+------------------+\n"
        f"| Đang tải         | {len(downloading):<16} |\n"
        f"| Tạm dừng         | {len(paused):<16} |\n"
        f"| Lỗi              | {len(error):<16} |\n"
        f"| Hoàn tất         | {len(finished):<16} |\n"
        f"+------------------+------------------+\n"
        f"| Tốc độ tải       | {format_speed(dl_speed):<16} |\n"
        f"| Tốc độ up        | {format_speed(ul_speed):<16} |\n"
        f"+------------------+------------------+\n"
        f"| Dung lượng       | {disk_text:<16} |\n"
        f"+------------------+------------------+\n"
        "```"
        f"{warn}"
    )

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Làm mới", callback_data="refresh_info")]])
    try:
        await message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

# ── /tasks — Task management ──────────────────────────────────────────────────

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    msg = await update.message.reply_text("Đang lấy danh sách tác vụ...")
    await _show_tasks(msg)

async def _show_tasks(message):
    tasks = ds_task_list()
    if not tasks:
        await message.edit_text("Không có tác vụ nào.", reply_markup=back_kb())
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

    text = "*Danh sách tác vụ:*\n\n" + "\n\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(con nua)"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Tạm dừng tất cả", callback_data="task_pause_all"),
            InlineKeyboardButton("Tiếp tục tất cả", callback_data="task_resume_all"),
        ],
        [
            InlineKeyboardButton("Xoá đã xong", callback_data="task_clear_done"),
            InlineKeyboardButton("Khởi động lại lỗi", callback_data="task_restart_error"),
        ],
        [InlineKeyboardButton("Làm mới", callback_data="refresh_tasks")],
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
        "Gửi link Fshare (hỗ trợ nhiều link, mỗi link một dòng):\n\n"
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
        await update.message.reply_text("Không có tác vụ nào đã hoàn tất.")
        return
    ds_task_action("delete", finished)
    await update.message.reply_text(f"Đã xoá {len(finished)} tác vụ hoàn tất.")

# ── /done ─────────────────────────────────────────────────────────────────────

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    tasks    = ds_task_list()
    finished = [t for t in tasks if t["status"] == "finished"]
    if not finished:
        await update.message.reply_text("Chưa có tệp nào hoàn tất.")
        return
    lines = [f"- {t['title']} ({format_size(t.get('size', 0))})" for t in finished]
    text  = f"*Đã tải xong ({len(finished)} tệp):*\n\n" + "\n".join(lines)
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
            await update.message.reply_text("Không có tệp nào hợp lệ. Vui lòng nhập lại.")
            return
        user_sessions[chat_id]["waiting_select"] = False
        await _do_download(update.message, selected)
        return

    # Quet tat ca link Fshare trong tin nhan
    folder_ids = re.findall(r"fshare\.vn/folder/(\w+)", text)
    file_urls  = re.findall(r"https?://(?:www\.)?fshare\.vn/file/\w+", text)

    if not folder_ids and not file_urls:
        await update.message.reply_text(
            "Không tìm thấy link Fshare. Vui lòng gửi link tệp hoặc thư mục Fshare."
        )
        return

    # Xu ly folder
    if folder_ids:
        msg = await update.message.reply_text("Đang lấy danh sách tệp...")
        all_links = []
        for fid in folder_ids:
            try:
                all_links.extend(fshare_get_folder(fid))
            except Exception as e:
                await update.message.reply_text(f"Lỗi thư mục {fid}: {e}")

        if not all_links and not file_urls:
            await msg.edit_text("Không tìm thấy tệp nào.")
            return

        # Them file don neu co
        for u in file_urls:
            m = re.search(r"fshare\.vn/file/(\w+)", u)
            if m:
                all_links.append({"name": m.group(1), "size": "?", "url": u})

        user_sessions[chat_id] = {"links": all_links}

        lines = [f"`{i}.` {item['name']} - {item['size']}" for i, item in enumerate(all_links, 1)]
        list_text = f"*Tìm thấy {len(all_links)} tệp:*\n\n" + "\n".join(lines)
        if len(list_text) > 4000:
            list_text = list_text[:4000] + "\n...(con nua)"

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Tải tất cả", callback_data="dl_all"),
                InlineKeyboardButton("Tải theo mục", callback_data="dl_select"),
            ],
            [InlineKeyboardButton("Huỷ", callback_data="dl_cancel")],
        ])
        await msg.edit_text(list_text, parse_mode="Markdown", reply_markup=kb)
        return

    # Chi co file don
    if file_urls:
        if len(file_urls) == 1:
            msg = await update.message.reply_text("Đang thêm vào Download Station...")
            ok = ds_add_task(file_urls[0])
            status = "Đã thêm vào Download Station." if ok else "Thêm thất bại."
            await msg.edit_text(status)
        else:
            links = [{"name": u.split("/")[-1], "size": "?", "url": u} for u in file_urls]
            user_sessions[chat_id] = {"links": links}
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"Tải tất cả ({len(links)} tệp)", callback_data="dl_all"),
                    InlineKeyboardButton("Tải theo mục", callback_data="dl_select"),
                ],
                [InlineKeyboardButton("Huỷ", callback_data="dl_cancel")],
            ])
            await update.message.reply_text(
                f"Tìm thấy {len(links)} link tệp. Bạn muốn làm gì?",
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
            await query.message.reply_text(f"Đã tạm dừng {len(ids)} tác vụ.")
        else:
            await query.message.reply_text("Không có tác vụ nào đang tải.")

    elif data == "task_resume_all":
        tasks = ds_task_list()
        ids   = [t["id"] for t in tasks if t["status"] == "paused"]
        if ids:
            ds_task_action("resume", ids)
            await query.message.reply_text(f"Đã tiếp tục {len(ids)} tác vụ.")
        else:
            await query.message.reply_text("Không có tác vụ nào đang tạm dừng.")

    elif data == "task_clear_done":
        tasks = ds_task_list()
        ids   = [t["id"] for t in tasks if t["status"] == "finished"]
        if ids:
            ds_task_action("delete", ids)
            await query.message.reply_text(f"Đã xoá {len(ids)} tác vụ hoàn tất.")
            await _show_tasks(query.message)
        else:
            await query.message.reply_text("Không có tác vụ nào hoàn tất.")

    elif data == "task_restart_error":
        tasks = ds_task_list()
        ids   = [t["id"] for t in tasks if t["status"] == "error"]
        if ids:
            ds_task_action("resume", ids)
            await query.message.reply_text(f"Đã khởi động lại {len(ids)} tác vụ lỗi.")
            await _show_tasks(query.message)
        else:
            await query.message.reply_text("Không có tác vụ nào bị lỗi.")

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
        await query.message.reply_text("Đã huỷ.")

    elif data == "back_main":
        await query.edit_message_reply_markup(None)

# ── Download helper ───────────────────────────────────────────────────────────

async def _do_download(message, links: list):
    global session_counter
    msg = await message.reply_text(f"Đang thêm {len(links)} tệp vào Download Station...")

    # Lay danh sach task truoc khi them
    tasks_before = {t["id"] for t in ds_task_list()}

    success = failed = 0
    names   = []
    for item in links:
        if ds_add_task(item["url"]):
            success += 1
            names.append(item.get("name", ""))
        else:
            failed += 1

    result = f"Đã thêm *{success}* tệp vào Download Station."
    if failed:
        result += f"\nThất bại: *{failed}* tệp."
    await msg.edit_text(result, parse_mode="Markdown")

    # Tao phien tai moi
    if success > 0:
        tasks_after  = {t["id"] for t in ds_task_list()}
        new_task_ids = list(tasks_after - tasks_before)
        session_counter += 1
        sid = session_counter
        download_sessions[sid] = {
            "task_ids":  new_task_ids,
            "names":     names,
            "chat_id":   message.chat_id,
            "done_ids":  set(),
            "error_ids": set(),
        }

    await asyncio.sleep(1)
    dash = await message.reply_text("Đang cập nhật dashboard...")
    await _show_dashboard(dash)

# ── Push notification (job queue) ─────────────────────────────────────────────

async def check_tasks(context: ContextTypes.DEFAULT_TYPE):
    global prev_tasks, download_sessions
    chat_id = ALLOWED_ID

    try:
        tasks   = ds_task_list()
        current = {t["id"]: t for t in tasks}

        # Theo doi tung phien tai
        finished_sessions = []
        for sid, session in list(download_sessions.items()):
            for tid in session["task_ids"]:
                task = current.get(tid)
                if not task:
                    continue
                status = task["status"]
                if status == "finished" and tid not in session["done_ids"]:
                    session["done_ids"].add(tid)
                elif status == "error" and tid not in session["error_ids"]:
                    session["error_ids"].add(tid)

            total     = len(session["task_ids"])
            completed = len(session["done_ids"]) + len(session["error_ids"])

            # Tat ca task trong phien da xong
            if total > 0 and completed >= total:
                done_count  = len(session["done_ids"])
                error_count = len(session["error_ids"])

                lines = [f"*[Phiên {sid}] Hoàn tất!*"]
                if done_count:
                    lines.append(f"Đã tải: *{done_count}* tệp")
                if error_count:
                    lines.append(f"Lỗi: *{error_count}* tệp")

                # Liet ke ten file da tai xong
                done_tasks = [current[tid] for tid in session["done_ids"] if tid in current]
                for t in done_tasks[:10]:
                    lines.append(f"- {t['title'][:40]} ({format_size(t.get('size', 0))})")
                if len(done_tasks) > 10:
                    lines.append(f"  ...và {len(done_tasks)-10} tệp khác")

                await context.bot.send_message(
                    session["chat_id"],
                    "\n".join(lines),
                    parse_mode="Markdown"
                )
                finished_sessions.append(sid)

        # Xoa phien da hoan tat
        for sid in finished_sessions:
            del download_sessions[sid]

        # Canh bao disk
        free, total = ds_disk_info()
        if free and free < DISK_WARN_GB * 1024 ** 3:
            free_gb = free / 1024 ** 3
            if not context.bot_data.get("disk_warned"):
                await context.bot.send_message(
                    chat_id,
                    f"[Cảnh báo] NAS chỉ còn {free_gb:.1f} GB. Vui lòng dọn dẹp."
                )
                context.bot_data["disk_warned"] = True
        else:
            context.bot_data["disk_warned"] = False

        prev_tasks = current

    except Exception as e:
        logger.error(f"Lỗi kiểm tra tác vụ: {e}")

# ── Bot setup ─────────────────────────────────────────────────────────────────

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",  "Khởi động bot"),
        BotCommand("add",    "Thêm link tải"),
        BotCommand("info",   "Tổng quan hệ thống"),
        BotCommand("tasks",  "Quản lý tác vụ"),
        BotCommand("done",   "Tệp đã hoàn tất"),
        BotCommand("clear",  "Xoá tác vụ đã xong"),
    ])
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Bot đã khởi tạo thành công.")

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

    logger.info("Fshare Bot đang khởi động...")
    app.run_polling()

if __name__ == "__main__":
    main()
