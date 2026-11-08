"""Dùng model đã train (file dss/models/<name>.pkl do train.py ghi):

- what_if : chấm lại 1 đơn có thật sau khi đổi vài cột gốc (vd hẹn giao thêm N ngày).
- predict : chấm 1 đơn GIẢ ĐỊNH do người dùng nhập (công cụ "Dự đoán rủi ro" trên frontend). Người dùng chỉ nhập
            vài thông tin chính; các đặc trưng còn lại điền bằng giá trị điển hình học từ tập train ("profile",
            lưu cùng model), kèm nguồn của từng giá trị để người dùng biết giá trị nào là giả định.
"""
import os
from datetime import date

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text

from common import get_engine
from risk.features import BASE_NUMERIC, WHAT_IF_FIELDS, derive, load_orders, risk_level, to_matrix

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
# Cột điền theo ngành hàng (đơn cùng ngành có giá / khối lượng tương tự)
CATEGORY_FIELDS = ["total_price", "total_freight", "total_weight_g", "total_volume_cm3", "n_items"]
# Cột điền theo tuyến (bang người bán -> bang khách)
ROUTE_FIELDS = ["distance_km", "carrier_days", "actual_days"]
MIN_GROUP_ORDERS = 20   # nhóm ít đơn hơn -> dùng giá trị chung toàn sàn
MAX_SHIPPING_LIMIT_DAYS = 60


class InvalidInput(ValueError):
    """Input của người dùng không hợp lệ (API trả 400)."""


class ModelNotTrained(FileNotFoundError):
    """Chưa có file model (API trả 409)."""


def model_path(name: str) -> str:
    return os.path.join(MODELS_DIR, f"{name}.pkl")


_bundles: dict[str, tuple[float, dict]] = {}


def load_bundle(name: str) -> dict:
    """Đọc file model một lần rồi giữ trong bộ nhớ; retrain ghi file mới (mtime đổi) thì tự đọc lại."""
    path = model_path(name)
    if not os.path.exists(path):
        raise ModelNotTrained("Model is not trained yet — call POST /risk/retrain first")
    mtime = os.path.getmtime(path)
    cached = _bundles.get(name)
    if cached is None or cached[0] != mtime:
        _bundles[name] = (mtime, joblib.load(path))
    return _bundles[name][1]


def _haversine(lat1, lng1, lat2, lng2) -> float:
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return float(2 * 6371 * np.arcsin(np.sqrt(a)))


def build_profile(history: pd.DataFrame) -> dict:
    """Giá trị điển hình từ các đơn đã giao trong giai đoạn train."""
    numeric = list(dict.fromkeys(BASE_NUMERIC + ["carrier_days", "actual_days"]))
    route = history.groupby(["seller_state", "customer_state"]).agg(
        orders=("order_id", "size"), late_rate=("is_late", lambda s: s.astype(float).mean()),
        low_review_rate=("is_low_review", lambda s: s.dropna().astype(float).mean()),
        **{f: (f, "median") for f in ROUTE_FIELDS})
    category = history.groupby("category").agg(orders=("order_id", "size"), **{f: (f, "median") for f in CATEGORY_FIELDS})
    centroids = pd.read_sql(text("SELECT state, AVG(lat) AS lat, AVG(lng) AS lng FROM dw.ref_zip_geo GROUP BY state"),
                            get_engine()).set_index("state")
    return {
        "global": {c: float(history[c].median()) for c in numeric},
        "payment_type": history["payment_type"].mode().iloc[0],
        "route": {f"{s}|{c}": row._asdict() for (s, c), row in zip(route.index, route.itertuples(index=False))},
        "category": {k: row._asdict() for k, row in zip(category.index, category.itertuples(index=False))},
        "centroid": centroids[["lat", "lng"]].to_dict(orient="index"),
    }


def route_distance(profile: dict, seller_state: str, customer_state: str) -> dict:
    """Khoảng cách điển hình của tuyến: trung vị các đơn thật -> tuyến hiếm dùng khoảng cách giữa tâm 2 bang."""
    route = profile["route"].get(f"{seller_state}|{customer_state}")
    if route is not None and route["orders"] >= MIN_GROUP_ORDERS and pd.notna(route["distance_km"]):
        return {"distance_km": round(float(route["distance_km"]), 1), "source": "trung vị tuyến",
                "orders": int(route["orders"])}
    a, b = profile["centroid"].get(seller_state), profile["centroid"].get(customer_state)
    if a and b:
        return {"distance_km": round(_haversine(a["lat"], a["lng"], b["lat"], b["lng"]), 1), "source": "tâm 2 bang",
                "orders": int(route["orders"]) if route is not None else 0}
    return {"distance_km": round(profile["global"]["distance_km"], 1), "source": "trung vị toàn sàn", "orders": 0}


def shipping_limit_policy(profile: dict) -> dict:
    """Hạn bàn giao mặc định (số ngày sau khi mua): cấu hình của sàn qua biến môi trường DEFAULT_SHIPPING_LIMIT_DAYS,
    không đặt thì dùng trung vị dữ liệu thật."""
    configured = os.getenv("DEFAULT_SHIPPING_LIMIT_DAYS", "").strip()
    if configured:
        days = float(configured)
        if not 0 <= days <= MAX_SHIPPING_LIMIT_DAYS:
            raise InvalidInput(f"DEFAULT_SHIPPING_LIMIT_DAYS must be within 0..{MAX_SHIPPING_LIMIT_DAYS}")
        return {"days": days, "source": "quy định sàn"}
    return {"days": round(profile["global"]["shipping_limit_days"], 1), "source": "trung vị dữ liệu"}


def _days(later: date | None, earlier: date) -> float | None:
    return None if later is None else float((later - earlier).days)


def build_order(profile: dict, req: dict, categories: dict[str, list]) -> tuple[pd.DataFrame, dict, dict | None]:
    """1 dòng DataFrame có đủ cột như load_orders() + bảng giá trị đã dùng (giá trị, nguồn) + lịch sử tuyến."""
    _check_known(categories, **{f: req[f] for f in ["seller_state", "customer_state", "category"]})
    purchase: date = req["purchase_date"]
    for field in ["estimated_date", "carrier_date", "shipping_limit_date", "delivered_date"]:
        if req.get(field) is not None and req[field] < purchase:
            raise InvalidInput(f"{field} must be on or after the purchase date")

    g = profile["global"]
    route_key = f"{req['seller_state']}|{req['customer_state']}"
    route = profile["route"].get(route_key)
    route_ok = route is not None and route["orders"] >= MIN_GROUP_ORDERS
    cat = profile["category"].get(req["category"])
    cat_ok = cat is not None and cat["orders"] >= MIN_GROUP_ORDERS

    used: dict[str, tuple[float | str | None, str]] = {}   # cột -> (giá trị, nguồn)

    def pick(field: str, user_value, fallbacks: list[tuple[bool, dict | None, str]]):
        if user_value is not None:
            used[field] = (user_value, "nhập")
            return user_value
        for ok, source, label in fallbacks:
            if ok and source is not None and pd.notna(source.get(field)):
                used[field] = (float(source[field]), label)
                return float(source[field])
        used[field] = (g[field], "trung vị toàn sàn")
        return g[field]

    route_fb = [(route_ok, route, "trung vị tuyến")]
    cat_fb = [(cat_ok, cat, "trung vị ngành")]

    # Khoảng cách: người dùng nhập -> trung vị tuyến -> khoảng cách giữa tâm 2 bang
    if req.get("distance_km") is not None:
        if req["distance_km"] < 0:
            raise InvalidInput("distance_km must be >= 0")
        distance, source = float(req["distance_km"]), "nhập"
    else:
        typical = route_distance(profile, req["seller_state"], req["customer_state"])
        distance, source = typical["distance_km"], typical["source"]
    used["distance_km"] = (round(distance, 1), source)

    # Hạn bàn giao: ngày cụ thể -> số ngày theo quy định sàn (người dùng chỉnh trên form) -> mặc định của sàn
    if req.get("shipping_limit_date") is not None:
        shipping_limit, source = _days(req["shipping_limit_date"], purchase), "nhập"
    elif req.get("shipping_limit_days") is not None:
        if not 0 <= req["shipping_limit_days"] <= MAX_SHIPPING_LIMIT_DAYS:
            raise InvalidInput(f"shipping_limit_days must be within 0..{MAX_SHIPPING_LIMIT_DAYS}")
        shipping_limit, source = float(req["shipping_limit_days"]), "quy định sàn (nhập)"
    else:
        policy = shipping_limit_policy(profile)
        shipping_limit, source = policy["days"], policy["source"]
    used["shipping_limit_days"] = (shipping_limit, source)

    row = {
        "order_id": "simulated",
        "customer_state": req["customer_state"], "seller_state": req["seller_state"], "category": req["category"],
        "payment_type": req.get("payment_type") or profile["payment_type"],
        "purchase_month": float(purchase.month), "purchase_dow": float(purchase.isoweekday()),
        "distance_km": distance,
        "same_state": float(req["seller_state"] == req["customer_state"]),
        "estimated_days": _days(req["estimated_date"], purchase),
        "approval_hours": pick("approval_hours", None, []),
        "shipping_limit_days": shipping_limit,
        "carrier_days": pick("carrier_days", _days(req.get("carrier_date"), purchase), route_fb),
        "actual_days": pick("actual_days", _days(req.get("delivered_date"), purchase), route_fb),
        "n_items": pick("n_items", req.get("n_items"), cat_fb),
        "n_sellers": 1.0,
        "total_price": pick("total_price", req.get("total_price"), cat_fb),
        "total_freight": pick("total_freight", req.get("total_freight"), cat_fb),
        "total_weight_g": pick("total_weight_g", None, cat_fb),
        "total_volume_cm3": pick("total_volume_cm3", None, cat_fb),
        "payment_installments": pick("payment_installments", None, []),
    }
    used["payment_type"] = (row["payment_type"], "nhập" if req.get("payment_type") else "phổ biến nhất")
    used["estimated_days"] = (row["estimated_days"], "nhập")
    # Ngày giao = ngày mua + số ngày giao (lấy phần ngày, giống ETL: delivered::DATE - estimated::DATE)
    row["delay_days"] = float(np.floor(row["actual_days"]) - row["estimated_days"])
    used["delay_days"] = (row["delay_days"], "nhập" if req.get("delivered_date") else "suy ra từ ngày giao giả định")

    history = None
    if route is not None:
        history = {"orders": int(route["orders"]), "late_rate": _round(route["late_rate"], 4),
                   "low_review_rate": _round(route["low_review_rate"], 4)}
    return derive(pd.DataFrame([row])), used, history


def _round(v, digits: int = 2):
    return None if v is None or pd.isna(v) else round(float(v), digits)


def what_if(model_name: str, order_id: str, overrides: dict[str, float]) -> dict:
    """Chấm lại 1 đơn sau khi thay đổi một số cột gốc (vd hẹn giao thêm 3 ngày, người bán bàn giao sớm hơn)."""
    unknown = set(overrides) - set(WHAT_IF_FIELDS)
    if unknown:
        raise InvalidInput(f"Field {sorted(unknown)} cannot be changed; choose from {WHAT_IF_FIELDS}")
    bundle = load_bundle(model_name)
    order = load_orders(order_id)
    if order.empty:
        raise KeyError(order_id)
    changed = derive(order.assign(**{f: float(v) for f, v in overrides.items()}))
    X = lambda df: to_matrix(df, bundle["spec"], bundle["categories"])  # noqa: E731
    scores = [float(bundle["model"].predict_proba(X(df))[0, 1]) for df in (order, changed)]
    levels = risk_level(np.array(scores), bundle["threshold"])
    state = lambda df, i: {"risk_score": round(scores[i], 4), "risk_level": str(levels[i]),  # noqa: E731
                           **{f: _round(df[f].iloc[0]) for f in WHAT_IF_FIELDS}}
    return {"order_id": order_id, "model": model_name, "threshold": bundle["threshold"],
            "before": state(order, 0), "after": state(changed, 1)}


def predict(model_name: str, req: dict) -> dict:
    """Chấm điểm 1 đơn giả định do người dùng nhập (tuyến, ngành, ngày mua, ngày hẹn giao, ...)."""
    bundle = load_bundle(model_name)
    spec = bundle["spec"]
    order, used, history = build_order(bundle["profile"], req, bundle["categories"])
    p = float(bundle["model"].predict_proba(to_matrix(order, spec, bundle["categories"]))[0, 1])
    derived = [f for f in ["slack_days", "handoff_vs_limit_days", "delay_days"] if f in spec.numeric]
    shown = [f for f in spec.numeric if f in used and f not in derived] + ["payment_type"]
    return {
        "model": model_name,
        "risk_score": round(p, 4),
        "risk_level": str(risk_level(np.array([p]), bundle["threshold"])[0]),
        "threshold": bundle["threshold"],
        "inputs_used": [{"feature": f, "value": used[f][0] if f == "payment_type" else _round(used[f][0]),
                         "source": used[f][1]} for f in shown],
        "derived": {f: _round(order[f].iloc[0]) for f in derived},
        "route_history": history,
    }


def _check_known(categories: dict[str, list], **values: str):
    for field, value in values.items():
        if value not in categories[field]:
            raise InvalidInput(f"{field} = {value!r} is not in the training data")


def predict_distance(seller_state: str, customer_state: str) -> dict:
    """Khoảng cách điển hình của tuyến (km) — form dự đoán tự điền khi người dùng chọn 2 bang."""
    bundle = load_bundle("late_delivery")
    _check_known(bundle["categories"], seller_state=seller_state, customer_state=customer_state)
    return {"seller_state": seller_state, "customer_state": customer_state,
            **route_distance(bundle["profile"], seller_state, customer_state)}


def predict_options() -> dict:
    """Danh sách giá trị hợp lệ cho form dự đoán (lấy từ tập train của model) + hạn bàn giao mặc định."""
    bundle = load_bundle("late_delivery")
    options = {c: [str(v) for v in values] for c, values in bundle["categories"].items()}
    options["shipping_limit"] = shipping_limit_policy(bundle["profile"])
    return options
