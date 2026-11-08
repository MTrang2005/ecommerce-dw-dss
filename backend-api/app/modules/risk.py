"""API cho Ops Console: 2 mô hình rủi ro của DSS (giao trễ, review thấp).
Proxy sang DSS service (/risk/...) — DSS sở hữu model và các bảng output trong DW.
"""
from datetime import date

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import DSS_BASE_URL

router = APIRouter(prefix="/risk", tags=["risk"])


_dss = httpx.Client(base_url=DSS_BASE_URL, timeout=60)  # dùng lại kết nối cho mọi request


def _proxy(method: str, path: str, params: dict | None = None, json: dict | None = None):
    resp = _dss.request(method, path, params=params, json=json)
    if 400 <= resp.status_code < 500:  # lỗi do input (400/404/409/422): giữ nguyên mã + thông báo của DSS
        raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail"))
    resp.raise_for_status()
    return resp.json()


@router.get("/metrics")
def model_metrics():
    return _proxy("GET", "/risk/metrics")


@router.get("/predict/options")
def predict_options():
    return _proxy("GET", "/risk/predict/options")


@router.get("/predict/distance")
def predict_distance(seller_state: str, customer_state: str):
    return _proxy("GET", "/risk/predict/distance", {"seller_state": seller_state, "customer_state": customer_state})


@router.get("/{model}/importance")
def feature_importance(model: str, limit: int = 10):
    return _proxy("GET", f"/risk/{model}/importance", {"limit": limit})


@router.get("/{model}/summary")
def risk_summary(model: str):
    return _proxy("GET", f"/risk/{model}/summary")


@router.get("/{model}/orders")
def risky_orders(model: str, scope: str = "holdout", level: str | None = None, min_score: float = 0,
                 limit: int = 100):
    params = {"scope": scope, "min_score": min_score, "limit": limit}
    if level:
        params["level"] = level
    return _proxy("GET", f"/risk/{model}/orders", params)


class WhatIfRequest(BaseModel):
    order_id: str
    overrides: dict[str, float]


@router.post("/{model}/what-if")
def what_if(model: str, req: WhatIfRequest):
    return _proxy("POST", f"/risk/{model}/what-if", json=req.model_dump())


class PredictRequest(BaseModel):
    seller_state: str
    customer_state: str
    category: str
    purchase_date: date
    estimated_date: date
    carrier_date: date | None = None
    shipping_limit_date: date | None = None
    shipping_limit_days: float | None = None
    delivered_date: date | None = None
    distance_km: float | None = None
    total_price: float | None = None
    total_freight: float | None = None
    n_items: int | None = None
    payment_type: str | None = None


@router.post("/{model}/predict")
def predict(model: str, req: PredictRequest):
    """Công cụ dự đoán: chấm điểm 1 đơn giả định do người dùng nhập."""
    return _proxy("POST", f"/risk/{model}/predict", json=req.model_dump(mode="json"))
