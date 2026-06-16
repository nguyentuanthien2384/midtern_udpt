# Ghi chú cập nhật — ARS Store (Distributed Key-Value)

Tài liệu này tổng hợp toàn bộ những gì đã được bổ sung/cập nhật so với bản gốc.
Mục tiêu chính: vá lỗi **mất dữ liệu khi tắt/khởi động lại node** và làm cho mọi
thứ chạy nhất quán cả khi chạy trực tiếp lẫn khi chạy bằng Docker.

---

## 1. Lưu trữ bền vững xuống đĩa (persistence) — `node.py`

Trước đây mỗi node chỉ giữ dữ liệu trong RAM, nên tắt node là mất sạch dữ liệu.
Nay mỗi node ghi dữ liệu xuống đĩa và tự nạp lại khi khởi động.

- Thêm tham số `data_dir` (mặc định thư mục `node_data/` cạnh `node.py`).
  Mỗi node ghi file riêng: `node_data/<node_id>.json`.
- `_persist()`: ghi atomic (ghi file `.tmp` rồi đổi tên) sau **mỗi** thao tác
  PUT/DELETE/đồng bộ. File gồm 4 phần: `primary`, `replica`, `versions`, `tombstones`.
- `_load_from_disk()`: lúc khởi động nạp lại từ đĩa **trước**, rồi mới đồng bộ mạng,
  có kiểm tra lại quyền sở hữu theo vòng băm để đặt key vào đúng store.
- Nút **"Đồng bộ"** (`force_sync`) được làm an toàn: nạp lại từ đĩa trước khi
  đồng bộ nên không bao giờ tự xóa trắng dữ liệu cục bộ khi node khác offline.
- Thêm tham số dòng lệnh `--data-dir` (và biến môi trường `DATA_DIR`).

Kết quả: tắt rồi bật lại node (hoặc cả cụm) không còn mất dữ liệu.

## 2. Tombstone + last-write-wins (chống dữ liệu "sống lại") — `node.py`

Xử lý tình huống: một key bị xóa **trong lúc** node primary của nó đang offline,
khi node đó bật lại không được làm dữ liệu cũ sống lại.

- Thêm bảng `versions` (thời điểm ghi cuối của mỗi key) và `tombstones`
  (thời điểm xóa của mỗi key).
- PUT ghi `versions`; DELETE ghi `tombstones` (thay vì chỉ xóa giá trị).
- Đồng bộ dùng quy tắc last-write-wins: tombstone mới hơn lần ghi cuối ⇒ key đã xóa.
- `get_all_data()` trao đổi cả `versions` + `tombstones` giữa các node.
- Dọn tombstone quá cũ theo `tombstone_ttl` (mặc định 86400s = 24h) để không phình.
- Thêm `"tombstone_ttl"` vào `cluster_config.json` và `cluster_config.docker.json`.

## 3. Docker: lưu trữ + đồng bộ

- `docker-compose.yml`: mỗi node gắn một named volume riêng vào `/app/node_data`
  (`node1-data`, `node2-data`, `node3-data`) ⇒ dữ liệu sống sót qua
  `docker compose down`/`up`. (Tránh `down -v` vì sẽ xóa volume.)
- `docker-compose.vm-node.yml`: thêm volume `kv-node-data:/app/node_data`.
- `.dockerignore`: thêm `node_data/` để dữ liệu cục bộ không lọt vào image.
- Đồng bộ (force_sync) chạy bình thường trong Docker qua RPC theo tên service.

## 4. Bật/tắt node trong Docker

- Hai script tiện dụng: `docker_node_stop.sh` / `docker_node_start.sh`
  (kèm bản `.bat`). Ví dụ: `./docker_node_stop.sh node3` rồi
  `./docker_node_start.sh node3` để demo chịu lỗi + khôi phục.

## 5. (Tùy chọn, opt-in) Nút Tắt/Bật node điều khiển container — `manager_app.py`

Mặc định TẮT. Khi bật, nút trên Web UI điều khiển được container Docker.

- Biến môi trường: `DOCKER_CONTROL=1`, `DOCKER_SOCKET` (mặc định
  `/var/run/docker.sock`), `DOCKER_NODE_PREFIX` (mặc định `udpt-`).
- Manager gọi Docker Engine API qua unix socket bằng thư viện chuẩn
  (không cần cài docker CLI vào image).
- File override `docker-compose.control.yml` để bật nhanh:
  `docker compose -f docker-compose.yml -f docker-compose.control.yml up --build -d`
- ⚠️ Cảnh báo: gắn `docker.sock` = trao quyền tương đương root host cho manager.
  Chỉ dùng cho dev/demo cục bộ, KHÔNG dùng production.

## 6. Tài liệu

- `SETUP_DOCKER.md`: thêm mục 8 (8.1–8.5) về lưu trữ bền vững, đồng bộ,
  bật/tắt node và demo chịu lỗi + khôi phục bằng Docker.

---

## Cách chạy (không đổi so với trước)

Chạy trực tiếp:

```bash
python node.py --node node1 --config cluster_config.json --bind-host 127.0.0.1
python node.py --node node2 --config cluster_config.json --bind-host 127.0.0.1
python node.py --node node3 --config cluster_config.json --bind-host 127.0.0.1
python manager_app.py --config cluster_config.json --host 127.0.0.1 --port 5000
```

Chạy Docker (nhớ `--build` sau khi sửa code):

```bash
docker compose up --build -d           # Web UI: http://localhost:5000
```

## Các file thay đổi/được thêm

- Sửa: `node.py`, `manager_app.py`, `cluster_config.json`,
  `cluster_config.docker.json`, `cluster_config.vm.docker.example.json`,
  `docker-compose.yml`, `docker-compose.vm-node.yml`, `.dockerignore`,
  `.gitignore`, `SETUP_DOCKER.md`
- Thêm mới: `docker-compose.control.yml`,
  `docker_node_stop.sh`, `docker_node_start.sh`,
  `docker_node_stop.bat`, `docker_node_start.bat`,
  `CHANGELOG_CAP_NHAT.md` (file này)
