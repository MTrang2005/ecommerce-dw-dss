"""Vẽ các hình cho chương Đánh giá kết quả từ dữ liệu thật trong kho (DB Docker, cổng 5433).

Chạy sau khi DAG dss_batch đã train xong:  python3 report/scripts/make_figures.py
Hình được ghi vào report/Picture/ dưới dạng PDF (vector).
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import psycopg2
from sklearn.metrics import precision_recall_curve, roc_auc_score, average_precision_score

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "report" / "Picture"
MODELS = {"late_delivery": "Rủi ro giao trễ", "low_review": "Rủi ro đánh giá thấp"}


def connect():
    env = dict(
        line.split("=", 1)
        for line in (ROOT / "infra" / ".env.local").read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    return psycopg2.connect(
        host="localhost", port=5433,
        user=env["POSTGRES_USER"], password=env["POSTGRES_PASSWORD"], dbname=env["POSTGRES_DB"],
    )


def rows(cur, sql, *params):
    cur.execute(sql, params)
    return cur.fetchall()


def fig_pr_curves(cur):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, (model, title) in zip(axes, MODELS.items()):
        data = rows(cur, """SELECT risk_score, actual_label FROM dw.order_risk_score
                            WHERE model_name = %s AND scope = 'holdout'""", model)
        score = np.array([float(s) for s, _ in data])
        label = np.array([bool(y) for _, y in data])
        precision, recall, _ = precision_recall_curve(label, score)
        ax.plot(recall, precision, color="#2563eb",
                label=f"HGB (PR-AUC {average_precision_score(label, score):.3f}, "
                      f"ROC-AUC {roc_auc_score(label, score):.3f})")
        ax.axhline(label.mean(), color="#9ca3af", ls="--", label=f"Ngẫu nhiên ({label.mean():.3f})")
        ax.set(title=title, xlabel="Recall", ylabel="Precision", xlim=(0, 1), ylim=(0, 1.02))
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "pr_curves.pdf")


def fig_importance(cur):
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    for ax, (model, title) in zip(axes, MODELS.items()):
        data = rows(cur, """SELECT feature, importance FROM dw.model_feature_importance
                            WHERE model_name = %s AND model_version = 'hgb_v1'
                            ORDER BY importance DESC LIMIT 8""", model)[::-1]
        ax.barh([f for f, _ in data], [float(v) for _, v in data], color="#f97316")
        ax.set(title=title, xlabel="Mức giảm PR-AUC khi xáo trộn")
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "feature_importance.pdf")


def fig_monthly(cur):
    data = rows(cur, """SELECT year, month, SUM(delivered_orders), SUM(late_orders),
                               SUM(reviewed_orders), SUM(low_review_orders)
                        FROM dw.mv_delivery_performance
                        WHERE make_date(year, month, 1) BETWEEN '2017-01-01' AND '2018-08-01'
                        GROUP BY 1, 2 ORDER BY 1, 2""")
    labels = [f"{m:02d}/{str(y)[2:]}" for y, m, *_ in data]
    late = [100 * float(l) / float(d) for _, _, d, l, _, _ in data]
    low = [100 * float(lo) / float(r) for *_, r, lo in data]
    fig, ax = plt.subplots(figsize=(10, 3.6))
    ax.plot(labels, late, marker="o", color="#dc2626", label="Tỷ lệ giao trễ (%)")
    ax.plot(labels, low, marker="s", color="#2563eb", label="Tỷ lệ đánh giá ≤ 2 sao (%)")
    ax.axvspan(labels.index("06/18") - 0.5, len(labels) - 0.5, color="#e5e7eb", label="Tập kiểm định")
    ax.set(xlabel="Tháng mua hàng", ylabel="%")
    ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "monthly_late_review.pdf")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    with connect() as conn, conn.cursor() as cur:
        fig_monthly(cur)
        fig_pr_curves(cur)
        fig_importance(cur)
    print("Đã ghi hình vào", OUT)
