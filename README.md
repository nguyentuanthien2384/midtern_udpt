# ARS Store — Hệ thống Key-Value phân tán, VM & Docker Ready

Project mô phỏng hệ thống lưu trữ dữ liệu dạng **Key – Value** hoạt động trên nhiều node. Các node giao tiếp với nhau qua **XML-RPC**, có Web UI quản lý bằng **Flask**.

## Chức năng chính

- `PUT`: lưu dữ liệu.
- `GET`: đọc dữ liệu.
- `DELETE`: xóa dữ liệu.
- **Consistent Hashing**: định tuyến key về primary node.
- **Forwarding**: client gửi vào node bất kỳ, hệ thống tự chuyển request đúng node.
- **Replication**: dữ liệu được sao chép sang node kế tiếp trên hash ring.
- **Heartbeat**: kiểm tra node online/offline.
- **Recovery / Force Sync**: node restart có thể đồng bộ lại dữ liệu từ cluster.
- **Management Web UI**: xem dashboard, danh sách key, trạng thái server, thêm/tìm/xóa dữ liệu.
- **Docker Compose**: chạy nhanh 3 node + Web UI bằng container.
- **Multi-VM Docker**: triển khai mỗi node trên một máy ảo riêng, chuẩn hóa môi trường bằng Docker.
# Link demo: https://youtu.be/1gJ9rt72rCQ
## Cấu trúc project

```text
UDPT/
├── node.py
├── client.py
├── manager_app.py
├── cluster_config.json
├── cluster_config.vm.example.json
├── Dockerfile
├── docker-compose.yml
├── docker-compose.vm-node.yml
├── docker-compose.vm-manager.yml
├── cluster_config.docker.json
├── cluster_config.docker.host.json
├── cluster_config.vm.docker.example.json
├── test_cluster.py
├── start_cluster.bat
├── start_cluster.ps1
├── start_cluster.sh
├── run_node_vm.bat/.sh
├── run_manager.bat/.sh
├── templates/
├── static/
├── SETUP_VM_WINDOWS.md
└── SETUP_DOCKER.md
```

## Chạy nhanh trên Windows

```bat
start_cluster.bat
```

Sau đó mở:

```text
http://localhost:5000
```

## Chạy nhanh bằng PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File .\start_cluster.ps1
```

## Chạy thủ công local 3 node

Terminal 1:

```bash
python node.py --node node1 --config cluster_config.json --bind-host 127.0.0.1
```

Terminal 2:

```bash
python node.py --node node2 --config cluster_config.json --bind-host 127.0.0.1
```

Terminal 3:

```bash
python node.py --node node3 --config cluster_config.json --bind-host 127.0.0.1
```

Terminal 4:

```bash
python manager_app.py --config cluster_config.json --host 0.0.0.0 --port 5000
```

## Chạy nhanh bằng Docker local

```bash
docker compose up --build -d
```

Sau đó mở:

```text
http://localhost:5000
```

Test từ máy host:

```bash
python test_cluster.py --config cluster_config.docker.host.json
```

Dừng Docker cluster:

```bash
docker compose down
```

Xem hướng dẫn Docker chi tiết trong file:

```text
SETUP_DOCKER.md
```

## Chạy client CLI

```bash
python client.py --node node1 --config cluster_config.json
```

Hoặc cách cũ vẫn dùng được:

```bash
python client.py 8000
```

## Test nhanh

```bash
python test_cluster.py --config cluster_config.json
```

## Chạy trên máy ảo không dùng Docker

Xem chi tiết trong file:

```text
SETUP_VM_WINDOWS.md
```

Tóm tắt:

1. Tạo 3 VM Ubuntu, đặt IP tĩnh cùng mạng Host-only.
2. Sửa `cluster_config.json` theo IP từng VM.
3. Copy project lên cả 3 VM.
4. Chạy từng node:

```bash
python3 node.py --node node1 --config cluster_config.json --bind-host 0.0.0.0
python3 node.py --node node2 --config cluster_config.json --bind-host 0.0.0.0
python3 node.py --node node3 --config cluster_config.json --bind-host 0.0.0.0
```

5. Chạy Web UI:

```bash
python3 manager_app.py --config cluster_config.json --host 0.0.0.0 --port 5000
```

## Lưu ý quan trọng

- Nếu chạy nhiều node trên **cùng một máy**, mỗi node cần **khác port**.
- Nếu chạy nhiều node trên **nhiều VM khác nhau**, các node có thể dùng **cùng port** nhưng phải khác IP.
- Khi chạy trên VM, phải dùng `--bind-host 0.0.0.0` để node nhận kết nối từ máy khác.
- Cần mở firewall cho port node, ví dụ `8000/tcp` và Web UI `5000/tcp`.

## Chạy Docker trên nhiều máy ảo

Xem file `SETUP_DOCKER.md`, phần **Chạy Docker trên nhiều máy ảo**.

Tóm tắt:

1. Tạo 3 VM Ubuntu và lấy IP của từng VM.
2. Cài Docker trên cả 3 VM.
3. Copy project lên cả 3 VM.
4. Copy `cluster_config.vm.docker.example.json` thành `cluster_config.vm.docker.json`.
5. Sửa IP trong `cluster_config.vm.docker.json` theo IP thật của VM.
6. Chạy từng node:

```bash
./docker_vm_node_up.sh node1
./docker_vm_node_up.sh node2
./docker_vm_node_up.sh node3
```

7. Chạy Web Manager:

```bash
./docker_vm_manager_up.sh
```
