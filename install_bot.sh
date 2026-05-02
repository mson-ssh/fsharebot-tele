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
    echo -e "${RED}  ✗ pip3 chưa được cài đặt.${NC}"
    exit 1
fi

# ── Menu ──────────────────────────────────────────────────────────────────────
echo -e "  ${BOLD}Chọn thao tác:${NC}"
echo ""
echo -e "  ${CYAN}1.${NC} Cài đặt bot mới"
echo -e "  ${CYAN}2.${NC} Cập nhật bot"
echo -e "  ${CYAN}3.${NC} Gỡ cài đặt bot"
echo -e "  ${CYAN}4.${NC} Huỷ"
echo ""

while true; do
    read -p "  Nhập lựa chọn [1/2/3/4]: " CHOICE
    case "$CHOICE" in
        1) break ;;
        2)
            if [ -f "$CONFIG_FILE" ]; then
                echo ""
                echo -e "${YELLOW}  →${NC} Tim thay config cu, dang cap nhat bot..."
                systemctl stop fshare-bot 2>/dev/null
                curl -fsSL "$REPO/fshare_bot.py" -o "$BOT_FILE"
                if [ $? -ne 0 ]; then
                    echo -e "${RED}  ✗ Tai fshare_bot.py that bai.${NC}"
                    exit 1
                fi
                systemctl daemon-reload 2>/dev/null
                systemctl start fshare-bot 2>/dev/null
                sleep 2
                if systemctl is-active --quiet fshare-bot; then
                    echo -e "${GREEN}  [OK] Cap nhat hoan tat! Bot dang chay.${NC}"
                else
                    echo -e "${RED}  [WARN] Kiem tra log: journalctl -u fshare-bot${NC}"
                fi
                echo ""
                exit 0
            else
                echo -e "${RED}  ✗ Khong tim thay config cu. Vui long chon 1 de cai moi.${NC}"
            fi
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
            echo -e "  ${RED}✗ Đã huỷ.${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}  ✗ Lựa chọn không hợp lệ.${NC}"
            ;;
    esac
done

echo ""

# ── Nhập cấu hình ─────────────────────────────────────────────────────────────
echo -e "  ${BOLD}Nhập thông tin cấu hình:${NC}"
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

# DS credentials
echo ""
read -p "  Tài khoản DSM (mặc định: admin): " DS_USER
DS_USER="${DS_USER:-admin}"

while true; do
    read -s -p "  Mật khẩu DSM: " DS_PASS
    echo ""
    if [ -n "$DS_PASS" ]; then break; fi
    echo -e "${RED}  ✗ Mật khẩu không được để trống.${NC}"
done

# DS Port
echo ""
read -p "  Port DS (mặc định: 2026): " DS_PORT
DS_PORT="${DS_PORT:-2026}"
DS_HOST="http://localhost:$DS_PORT"

echo ""

# ── Cài đặt ───────────────────────────────────────────────────────────────────
echo -e "${YELLOW}  →${NC} Tạo thư mục bot..."
mkdir -p "$BOT_DIR"

echo -e "${YELLOW}  →${NC} Cài thư viện python-telegram-bot..."
pip3 install python-telegram-bot --break-system-packages -q
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

echo -e "${YELLOW}  →${NC} Lưu cấu hình..."
cat > "$CONFIG_FILE" << CONF
{
    "BOT_TOKEN":  "$BOT_TOKEN",
    "ALLOWED_ID": $ALLOWED_ID,
    "DS_HOST":    "$DS_HOST",
    "DS_USER":    "$DS_USER",
    "DS_PASS":    "$DS_PASS"
}
CONF
chmod 600 "$CONFIG_FILE"

echo -e "${YELLOW}  →${NC} Tạo systemd service..."
cat > "$SERVICE_FILE" << SERVICE
[Unit]
Description=Fshare Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/python3 $BOT_FILE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

echo -e "${YELLOW}  →${NC} Khởi động service..."
systemctl daemon-reload
systemctl enable fshare-bot
systemctl start fshare-bot
sleep 2

# Kiểm tra service đã chạy chưa
if systemctl is-active --quiet fshare-bot; then
    STATUS="${GREEN}[OK] Dang chay${NC}"
else
    STATUS="${RED}[WARN] Kiem tra log: journalctl -u fshare-bot${NC}"
fi

echo ""
echo -e "${GREEN}--------------------------------------------${NC}"
echo -e "  ${GREEN}${BOLD}[OK] Cai dat hoan tat!${NC}"
echo -e "${GREEN}--------------------------------------------${NC}"
echo ""
echo -e "  ${BOLD}Trang thai bot:${NC} $(echo -e $STATUS)"
echo ""
echo -e "  ${BOLD}Cac lenh tren Telegram:${NC}"
echo -e "  ${CYAN}/start${NC}  — Bat dau"
echo -e "  ${CYAN}/status${NC} — Task dang tai"
echo -e "  ${CYAN}/done${NC}   — File da xong"
echo ""
echo -e "  ${BOLD}Enjoy! <3${NC}"
echo ""
