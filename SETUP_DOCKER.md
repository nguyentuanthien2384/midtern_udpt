# Hướng dẫn chạy project UDPT bằng Docker và nhiều máy ảo

Project này là hệ thống **Key-Value Store phân tán** dùng Python XML-RPC + Flask Web UI.
Bản này hỗ trợ 3 kiểu chạy:

1. Chạy trực tiếp trên Windows/WSL/Linux bằng Python.
2. Chạy Docker local trên 1 máy, gồm 3 node + 1 Web Manager.
3. Chạy Docker trên nhiều máy ảo, mỗi VM là 1 node độc lập có IP riêng.

---

## 1. Yêu cầu cài đặt

### Windows dùng Docker Desktop

Cài:

- Docker Desktop
- WSL2 Ubuntu
- Python 3 nếu muốn chạy script test từ host

Kiểm tra Docker:

```bash
docker --version
docker compose version
```

Nếu đang dùng WSL như Ubuntu terminal, có thể chạy Docker ngay trong WSL nếu Docker Desktop đã bật **WSL integration**.

---

## 2. Chạy Docker local trên 1 máy

Cách này phù hợp để test nhanh trước khi đưa lên nhiều VM.

Trong thư mục project, chạy:

```bash
docker compose up --build -d
```

Hoặc dùng script:

```bash
./docker_local_up.sh
```

Trên Windows có thể chạy:

```bat
docker_local_up.bat
```

Sau khi chạy xong, mở trình duyệt:

```text
http://localhost:5000
```

Các container được tạo:

| Service | Container | Bên trong Docker | Cổng ngoài host |
|---|---|---:|---:|
| node1 | udpt-node1 | node1:8000 | localhost:8001 |
| node2 | udpt-node2 | node2:8000 | localhost:8002 |
| node3 | udpt-node3 | node3:8000 | localhost:8003 |
| manager | udpt-manager | manager:5000 | localhost:5000 |

Kiểm tra trạng thái:

```bash
docker compose ps
```

Xem log:

```bash
docker compose logs -f
```

Test cluster từ máy host:

```bash
python test_cluster.py --config cluster_config.docker.host.json
```

Dừng cluster:

```bash
docker compose down
```

---

## 3. Giải thích các file Docker mới

| File | Tác dụng |
|---|---|
| `Dockerfile` | Đóng gói project Python/Flask/XML-RPC thành image Docker |
| `.dockerignore` | Loại bỏ file không cần thiết khi build image |
| `docker-compose.yml` | Chạy đủ 3 node + Web Manager trên 1 máy |
| `cluster_config.docker.json` | Config nội bộ cho các container gọi nhau bằng service name `node1`, `node2`, `node3` |
| `cluster_config.docker.host.json` | Config để máy host test vào container qua `localhost:8001/8002/8003` |
| `docker-compose.vm-node.yml` | Chạy 1 node bằng Docker trên từng VM |
| `docker-compose.vm-manager.yml` | Chạy Web Manager bằng Docker, kết nối tới cluster nhiều VM |
| `cluster_config.vm.docker.example.json` | File mẫu cấu hình IP cho nhiều VM |
| `healthcheck_node.py` | Kiểm tra container node còn sống không |
| `healthcheck_manager.py` | Kiểm tra container manager còn sống không |

---

## 4. Chạy Docker trên nhiều máy ảo

Giả sử bạn có 3 VM Ubuntu:

| VM | IP ví dụ | Vai trò |
|---|---|---|
| VM1 | `192.168.56.101` | node1 |
| VM2 | `192.168.56.102` | node2 |
| VM3 | `192.168.56.103` | node3 |

> IP thực tế có thể khác. Kiểm tra bằng lệnh `ip a` hoặc `hostname -I` trong từng VM.

### Bước 1: Cài Docker trên mỗi VM

Trên từng VM Ubuntu chạy:

```bash
sudo apt update
sudo apt install docker.io docker-compose-plugin -y
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
```

Sau đó đăng xuất/đăng nhập lại hoặc chạy:

```bash
newgrp docker
```

Kiểm tra:

```bash
docker --version
docker compose version
```

### Bước 2: Copy project sang cả 3 VM

Bạn có thể dùng Git, WinSCP hoặc `scp`. Ví dụ:

```bash
scp -r UDPT_VM_Docker_Complete user@192.168.56.101:~/
scp -r UDPT_VM_Docker_Complete user@192.168.56.102:~/
scp -r UDPT_VM_Docker_Complete user@192.168.56.103:~/
```

### Bước 3: Tạo file cấu hình IP VM

Trên mỗi VM, vào thư mục project:

```bash
cd ~/UDPT_VM_Docker_Complete
cp cluster_config.vm.docker.example.json cluster_config.vm.docker.json
nano cluster_config.vm.docker.json
```

Sửa IP đúng với máy của bạn:

```json
{
  "replication_factor": 2,
  "heartbeat_interval": 3,
  "failure_timeout": 10,
  "rpc_timeout": 2.0,
  "nodes": [
    { "id": "node1", "host": "192.168.56.101", "port": 8000 },
    { "id": "node2", "host": "192.168.56.102", "port": 8000 },
    { "id": "node3", "host": "192.168.56.103", "port": 8000 }
  ]
}
```

Lưu ý: file `cluster_config.vm.docker.json` trên cả 3 VM nên giống nhau.

### Bước 4: Mở firewall nếu cần

Trên từng VM:

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 5000/tcp
```

Nếu dùng Windows Defender/VirtualBox/VMware network, hãy chắc chắn các VM ping được nhau:

```bash
ping 192.168.56.102
ping 192.168.56.103
```

### Bước 5: Chạy node trên từng VM

Trên VM1:

```bash
./docker_vm_node_up.sh node1
```

Trên VM2:

```bash
./docker_vm_node_up.sh node2
```

Trên VM3:

```bash
./docker_vm_node_up.sh node3
```

Hoặc chạy thủ công:

```bash
NODE_ID=node1 docker compose -f docker-compose.vm-node.yml up --build -d
```

VM2 đổi thành `NODE_ID=node2`, VM3 đổi thành `NODE_ID=node3`.

Xem log node:

```bash
docker logs -f udpt-node1
```

### Bước 6: Chạy Web Manager

Bạn có thể chạy manager trên VM1 hoặc một VM riêng.

Trên VM muốn chạy giao diện:

```bash
./docker_vm_manager_up.sh
```

Mở trình duyệt từ Windows:

```text
http://IP_CUA_VM_MANAGER:5000
```

Ví dụ:

```text
http://192.168.56.101:5000
```

---

## 5. Test cluster nhiều VM

Nếu bạn đang ở một máy có thể truy cập 3 IP VM, chạy:

```bash
python test_cluster.py --config cluster_config.vm.docker.json
```

Kết quả đúng sẽ có dạng:

```text
[1] Kiểm tra trạng thái node
  - node1: ONLINE
  - node2: ONLINE
  - node3: ONLINE
[2] PUT ...
[3] GET key từ tất cả node
[4] DELETE key
✅ Test cluster hoàn tất.
```

---

## 6. Một số lệnh Docker hay dùng

Xem container đang chạy:

```bash
docker ps
```

Xem log toàn bộ local cluster:

```bash
docker compose logs -f
```

Xem log một container:

```bash
docker logs -f udpt-node1
```

Restart container:

```bash
docker restart udpt-node1
```

Dừng node trên VM:

```bash
docker compose -f docker-compose.vm-node.yml down
```

Dừng manager:

```bash
docker compose -f docker-compose.vm-manager.yml down
```

Xóa image để build lại sạch:

```bash
docker rmi udpt-kv-store:latest
```

---

## 7. Gợi ý báo cáo/thuyết trình

Bạn có thể mô tả kiến trúc như sau:

- Mỗi node chạy trong một container Docker độc lập.
- Các node giao tiếp với nhau qua XML-RPC.
- Consistent hashing dùng để xác định primary node cho từng key.
- Replication factor = 2, dữ liệu được sao lưu sang node kế tiếp trên hash ring.
- Heartbeat dùng để phát hiện node sống/chết.
- Flask Web Manager dùng để quan sát trạng thái cluster, thêm/sửa/xóa/đọc dữ liệu.
- Khi triển khai trên nhiều VM, mỗi VM đóng vai trò một host vật lý/ảo riêng, Docker giúp chuẩn hóa môi trường chạy.

---

## 8. Lưu trữ bền vững & đồng bộ khi chạy Docker

### 8.1. Dữ liệu được lưu ở đâu

M��i node ghi dữ liệu xuống `/app/node_data/<node_id>.json` bên trong container (gồm `primary`, `replica`, `versions`, `tombstones`). Trong `docker-compose.yml`, mỗi node được gắn một named volume riêng vào đúng thư mục đó:

- `node1-data` → `/app/node_data` của node1
- `node2-data` → `/app/node_data` của node2
- `node3-data` → `/app/node_data` của node3

Nhờ vậy dữ liệu **sống sót qua `docker compose down`/`up` và qua việc restart/recreate container**. Lưu ý: `docker compose down -v` sẽ xóa volume (mất dữ liệu) — tránh cờ `-v` khi muốn giữ dữ liệu.

Xem nội dung file dữ liệu của một node:

```bash
docker compose exec node1 cat /app/node_data/node1.json
```

Liệt kê volume:

```bash
docker volume ls | grep data
```

### 8.2. Đồng bộ (force_sync) trong Docker

Nút "Đồng bộ" trên Web UI gọi RPC tới node tương ứng và hoạt động bình thường trong Docker, vì các container nói chuyện với nhau qua mạng nội bộ của Docker Compose (theo tên service node1/node2/node3, cổng nội bộ 8000). Khi bấm, node nạp lại từ volume, kéo dữ liệu từ các node online, hòa hợp theo last-write-wins (có tính tombstone) rồi sắp xếp lại primary/replica.

### 8.3. Bật/tắt node trong Docker

Hai nút "Tắt node"/"Bật node" trên Web UI **chỉ điều khiển được node chạy ở 127.0.0.1** (chạy trực tiếp cùng máy với manager). Khi chạy bằng Docker, mỗi node là một container riêng nên hãy bật/tắt bằng lệnh Docker (hoặc script kèm theo):

```bash
# Tắt node3 (mô phỏng sự cố)
./docker_node_stop.sh node3        # hoặc: docker compose stop node3

# Bật lại node3 -> tự nạp từ volume + đồng bộ với cluster
./docker_node_start.sh node3       # hoặc: docker compose start node3
```

Trên Windows dùng `docker_node_stop.bat node3` và `docker_node_start.bat node3`.

### 8.4. Demo chịu lỗi + khôi phục bằng Docker

```bash
# 1) Chạy cluster
docker compose up --build -d

# 2) Thêm vài key qua Web UI (http://localhost:5000) hoặc test_cluster.py
python3 test_cluster.py --config cluster_config.docker.host.json

# 3) Tắt node3 -> cluster vẫn đọc/ghi được nhờ replica
./docker_node_stop.sh node3

# 4) Bật lại node3 -> dữ liệu được khôi phục từ volume + đồng bộ
./docker_node_start.sh node3
```

Lưu ý: sau khi sửa code (ví dụ `node.py`), phải build lại image: `docker compose up --build -d`.

### 8.5. (Tùy chọn) Cho nút Tắt/Bật node điều khiển container Docker

M��c định, nút "Tắt node"/"Bật node" trên Web UI **không** điều khiển container (xem 8.3). Nếu muốn bật tính năng này để demo cho tiện, dùng file override `docker-compose.control.yml`:

```bash
docker compose -f docker-compose.yml -f docker-compose.control.yml up --build -d
```

Override này đặt biến `DOCKER_CONTROL=1` và gắn `/var/run/docker.sock` vào container manager; manager sẽ gọi Docker Engine API để start/stop container `udpt-<node_id>`.

> ⚠️ **Cảnh báo bảo mật:** gắn `docker.sock` cho phép manager điều khiển toàn bộ Docker trên máy host (tương đương quyền root host). **Chỉ dùng cho môi trường dev/demo cục bộ, không dùng cho production.** Không bật cờ này khi triển khai thật.

Biến môi trường liên quan: `DOCKER_CONTROL` (1/0), `DOCKER_SOCKET` (mặc định `/var/run/docker.sock`), `DOCKER_NODE_PREFIX` (mặc định `udpt-`, phải khớp `container_name` trong compose).
