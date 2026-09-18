"""
product_transmission.py
=========================

Lớp thuật toán thứ hai: Ma trận Tác động Vĩ mô lên Ngành & Chuỗi Cung ứng
(Macro-to-Product Transmission Matrix).

Kết nối chỉ số MSI (Macro Stress Index) với sự luân chuyển sản phẩm/hàng hóa
theo khung lý thuyết: Vĩ mô = "Môi trường sinh thái", Sản phẩm = "Sinh vật"
sống trong môi trường đó.

Gồm 2 phần:
    1. analyze_product_circulation() — tín hiệu định tính theo 3 trục truyền
       dẫn (Sức mua nội địa / Chi phí đầu vào / Dòng hàng XNK), dựa trên
       MSI, USDVND_Change, PMI.
    2. classify_market_flow() — Ma trận 4 trạng thái Luân chuyển Thị trường,
       kết hợp MSI (trục vĩ mô) với tốc độ vòng quay tồn kho của TỪNG nhóm
       sản phẩm (trục vi mô/ngành).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 1. PHÂN LOẠI NHÓM SẢN PHẨM THEO ĐỘ NHẠY VĨ MÔ
# ---------------------------------------------------------------------------

class ProductCategory(Enum):
    ESSENTIAL = "Hàng Thiết yếu (Defensive/Non-cyclical)"           # FMCG, y tế, điện nước
    DISCRETIONARY = "Hàng Xa xỉ & Không thiết yếu (Discretionary)"  # điện tử cao cấp, ô tô, thời trang
    IMPORT_DEPENDENT = "Hàng Nhập khẩu Nguyên liệu (Import-dependent)"  # dược ngoại, hóa chất, linh kiện
    EXPORT_DRIVEN = "Hàng Xuất khẩu chủ lực (Export-driven)"        # dệt may, thủy sản, gỗ, nông sản


# Ánh xạ mặc định: tên cột tồn kho trong CSV -> nhóm sản phẩm tương ứng.
# Có thể tùy chỉnh thêm/bớt tùy danh mục hàng hóa thực tế của bạn.
DEFAULT_INVENTORY_COLUMNS = {
    "Inventory_Days_FMCG": ProductCategory.ESSENTIAL,
    "Inventory_Days_Discretionary": ProductCategory.DISCRETIONARY,
    "Inventory_Days_ImportDep": ProductCategory.IMPORT_DEPENDENT,
    "Inventory_Days_ExportDriven": ProductCategory.EXPORT_DRIVEN,
}


# ---------------------------------------------------------------------------
# 2. TÍN HIỆU TRUYỀN DẪN VĨ MÔ -> SẢN PHẨM (theo 3 trục lý thuyết)
# ---------------------------------------------------------------------------

def analyze_product_circulation(
    msi: float,
    pmi: Optional[float] = None,
    usdvnd_change: Optional[float] = None,
    msi_hot_threshold: float = 2.0,
    fx_shock_threshold: float = 2.0,
    pmi_cold_threshold: float = 48.0,
) -> list[str]:
    """
    Sinh danh sách tín hiệu định tính về luân chuyển sản phẩm, dựa trên
    3 trục truyền dẫn: Sức mua nội địa (MSI), Chi phí đầu vào (tỷ giá),
    Dòng hàng xuất nhập khẩu (PMI).

    Args:
        msi: Điểm Macro Stress Index mới nhất.
        pmi: Chỉ số PMI (nếu có).
        usdvnd_change: % biến động tỷ giá USD/VND yoy (nếu có).
        msi_hot_threshold: Ngưỡng MSI coi là "vĩ mô sốt cao" (mặc định 2.0).
        fx_shock_threshold: Ngưỡng % biến động tỷ giá coi là "tăng cao" (mặc định 2.0).
        pmi_cold_threshold: Ngưỡng PMI coi là "sản xuất đóng băng" (mặc định 48.0).

    Returns:
        Danh sách chuỗi tín hiệu (rỗng nếu không có cảnh báo nào).
    """
    signals: list[str] = []

    # Trục 1: Sức mua nội địa
    if msi is not None and msi >= msi_hot_threshold:
        signals.append("🚨 VĨ MÔ THẮT CHẶT: Dòng tiền thị trường bị thu hẹp.")
        signals.append("👉 Hàng Xa xỉ / Điện tử: Rủi ro đọng vốn cao, cần giảm nhập kho, đẩy mạnh xả hàng tồn.")
        signals.append("👉 Hàng Thiết yếu (FMCG): Ưu tiên duy trì chuỗi cung ứng, luân chuyển ổn định.")

    # Trục 2: Chi phí đầu vào
    if usdvnd_change is not None and usdvnd_change > fx_shock_threshold:
        signals.append("⚠️ TỶ GIÁ TĂNG CAO: Doanh nghiệp nhập khẩu nguyên liệu chịu áp lực chi phí.")
        signals.append("👉 Sản phẩm phụ thuộc nguyên liệu ngoại: Cần điều chỉnh giá bán hoặc gia tăng tỷ lệ nội địa hóa.")

    # Trục 3: Dòng hàng xuất nhập khẩu
    if pmi is not None and pmi < pmi_cold_threshold:
        signals.append("📉 SẢN XUẤT ĐÓNG BĂNG: Nhu cầu đơn hàng sụt giảm.")
        signals.append("👉 Hàng Xuất khẩu: Tốc độ luân chuyển tại các cảng chậm lại, nguy cơ dư thừa năng lực sản xuất.")

    return signals


# ---------------------------------------------------------------------------
# 3. MA TRẬN 4 TRẠNG THÁI LUÂN CHUYỂN THỊ TRƯỜNG (MSI x Tốc độ Tồn kho)
# ---------------------------------------------------------------------------

class MarketFlowState(Enum):
    OPTIMAL_FLOW = "1. Lưu thông hoàn hảo (Optimal Flow)"
    STAGNATION = "2. Tắc nghẽn Mạch máu / Ứ đọng (Stagnation)"
    SUPPLY_BOTTLENECK = "3. Nghẽn Chuỗi Cung ứng (Supply Bottleneck)"
    PANIC_BUYING = "4. Bán Tháo / Tích Trữ Vì Lạm Phát (Panic Buying)"


@dataclass
class MarketFlowResult:
    state: MarketFlowState
    description: str
    strategy: str


_MATRIX = {
    ("low", "fast"): MarketFlowResult(
        MarketFlowState.OPTIMAL_FLOW,
        "Nền kinh tế khỏe, tiêu dùng mạnh, hàng ra đến đâu bán hết đến đó.",
        "Mở rộng sản xuất, gia tăng tồn kho an toàn, nhập khẩu mạnh tay.",
    ),
    ("high", "slow"): MarketFlowResult(
        MarketFlowState.STAGNATION,
        "Lạm phát/Lãi suất cao dập tắt sức mua, hàng hóa nằm chết trong kho.",
        "Siết chặt vòng quay vốn, giảm giá kích cầu, cắt giảm đơn hàng nhập mới.",
    ),
    ("low", "slow"): MarketFlowResult(
        MarketFlowState.SUPPLY_BOTTLENECK,
        "Nhu cầu có nhưng thiếu nguyên liệu hoặc tắc/đứt gãy logistics.",
        "Tập trung giải phóng hạ tầng vận tải, tìm nguồn cung thay thế.",
    ),
    ("high", "fast"): MarketFlowResult(
        MarketFlowState.PANIC_BUYING,
        "Người dân mua tích trữ hàng hóa do sợ đồng tiền mất giá.",
        "Kiểm soát giá bán, ưu tiên điều tiết hàng thiết yếu, tránh đầu cơ.",
    ),
}


def classify_msi_level(msi: float, low_cut: float = 1.0, high_cut: float = 2.0) -> str:
    """Phân MSI thành 'low' (< low_cut) hoặc 'high' (>= high_cut).

    Vùng giữa [low_cut, high_cut) không có ô ma trận rõ ràng trong lý thuyết
    gốc — được coi là 'mid' (vùng chuyển tiếp, cần theo dõi thêm)."""
    if msi is None:
        return "mid"
    if msi < low_cut:
        return "low"
    if msi >= high_cut:
        return "high"
    return "mid"


def classify_inventory_speed(
    current_days: float,
    baseline_days: float,
    tolerance_pct: float = 0.15,
) -> str:
    """
    Phân loại tốc độ vòng quay tồn kho so với mức nền lịch sử (baseline):
        - 'stable': lệch trong khoảng ±tolerance_pct so với baseline
                    (biến động nhiễu bình thường, KHÔNG coi là đổi màu -
                    đúng với đặc tính "Hàng Thiết yếu" trong lý thuyết).
        - 'fast':   thấp hơn baseline quá tolerance_pct (vòng quay nhanh hơn).
        - 'slow':   cao hơn baseline quá tolerance_pct (tồn kho ứ đọng).

    Args:
        current_days: Số ngày tồn kho trung bình hiện tại.
        baseline_days: Mức trung bình lịch sử/tham chiếu (vd: rolling mean).
        tolerance_pct: Biên độ dung sai coi là "không đổi" (mặc định 15%).
    """
    if current_days is None or baseline_days is None or baseline_days == 0:
        return "unknown"

    deviation = (current_days - baseline_days) / baseline_days
    if abs(deviation) <= tolerance_pct:
        return "stable"
    return "slow" if deviation > 0 else "fast"


def classify_market_flow(msi: float, current_days: float, baseline_days: float) -> Optional[MarketFlowResult]:
    """
    Tra cứu Ma trận 4 Trạng thái Luân chuyển Thị trường dựa trên MSI và
    tốc độ vòng quay tồn kho hiện tại so với mức nền lịch sử.

    Trả về None nếu MSI đang ở vùng giữa (mid, 1.0 <= MSI < 2.0) hoặc thiếu
    dữ liệu tồn kho — vì lý thuyết gốc không định nghĩa ô ma trận cho vùng đó.
    """
    msi_level = classify_msi_level(msi)
    speed = classify_inventory_speed(current_days, baseline_days)

    if msi_level == "mid" or speed == "unknown":
        return None

    return _MATRIX.get((msi_level, speed))


# ---------------------------------------------------------------------------
# 4. BÁO CÁO TỔNG HỢP CHO TỪNG NHÓM SẢN PHẨM (dùng trong pipeline chính)
# ---------------------------------------------------------------------------

def build_product_report(
    msi: float,
    pmi: Optional[float],
    usdvnd_change: Optional[float],
    inventory_snapshot: dict,
) -> dict:
    """
    Tổng hợp báo cáo truyền dẫn Vĩ mô -> Sản phẩm cho pipeline chính.

    Args:
        msi: MSI mới nhất.
        pmi: PMI mới nhất.
        usdvnd_change: % biến động tỷ giá mới nhất.
        inventory_snapshot: dict {tên_cột_tồn_kho: (giá_trị_hiện_tại, mức_nền_baseline)}
            Ví dụ: {"Inventory_Days_FMCG": (16.4, 15.2), ...}

    Returns:
        dict gồm:
            - "circulation_signals": list[str] tín hiệu định tính
            - "market_flow": dict {tên_cột: MarketFlowResult | None}
    """
    signals = analyze_product_circulation(msi, pmi, usdvnd_change)

    flow_by_category = {}
    for col, (current, baseline) in inventory_snapshot.items():
        category = DEFAULT_INVENTORY_COLUMNS.get(col)
        result = classify_market_flow(msi, current, baseline)
        flow_by_category[col] = {
            "category": category.value if category else col,
            "current_days": current,
            "baseline_days": round(baseline, 2) if baseline is not None else None,
            "speed": classify_inventory_speed(current, baseline),
            "flow_result": result,
        }

    return {
        "circulation_signals": signals,
        "market_flow": flow_by_category,
    }
