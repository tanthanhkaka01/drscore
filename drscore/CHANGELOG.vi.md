# Các phiên bản DRS

Mỗi lần sửa code đều tăng phiên bản (`pyproject.toml`): sửa lỗi tăng số cuối (1.6.3 → 1.6.4), thêm chức năng tăng số giữa (1.6.4 → 1.7.0). Phiên bản hiện ở cuối menu trái và ở trang đăng nhập; bấm vào để xem các thay đổi, và bản mới tự hiện "Có gì mới" một lần trên mỗi máy. Mỗi dòng ghi cho ai đọc: `[admin]` chỉ quản trị, `[designer]` người thiết kế báo cáo và quản trị, không ghi là mọi người; bản không có dòng nào cho một tài khoản thì tài khoản đó thấy "Cải thiện hệ thống và sửa lỗi nhỏ." Bản tiếng Anh ở `CHANGELOG.md`, cùng các phiên bản; bộ kiểm thử không chạy qua nếu phiên bản trong `pyproject.toml` chưa được ghi ở đầu cả hai file.

## 1.10.0 — 2026-11-26
- **Giao diện mới, theo bộ giao diện chung (kit 1.7.0) của dbabrain.dev**: chữ Manrope có dấu tiếng Việt, màu chàm, góc bo mềm, sáng / tối tự theo máy hoặc theo nút trên thanh trên cùng. Menu trái sáng khi giao diện sáng; mỗi mục có **biểu tượng trên ô màu**, mỗi nhóm báo cáo một màu, trang chủ dùng lại đúng màu đó trên thẻ của nhóm.
- Trang đăng nhập và trang báo cáo mở bằng link cũng theo giao diện mới.
- [admin] **Trang quản trị nằm chung khung với portal**: cùng menu trái, cùng thanh trên cùng (tên trang, VN / EN, sáng / tối, tài khoản, đăng xuất thật). Không còn menu tối riêng và nút "Logout" không đăng xuất.
- [admin] **Menu gom nhóm lại**: **Quản trị** - Trang quản trị, Thiết lập báo cáo (nhóm, báo cáo, tham số, cột lưới, lọc dòng), Nguồn dữ liệu, Người dùng và quyền (người dùng, vai trò, thuộc tính, ba loại quyền), Chia sẻ và thông báo (link công khai, Telegram / Zalo, trợ lý AI); **Giám sát** - Góp ý, Nhật ký (tra cứu nhật ký, thao tác, truy cập, thay đổi, câu hỏi AI, thông báo đã gửi, lịch sử cache, bảng góp ý). Trang quản trị (/admin/) liệt kê các nhóm đó thành thẻ.
- [admin] Superset (DRS BI) cùng màu, chữ, bo góc và logo với portal, sáng và tối.
- [designer] **Hướng dẫn mới: từ nhiều file SQL đến một dashboard hoàn chỉnh** (`docs/dashboard-from-sql.md`): chuẩn bị SQL (bind `:name` đọc dạng chữ, có mặc định, không viết `:name` trong comment), mỗi file một report (tham số, test, cột, publish, chạy một lần), dựng chart trong Superset (loại chart nên dùng cho từng kiểu dữ liệu), sắp layout, tab và màu (`label_colors`), gắn dashboard vào một report chủ để đọc dataset của các report khác, link không cần đăng nhập (cấp quyền cho report chủ và mọi report thành viên; report có tên người thì để link cần đăng nhập), và cách xử lý khi dashboard không hiện. Có hai mẹo: lớp area xếp chồng bằng 0 vẫn vẽ viền, nên dùng `NULLIF(SUM(...), 0)`; ô KPI "so với hôm trước" sai sau ngày lễ. Viết từ một lần làm thật: 8 file SQL, 8 report, 17 chart, 2 tab.

## 1.9.0 — 2026-11-11
- **Tự sắp xếp các cột của bảng**: kéo tiêu đề cột để đổi chỗ; ẩn / hiện cột bằng nút **Cột** (tick / bỏ tick) hoặc menu của cột (⋮), menu này cũng **ghim cột sang trái** khi cuộn ngang. Trình duyệt nhớ cách sắp xếp, thứ tự sort và số dòng mỗi trang cho từng báo cáo; **Về mặc định** trong nút Cột trả lại như thiết kế.
- **File XLSX / CSV / TXT xuất theo đúng bảng đã sắp xếp**: các cột đang hiện, theo thứ tự trên màn hình - vẫn đủ mọi dòng. Cột mà thiết kế báo cáo không cho xuất thì vẫn không xuất.

## 1.8.0 — 2026-11-11
- [admin] **Link công khai có thể nhúng vào trang khác** (iframe): mỗi link, ở Hệ thống → Link công khai, ghi các trang được nhúng - `*` cho mọi trang, hoặc các trang như `https://hrms.example.com` - khi tạo link hoặc sửa sau ở dòng của link (`publiclink add|set --frame`). Để trống thì chỉ portal này, như trước; các trang khác của portal vẫn không cho trang ngoài nhúng.

## 1.7.2 — 2026-11-09
- [admin] **Hệ thống → Link công khai**: tạo link báo cáo ngay trên trang - chọn báo cáo, các nút (xuất file, làm mới), ai được mở (bất kỳ ai có địa chỉ, hoặc người đã đăng nhập), tham số người xem được đổi, tiêu đề, ngày hết hạn - và tắt / bật lại, chuyển giữa công khai và cần đăng nhập; địa chỉ chỉ hiện một lần. Không cần dòng lệnh.
- [admin] Tài khoản mặc định cho link, `user_public` (`[public] default_user`), được tạo sẵn khi khởi động, không có mật khẩu: tạo link cho nó thì nó được cấp báo cáo đó, kèm các nút đã chọn.
- [admin] Nhật ký thao tác ghi rõ các lần chạy và xuất file qua link, và các thay đổi trên trang Link công khai.

## 1.7.1 — 2026-11-09
- Link báo cáo dạng bảng (mở không cần đăng nhập) giờ hiện **chính trang báo cáo, không có menu**: cùng form tham số (ô chọn vẫn là ô chọn, có mã và tên), cùng các nút - **Làm mới dữ liệu, In / Lưu PDF, XLSX / CSV / TXT đủ mọi dòng** - và cùng bảng có lọc từng cột, sắp xếp, phân trang.
- [admin] Người xem link dùng được nút nào là theo quyền (grant) của tài khoản mà link chạy dưới tên (`--as`): xuất file và làm mới, như trong portal; form chỉ hiện các tham số link cho phép đổi.

## 1.7.0 — 2026-11-09
- **Góp ý** (menu trái): báo lỗi, đề xuất hay hỏi quản trị ngay từ trang đang xem, kèm ảnh chụp màn hình hoặc file (ảnh, PDF, XLSX, DOCX, CSV, TXT). Quản trị trả lời trong từng góp ý; bạn thấy trạng thái (mới, đã xem, đang xử lý, đã xong, không xử lý), viết thêm để mở lại, và bấm "Tôi đã kiểm tra" khi đã sửa xong.
- **Typhon**, trợ lý AI (menu trái, khi quản trị bật): hỏi báo cáo nào cho số liệu gì, tham số điền thế nào, cách dùng portal. Typhon chỉ thấy những gì tài khoản của bạn được thấy và không thay đổi gì.
- Phiên bản ở cuối menu trái: bấm vào để xem có gì mới; bản mới tự hiện "Có gì mới" một lần.
- **Giao diện mới** theo petweb: menu trái chia mục (Công cụ, Hệ thống), thu gọn còn biểu tượng; thanh trên cùng có tên trang, VN / EN, sáng / tối và tài khoản; nút **Góp ý** nổi ở góc phải mọi trang. Dùng tốt trên máy tính, iPad (dọc và ngang) và điện thoại: trên điện thoại menu kéo ra từ nút ☰, bảng dài thành từng thẻ, hộp thoại chiếm cả màn hình.
- [admin] Menu **Hệ thống → Quản trị hệ thống** (như petweb): **Nhật ký** (lọc theo ngày, tài khoản, việc, chỉ việc bị từ chối / lỗi, tìm; ba thẻ Thao tác, Xem báo cáo, Thay đổi dữ liệu; **Xuất Excel**), **Góp ý** dạng bảng, **Trợ lý AI** (tổng quan: trạng thái, chi phí tháng / ngân sách, token hôm nay và tháng; theo tài khoản, theo ngày, theo model; lịch sử hỏi; cài đặt), **Thông báo Telegram / Zalo** (kênh nào sẵn sàng, gửi thử, các tin đã gửi), và Trang quản trị.
- [admin] **Nhật ký thao tác** (Quản trị → Logs): mọi yêu cầu của người dùng - mỗi thay đổi kèm nội dung đã gửi (không bao giờ có mật khẩu, token, khóa), mỗi trang đã mở, mỗi yêu cầu bị từ chối hay lỗi - cả trang thiết kế, BI, link công khai và trang quản trị.
- [admin] Góp ý: thấy tất cả (số góp ý mới đỏ trên menu), trả lời, đặt trạng thái, loại, mức độ và bản đã sửa. Typhon đọc góp ý mới trước: trả lời người gửi và ghi chú cho quản trị.
- [admin] Thông báo vào **nhóm Telegram và nhóm Zalo**: góp ý mới, người gửi viết thêm hay mở lại, báo cáo chờ duyệt, Typhon hết ngân sách tháng. Mỗi kênh chỉ chạy khi có cả nhóm (`chat_id`) và token của bot.
  - Khóa AI và token bot cất **mã hóa** trong cơ sở dữ liệu DRS bằng `DRS_SECRET_KEY` (`python -m drscore secret set ...`), không bao giờ nằm trong file; `notify status`, `notify test`, `ai status` cho biết còn thiếu gì.
  - Trang log mới: Câu hỏi trợ lý AI (câu hỏi, trả lời, token, chi phí), Thông báo đã gửi.
- [admin] Cài đặt mới trong `app.toml`: `[activity]`, `[feedback]`, `[ai]`, `[notify]` (`docs/feedback-typhon-notices.md`). Migration 0019 (chỉ thêm bảng). Cần gói `anthropic` (image phải cài được từ PyPI).

- [admin] Typhon: chọn người được hỏi theo từng tài khoản (`[ai] allowed_users`) hoặc theo role (`allowed_roles`); report Typhon chạy sẽ dừng khi vượt `max_report_rows` dòng (mặc định 10 000) và Typhon xin người dùng thu hẹp tham số.

## 1.6.4 — 2026-11-09
- Portal mở được ở cả địa chỉ IP (http) và tên miền (https qua reverse proxy); dashboard hiện đúng ở cả hai.
- [admin] `[app] other_urls` và `[bi.superset] site_urls`; trang quản trị đủ kiểu dáng khi mở qua tên miền; `bi sync` cho mọi địa chỉ portal nhúng dashboard.

## 1.6.3 — 2026-10-26
- Link báo cáo: tham số được đổi hiện giá trị đang dùng, ngày là ô chọn ngày.

## 1.6.2 — 2026-10-26
- Link báo cáo có thể không công khai: mở link thì đăng nhập trước, rồi xem báo cáo bằng chính quyền của mình.

## 1.6.1 — 2026-10-26
- [admin] Số ngày đăng nhập riêng từng tài khoản (`user set-session`): tài khoản chỉ xem có thể giữ đăng nhập 30 ngày.

## 1.6.0 — 2026-10-24
- Danh sách chọn: người quyết định tick các lựa chọn trên một trang mở bằng link, báo cáo dùng ngay danh sách đã tick.

## 1.5.1 — 2026-10-22
- Link công khai hiện được bố cục HTML của báo cáo, tự làm mới.
- [admin] `publiclink set` đổi nội dung link mà giữ nguyên địa chỉ.

## 1.5.0 — 2026-10-22
- Link công khai: mở một báo cáo không cần đăng nhập (màn hình treo tường, người không có tài khoản).
- [admin] Mirror: chép bảng nguồn vào cơ sở dữ liệu DRS theo lịch, báo cáo nặng đọc bản chép thay vì nguồn production.

## 1.4.2 — 2026-10-15
- Nhãn trạng thái dễ đọc lại.

## 1.4.1 — 2026-10-15
- [admin] Tài khoản Superset riêng cho từng người dùng DRS.

## 1.4.0 — 2026-10-15
- Dashboard của một báo cáo hiện được biểu đồ trên dữ liệu của các báo cáo khác, mỗi báo cáo theo tham số của nó.

## 1.3.1 — 2026-10-10
- [designer] Trang BI liệt kê lại được các dashboard Superset.

## 1.3.0 — 2026-10-10
- [designer] Tự thiết kế báo cáo và dashboard; "Có thể xuất bản" để xuất bản không cần duyệt.

## 1.2.0 — 2026-10-10
- [designer] Vai trò thiết kế báo cáo: viết, chạy thử và đề xuất báo cáo, quản trị duyệt.

## 1.1.1 — 2026-10-08
- Cải thiện hệ thống và sửa lỗi nhỏ.

## 1.1.0 — 2026-10-08
- Cải thiện hệ thống và sửa lỗi nhỏ.

## 1.0.0 — 2026-10-07
- Bản đầu tiên của gói `drscore`: báo cáo viết bằng SQL, xem dạng lưới, bố cục HTML và dashboard BI.
