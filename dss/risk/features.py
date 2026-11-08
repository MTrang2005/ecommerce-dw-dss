"""Dữ liệu + feature cho 2 mô hình rủi ro, đọc từ dw.fact_order_fulfillment (1 dòng / đơn) và các dimension.

Mỗi mô hình chấm điểm ở MỘT thời điểm trong vòng đời đơn và chỉ dùng thông tin đã biết tại thời điểm đó
(tránh leakage):
- late_delivery: lúc người bán vừa bàn giao cho đơn vị vận chuyển (biết carrier_days, chưa biết ngày giao).
- low_review   : lúc đơn vừa giao tới khách, trước khi khách viết review (biết số ngày giao / trễ thực tế).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import get_engine

LOAD_SQL = """
    SELECT f.order_id, f.order_status, f.purchase_ts, f.carrier_ts, f.delivered_ts,
           l.state AS customer_state, s.state AS seller_state, p.category, pt.payment_type,
           d.month AS purchase_month, d.day_of_week AS purchase_dow,
           f.n_items, f.n_sellers, f.total_price, f.total_freight, f.total_weight_g, f.total_volume_cm3,
           f.payment_installments, f.distance_km, f.same_state::INT AS same_state, f.estimated_days,
           f.shipping_limit_days, f.approval_hours, f.carrier_days, f.actual_days, f.delay_days,
           f.is_late, f.is_low_review
    FROM dw.fact_order_fulfillment f
    JOIN dw.dim_date d               ON d.date_sk = f.purchase_date_sk
    LEFT JOIN dw.dim_location l      ON l.location_sk = f.location_sk
    LEFT JOIN dw.dim_seller s        ON s.seller_sk = f.seller_sk
    LEFT JOIN dw.dim_product p       ON p.product_sk = f.product_sk
    LEFT JOIN dw.dim_payment_type pt ON pt.payment_type_sk = f.payment_type_sk
"""

# Thông tin có ngay khi đơn được đặt / duyệt
BASE_NUMERIC = [
    "distance_km", "same_state", "estimated_days", "shipping_limit_days", "approval_hours",
    "purchase_month", "purchase_dow", "n_items", "n_sellers", "total_price", "total_freight",
    "freight_ratio", "total_weight_g", "total_volume_cm3", "payment_installments",
]
CATEGORICAL = ["customer_state", "seller_state", "category", "payment_type"]
# Cột gốc trong fact mà người dùng được phép thay đổi khi chạy what-if (các cột suy ra sẽ tính lại)
WHAT_IF_FIELDS = ["estimated_days", "carrier_days", "shipping_limit_days", "actual_days", "delay_days",
                  "distance_km", "total_freight"]


@dataclass(frozen=True)
class ModelSpec:
    name: str
    target: str
    numeric: list[str]
    description: str

    @property
    def features(self) -> list[str]:
        return self.numeric + CATEGORICAL


SPECS = {
    "late_delivery": ModelSpec(
        name="late_delivery",
        target="is_late",
        numeric=BASE_NUMERIC + ["carrier_days", "slack_days", "handoff_vs_limit_days"],
        description="Rủi ro giao trễ hơn ngày hẹn — chấm điểm lúc đơn được bàn giao vận chuyển",
    ),
    "low_review": ModelSpec(
        name="low_review",
        target="is_low_review",
        numeric=BASE_NUMERIC + ["carrier_days", "actual_days", "delay_days", "late_flag"],
        description="Rủi ro nhận review <= 2 sao — chấm điểm lúc đơn vừa giao tới khách",
    ),
}


def derive(df: pd.DataFrame) -> pd.DataFrame:
    """Feature suy ra từ cột gốc. Tách riêng để what-if đổi cột gốc rồi tính lại nhất quán."""
    df = df.copy()
    df["freight_ratio"] = df["total_freight"] / df["total_price"].replace(0, np.nan)
    df["slack_days"] = df["estimated_days"] - df["carrier_days"]            # còn bao nhiêu ngày tới hạn giao
    df["handoff_vs_limit_days"] = df["carrier_days"] - df["shipping_limit_days"]  # > 0: người bán giao muộn
    df["late_flag"] = (df["delay_days"] > 0).astype(float).where(df["delay_days"].notna())
    return df


def load_orders(order_id: str | None = None) -> pd.DataFrame:
    sql, params = LOAD_SQL, {}
    if order_id:
        sql += " WHERE f.order_id = %(order_id)s"
        params["order_id"] = order_id
    # Thứ tự dòng cố định: HGB tách validation nội bộ (early stopping) theo thứ tự dòng, nên không ORDER BY
    # thì kết quả train đổi mỗi khi Postgres ghi lại bảng (VACUUM, UPDATE, ALTER TABLE...).
    sql += " ORDER BY f.order_id"
    df = pd.read_sql(sql, get_engine(), params=params)
    numeric_cols = list(dict.fromkeys(c for c in BASE_NUMERIC + WHAT_IF_FIELDS if c != "freight_ratio"))
    df[numeric_cols] = df[numeric_cols].astype(float)
    return derive(df)


def to_matrix(df: pd.DataFrame, spec: ModelSpec, categories: dict[str, list]) -> pd.DataFrame:
    """Chọn feature; biến phân loại -> pandas Category với tập giá trị CỐ ĐỊNH lấy từ tập train
    (giá trị lạ -> NaN, HGB coi như thiếu)."""
    X = df[spec.features].copy()
    for col in CATEGORICAL:
        X[col] = pd.Categorical(X[col], categories=categories[col])
    return X


def risk_level(p: np.ndarray, threshold: float) -> np.ndarray:
    """High >= ngưỡng theo chi phí, Medium >= nửa ngưỡng, còn lại Low."""
    return np.where(p >= threshold, "High", np.where(p >= threshold / 2, "Medium", "Low"))


def labeled_mask(df: pd.DataFrame, spec: ModelSpec) -> pd.Series:
    """Đơn dùng để train/kiểm định: đã giao (có nhãn) và đã qua thời điểm chấm điểm của mô hình."""
    mask = df["delivered_ts"].notna() & df[spec.target].notna()
    if spec.name == "late_delivery":
        mask &= df["carrier_ts"].notna()
    return mask


def open_mask(df: pd.DataFrame, spec: ModelSpec) -> pd.Series:
    """Đơn 'đang mở' cần chấm điểm thật: đang vận chuyển (đã bàn giao, chưa giao tới khách)."""
    if spec.name != "late_delivery":
        return pd.Series(False, index=df.index)
    return (df["order_status"] == "shipped") & df["carrier_ts"].notna() & df["delivered_ts"].isna()
