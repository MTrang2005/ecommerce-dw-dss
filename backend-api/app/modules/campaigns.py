"""API campaign chăm sóc: thêm/sửa/xoá campaign, và gắn tệp khách hàng mục tiêu vào 1 campaign.

Tệp mục tiêu lấy từ DSS: khách của các đơn có rủi ro review thấp >= min_risk (mô hình low_review,
dw.order_risk_score). Olist là dữ liệu lịch sử nên các đơn này thuộc tập kiểm định (đã có review thật): quy trình
hiện là mô phỏng; khi triển khai thật sẽ chấm các đơn vừa giao để Ops chăm sóc TRƯỚC khi khách viết review.
"""
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import engine

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignInput(BaseModel):
    campaign_name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=1000)


def _clean(body: CampaignInput) -> dict:
    name = body.campaign_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Campaign name must not be empty")
    return {"name": name, "description": (body.description or "").strip() or None}


def _duplicate_name(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"A campaign named '{name}' already exists")


def _campaign_sk(conn, code: str) -> int:
    sk = conn.execute(text("""
        SELECT campaign_sk FROM dw.care_campaign WHERE campaign_code = :code AND deleted_at IS NULL
    """), {"code": code}).scalar()
    if sk is None:
        raise HTTPException(status_code=404, detail=f"Unknown campaign_code {code}")
    return sk


@router.get("")
def list_campaigns():
    df = pd.read_sql(text("""
        SELECT c.campaign_code, c.campaign_name, c.description, COUNT(t.customer_sk)::INT AS total_targets
        FROM dw.care_campaign c
        LEFT JOIN dw.campaign_target t ON t.campaign_sk = c.campaign_sk
        WHERE c.deleted_at IS NULL
        GROUP BY c.campaign_sk
        ORDER BY c.campaign_sk
    """), engine)
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


@router.post("", status_code=201)
def create_campaign(body: CampaignInput):
    values = _clean(body)
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                WITH new_id AS (SELECT nextval(pg_get_serial_sequence('dw.care_campaign', 'campaign_sk')) AS sk)
                INSERT INTO dw.care_campaign (campaign_sk, campaign_code, campaign_name, description)
                SELECT sk, 'CMP' || LPAD(sk::TEXT, 4, '0'), :name, :description FROM new_id
                RETURNING campaign_code, campaign_name, description
            """), values).mappings().one()
    except IntegrityError:
        raise _duplicate_name(values["name"])
    return {**row, "total_targets": 0}


@router.put("/{code}")
def update_campaign(code: str, body: CampaignInput):
    values = _clean(body)
    try:
        with engine.begin() as conn:
            sk = _campaign_sk(conn, code)
            conn.execute(text("""
                UPDATE dw.care_campaign SET campaign_name = :name, description = :description WHERE campaign_sk = :sk
            """), {**values, "sk": sk})
    except IntegrityError:
        raise _duplicate_name(values["name"])
    return {"campaign_code": code, "campaign_name": values["name"], "description": values["description"]}


@router.delete("/{code}")
def delete_campaign(code: str):
    """Xoá campaign cùng tệp khách đã gắn."""
    with engine.begin() as conn:
        sk = _campaign_sk(conn, code)
        removed = conn.execute(text("DELETE FROM dw.campaign_target WHERE campaign_sk = :sk"), {"sk": sk}).rowcount
        conn.execute(text("UPDATE dw.care_campaign SET deleted_at = now() WHERE campaign_sk = :sk"), {"sk": sk})
    return {"campaign_code": code, "deleted_targets": removed}


@router.post("/{code}/targets")
def assign_risky_customers(code: str, min_risk: float = Query(0.2, ge=0, le=1,
                                                               description="Ngưỡng rủi ro review thấp")):
    """Gắn khách của các đơn có risk_score (low_review) >= min_risk vào campaign. Gọi lại không nhân đôi.

    1 khách thật (customer_id) chỉ gắn 1 lần / campaign — kể cả khi khách có nhiều version SCD2 (đổi địa chỉ):
    chọn đơn rủi ro nhất của khách, lưu customer_sk của version tại đơn đó.
    """
    with engine.begin() as conn:
        sk = _campaign_sk(conn, code)
        inserted = conn.execute(text("""
            INSERT INTO dw.campaign_target (campaign_sk, customer_sk, order_id, risk_score)
            SELECT DISTINCT ON (c.customer_id) :sk, f.customer_sk, r.order_id, r.risk_score
            FROM dw.order_risk_score r
            JOIN dw.fact_order_fulfillment f ON f.order_id = r.order_id
            JOIN dw.dim_customer c ON c.customer_sk = f.customer_sk
            WHERE r.model_name = 'low_review' AND r.risk_score >= :min_risk
              AND NOT EXISTS (SELECT 1 FROM dw.campaign_target t JOIN dw.dim_customer tc ON tc.customer_sk = t.customer_sk
                              WHERE t.campaign_sk = :sk AND tc.customer_id = c.customer_id)
            ORDER BY c.customer_id, r.risk_score DESC
        """), {"sk": sk, "min_risk": min_risk}).rowcount
    return {"campaign_code": code, "min_risk": min_risk, "inserted": inserted}


@router.get("/{code}/targets")
def list_targets(code: str, limit: int = Query(200, ge=1)):
    with engine.connect() as conn:
        sk = _campaign_sk(conn, code)
        stats = conn.execute(text("""
            SELECT COUNT(*) AS total, AVG(risk_score) AS avg_risk FROM dw.campaign_target WHERE campaign_sk = :sk
        """), {"sk": sk}).mappings().one()
        customers = conn.execute(text("""
            SELECT c.customer_id, c.city, c.state, t.order_id, t.risk_score, t.assigned_at
            FROM dw.campaign_target t JOIN dw.dim_customer c ON c.customer_sk = t.customer_sk
            WHERE t.campaign_sk = :sk ORDER BY t.risk_score DESC, c.customer_id LIMIT :limit
        """), {"sk": sk, "limit": limit}).mappings().all()
    return {
        "campaign_code": code,
        "total": stats["total"],
        "avg_risk": float(stats["avg_risk"]) if stats["avg_risk"] is not None else None,
        "customers": [dict(r) for r in customers],
    }


@router.delete("/{code}/targets")
def clear_targets(code: str):
    with engine.begin() as conn:
        sk = _campaign_sk(conn, code)
        deleted = conn.execute(text("DELETE FROM dw.campaign_target WHERE campaign_sk = :sk"), {"sk": sk}).rowcount
    return {"campaign_code": code, "deleted": deleted}
