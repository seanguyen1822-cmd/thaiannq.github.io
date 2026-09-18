"""
macro_early_warning.py
=======================

Hệ thống Cảnh báo Vĩ mô Tự động (Automated Macro Early Warning System)
dựa trên khung lý thuyết "Kinh tế Vĩ mô Tương đồng Sinh học".

Luồng xử lý:
    1. Đọc dữ liệu lịch sử từ macro_historical.csv
    2. (Tùy chọn) Thêm dòng dữ liệu mới của ngày hôm nay
    3. Forward-fill các cột theo tháng (CPI, PMI, M2 Growth)
    4. Tính Rolling Z-score (cửa sổ trượt LOOKBACK_WINDOW dòng)
    5. Tính Macro Stress Index (MSI) tổng hợp
    6. So sánh MSI mới nhất với ngưỡng cảnh báo (1.2 / 2.0)
    7. Nếu vượt ngưỡng và có cấu hình Telegram -> gửi cảnh báo
    8. Ghi đè lại macro_historical.csv (nếu có dữ liệu mới)

Chạy thủ công:
    python macro_early_warning.py

Chạy kèm thêm dữ liệu ngày hôm nay (ví dụ):
    python macro_early_warning.py \
        --interbank-rate 5.25 --usdvnd-change 6.4

Biến môi trường (secrets) dùng để gửi Telegram (tùy chọn):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import argparse
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import requests

from product_transmission import DEFAULT_INVENTORY_COLUMNS, build_product_report


# ---------------------------------------------------------------------------
# CẤU HÌNH
# ---------------------------------------------------------------------------

CSV_PATH = "macro_historical.csv"

# Cửa sổ trượt tính Z-score (số DÒNG dữ liệu, không nhất thiết = số tháng
# nếu tần suất dữ liệu là ngày). Với dữ liệu mẫu (theo tháng), 36 = 36 tháng.
LOOKBACK_WINDOW = 36

# Trọng số trong công thức MSI (phải theo đúng lý thuyết cung cấp)
WEIGHTS = {
    "cpi": 0.30,
    "interbank_rate": 0.30,
    "usdvnd_change": 0.20,
    "pmi": 0.20,
}

# Ngưỡng cảnh báo
ALERT_THRESHOLD_RED = 2.0     # Báo động đỏ
ALERT_THRESHOLD_YELLOW = 1.2  # Cảnh báo vàng

# Các cột chỉ cập nhật theo THÁNG -> cần forward-fill khi thêm dòng theo ngày
MONTHLY_COLUMNS = ["CPI_YOY", "PMI", "M2_Growth", "FX_Reserve_Months"] + list(DEFAULT_INVENTORY_COLUMNS.keys())


# ---------------------------------------------------------------------------
# 1. ĐỌC / CẬP NHẬT DỮ LIỆU
# ---------------------------------------------------------------------------

def load_historical_data(csv_path: str = CSV_PATH) -> pd.DataFrame:
    """Đọc file CSV lịch sử, sắp xếp theo ngày tăng dần."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(
            f"Không tìm thấy file {csv_path}. Hãy tạo file dữ liệu lịch sử "
            f"theo đúng cấu trúc: Date, CPI_YOY, Interbank_Rate, "
            f"USDVND_Change, PMI, FX_Reserve_Months, M2_Growth."
        )
    df = pd.read_csv(csv_path, parse_dates=["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def append_daily_data(df: pd.DataFrame, new_values: dict) -> pd.DataFrame:
    """
    Thêm một dòng dữ liệu mới (thường là của ngày hôm nay) vào cuối bảng.

    new_values: dict chỉ cần chứa các cột NGƯỜI DÙNG CÓ dữ liệu thật
    (thường là Interbank_Rate, USDVND_Change - cập nhật hàng ngày).
    Các cột theo THÁNG (CPI_YOY, PMI, M2_Growth, FX_Reserve_Months) nếu
    không truyền vào sẽ được forward-fill từ dòng gần nhất ở bước sau.

    Nếu đã có dòng của ngày hôm nay, dòng đó sẽ được CẬP NHẬT thay vì
    tạo dòng trùng lặp.
    """
    today = pd.Timestamp(date.today())
    row = {col: np.nan for col in df.columns}
    row["Date"] = today
    row.update(new_values)

    if (df["Date"] == today).any():
        idx = df.index[df["Date"] == today][0]
        for k, v in row.items():
            if k != "Date" and v is not None and not (isinstance(v, float) and np.isnan(v)):
                df.loc[idx, k] = v
    else:
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    return df.sort_values("Date").reset_index(drop=True)


def forward_fill_monthly_columns(df: pd.DataFrame, monthly_cols=MONTHLY_COLUMNS) -> pd.DataFrame:
    """Điền giá trị thiếu cho các cột chỉ công bố theo tháng (CPI, PMI, M2...)."""
    df = df.copy()
    for col in monthly_cols:
        if col in df.columns:
            df[col] = df[col].ffill()
    return df


# ---------------------------------------------------------------------------
# 2. TÍNH ROLLING Z-SCORE & MSI
# ---------------------------------------------------------------------------

def _rolling_z(series: pd.Series, window: int) -> pd.Series:
    """Z-score động: (giá trị - trung bình trượt) / độ lệch chuẩn trượt."""
    mean = series.rolling(window).mean()
    std = series.rolling(window).std()
    return (series - mean) / std


def compute_rolling_msi(
    df: pd.DataFrame,
    window: int = LOOKBACK_WINDOW,
    weights: dict = WEIGHTS,
) -> pd.DataFrame:
    """
    Tính Rolling Z-score cho từng chỉ số và chỉ số tổng hợp MSI:

        MSI = w_cpi*Z_CPI + w_rate*Z_LãiSuất + w_fx*Z_TỷGiá - w_pmi*Z_PMI

    (Z_PMI bị TRỪ vì PMI càng THẤP thì rủi ro càng CAO — cơ bắp co quắt).
    """
    df = df.copy()

    df["Z_CPI"] = _rolling_z(df["CPI_YOY"], window)
    df["Z_Rate"] = _rolling_z(df["Interbank_Rate"], window)
    df["Z_FX"] = _rolling_z(df["USDVND_Change"], window)
    df["Z_PMI"] = _rolling_z(df["PMI"], window)

    df["MSI"] = (
        weights["cpi"] * df["Z_CPI"]
        + weights["interbank_rate"] * df["Z_Rate"]
        + weights["usdvnd_change"] * df["Z_FX"]
        - weights["pmi"] * df["Z_PMI"]
    )
    return df


# ---------------------------------------------------------------------------
# 3. ĐÁNH GIÁ CẢNH BÁO
# ---------------------------------------------------------------------------

def evaluate_alert(msi: float) -> tuple[str, str]:
    """
    Trả về (mức độ, thông điệp) dựa trên điểm MSI mới nhất.
    mức độ ∈ {"RED", "YELLOW", "OK"}
    """
    if pd.isna(msi):
        return "OK", "Chưa đủ dữ liệu lịch sử để tính MSI (cần tối thiểu bằng LOOKBACK_WINDOW dòng)."
    if msi >= ALERT_THRESHOLD_RED:
        return "RED", f"🚨 BÁO ĐỘNG ĐỎ: Hệ thống vĩ mô SỐT NẶNG / RỦI RO CỰC ĐẠI! (MSI = {msi:.2f})"
    if msi >= ALERT_THRESHOLD_YELLOW:
        return "YELLOW", f"⚠️ CẢNH BÁO VÀNG: Xuất hiện căng thẳng vĩ mô cục bộ. (MSI = {msi:.2f})"
    return "OK", f"✅ CƠ THỂ VĨ MÔ KHỎE MẠNH: Các chỉ số ở mức cân bằng. (MSI = {msi:.2f})"


# ---------------------------------------------------------------------------
# 4. GỬI CẢNH BÁO TELEGRAM
# ---------------------------------------------------------------------------

def send_telegram_message(message: str, bot_token: str = None, chat_id: str = None) -> bool:
    """
    Gửi tin nhắn cảnh báo qua Telegram Bot API.
    Trả về True nếu gửi thành công, False nếu thiếu cấu hình hoặc lỗi.

    Cách tạo bot & lấy chat_id:
        1. Chat với @BotFather trên Telegram -> /newbot -> lấy TELEGRAM_BOT_TOKEN
        2. Nhắn tin bất kỳ cho bot vừa tạo
        3. Mở: https://api.telegram.org/bot<TOKEN>/getUpdates -> lấy "chat":{"id": ...}
    """
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("[Thông tin] Chưa cấu hình TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID -> bỏ qua gửi Telegram.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)
        resp.raise_for_status()
        print("[Thông tin] Đã gửi cảnh báo Telegram thành công.")
        return True
    except requests.RequestException as e:
        print(f"[Lỗi] Gửi Telegram thất bại: {e}")
        return False


# ---------------------------------------------------------------------------
# 5. MAIN PIPELINE
# ---------------------------------------------------------------------------

def run_pipeline(new_values: dict = None, save: bool = True) -> pd.DataFrame:
    """Chạy toàn bộ pipeline: đọc dữ liệu -> (thêm mới) -> tính MSI -> cảnh báo."""
    df = load_historical_data()

    if new_values:
        df = append_daily_data(df, new_values)

    df = forward_fill_monthly_columns(df)
    df = compute_rolling_msi(df)

    latest = df.iloc[-1]
    latest_date = pd.Timestamp(latest["Date"]).strftime("%Y-%m-%d")
    level, message = evaluate_alert(latest["MSI"])

    print(f"=== Báo cáo Macro Stress Index ngày {latest_date} ===")
    print(f"  CPI_YOY={latest['CPI_YOY']}  Interbank_Rate={latest['Interbank_Rate']}  "
          f"USDVND_Change={latest['USDVND_Change']}  PMI={latest['PMI']}")
    print(f"  MSI = {latest['MSI']:.3f}" if pd.notna(latest["MSI"]) else "  MSI = N/A")
    print(f"  Mức cảnh báo: {level}")
    print(f"  {message}")

    # --- Lớp thứ 2: Ma trận Truyền dẫn Vĩ mô -> Sản phẩm ---
    inventory_snapshot = {}
    for col in DEFAULT_INVENTORY_COLUMNS:
        if col in df.columns:
            baseline = df[col].rolling(LOOKBACK_WINDOW).mean().iloc[-1]
            inventory_snapshot[col] = (latest[col], baseline)

    product_report = None
    if inventory_snapshot:
        product_report = build_product_report(
            msi=latest["MSI"],
            pmi=latest["PMI"],
            usdvnd_change=latest["USDVND_Change"],
            inventory_snapshot=inventory_snapshot,
        )

        print("\n=== Truyền dẫn Vĩ mô -> Sản phẩm/Hàng hóa ===")
        if product_report["circulation_signals"]:
            for s in product_report["circulation_signals"]:
                print(f"  {s}")
        else:
            print("  Không có tín hiệu cảnh báo truyền dẫn nào ở thời điểm này.")

        print("\n  -- Ma trận Luân chuyển Thị trường theo nhóm sản phẩm --")
        for col, info in product_report["market_flow"].items():
            flow = info["flow_result"]
            if info["speed"] == "stable":
                flow_txt = "Ổn định — không đổi màu theo chu kỳ vĩ mô"
            elif flow is None:
                flow_txt = "Vùng chuyển tiếp (MSI trung bình) — chưa đủ căn cứ phân loại"
            else:
                flow_txt = flow.state.value
            print(f"  • {info['category']}: tồn kho {info['current_days']} ngày "
                  f"(nền {info['baseline_days']} ngày, xu hướng {info['speed']}) -> {flow_txt}")
            if flow:
                print(f"      Chiến lược: {flow.strategy}")

    if level in ("RED", "YELLOW"):
        telegram_msg = message
        if product_report and product_report["circulation_signals"]:
            telegram_msg += "\n\n" + "\n".join(product_report["circulation_signals"])
        send_telegram_message(telegram_msg)

    if save and new_values:
        # Chỉ ghi lại các cột dữ liệu gốc, không lưu các cột Z/MSI tính toán
        original_cols = (
            ["Date", "CPI_YOY", "Interbank_Rate", "USDVND_Change", "PMI", "FX_Reserve_Months", "M2_Growth"]
            + list(DEFAULT_INVENTORY_COLUMNS.keys())
        )
        save_cols = [c for c in original_cols if c in df.columns]
        out = df[save_cols].copy()
        out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")
        out.to_csv(CSV_PATH, index=False)
        print(f"[Thông tin] Đã cập nhật {CSV_PATH}.")

    return df


# ---------------------------------------------------------------------------
# 6. CLI ENTRYPOINT
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Macro Early Warning System")
    p.add_argument("--cpi", type=float, default=None, help="CPI_YOY (%%, chỉ cập nhật khi có số liệu tháng mới)")
    p.add_argument("--interbank-rate", type=float, default=None, help="Lãi suất liên ngân hàng hôm nay (%%)")
    p.add_argument("--usdvnd-change", type=float, default=None, help="Biến động tỷ giá USD/VND hôm nay (%% yoy)")
    p.add_argument("--pmi", type=float, default=None, help="PMI (chỉ cập nhật khi có số liệu tháng mới)")
    p.add_argument("--fx-reserve-months", type=float, default=None, help="Dự trữ ngoại hối (số tháng nhập khẩu)")
    p.add_argument("--m2-growth", type=float, default=None, help="Tăng trưởng cung tiền M2 (%%)")
    p.add_argument("--no-save", action="store_true", help="Không ghi đè lại file CSV")
    return p.parse_args()


def main():
    args = parse_args()

    new_values = {}
    mapping = {
        "cpi": "CPI_YOY",
        "interbank_rate": "Interbank_Rate",
        "usdvnd_change": "USDVND_Change",
        "pmi": "PMI",
        "fx_reserve_months": "FX_Reserve_Months",
        "m2_growth": "M2_Growth",
    }
    for arg_name, col_name in mapping.items():
        val = getattr(args, arg_name)
        if val is not None:
            new_values[col_name] = val

    run_pipeline(new_values=new_values or None, save=not args.no_save)


if __name__ == "__main__":
    sys.exit(main())
