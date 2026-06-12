# Hướng dẫn phát triển/chạy project bằng máy ảo trên Windows

## 1. Kết luận nhanh

Project này **chạy được trên Windows**. Nếu muốn mô phỏng hệ phân tán thật hơn, bạn nên chạy mỗi node trên một máy ảo Ubuntu khác nhau, còn Windows đóng vai trò máy host để mở Web UI/quản lý.

Có 3 cách chạy:

1. **Chạy local trên Windows**: 3 node chạy trên 3 port `8000`, `8001`, `8002`.
2. **Chạy bằng WSL2**: giống Linux nhưng vẫn ở Windows.
3. **Chạy nhiều máy ảo**: mỗi VM là một node riêng, ví dụ `192.168.56.101`, `192.168.56.102`, `192.168.56.103`.

## 2. Kiến trúc sau khi phát triển

```text
Windows Host / Browser
        |
        | http://<manager-ip>:5000
        v
+--------------------------+
| Flask Management Web UI  |
+--------------------------+
       /         |         \
 XML-RPC      XML-RPC     XML-RPC
     /           |           \
+---------+  +---------+  +---------+
| node1   |  | node2   |  | node3   |
| VM 1    |  | VM 2    |  | VM 3    |
+---------+  +---------+  +---------+
```

Mỗi node có:

- `PUT`: lưu dữ liệu.
- `GET`: đọc dữ liệu.
- `DELETE`: xóa dữ liệu.
- `Consistent hashing`: xác định key thuộc node nào.
- `Replication`: sao chép sang node kế tiếp.
- `Heartbeat`: kiểm tra node còn sống không.
- `Recovery/Force sync`: đồng bộ lại dữ liệu khi node restart.

## 3. Chạy nhanh trên Windows không cần máy ảo

Mở thư mục project, chạy:

```bat
start_cluster.bat
```

Hoặc PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_cluster.ps1
```

Sau đó mở:

```text
http://localhost:5000
```

## 4. Chuẩn bị máy ảo Ubuntu trên Windows

Bạn có thể dùng VirtualBox hoặc VMware.

Gợi ý cấu hình mạng dễ làm nhất:

- Adapter 1: NAT để VM có Internet.
- Adapter 2: Host-only Adapter để các VM và Windows nhìn thấy nhau.

Ví dụ IP tĩnh:

| Node  | VM IP          | Port |
|-------|----------------|------|
| node1 | 192.168.56.101 | 8000 |
| node2 | 192.168.56.102 | 8000 |
| node3 | 192.168.56.103 | 8000 |

> Lưu ý: Nếu dùng cùng một máy Windows chạy nhiều node thì mỗi node phải khác port. Nếu dùng nhiều VM thì các node có thể cùng port `8000` vì IP khác nhau.

## 5. Sửa file cấu hình cluster

Copy file mẫu:

```bash
cp cluster_config.vm.example.json cluster_config.json
```

Sửa IP trong `cluster_config.json` đúng với IP máy ảo của bạn:

```json
{
  "replication_factor": 2,
  "heartbeat_interval": 3,
  "failure_timeout": 10,
  "rpc_timeout": 1.5,
  "nodes": [
    { "id": "node1", "host": "192.168.56.101", "port": 8000 },
    { "id": "node2", "host": "192.168.56.102", "port": 8000 },
    { "id": "node3", "host": "192.168.56.103", "port": 8000 }
  ]
}
```

File này phải giống nhau trên cả 3 VM và máy chạy Web UI.

## 6. Cài môi trường trên từng VM

Trên mỗi VM Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-pip unzip
python3 -m pip install -r requirements.txt
```

Nếu firewall Ubuntu đang bật:

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 5000/tcp
```

## 7. Chạy từng node trên từng máy ảo

Trên VM node1:

```bash
python3 node.py --node node1 --config cluster_config.json --bind-host 0.0.0.0
```

Trên VM node2:

```bash
python3 node.py --node node2 --config cluster_config.json --bind-host 0.0.0.0
```

Trên VM node3:

```bash
python3 node.py --node node3 --config cluster_config.json --bind-host 0.0.0.0
```

Hoặc dùng script:

```bash
./run_node_vm.sh node1
./run_node_vm.sh node2
./run_node_vm.sh node3
```

## 8. Chạy Web UI quản lý

Bạn có thể chạy Web UI trên Windows hoặc trên một VM bất kỳ.

Trên Windows:

```bat
run_manager.bat cluster_config.json
```

Trên Linux/Ubuntu:

```bash
./run_manager.sh cluster_config.json
```

Mở trình duyệt:

```text
http://localhost:5000
```

Nếu Web UI chạy trong VM, mở từ Windows bằng IP VM, ví dụ:

```text
http://192.168.56.101:5000
```

## 9. Test nhanh

Sau khi chạy đủ node:

```bash
python3 test_cluster.py --config cluster_config.json
```

Kết quả đúng sẽ có dạng:

```text
[1] Kiểm tra trạng thái node
  - node1: ONLINE
  - node2: ONLINE
  - node3: ONLINE
[2] PUT ...
[3] GET key từ tất cả node
✅ Test cluster hoàn tất.
```

## 10. Các lỗi thường gặp

### Lỗi không kết nối được node

Kiểm tra IP:

```bash
ip addr
```

Từ Windows ping VM:

```bat
ping 192.168.56.101
```

Từ VM này ping VM khác:

```bash
ping 192.168.56.102
```

### Lỗi port không mở

Kiểm tra tiến trình có lắng nghe không:

```bash
ss -tulpn | grep 8000
```

Mở firewall:

```bash
sudo ufw allow 8000/tcp
```

### Web UI hiện node offline

Nguyên nhân thường là:

- Sai IP trong `cluster_config.json`.
- Chưa chạy node.
- VM không cùng mạng Host-only.
- Windows Firewall hoặc Ubuntu Firewall chặn port.

## 11. Ý nghĩa các file mới

| File | Công dụng |
|------|----------|
| `cluster_config.json` | Cấu hình cluster local mặc định |
| `cluster_config.vm.example.json` | Mẫu cấu hình khi chạy trên nhiều VM |
| `node.py` | Node XML-RPC đã hỗ trợ multi-machine |
| `client.py` | Client CLI hỗ trợ kết nối theo node ID/config |
| `manager_app.py` | Web UI đọc node từ config, không hard-code localhost nữa |
| `start_cluster.bat` | Chạy nhanh local cluster trên Windows |
| `start_cluster.ps1` | Chạy nhanh local cluster bằng PowerShell |
| `run_node_vm.sh/.bat` | Chạy node trên VM/Linux/Windows |
| `run_manager.sh/.bat` | Chạy Web UI quản lý |
| `test_cluster.py` | Test PUT/GET/DELETE toàn cluster |
