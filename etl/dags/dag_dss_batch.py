"""Airflow DAG: retrain 2 mô hình rủi ro của DSS (giao trễ, review thấp) và ghi kết quả trở lại DW
(dw.model_metrics, dw.model_feature_importance, dw.order_risk_score).
Gọi qua HTTP tới `dss` service để tách runtime Python (scikit-learn) khỏi Airflow worker.
Kích hoạt bởi `dw_load`, hoặc chạy hàng tuần.
"""
from datetime import datetime

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator

DSS_BASE_URL = "http://dss:8001"
default_args = {"owner": "data-team", "retries": 1}


def _post(path: str, timeout: int):
    resp = requests.post(f"{DSS_BASE_URL}{path}", timeout=timeout)
    resp.raise_for_status()
    print(resp.json())


with DAG(
    dag_id="dss_batch",
    description="Retrain mô hình rủi ro giao trễ + rủi ro review thấp",
    schedule="@weekly",
    start_date=datetime(2026, 9, 1),
    catchup=False,
    default_args=default_args,
    tags=["ecommerce-dw", "dss"],
) as dag:

    retrain_risk_models = PythonOperator(
        task_id="retrain_risk_models", python_callable=_post, op_kwargs={"path": "/risk/retrain", "timeout": 1800},
    )
