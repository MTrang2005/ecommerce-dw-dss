"""DSS model service — FastAPI. Expose 2 mô hình rủi ro (giao trễ, review thấp) để backend-api (BFF) gọi vào.
Chạy: uvicorn api.main:app --port 8001
"""
from datetime import date

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from common import get_engine
from risk.features import SPECS
from risk.predict import InvalidInput, ModelNotTrained, predict, predict_distance, predict_options, what_if
from risk.train import run_all

app = FastAPI(title="E-commerce DSS Service", version="0.3.0")


# Lỗi nghiệp vụ từ risk/*: input không hợp lệ -> 400, chưa có file model -> 409
@app.exception_handler(InvalidInput)
def bad_input(_: Request, exc: InvalidInput):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ModelNotTrained)
def model_missing(_: Request, exc: ModelNotTrained):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def query(sql: str, **params) -> list[dict]:
    df = pd.read_sql(text(sql), get_engine(), params=params)
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")  # NaN -> null cho JSON


def check_model(model: str):
    if model not in SPECS:
        raise HTTPException(status_code=404, detail=f"Unknown model {model}; choose one of {list(SPECS)}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/risk/retrain")
def retrain():
    return run_all()


@app.get("/risk/metrics")
def get_metrics():
    return query("SELECT * FROM dw.model_metrics ORDER BY model_name, is_primary DESC")


@app.get("/risk/predict/options")
def get_predict_options():
    """Bang, ngành hàng, loại thanh toán hợp lệ cho form dự đoán."""
    return predict_options()


@app.get("/risk/predict/distance")
def get_predict_distance(seller_state: str, customer_state: str):
    """Khoảng cách điển hình của tuyến để form dự đoán tự điền."""
    return predict_distance(seller_state, customer_state)


@app.get("/risk/{model}/importance")
def get_importance(model: str, limit: int = 10):
    check_model(model)
    return query("""
        SELECT feature, importance FROM dw.model_feature_importance
        WHERE model_name = :model ORDER BY importance DESC LIMIT :limit
    """, model=model, limit=limit)


@app.get("/risk/{model}/summary")
def get_summary(model: str):
    """Số đơn theo mức rủi ro; với đơn holdout kèm tỷ lệ xảy ra thật -> kiểm chứng mức rủi ro có ý nghĩa."""
    check_model(model)
    return query("""
        SELECT scope, risk_level, COUNT(*) AS orders, AVG(risk_score) AS avg_score,
               AVG(actual_label::INT) AS actual_rate
        FROM dw.order_risk_score WHERE model_name = :model
        GROUP BY scope, risk_level
        ORDER BY scope, CASE risk_level WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END
    """, model=model)


@app.get("/risk/{model}/orders")
def get_risky_orders(model: str, scope: str = "holdout", level: str | None = None, min_score: float = 0,
                     limit: int = 100):
    check_model(model)
    sql = """
        SELECT r.order_id, r.risk_score, r.risk_level, r.actual_label, f.order_status,
               f.purchase_ts, f.carrier_ts, f.delivered_ts, f.estimated_date,
               s.state AS seller_state, l.state AS customer_state, p.category,
               f.distance_km, f.estimated_days, f.carrier_days, f.delay_days, f.total_price, f.review_score
        FROM dw.order_risk_score r
        JOIN dw.fact_order_fulfillment f ON f.order_id = r.order_id
        LEFT JOIN dw.dim_seller s   ON s.seller_sk = f.seller_sk
        LEFT JOIN dw.dim_location l ON l.location_sk = f.location_sk
        LEFT JOIN dw.dim_product p  ON p.product_sk = f.product_sk
        WHERE r.model_name = :model AND r.scope = :scope AND r.risk_score >= :min_score
    """
    params = {"model": model, "scope": scope, "min_score": min_score, "limit": limit}
    if level:
        sql += " AND r.risk_level = :level"
        params["level"] = level
    sql += " ORDER BY r.risk_score DESC LIMIT :limit"
    return query(sql, **params)


class WhatIfRequest(BaseModel):
    order_id: str
    overrides: dict[str, float]


@app.post("/risk/{model}/what-if")
def post_what_if(model: str, req: WhatIfRequest):
    check_model(model)
    try:
        return what_if(model, req.order_id, req.overrides)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown order_id")


class PredictRequest(BaseModel):
    """Đơn giả định. Bắt buộc: tuyến, ngành, ngày mua, ngày hẹn giao; còn lại tuỳ chọn (thiếu -> giá trị điển hình)."""
    seller_state: str
    customer_state: str
    category: str
    purchase_date: date
    estimated_date: date
    carrier_date: date | None = None         # ngày người bán bàn giao cho đơn vị vận chuyển
    shipping_limit_date: date | None = None  # hạn bàn giao (ngày cụ thể)
    shipping_limit_days: float | None = None # hoặc: số ngày sau khi mua theo quy định sàn; trống -> DEFAULT_SHIPPING_LIMIT_DAYS
    delivered_date: date | None = None       # ngày giao tới khách (mô hình review thấp)
    distance_km: float | None = None         # bỏ trống -> khoảng cách điển hình của tuyến
    total_price: float | None = None
    total_freight: float | None = None
    n_items: int | None = None
    payment_type: str | None = None


@app.post("/risk/{model}/predict")
def post_predict(model: str, req: PredictRequest):
    check_model(model)
    return predict(model, req.model_dump())
