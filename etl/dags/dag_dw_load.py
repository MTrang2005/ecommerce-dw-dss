"""Airflow DAG: transform staging -> DW (dimension upsert + SCD2 địa chỉ) rồi build 2 fact + refresh OLAP views.
Được kích hoạt bởi DAG `staging_load`; xong thì kích hoạt `dss_batch`.
"""
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from etl.load import load_dw
from etl.transform import transform_dw

default_args = {"owner": "data-team", "retries": 1}

with DAG(
    dag_id="dw_load",
    description="Transform + load staging -> DW, refresh OLAP views",
    schedule=None,
    start_date=datetime(2026, 9, 1),
    catchup=False,
    default_args=default_args,
    tags=["ecommerce-dw", "dw"],
) as dag:

    t_dim_date = PythonOperator(task_id="build_dim_date", python_callable=transform_dw.build_dim_date)
    t_zip_geo = PythonOperator(task_id="build_ref_zip_geo", python_callable=transform_dw.build_ref_zip_geo)
    t_dims = [
        PythonOperator(task_id="upsert_dim_location", python_callable=transform_dw.upsert_dim_location),
        PythonOperator(task_id="upsert_dim_seller", python_callable=transform_dw.upsert_dim_seller),
        PythonOperator(task_id="upsert_dim_payment_type", python_callable=transform_dw.upsert_dim_payment_type),
        PythonOperator(task_id="upsert_dim_product", python_callable=transform_dw.upsert_dim_product),
        PythonOperator(task_id="upsert_dim_customer", python_callable=transform_dw.upsert_dim_customer_scd2),
    ]
    t_fact_order = PythonOperator(task_id="load_fact_order_item", python_callable=load_dw.load_fact_order_item)
    t_fact_fulfillment = PythonOperator(task_id="load_fact_order_fulfillment",
                                        python_callable=load_dw.load_fact_order_fulfillment)
    t_create_views = PythonOperator(task_id="create_olap_views", python_callable=load_dw.create_olap_views)
    t_refresh_views = PythonOperator(task_id="refresh_olap_views", python_callable=load_dw.refresh_olap_views)
    t_trigger_dss = TriggerDagRunOperator(task_id="trigger_dss_batch", trigger_dag_id="dss_batch")

    t_dim_date >> t_zip_geo >> t_dims >> t_fact_order >> t_fact_fulfillment >> t_create_views >> t_refresh_views >> t_trigger_dss
