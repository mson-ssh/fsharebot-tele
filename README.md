# Hướng dẫn sử dụng Fshare Telegram Bot

Bot Telegram hỗ trợ quản lý tải xuống Fshare trực tiếp trên Synology NAS thông qua giao diện Telegram. Bạn có thể thêm link tải, theo dõi tiến độ và quản lý tác vụ mà không cần mở giao diện Download Station.

---

## Yêu cầu

- Synology NAS đã cài Download Station
- Tài khoản Fshare.vn VIP
- Telegram đã đăng nhập trên điện thoại hoặc máy tính

---

## Cài đặt

### Bước 1. Tạo Bot Token

1. Mở Telegram, tìm **@BotFather**
2. Nhắn `/newbot`
3. Đặt tên và username cho bot
4. Sao chép **Bot Token** được cấp (dạng `123456789:AAxxxxxx`)

### Bước 2. Lấy Chat ID

1. Tìm **@userinfobot** trên Telegram
2. Nhắn `/start`
3. Sao chép số **Id** trong kết quả trả về (dạng `123456789`)

### Bước 3. Chạy lệnh cài đặt trên NAS

Kết nối SSH vào NAS và chạy:

```bash
curl -fsSL https://raw.githubusercontent.com/mson-ssh/fsharebot-tele/main/install_bot.sh -o /tmp/install_bot.sh && bash /tmp/install_bot.sh
```

> Nếu gặp lỗi quyền truy cập, hãy chạy `sudo -i` trước rồi thử lại.

### Bước 4. Nhập thông tin cấu hình

Script sẽ lần lượt hỏi:

| Thông tin | Mô tả |
|-----------|-------|
| Bot Token | Token lấy từ @BotFather |
| Chat ID | ID lấy từ @userinfobot |
| Tài khoản DSM | Tên đăng nhập DSM (ví dụ: admin) |
| Mật khẩu DSM | Mật khẩu tài khoản DSM |
| Port DS | Port Download Station đang dùng (ví dụ: 5000) |

> **Lưu ý:** Toàn bộ thông tin được mã hoá 100% và lưu trên thiết bị của bạn.

---

## Các lệnh

Sau khi cài đặt, tìm bot trên Telegram và nhắn `/start` để bắt đầu.

| Lệnh | Chức năng |
|------|-----------|
| `/start` | Khởi động bot, hiển thị menu |
| `/add` | Hướng dẫn thêm link tải |
| `/info` | Xem tổng quan hệ thống |
| `/tasks` | Quản lý tác vụ đang tải |
| `/done` | Xem danh sách tệp đã hoàn tất |
| `/clear` | Xoá tất cả tác vụ đã xong |

---

## Hướng dẫn sử dụng

### Thêm link tải

Bạn có thể gửi link trực tiếp vào ô chat mà không cần qua menu. Bot tự động nhận diện tất cả các dạng link Fshare:

```
https://www.fshare.vn/file/XXXXXXXXXX
https://fshare.vn/file/XXXXXXXXXX
www.fshare.vn/file/XXXXXXXXXX
fshare.vn/file/XXXXXXXXXX
```

**Thêm nhiều link cùng lúc:** Dán nhiều link vào một tin nhắn, mỗi link một dòng.

---

### Tải thư mục Fshare

Gửi link thư mục vào ô chat:

```
https://www.fshare.vn/folder/XXXXXXXXXX
```

Bot sẽ lấy danh sách toàn bộ tệp và hiển thị để bạn chọn:

```
Tìm thấy 8 tệp:

1. Episode.01.mkv — 1.4 GB
2. Episode.02.mkv — 1.3 GB
3. Episode.03.mkv — 1.5 GB
...

[Tải tất cả]  [Tải theo mục]
[Huỷ]
```

- **Tải tất cả** — Thêm toàn bộ tệp vào Download Station
- **Tải theo mục** — Nhập số thứ tự các tệp muốn tải, ví dụ: `1 3 5` hoặc `1, 3, 5`

---

### Dashboard

Nhắn `/info` để xem tổng quan hệ thống:

```
Dashboard
+------------------+------------------+
| Đang tải         | 3                |
| Tạm dừng         | 0                |
| Lỗi              | 0                |
| Hoàn tất         | 12               |
+------------------+------------------+
| Tốc độ tải       | 15.2 MB/s        |
| Tốc độ up        | 0 B/s            |
+------------------+------------------+
| SSD Disk         | 94.0 GB còn  43% |
| HDD              | 4.1 TB còn   65% |
+------------------+------------------+
```

Bấm **Làm mới** để cập nhật số liệu mới nhất.

---

### Quản lý tác vụ

Nhắn `/tasks` để xem và quản lý tác vụ:

```
Danh sách tác vụ (trang 1/2):

1. Episode.01.mkv
   [########--] 85% - 12.1 MB/s
2. Episode.02.mkv
   [###-------] 30% - 8.4 MB/s
...

[Tạm dừng tất cả]  [Tiếp tục tất cả]
[Xoá đã xong]      [Khởi động lại lỗi]
[Làm mới]
```

Nếu có nhiều tác vụ, sử dụng nút **Trang trước** / **Trang sau** để điều hướng.

---

### Thông báo tự động

Bot tự động gửi thông báo khi:

- **Hoàn tất phiên tải** — Tổng hợp toàn bộ tệp vừa tải xong trong một tin nhắn duy nhất:

```
[Phiên 1] Hoàn tất
3 tệp  |  4.1 GB  |  12 phút
  - Episode.01.mkv
  - Episode.02.mkv
  - Episode.03.mkv
```

- **Tác vụ bị lỗi** — Thông báo tên tệp bị lỗi để bạn xử lý kịp thời
- **NAS sắp đầy** — Cảnh báo khi dung lượng còn lại dưới 50 GB

---

## Cập nhật bot

Để cập nhật bot lên phiên bản mới nhất, chạy lại lệnh cài đặt và chọn **1**. Thông tin đăng nhập sẽ được nhập lại và config được mã hoá mới.

---

## Gỡ cài đặt

Chạy lại lệnh cài đặt và chọn **3. Gỡ bot Fshare** — script sẽ xoá toàn bộ file và dừng service.

---

## Kiểm tra trạng thái

Chạy lại lệnh cài đặt và chọn **2. Kiểm tra trạng thái bot** để xem bot có đang hoạt động không.

Hoặc kiểm tra trực tiếp qua SSH:

```bash
systemctl status fshare-bot
journalctl -u fshare-bot -n 20 --no-pager
```

---

## Câu hỏi thường gặp

**Bot không phản hồi?**
Kiểm tra bot có đang chạy không bằng `systemctl status fshare-bot`. Nếu dừng, chạy `systemctl start fshare-bot`.

**Verify báo lỗi trong File Hosting?**
Tài khoản Fshare của bạn có thể không phải VIP. Plugin chỉ hỗ trợ tài khoản VIP.

**Tải xong nhưng không có thông báo?**
Kiểm tra Chat ID đã nhập đúng chưa. Bot chỉ gửi thông báo đến đúng Chat ID được cấu hình.

**Muốn đổi mật khẩu DSM?**
Chạy lại script cài đặt, chọn **1** và nhập thông tin mới.
