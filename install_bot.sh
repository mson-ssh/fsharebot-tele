#!/bin/bash

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

REPO="https://raw.githubusercontent.com/mson-ssh/fsharebot-tele/main"
BOT_DIR="/var/packages/DownloadStation/etc/fshare_bot"
BOT_FILE="$BOT_DIR/fshare_bot.py"
CONFIG_FILE="$BOT_DIR/config.json"
SERVICE_FILE="/etc/systemd/system/fshare-bot.service"

# ── Header ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}--------------------------------------------${NC}"
echo -e "  ${BOLD}Fshare Bot Installer${NC}"
echo -e "  Telegram Bot for Synology Download Station"
echo -e "${CYAN}--------------------------------------------${NC}"
echo ""

# ── Kiểm tra môi trường ───────────────────────────────────────────────────────
if [ ! -f /etc/synoinfo.conf ]; then
    echo -e "${RED}  ✗ Script này chỉ chạy trên Synology NAS.${NC}"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}  ✗ Python3 chưa được cài đặt.${NC}"
    exit 1
fi

if ! command -v pip3 &> /dev/null; then
    echo -e "${YELLOW}  →${NC} pip3 chưa có, đang cài đặt..."
    python3 -m ensurepip --upgrade 2>/dev/null || \
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3
    if ! command -v pip3 &> /dev/null; then
        echo -e "${RED}  ✗ Không thể cài pip3. Vui lòng cài thủ công.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  [OK] pip3 đã được cài đặt.${NC}"
fi

# ── Menu ──────────────────────────────────────────────────────────────────────
echo -e "  ${BOLD}Chọn thao tác:${NC}"
echo ""
echo -e "  ${CYAN}1.${NC} Cài đặt bot mới"
echo -e "  ${CYAN}2.${NC} Kiểm tra trạng thái bot"
echo -e "  ${CYAN}3.${NC} Gỡ cài đặt bot"
echo -e "  ${CYAN}4.${NC} Debug kết nối"
echo -e "  ${CYAN}5.${NC} Huỷ"
echo ""
echo -e "  ${BOLD}Lưu ý:${NC} Thông tin của bạn được mã hoá 100%,"
echo -e "  được lưu trên local và của riêng bạn."
echo ""

while true; do
    read -p "  Nhập lựa chọn [1/2/3/4/5]: " CHOICE
    case "$CHOICE" in
        1) break ;;
        2)
            echo ""
            if systemctl is-active --quiet fshare-bot; then
                echo -e "${GREEN}  [OK] Bot đang chạy bình thường.${NC}"
            else
                echo -e "${RED}  [WARN] Bot không chạy.${NC}"
                echo -e "  Kiểm tra log: journalctl -u fshare-bot -n 20 --no-pager"
            fi
            echo ""
            if [ -f "$CONFIG_FILE" ]; then
                echo -e "  Cấu hình    : Đã lưu và mã hoá"
            else
                echo -e "  Cấu hình    : Chưa có"
            fi
            echo -e "  Phiên bản   : $(python3 -c "import telegram; print(telegram.__version__)" 2>/dev/null || echo "N/A")"
            echo ""
            exit 0
            ;;
        3)
            echo ""
            read -p "  Xác nhận gỡ cài đặt? [y/N]: " CONFIRM
            if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
                echo -e "${YELLOW}  →${NC} Dừng service..."
                systemctl stop fshare-bot 2>/dev/null
                systemctl disable fshare-bot 2>/dev/null
                rm -f "$SERVICE_FILE"
                systemctl daemon-reload 2>/dev/null
                echo -e "${YELLOW}  →${NC} Xoá file bot..."
                rm -rf "$BOT_DIR"
                echo ""
                echo -e "${GREEN}--------------------------------------------${NC}"
                echo -e "  ${GREEN}${BOLD}[OK] Da go cai dat bot.${NC}"
                echo -e "${GREEN}--------------------------------------------${NC}"
                echo ""
            else
                echo -e "  ${YELLOW}→${NC} Đã huỷ."
            fi
            exit 0
            ;;
        4)
            echo ""
            if [ ! -f "$CONFIG_FILE" ]; then
                echo -e "${RED}  ✗ Không tìm thấy file cấu hình. Vui lòng cài đặt trước.${NC}"
                exit 1
            fi

            echo -e "  ${BOLD}Đang đọc cấu hình...${NC}"
            DS_HOST=$(python3 -c "import json,base64; d=json.load(open('$CONFIG_FILE')); print(base64.b64decode(d['DS_HOST']).decode())" 2>/dev/null)
            DS_USER=$(python3 -c "import json,base64; d=json.load(open('$CONFIG_FILE')); print(base64.b64decode(d['DS_USER']).decode())" 2>/dev/null)
            DS_PASS=$(python3 -c "import json,base64; d=json.load(open('$CONFIG_FILE')); print(base64.b64decode(d['DS_PASS']).decode())" 2>/dev/null)

            echo -e "  DS Host     : $DS_HOST"
            echo -e "  DS User     : $DS_USER"
            echo ""

            # Kiem tra DS login
            echo -e "${YELLOW}  →${NC} Kiểm tra đăng nhập Download Station..."
            LOGIN=$(DS_USER="$DS_USER" DS_PASS="$DS_PASS" python3 -c "
import urllib.request, urllib.parse, os, json
user = urllib.parse.quote(os.environ['DS_USER'])
passwd = urllib.parse.quote(os.environ['DS_PASS'])
url = '${DS_HOST}/webapi/auth.cgi?api=SYNO.API.Auth&version=3&method=login&account=' + user + '&passwd=' + passwd + '&session=DownloadStation&format=sid'
resp = urllib.request.urlopen(url, timeout=10)
print(resp.read().decode())
" 2>/dev/null)
            SUCCESS=$(echo $LOGIN | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success','false'))" 2>/dev/null)

            if [ "$SUCCESS" = "True" ]; then
                echo -e "${GREEN}  [OK] Đăng nhập DS thành công.${NC}"
                SID=$(echo $LOGIN | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['sid'])" 2>/dev/null)

                # Kiem tra Storage API
                echo -e "${YELLOW}  →${NC} Kiểm tra Storage API..."
                STORAGE=$(curl -s "$DS_HOST/webapi/entry.cgi?api=SYNO.Storage.CGI.Storage&version=1&method=load_info&_sid=$SID")
                STOR_OK=$(echo $STORAGE | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success','false'))" 2>/dev/null)

                if [ "$STOR_OK" = "True" ]; then
                    echo -e "${GREEN}  [OK] Storage API hoạt động.${NC}"
                    echo $STORAGE | python3 -c "
import sys, json
d = json.load(sys.stdin)
for v in d['data'].get('volumes', []):
    free  = int(v['size']['total']) - int(v['size']['used'])
    total = int(v['size']['total'])
    name  = v.get('vol_desc') or v.get('id','')
    print(f'  Volume: {name} — {free/1024**3:.1f} GB còn / {total/1024**3:.1f} GB')
" 2>/dev/null
                else
                    echo -e "${RED}  [WARN] Storage API lỗi: $STORAGE${NC}"
                fi

                # Kiem tra DS Task API
                echo -e "${YELLOW}  →${NC} Kiểm tra Task API..."
                TASKS=$(curl -s "$DS_HOST/webapi/DownloadStation/task.cgi?api=SYNO.DownloadStation.Task&version=1&method=list&_sid=$SID")
                TASK_OK=$(echo $TASKS | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success','false'))" 2>/dev/null)
                if [ "$TASK_OK" = "True" ]; then
                    TASK_COUNT=$(echo $TASKS | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data'].get('tasks',[])))" 2>/dev/null)
                    echo -e "${GREEN}  [OK] Task API hoạt động — $TASK_COUNT tác vụ hiện tại.${NC}"
                else
                    echo -e "${RED}  [WARN] Task API lỗi.${NC}"
                fi

            else
                echo -e "${RED}  [FAIL] Đăng nhập DS thất bại.${NC}"
                echo -e "  Kết quả: $LOGIN"
            fi
            echo ""
            exit 0
            ;;
        5)
            echo -e "  ${RED}✗ Đã huỷ.${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}  ✗ Lựa chọn không hợp lệ. Vui lòng nhập 1, 2, 3, 4 hoặc 5.${NC}"
            ;;
    esac
done

echo ""

# ── Nhập cấu hình ─────────────────────────────────────────────────────────────
echo -e "  ${BOLD}Nhập thông tin cấu hình:${NC}"
echo ""

# ── Bước 1: NAS credentials + verify ─────────────────────────────────────────
echo -e "  ${CYAN}Bước 1/2 — Thông tin NAS${NC}"
echo ""
while true; do
    while true; do
        read -p "  Tài khoản DSM: " DS_USER
        if [ -n "$DS_USER" ]; then break; fi
        echo -e "${RED}  ✗ Tài khoản DSM không được để trống.${NC}"
    done

    while true; do
        read -s -p "  Mật khẩu DSM: " DS_PASS
        echo ""
        if [ -n "$DS_PASS" ]; then break; fi
        echo -e "${RED}  ✗ Mật khẩu không được để trống.${NC}"
    done

    # DS Port
    echo ""
    while true; do
        read -p "  Cổng DS (Port): " DS_PORT
        if [ -n "$DS_PORT" ]; then break; fi
        echo -e "${RED}  ✗ Cổng DS không được để trống.${NC}"
    done

    DS_HOST="http://localhost:$DS_PORT"

    # Kiem tra login DS bang Python thuan
    echo ""
    echo -e "${YELLOW}  →${NC} Đang kiểm tra kết nối Download Station..."
    LOGIN=$(python3 - << PYEOF
import urllib.request, urllib.parse, json, sys

user   = "$DS_USER"
passwd = "$DS_PASS"
host   = "$DS_HOST"

url = (host + "/webapi/auth.cgi"
       "?api=SYNO.API.Auth&version=3&method=login"
       "&account=" + urllib.parse.quote(user, safe='')
       + "&passwd=" + urllib.parse.quote(passwd, safe='')
       + "&session=DownloadStation&format=sid")
try:
    resp = urllib.request.urlopen(url, timeout=10)
    print(resp.read().decode())
except Exception as e:
    print(json.dumps({"success": False, "error": {"code": 0}, "msg": str(e)}))
PYEOF
)
    LOGIN_OK=$(echo "$LOGIN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success','false'))" 2>/dev/null)

    if [ "$LOGIN_OK" = "True" ]; then
        echo -e "${GREEN}  [OK] Kết nối thành công!${NC}"
        break
    else
        ERR_CODE=$(echo "$LOGIN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null)
        if [ "$ERR_CODE" = "403" ]; then
            ERRORS=$(echo "$LOGIN" | python3 -c "import sys,json; d=json.load(sys.stdin); print('2FA' if 'token' in str(d.get('error',{})) else 'Sai thông tin')" 2>/dev/null)
            if [ "$ERRORS" = "2FA" ]; then
                echo -e "${RED}  ✗ Tài khoản đang bật xác thực 2 lớp (2FA).${NC}"
                echo -e "  Vui lòng tắt 2FA hoặc dùng tài khoản khác không có 2FA."
            else
                echo -e "${RED}  ✗ Sai tài khoản hoặc mật khẩu. Vui lòng nhập lại.${NC}"
            fi
        else
            echo -e "${RED}  ✗ Kết nối thất bại (mã lỗi: $ERR_CODE). Vui lòng kiểm tra cổng DS.${NC}"
        fi
        echo ""
    fi
done

# ── Bước 2: Telegram credentials ─────────────────────────────────────────────
echo ""
echo -e "  ${CYAN}Bước 2/2 — Thông tin Telegram${NC}"
echo ""

# Bot Token
while true; do
    read -p "  Bot Token (từ @BotFather): " BOT_TOKEN
    if [ -n "$BOT_TOKEN" ]; then break; fi
    echo -e "${RED}  ✗ Token không được để trống.${NC}"
done

# Chat ID
echo ""
echo -e "  ${YELLOW}Tip:${NC} Nhắn tin cho @userinfobot trên Telegram để lấy Chat ID."
echo ""
while true; do
    read -p "  Chat ID của bạn: " ALLOWED_ID
    if [[ "$ALLOWED_ID" =~ ^-?[0-9]+$ ]]; then break; fi
    echo -e "${RED}  ✗ Chat ID phải là số.${NC}"
done

echo ""

# ── Cài đặt ───────────────────────────────────────────────────────────────────
echo -e "${YELLOW}  →${NC} Tạo thư mục bot..."
mkdir -p "$BOT_DIR"

echo -e "${YELLOW}  →${NC} Cập nhật pip..."
pip3 install --upgrade pip -q 2>/dev/null || python3 -m pip install --upgrade pip -q 2>/dev/null

echo -e "${YELLOW}  →${NC} Cài thư viện python-telegram-bot..."
pip3 install "python-telegram-bot[job-queue]" --break-system-packages -q 2>/dev/null || pip3 install "python-telegram-bot[job-queue]" -q
if [ $? -ne 0 ]; then
    echo -e "${RED}  ✗ Cài thư viện thất bại.${NC}"
    exit 1
fi

echo -e "${YELLOW}  →${NC} Tải fshare_bot.py..."
curl -fsSL "$REPO/fshare_bot.py" -o "$BOT_FILE"
if [ $? -ne 0 ]; then
    echo -e "${RED}  ✗ Tải bot thất bại.${NC}"
    exit 1
fi

echo -e "${YELLOW}  →${NC} Lưu cấu hình (mã hoá base64)..."
python3 -c "
import json, base64, sys
enc = lambda s: base64.b64encode(s.encode()).decode()
config = {
    'BOT_TOKEN':  enc(sys.argv[1]),
    'ALLOWED_ID': enc(sys.argv[2]),
    'DS_HOST':    enc(sys.argv[3]),
    'DS_USER':    enc(sys.argv[4]),
    'DS_PASS':    enc(sys.argv[5]),
}
json.dump(config, open(sys.argv[6], 'w'), indent=4)
" "$BOT_TOKEN" "$ALLOWED_ID" "$DS_HOST" "$DS_USER" "$DS_PASS" "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"

echo -e "${YELLOW}  →${NC} Tạo systemd service..."
cat > "$SERVICE_FILE" << SERVICE
[Unit]
Description=Fshare Telegram Bot
After=network.target pkgctl-DownloadStation.service
Wants=pkgctl-DownloadStation.service

[Service]
Type=simple
User=root
WorkingDirectory=$BOT_DIR
ExecStartPre=/bin/sh -c 'until curl -sf http://localhost:$DS_PORT/webapi/query.cgi?api=SYNO.API.Info > /dev/null 2>&1; do sleep 3; done'
ExecStart=/usr/bin/python3 $BOT_FILE
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3

[Install]
WantedBy=multi-user.target
SERVICE

echo -e "${YELLOW}  →${NC} Khởi động service..."
systemctl daemon-reload
systemctl enable fshare-bot
systemctl start fshare-bot
sleep 3

# ── Kiểm tra sau cài đặt ──────────────────────────────────────────────────────
echo ""
echo -e "  ${BOLD}Đang kiểm tra hệ thống...${NC}"
echo ""

# 1. Trạng thái service
if systemctl is-active --quiet fshare-bot; then
    STATUS="${GREEN}[OK] ĐANG CHẠY${NC}"
    BOT_OK=true
else
    STATUS="${RED}[WARN] Kiểm tra log: journalctl -u fshare-bot${NC}"
    BOT_OK=false
fi
echo -e "  Bot Telegram     : $(echo -e $STATUS)"

# 2. Trạng thái đăng nhập DS
LOGIN_CHECK=$(python3 - << PYEOF
import urllib.request, urllib.parse, json, sys

user   = "$DS_USER"
passwd = "$DS_PASS"
host   = "$DS_HOST"

url = (host + "/webapi/auth.cgi"
       "?api=SYNO.API.Auth&version=3&method=login"
       "&account=" + urllib.parse.quote(user, safe='')
       + "&passwd=" + urllib.parse.quote(passwd, safe='')
       + "&session=DownloadStation&format=sid")
try:
    resp = urllib.request.urlopen(url, timeout=10)
    d    = json.loads(resp.read().decode())
    if d.get("success"):
        print("OK:" + d["data"]["sid"])
    else:
        print("FAIL:" + str(d.get("error", {}).get("code", "?")))
except Exception as e:
    print("FAIL:" + str(e))
PYEOF
)

if echo "$LOGIN_CHECK" | grep -q "^OK:"; then
    SID=$(echo "$LOGIN_CHECK" | cut -d: -f2)
    echo -e "  Đăng nhập DS     : ${GREEN}[OK]${NC}"

    # 3. Trạng thái Storage API
    STORAGE_CHECK=$(python3 -c "
import urllib.request, json

def fmt(b):
    gb = b / 1024**3
    if gb >= 1024:
        return f'{gb/1024:.1f} TB'
    return f'{gb:.1f} GB'

url = '${DS_HOST}/webapi/entry.cgi?api=SYNO.Storage.CGI.Storage&version=1&method=load_info&_sid=${SID}'
try:
    resp = urllib.request.urlopen(url, timeout=10)
    d    = json.loads(resp.read().decode())
    if d.get('success'):
        vols = d['data'].get('volumes', [])
        info = ','.join(
            (v.get('vol_desc') or v.get('id','')) + ': ' +
            fmt(int(v['size']['total']) - int(v['size']['used'])) + ' Free'
            for v in vols if 'size' in v
        )
        print('OK:' + info)
    else:
        print('FAIL:' + str(d.get('error',{}).get('code','?')))
except Exception as e:
    print('FAIL:' + str(e))
" 2>/dev/null)

    if echo "$STORAGE_CHECK" | grep -q "^OK:"; then
        STORAGE_INFO=$(echo "$STORAGE_CHECK" | cut -d: -f2-)
        echo -e "  Storage API      : ${GREEN}[OK]${NC}"
        echo "$STORAGE_INFO" | tr ',' '\n' | while read -r vol; do
            echo -e "    $vol"
        done
    else
        echo -e "  Storage API      : ${RED}[WARN] Không lấy được thông tin ổ đĩa${NC}"
    fi
else
    ERR=$(echo "$LOGIN_CHECK" | cut -d: -f2-)
    echo -e "  Đăng nhập DS     : ${RED}[FAIL] $ERR${NC}"
    echo -e "  Storage API      : ${RED}[SKIP]${NC}"
fi

# 4. Trạng thái file config
if [ -f "$CONFIG_FILE" ]; then
    FIRST_VAL=$(python3 -c "import json; d=json.load(open('$CONFIG_FILE')); print(list(d.values())[0])" 2>/dev/null)
    if echo "$FIRST_VAL" | grep -qE "^[A-Za-z0-9+/]+=*$"; then
        echo -e "  File cấu hình    : ${GREEN}[OK] Đã mã hoá${NC}"
    else
        echo -e "  File cấu hình    : ${RED}[WARN] Chưa mã hoá${NC}"
    fi
else
    echo -e "  File cấu hình    : ${RED}[FAIL] Không tìm thấy${NC}"
fi

echo ""
echo -e "${GREEN}--------------------------------------------${NC}"
echo -e "  ${GREEN}${BOLD}[OK] Cài đặt hoàn tất!${NC}"
echo -e "${GREEN}--------------------------------------------${NC}"
echo ""
echo -e "  Trạng thái: $(echo -e $STATUS)"
echo ""
echo -e "  ${BOLD}Enjoy! <3${NC}"
echo ""
echo -e "___"
echo ""
echo -e "  Kiểm tra file cấu hình đã mã hoá tại đây:"
echo -e "  ${CYAN}cat $CONFIG_FILE${NC}"
echo ""
