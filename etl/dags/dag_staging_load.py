"""Airflow DAG: nạp CSV Olist (data/raw) vào schema staging,
xong thì kích hoạt DAG `dw_load`.

Olist là dataset lịch sử tĩnh nên DAG chạy thủ công (schedule=None) thay vì @daily.
"""
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from etl.extract.extract_olist import SOURCES, load_table

default_args = {"owner": "data-team", "retries": 1}

with DAG(
    dag_id="staging_load",
    description="Load Olist CSV vào schema staging",
    schedule=None,
    start_date=datetime(2026, 9, 1),
    catchup=False,
    default_args=default_args,
    tags=["ecommerce-dw", "staging"],
) as dag:

    load_tasks = [
        PythonOperator(task_id=f"load_{table}", python_callable=load_table, op_kwargs={"table": table})
        for table in SOURCES
    ]

    trigger_dw_load = TriggerDagRunOperator(task_id="trigger_dw_load", trigger_dag_id="dw_load")

    load_tasks >> trigger_dw_load
