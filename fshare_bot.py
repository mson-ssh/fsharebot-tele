#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fshare.vn Telegram Bot for Synology Download Station
Features: add link, dashboard, task management, push notification
"""

import asyncio
import base64
import json
import logging
import os
import re
import time as _time
import urllib.request
import urllib.parse

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
        raw = json.load(f)
    decoded = {}
    for k, v in raw.items():
        try:
            decoded[k] = base64.b64decode(v.encode()).decode("utf-8")
        except Exception:
            decoded[k] = v
    return decoded

_cfg       = load_config()
BOT_TOKEN  = _cfg["BOT_TOKEN"]
ALLOWED_ID = int(_cfg["ALLOWED_ID"])
DS_HOST    = _cfg["DS_HOST"]
DS_USER    = _cfg["DS_USER"]
DS_PASS    = _cfg["DS_PASS"]

USERAGENT        = "pyLoad-B1RS5N"
POLL_INTERVAL    = 30
DISK_WARN_GB     = 50
SESSION_TTL      = 600    # 10 phút — xoá user_session không hoạt động
DS_SESSION_TTL   = 3600   # 1 giờ — xoá download_session task bị xoá thủ công
PREV_TASKS_LIMIT = 500    # Giới hạn tối đa entries trong prev_tasks
BATCH_SIZE       = 200    # Số link tối đa thêm vào DS mỗi lần
TASKS_PER_PAGE   = 5      # Số task hiển thị mỗi trang

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
user_sessions    = {}   # { chat_id: { links, waiting_select, created_at } }
ds_sid           = None
prev_tasks       = {}   # { task_id: status }
download_sessions = {}  # { session_id: { task_ids, names, chat_id, done_ids, error_ids } }
session_counter   = 0
cancel_flag       = {}  # { chat_id: True } — đánh dấu yêu cầu huỷ

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

def escape_md(text):
    """Escape ký tự đặc biệt Markdown trong tên file."""
    for ch in ['_', '*', '[', ']', '`']:
        text = text.replace(ch, f'\\{ch}')
    return text

def progress_bar(pct, width=10):
    filled = int(width * pct / 100)
    return "[" + "#" * filled + "-" * (width - filled) + "]"

# ── DS API ────────────────────────────────────────────────────────────────────

def _ds_get(path, params=None, _is_auth_call=False):
    """Gọi DS API — không tự retry, để ds_request() xử lý."""
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
    try:
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
    except Exception as e:
        logger.error(f"Đăng nhập DS thất bại: {e}")
    return False

def ds_ensure_login():
    global ds_sid
    if not ds_sid:
        ds_login()

def ds_request(path, params, retry=True):
    """Gọi DS API với retry và auto re-login khi session hết hạn."""
    global ds_sid
    ds_ensure_login()
    try:
        data = _ds_get(path, params)
        err_code = data.get("error", {}).get("code")
        # Session hết hạn (105, 106, 107) hoặc chưa auth (400)
        if not data.get("success") and err_code in (105, 106, 107, 400):
            if retry:
                logger.warning(f"Session hết hạn (code {err_code}), đang đăng nhập lại...")
                ds_sid = None
                if ds_login():
                    return ds_request(path, params, retry=False)
        return data
    except urllib.error.URLError as e:
        logger.error(f"Lỗi kết nối DS: {e}")
        if retry:
            logger.info("Thử kết nối lại DS...")
            _time.sleep(2)
            ds_sid = None
            if ds_login():
                return ds_request(path, params, retry=False)
        return {"success": False, "error": {"code": -1}}
    except Exception as e:
        logger.error(f"Lỗi DS API: {e}")
        return {"success": False, "error": {"code": -1}}

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
    """Lay thong tin disk qua SYNO.Storage.CGI.Storage"""
    try:
        data = ds_request("entry.cgi", {
            "api":     "SYNO.Storage.CGI.Storage",
            "version": "1",
            "method":  "load_info",
        })
        if data.get("success"):
            volumes = data["data"].get("volumes", [])
            total_free  = sum(int(v["size"]["total"]) - int(v["size"]["used"]) for v in volumes if "size" in v)
            total_size  = sum(int(v["size"]["total"]) for v in volumes if "size" in v)
            # Tra ve tung volume de hien thi chi tiet
            vol_details = []
            for v in volumes:
                if "size" in v:
                    vol_free  = int(v["size"]["total"]) - int(v["size"]["used"])
                    vol_total = int(v["size"]["total"])
                    vol_details.append({
                        "name":  v.get("vol_desc") or v.get("id", ""),
                        "free":  vol_free,
                        "total": vol_total,
                    })
            return total_free, total_size, vol_details
    except Exception as e:
        logger.warning(f"Không thể lấy thông tin ổ đĩa: {e}")
    return 0, 0, []

# ── Fshare folder ─────────────────────────────────────────────────────────────

MAX_FOLDER_DEPTH = 10  # Giới hạn độ sâu tối đa khi duyệt thư mục

def fshare_get_folder(root_folder_id):
    """Lấy danh sách file bằng iterative DFS — tránh đệ quy vô tận."""
    links  = []
    # Stack chứa (folder_id, depth)
    stack  = [(root_folder_id, 0)]

    while stack:
        folder_id, depth = stack.pop()

        if depth >= MAX_FOLDER_DEPTH:
            logger.warning(f"Bỏ qua thư mục {folder_id}: vượt độ sâu tối đa {MAX_FOLDER_DEPTH}")
            continue

        page = 1
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
                    # File
                    links.append({
                        "name": item.get("realname") or item.get("name", ""),
                        "size": format_size(item.get("size", 0)),
                        "url":  "https://www.fshare.vn/file/" + item["linkcode"],
                    })
                else:
                    # Subfolder — đưa vào stack thay vì đệ quy
                    stack.append((item["linkcode"], depth + 1))

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
    return InlineKeyboardMarkup([[InlineKeyboardButton("Quay lại", callback_data="back_main")]])

# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        await update.message.reply_text("Bạn không có quyền sử dụng bot này.")
        return
    now = _time.strftime("%H:%M")
    await update.message.reply_text(
        f"Xin chào! Hiện tại là {now}.\n\n"
        "*Fshare Bot* sẵn sàng phục vụ.\n\n"
        "Chọn lệnh từ menu hoặc gửi link Fshare trực tiếp.",
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
    free, total, vol_details = ds_disk_info()

    downloading = [t for t in tasks if t["status"] == "downloading"]
    paused      = [t for t in tasks if t["status"] == "paused"]
    error       = [t for t in tasks if t["status"] == "error"]
    finished    = [t for t in tasks if t["status"] == "finished"]

    dl_speed = stats.get("speed_download", 0)
    ul_speed = stats.get("speed_upload", 0)

    warn = " [!] Sắp đầy" if free and free < DISK_WARN_GB * 1024 ** 3 else ""

    # Hien thi tung volume
    disk_rows = ""
    for v in vol_details:
        pct      = int((v["total"] - v["free"]) / v["total"] * 100) if v["total"] else 0
        vol_name = (v["name"][:14] if v["name"] else "Volume")
        vol_text = f"{format_size(v['free'])} còn"
        disk_rows += f"| {vol_name:<16} | {vol_text:<13} {pct:>2}% |\n"
    if not disk_rows:
        disk_rows = f"| Dung lượng       | {'N/A':<16} |\n"

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
        + disk_rows +
        f"+------------------+------------------+\n"
        "```"
        + warn
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

async def _show_tasks(message, page=1):
    tasks = ds_task_list()
    if not tasks:
        await message.edit_text("Không có tác vụ nào.", reply_markup=back_kb())
        return

    total_pages = max(1, (len(tasks) + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE)
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * TASKS_PER_PAGE
    paged_tasks = tasks[start:start + TASKS_PER_PAGE]

    lines = []
    for i, t in enumerate(paged_tasks, start + 1):
        status = t["status"]
        size   = int(t.get("size", 0))
        dl     = int(t.get("additional", {}).get("transfer", {}).get("size_downloaded", 0))
        pct    = int(dl / size * 100) if size > 0 else 0
        speed  = int(t.get("additional", {}).get("transfer", {}).get("speed_download", 0))
        name   = t["title"][:30] + "..." if len(t["title"]) > 30 else t["title"]

        if status == "downloading":
            line = f"{i}. {name}\n    {progress_bar(pct)} {pct}% - {format_speed(speed)}"
        elif status == "finished":
            line = f"{i}. {name}\n    [Hoàn tất] - {format_size(size)}"
        elif status == "paused":
            line = f"{i}. {name}\n    [Tạm dừng] {pct}%"
        elif status == "error":
            line = f"{i}. {name}\n    [Lỗi]"
        else:
            line = f"{i}. {name}\n    [{status}]"
        lines.append(line)

    text = f"Danh sách tác vụ (trang {page}/{total_pages}):\n\n" + "\n\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(còn nữa)"

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("Trang trước", callback_data=f"tasks_page_{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Trang sau", callback_data=f"tasks_page_{page+1}"))

    kb_rows = [
        [
            InlineKeyboardButton("Tạm dừng tất cả", callback_data="task_pause_all"),
            InlineKeyboardButton("Tiếp tục tất cả", callback_data="task_resume_all"),
        ],
        [
            InlineKeyboardButton("Xoá đã xong", callback_data="task_clear_done"),
            InlineKeyboardButton("Khởi động lại lỗi", callback_data="task_restart_error"),
        ],
        [InlineKeyboardButton("Làm mới", callback_data="refresh_tasks")],
    ]
    if nav_buttons:
        kb_rows.insert(2, nav_buttons)

    kb = InlineKeyboardMarkup(kb_rows)

    try:
        await message.edit_text(text, reply_markup=kb)
    except Exception:
        await message.reply_text(text, reply_markup=kb)

# ── /cancel ───────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    chat_id = update.effective_chat.id
    cancelled = []

    # Huỷ session đang chờ chọn file
    if chat_id in user_sessions:
        user_sessions.pop(chat_id, None)
        cancelled.append("phiên chọn tệp")

    # Đánh dấu huỷ batch đang thêm vào DS
    cancel_flag[chat_id] = True
    cancelled.append("tiến trình thêm link")

    if cancelled:
        await update.message.reply_text(
            f"Đã huỷ: {', '.join(cancelled)}.\n"
            "Các tệp đang tải trên Download Station vẫn tiếp tục."
        )
    else:
        await update.message.reply_text("Không có tiến trình nào đang chạy.")

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
    text  = f"Đã tải xong ({len(finished)} tệp):\n\n" + "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text)

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
    folder_ids = re.findall(r"(?:https?://)?(?:www\.)?fshare\.vn/folder/(\w+)", text)
    file_urls  = [f"https://www.fshare.vn/file/{m}" for m in re.findall(r"(?:https?://)?(?:www\.)?fshare\.vn/file/(\w+)", text)]

    if not folder_ids and not file_urls:
        await update.message.reply_text(
            "Không tìm thấy link Fshare. Vui lòng gửi link tệp hoặc thư mục Fshare."
        )
        return

    # Xu ly folder
    if folder_ids:
        msg = await update.message.reply_text(
            "Đang lấy danh sách tệp...\n"
            "Gõ /cancel để huỷ."
        )
        all_links = []
        for fid in folder_ids:
            # Kiểm tra huỷ trước mỗi folder
            if cancel_flag.get(chat_id):
                cancel_flag.pop(chat_id, None)
                await msg.edit_text("Đã huỷ lấy danh sách tệp.")
                return
            try:
                # Chạy trong thread riêng — không block bot
                links = await asyncio.to_thread(fshare_get_folder, fid)
                all_links.extend(links)
            except Exception as e:
                await update.message.reply_text(f"Lỗi thư mục {fid}: {e}")

        # Kiểm tra huỷ sau khi lấy xong
        if cancel_flag.get(chat_id):
            cancel_flag.pop(chat_id, None)
            await msg.edit_text("Đã huỷ lấy danh sách tệp.")
            return

        if not all_links and not file_urls:
            await msg.edit_text("Không tìm thấy tệp nào.")
            return

        # Them file don neu co
        for u in file_urls:
            m = re.search(r"fshare\.vn/file/(\w+)", u)
            if m:
                all_links.append({"name": m.group(1), "size": "?", "url": u})

        user_sessions[chat_id] = {"links": all_links, "created_at": _time.time()}

        lines = [f"`{i}.` {item['name']} - {item['size']}" for i, item in enumerate(all_links, 1)]
        list_text = f"*Tìm thấy {len(all_links)} tệp:*\n\n" + "\n".join(lines)
        if len(list_text) > 4000:
            list_text = list_text[:4000] + "\n...(còn nữa)"

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
            if ok:
                status = "Đã thêm *1* tệp vào Download Station và đang tải về.\n\nKiểm tra tiến độ tại /tasks"
            else:
                status = "Thêm tệp thất bại. Vui lòng thử lại."
            await msg.edit_text(status, parse_mode="Markdown")
        else:
            links = [{"name": u.split("/")[-1], "size": "?", "url": u} for u in file_urls]
            user_sessions[chat_id] = {"links": links, "created_at": _time.time()}
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
        await query.message.delete()
        msg = await query.message.reply_text("Đang cập nhật dashboard...")
        await _show_dashboard(msg)

    # ── Tasks ─────────────────────────────────────────────────────────────────
    elif data == "refresh_tasks":
        await query.message.delete()
        msg = await query.message.reply_text("Đang cập nhật danh sách tác vụ...")
        await _show_tasks(msg)

    elif data.startswith("tasks_page_"):
        page = int(data.split("_")[-1])
        await _show_tasks(query.message, page=page)

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
            f"Nhập số thứ tự các tệp muốn tải (1-{total}), cách nhau bằng dấu cách hoặc dấu phẩy.\n"
            f"Ví dụ: `1 3 5` hoặc `1, 3, 5`",
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
    chat_id = message.chat_id

    # Xoá flag huỷ cũ khi bắt đầu phiên mới
    cancel_flag.pop(chat_id, None)

    total_links   = len(links)
    total_success = 0
    total_failed  = 0

    # Chia links thành các batch
    batches = [links[i:i + BATCH_SIZE] for i in range(0, total_links, BATCH_SIZE)]
    total_batches = len(batches)

    if total_batches > 1:
        msg = await message.reply_text(
            f"Đang thêm *{total_links}* tệp vào Download Station "
            f"({total_batches} đợt, mỗi đợt tối đa {BATCH_SIZE} tệp)...",
            parse_mode="Markdown"
        )
    else:
        msg = await message.reply_text(
            f"Đang thêm *{total_links}* tệp vào Download Station...",
            parse_mode="Markdown"
        )

    for batch_idx, batch in enumerate(batches, 1):
        # Kiểm tra huỷ trước mỗi batch
        if cancel_flag.get(chat_id):
            cancel_flag.pop(chat_id, None)
            await msg.edit_text(
                f"Đã huỷ. Đã thêm *{total_success}* tệp trước khi huỷ.\n\nKiểm tra tiến độ tại /tasks",
                parse_mode="Markdown"
            )
            return

        if total_batches > 1:
            await msg.edit_text(
                f"Đang thêm đợt *{batch_idx}/{total_batches}* "
                f"({len(batch)} tệp)...",
                parse_mode="Markdown"
            )

        tasks_before = {t["id"] for t in ds_task_list()}

        success_items = []
        failed = 0
        for item in batch:
            if ds_add_task(item["url"]):
                success_items.append(item)
            else:
                failed += 1

        total_success += len(success_items)
        total_failed  += failed

        # Tao phien tai cho batch nay
        if success_items:
            try:
                tasks_after  = {t["id"] for t in ds_task_list()}
                new_task_ids = list(tasks_after - tasks_before)
                session_counter += 1
                sid = session_counter
                download_sessions[sid] = {
                    "task_ids":   new_task_ids,
                    "names":      [item.get("name", "") for item in success_items],
                    "chat_id":    message.chat_id,
                    "done_ids":   set(),
                    "error_ids":  set(),
                    "start_time": int(_time.time()),
                }
            except Exception as e:
                logger.error(f"Lỗi tạo phiên tải batch {batch_idx}: {e}")

        # Nghỉ giữa các batch để tránh quá tải DS
        if batch_idx < total_batches:
            await asyncio.sleep(2)

    # Thông báo kết quả cuối
    if total_success > 0 and total_failed == 0:
        result = (
            f"Đã thêm *{total_success}* tệp vào Download Station và đang tải về.\n\n"
            f"Kiểm tra tiến độ tại /tasks"
        )
    elif total_success > 0 and total_failed > 0:
        result = (
            f"Đã thêm *{total_success}* tệp vào Download Station và đang tải về.\n"
            f"Thất bại: *{total_failed}* tệp.\n\n"
            f"Kiểm tra tiến độ tại /tasks"
        )
    else:
        result = "Thêm tệp thất bại. Vui lòng thử lại."

    await msg.edit_text(result, parse_mode="Markdown")

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

                done_tasks   = [current[tid] for tid in session["done_ids"] if tid in current]
                total_size   = sum(int(t.get("size", 0)) for t in done_tasks)

                # Tinh tong thoi gian tai (giay)
                elapsed = int(_time.time()) - session.get("start_time", int(_time.time()))
                if elapsed >= 3600:
                    elapsed_str = f"{elapsed // 3600} giờ {(elapsed % 3600) // 60} phút"
                elif elapsed >= 60:
                    elapsed_str = f"{elapsed // 60} phút"
                else:
                    elapsed_str = f"{elapsed} giây"

                lines = [
                    f"*[Phiên {sid}] Hoàn tất*",
                    f"`{done_count} tệp  |  {format_size(total_size)}  |  {elapsed_str}`",
                ]
                if error_count:
                    lines.append(f"Lỗi: *{error_count}* tệp")
                lines.append("")
                for t in done_tasks[:8]:
                    lines.append(f"  - {t['title'][:40]}")
                if len(done_tasks) > 8:
                    lines.append(f"  ...và {len(done_tasks)-8} tệp khác")

                await context.bot.send_message(
                    session["chat_id"],
                    "\n".join(lines),
                    parse_mode="Markdown"
                )
                finished_sessions.append(sid)

        # Xoá phiên đã hoàn tất
        for sid in finished_sessions:
            del download_sessions[sid]

        # Xoá download_sessions và user_sessions quá hạn TTL
        now = _time.time()

        expired_dl = [
            sid for sid, s in download_sessions.items()
            if now - s.get("start_time", now) > DS_SESSION_TTL
        ]
        for sid in expired_dl:
            del download_sessions[sid]
            logger.info(f"Đã xoá phiên tải hết hạn: session {sid}")

        expired_sessions = [
            cid for cid, s in user_sessions.items()
            if now - s.get("created_at", now) > SESSION_TTL
        ]
        for cid in expired_sessions:
            del user_sessions[cid]
            logger.info(f"Đã xoá session hết hạn của chat_id {cid}")

        # Canh bao disk
        free, total, _ = ds_disk_info()
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

        # Giới hạn kích thước prev_tasks — xoá các entry cũ nhất nếu vượt giới hạn
        if len(current) > PREV_TASKS_LIMIT:
            excess = list(current.keys())[:-PREV_TASKS_LIMIT]
            for k in excess:
                del current[k]

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
        BotCommand("cancel", "Huỷ tiến trình đang thực thi"),
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

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("info",   cmd_info))
    app.add_handler(CommandHandler("tasks",  cmd_tasks))
    app.add_handler(CommandHandler("add",    cmd_add))
    app.add_handler(CommandHandler("clear",  cmd_clear))
    app.add_handler(CommandHandler("done",   cmd_done))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Push notification job
    app.job_queue.run_repeating(check_tasks, interval=POLL_INTERVAL, first=10)

    logger.info("Fshare Bot đang khởi động...")
    app.run_polling()

if __name__ == "__main__":
    main()
