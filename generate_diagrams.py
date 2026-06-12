
import matplotlib
matplotlib.use('Agg')  # Headless mode for running without GUI
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

def draw_hash_ring():
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.set_aspect('equal')


    ring = patches.Circle((0, 0), radius=1.5, fill=False, color='#2c3e50', linewidth=3, linestyle='--')
    ax.add_patch(ring)

    # Tọa độ các Node (0, 120, 240 độ)
    angles = [90, 210, 330]  # Đưa node 8000 lên đỉnh (90 độ)
    colors = ['#e74c3c', '#3498db', '#2ecc71']
    nodes = ['Node 8000\n(Index 0)', 'Node 8001\n(Index 1)', 'Node 8002\n(Index 2)']

    # Vẽ các node trên vòng tròn
    for idx, (angle, color, name) in enumerate(zip(angles, colors, nodes)):
        rad = np.deg2rad(angle)
        x = 1.5 * np.cos(rad)
        y = 1.5 * np.sin(rad)

        # Vẽ node tròn
        node_circle = patches.Circle((x, y), radius=0.2, fill=True, color=color, zorder=3)
        ax.add_patch(node_circle)

        # Nhãn node
        ax.text(x * 1.3, y * 1.3, name, ha='center', va='center', fontweight='bold', fontsize=10,
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor=color, alpha=0.9))

    # 2. Minh họa Key băm và gán Primary/Replica
    key_angle = 155
    key_rad = np.deg2rad(key_angle)
    kx = 1.5 * np.cos(key_rad)
    ky = 1.5 * np.sin(key_rad)

    # Vẽ điểm Key
    ax.plot(kx, ky, 'o', color='#f39c12', markersize=12, label='Key "my_key"', zorder=4)
    ax.text(kx * 1.15, ky * 1.15, 'Key "my_key"\nIndex = Hash(key)%3 = 1', ha='center', va='center', fontsize=9,
            color='#d35400', fontweight='bold')

    arrow_style = patches.FancyArrowPatch(
        (1.4 * np.cos(np.deg2rad(150)), 1.4 * np.sin(np.deg2rad(150))),
        (1.4 * np.cos(np.deg2rad(190)), 1.4 * np.sin(np.deg2rad(190))),
        connectionstyle="arc3,rad=.15", color='#e67e22',
        arrowstyle="->", mutation_scale=15, linewidth=2.5, linestyle='-'
    )
    ax.add_patch(arrow_style)
    ax.text(-0.8, -0.6, 'Primary: Node 8001', color='#3498db', fontsize=9, fontweight='bold')

    # Mũi tên sao lưu từ Node 8001 (210 độ) đến Node 8002 (330 độ)
    backup_style = patches.FancyArrowPatch(
        (1.4 * np.cos(np.deg2rad(220)), 1.4 * np.sin(np.deg2rad(220))),
        (1.4 * np.cos(np.deg2rad(320)), 1.4 * np.sin(np.deg2rad(320))),
        connectionstyle="arc3,rad=.3", color='#27ae60',
        arrowstyle="->", mutation_scale=15, linewidth=2.5, linestyle=':'
    )
    ax.add_patch(backup_style)
    ax.text(0.7, -1.0, 'Replica: Node 8002\n(Sao lưu)', color='#2ecc71', fontsize=9, fontweight='bold')

    # Trình bày sơ đồ
    ax.set_xlim(-2.5, 2.5)
    ax.set_ylim(-2.5, 2.5)
    ax.axis('off')
    plt.title('Sơ đồ Vòng tròn Consistent Hashing & Nhân bản bản sao', fontsize=12, fontweight='bold', pad=20)
    plt.tight_layout()
    plt.savefig('diagram_hash_ring.png', dpi=150)
    plt.close()

def draw_request_flow():
    fig, ax = plt.subplots(figsize=(8, 9))

    # Cấu hình khung vẽ
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 11)
    ax.axis('off')

    # Các khối vẽ sơ đồ (hộp chữ nhật)
    def draw_box(text, x, y, w, h, bg_color='#34495e', fg_color='white', align='center'):
        rect = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.1",
                                      facecolor=bg_color, edgecolor='#2c3e50', linewidth=1.5)
        ax.add_patch(rect)
        ax.text(x + w/2, y + h/2, text, color=fg_color, ha='center', va='center', fontsize=9.5, fontweight='bold')

    def draw_arrow(x1, y1, x2, y2, text=""):
        ax.annotate(text, xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(facecolor='#2c3e50', shrink=0.05, width=1.5, headwidth=7),
                    fontsize=8.5, color='#7f8c8d', ha='center', va='bottom')

    # Vẽ các bước
    draw_box("Client gửi PUT/GET\ntới Node A (ngẫu nhiên)", 3.5, 9.5, 3.0, 1.0, bg_color='#34495e')
    draw_arrow(5.0, 9.5, 5.0, 8.2)

    draw_box("Node A tính toán vị trí key:\nIndex = Hash(key) % 3", 3.0, 7.2, 4.0, 1.0, bg_color='#f39c12')
    draw_arrow(5.0, 7.2, 5.0, 5.8)

    # Khối rẽ nhánh: Node A có giữ key không?
    draw_box("Node A là Primary/Replica\ncủa Key?", 2.5, 4.8, 5.0, 1.0, bg_color='#9b59b6')

    # Nhánh có (Lưu cục bộ)
    draw_arrow(2.5, 5.3, 1.5, 5.3)
    draw_arrow(1.5, 5.3, 1.5, 3.8, "Có")
    draw_box("Xử lý trực tiếp cục bộ\n(data_store/replica_store)\nTrả về phản hồi", 0.2, 2.5, 2.6, 1.3, bg_color='#2ecc71')

    # Nhánh không (Chuyển tiếp)
    draw_arrow(7.5, 5.3, 8.5, 5.3)
    draw_arrow(8.5, 5.3, 8.5, 3.8, "Không")
    draw_box("Chuyển tiếp (Forward)\ntới Node Primary", 7.2, 2.8, 2.6, 1.0, bg_color='#e74c3c')

    # Khối rẽ nhánh 2: Node Primary có ONLINE không?
    draw_arrow(8.5, 2.8, 8.5, 1.8)

    # Khối xử lý khi Primary chết -> Fallback sang Replica
    draw_box("Thành công?", 7.5, 0.8, 2.0, 1.0, bg_color='#e67e22')

    # Kết nối cuối cùng về đích
    draw_arrow(1.5, 2.5, 5.0, 0.5) # từ xử lý cục bộ về đích
    draw_arrow(8.5, 0.8, 6.5, 0.5, "Thành công") # Primary hoạt động

    # Nhánh Primary sập -> Fallback sang Replica
    draw_arrow(9.5, 1.3, 10.0, 1.3)
    draw_arrow(10.0, 1.3, 10.0, -0.8)
    draw_arrow(10.0, -0.8, 7.2, -0.8, "Lỗi kết nối (DOWN)")
    draw_box("Fallback: Chuyển tiếp tới\nReplica Node tương ứng", 4.2, -1.3, 3.0, 1.0, bg_color='#1abc9c')

    # Về đích
    draw_arrow(4.2, -0.8, 3.0, -0.8)
    draw_box("Đích: Trả kết quả\nvề cho Client", 1.0, -1.3, 2.0, 1.0, bg_color='#27ae60')
    draw_arrow(1.0, -0.3, 1.0, 2.5)

    plt.title('Sơ đồ Luồng Định tuyến và Chuyển tiếp Request (Failover Routing)', fontsize=12, fontweight='bold')
    plt.tight_layout()
    plt.savefig('diagram_request_flow.png', dpi=150)
    plt.close()

def draw_heartbeat_sync():
    # Sơ đồ gộp cả Heartbeat và Sync khi Startup
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5))

    # 1. Vẽ sơ đồ Heartbeat
    ax1.set_xlim(0, 10)
    ax1.set_ylim(0, 10)
    ax1.axis('off')
    ax1.set_title("Cơ chế Heartbeat & Phát hiện Lỗi", fontsize=11, fontweight='bold', pad=10)

    # Vẽ 3 node tam giác
    def draw_node_ax1(name, x, y, color):
        circle = patches.Circle((x, y), radius=0.8, facecolor=color, edgecolor='#2c3e50', linewidth=1.5)
        ax1.add_patch(circle)
        ax1.text(x, y, name, color='white', ha='center', va='center', fontweight='bold', fontsize=9)

    draw_node_ax1("Node\n8000", 2.0, 8.0, '#e74c3c')
    draw_node_ax1("Node\n8001", 8.0, 8.0, '#3498db')
    draw_node_ax1("Node\n8002", 5.0, 2.0, '#2ecc71')

    # Vẽ các mũi tên heartbeat qua lại
    # 8000 <-> 8001
    ax1.annotate("", xy=(7.0, 8.2), xytext=(3.0, 8.2), arrowprops=dict(arrowstyle="<->", color='#34495e', linewidth=1.5))
    ax1.text(5.0, 8.4, "ping (mỗi 3s)", ha='center', fontsize=8, color='#7f8c8d')

    # 8000 <-> 8002
    ax1.annotate("", xy=(4.5, 2.8), xytext=(2.3, 7.0), arrowprops=dict(arrowstyle="<->", color='#34495e', linewidth=1.5))

    # 8001 <-> 8002
    ax1.annotate("", xy=(5.5, 2.8), xytext=(7.7, 7.0), arrowprops=dict(arrowstyle="<->", color='#34495e', linewidth=1.5))

    # Chú thích timeout
    ax1.text(5.0, 0.2, "* Nếu không nhận ping > 10s\n-> Node bị đánh dấu DEAD trong bảng trạng thái",
             ha='center', fontsize=8, fontstyle='italic', bbox=dict(boxstyle='round', facecolor='#fcf8e3', alpha=0.5))

    # 2. Vẽ sơ đồ Startup Sync
    ax2.set_xlim(0, 10)
    ax2.set_ylim(0, 10)
    ax2.axis('off')
    ax2.set_title("Đồng bộ & Lọc Khóa khi Khởi động lại", fontsize=11, fontweight='bold', pad=10)

    # Vẽ Khối Node Khởi động
    rect_new = patches.FancyBboxPatch((0.5, 5.0), 3.5, 3.0, boxstyle="round,pad=0.1", facecolor='#e67e22', edgecolor='#2c3e50')
    ax2.add_patch(rect_new)
    ax2.text(2.25, 6.5, "Node Mới Khởi Động\n(Ví dụ: Node 8000)", color='white', ha='center', va='center', fontweight='bold', fontsize=9)

    # Vẽ Khối Node Neighbor
    rect_neigh = patches.FancyBboxPatch((6.0, 5.0), 3.5, 3.0, boxstyle="round,pad=0.1", facecolor='#9b59b6', edgecolor='#2c3e50')
    ax2.add_patch(rect_neigh)
    ax2.text(7.75, 6.5, "Node Neighbor\n(Ví dụ: Node 8001)\n[Đang ONLINE]", color='white', ha='center', va='center', fontweight='bold', fontsize=9)

    # Mũi tên gọi sync
    ax2.annotate("1. Gọi get_all_data()", xy=(6.0, 7.0), xytext=(4.0, 7.0),
                 arrowprops=dict(facecolor='#2c3e50', shrink=0.05, width=1.0, headwidth=5))

    # Mũi tên trả dữ liệu
    ax2.annotate("2. Trả về toàn bộ data thô", xy=(4.0, 6.0), xytext=(6.0, 6.0),
                 arrowprops=dict(facecolor='#2c3e50', shrink=0.05, width=1.0, headwidth=5))

    # Hộp xử lý dưới Node khởi động
    rect_filter = patches.FancyBboxPatch((0.5, 0.8), 9.0, 2.8, boxstyle="round,pad=0.1", facecolor='#bdc3c7', edgecolor='#2c3e50')
    ax2.add_patch(rect_filter)

    filter_text = (
        "3. Cơ chế Lọc Key thông minh tại Node 8000:\n"
        "   Duyệt qua từng Key nhận được từ data thô:\n"
        "   - Nếu Hash(Key) % 3 == 0  --> Lưu vào data_store (Primary của Node 8000)\n"
        "   - Nếu (Hash(Key) % 3 + 1)%3 == 0 --> Lưu vào replica_store (Replica của Node 8000)\n"
        "   - Trường hợp khác --> BỎ QUA không lưu trữ"
    )
    ax2.text(5.0, 2.2, filter_text, color='#2c3e50', ha='center', va='center', fontsize=8.5, fontweight='bold')

    # Mũi tên đi xuống khối lọc
    ax2.annotate("", xy=(2.25, 3.8), xytext=(2.25, 4.8), arrowprops=dict(arrowstyle="->", color='#2c3e50', linewidth=1.5))

    plt.tight_layout()
    plt.savefig('diagram_heartbeat_sync.png', dpi=150)
    plt.close()

if __name__ == "__main__":
    draw_hash_ring()
    draw_request_flow()
    draw_heartbeat_sync()
    print("All diagrams drawn successfully!")
