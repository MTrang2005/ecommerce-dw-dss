"""Train + đánh giá + chấm điểm 2 mô hình rủi ro (late_delivery, low_review).

Chia dữ liệu THEO THỜI GIAN MUA (không xáo trộn) để mô phỏng dùng mô hình cho đơn tương lai:
    train      : 2017-01-01 -> 2018-05-31   (2016 rất thưa nên bỏ)
      fit      : 2017-01-01 -> 2018-02-28   (dùng để chọn ngưỡng trên validation)
      validation: 2018-03-01 -> 2018-05-31
    test       : 2018-06-01 -> 2018-08-31   (từ 09/2018 dataset gần như không còn đơn)

Mô hình chính: HistGradientBoostingClassifier (xử lý được NULL + biến phân loại, bắt quan hệ phi tuyến).
Baseline     : LogisticRegression (one-hot + chuẩn hoá) để chứng minh mô hình chính tốt hơn.
Nhãn lệch (6-14% dương) -> không đánh giá bằng accuracy; dùng ROC-AUC, PR-AUC, precision/recall/F1 tại
ngưỡng ra quyết định, và precision/recall khi chỉ xử lý 10% đơn rủi ro nhất.

Ngưỡng ra quyết định (High) theo chi phí: cảnh báo khi p * C_FN > (1 - p) * C_FP <=> p >= C_FP / (C_FP + C_FN).
Ngưỡng max-F1 chọn trên validation vẫn được tính và lưu để so sánh: với late_delivery nó lệch rất xa vì
tỷ lệ trễ đổi mạnh theo thời gian (03/2018: 19%, 06-08/2018: 3.6%) — xem chương Đánh giá.

Output: dw.model_metrics, dw.model_feature_importance, dw.order_risk_score, file dss/models/<name>.pkl
(what-if và dự đoán đơn giả định dùng file .pkl này: xem risk/predict.py)
"""
import os
from datetime import date

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_fscore_support, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from common import bulk_execute
# to_matrix, risk_level nằm ở features.py (dùng chung với predict.py); import vào đây để notebook gọi T.to_matrix
from risk.features import CATEGORICAL, SPECS, ModelSpec, labeled_mask, load_orders, open_mask, risk_level, to_matrix
from risk.predict import MODELS_DIR, build_profile, model_path
TRAIN_START = date(2017, 1, 1)
VALID_START = date(2018, 3, 1)
TEST_START = date(2018, 6, 1)
TEST_END = date(2018, 9, 1)          # exclusive
PRIMARY_VERSION = "hgb_v1"
BASELINE_VERSION = "logreg_v1"
RANDOM_STATE = 42
TOP_SHARE = 0.10
IMPORTANCE_SAMPLE = 6000
# (C_FP, C_FN): chi phí 1 lần can thiệp không cần thiết / chi phí bỏ sót 1 đơn có sự cố (đơn vị tương đối)
DECISION_COSTS = {
    # can thiệp = liên hệ người bán/hãng vận chuyển + báo trước khách; bỏ sót = đơn trễ không được báo trước
    "late_delivery": (1.0, 1.0),
    # can thiệp = voucher ~R$10; bỏ sót = khách đánh giá thấp và không quay lại, ước tính ~R$40
    "low_review": (10.0, 40.0),
}


def decision_threshold(name: str) -> float:
    cost_fp, cost_fn = DECISION_COSTS[name]
    return cost_fp / (cost_fp + cost_fn)


def new_primary() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(categorical_features="from_dtype", max_iter=300, learning_rate=0.05,
                                          random_state=RANDOM_STATE)


def new_baseline(spec: ModelSpec):
    pre = ColumnTransformer([
        ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), spec.numeric),
        ("cat", make_pipeline(SimpleImputer(strategy="constant", fill_value="missing"),
                              OneHotEncoder(handle_unknown="ignore", min_frequency=20)), CATEGORICAL),
    ])
    return make_pipeline(pre, LogisticRegression(max_iter=2000))


def best_f1_threshold(y: np.ndarray, p: np.ndarray) -> float:
    candidates = np.arange(0.05, 0.95, 0.01)
    f1 = [precision_recall_fscore_support(y, p >= t, average="binary", zero_division=0)[2] for t in candidates]
    return float(candidates[int(np.argmax(f1))])


def evaluate(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    precision, recall, f1, _ = precision_recall_fscore_support(y, p >= threshold, average="binary", zero_division=0)
    top = np.argsort(-p)[: max(1, int(len(p) * TOP_SHARE))]
    return {
        "positive_rate": float(y.mean()),
        "roc_auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "threshold": threshold,
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "precision_top10": float(y[top].mean()),
        "recall_top10": float(y[top].sum() / max(1, y.sum())),
    }


def train_one(spec: ModelSpec, orders: pd.DataFrame) -> dict:
    labeled = orders[labeled_mask(orders, spec)].copy()
    labeled["y"] = labeled[spec.target].astype(bool).astype(int)
    day = labeled["purchase_ts"].dt.date
    train = labeled[(day >= TRAIN_START) & (day < TEST_START)]
    fit, valid = train[train["purchase_ts"].dt.date < VALID_START], train[train["purchase_ts"].dt.date >= VALID_START]
    test = labeled[(day >= TEST_START) & (day < TEST_END)]
    categories = {c: sorted(train[c].dropna().unique()) for c in CATEGORICAL}
    X = lambda df: to_matrix(df, spec, categories)  # noqa: E731

    threshold = decision_threshold(spec.name)
    metric_rows = []
    for version, factory in [(PRIMARY_VERSION, new_primary), (BASELINE_VERSION, lambda: new_baseline(spec))]:
        # ngưỡng max-F1 trên validation (fit -> valid) chỉ để so sánh; mô hình cuối train trên toàn bộ train
        p_valid = factory().fit(X(fit), fit["y"]).predict_proba(X(valid))[:, 1]
        threshold_valid = best_f1_threshold(valid["y"].to_numpy(), p_valid)
        model = factory().fit(X(train), train["y"])
        p_test = model.predict_proba(X(test))[:, 1]
        metrics = evaluate(test["y"].to_numpy(), p_test, threshold)
        metrics["threshold_valid"] = threshold_valid
        metrics["f1_valid_threshold"] = evaluate(test["y"].to_numpy(), p_test, threshold_valid)["f1"]
        metric_rows.append({"version": version, "model": model, "p_test": p_test, **metrics})

    primary = metric_rows[0]
    model = primary["model"]

    sample = test.sample(min(IMPORTANCE_SAMPLE, len(test)), random_state=RANDOM_STATE)
    imp = permutation_importance(model, X(sample), sample["y"], scoring="average_precision", n_repeats=5,
                                 random_state=RANDOM_STATE)
    importance = sorted(zip(spec.features, imp.importances_mean), key=lambda x: -x[1])

    # Chấm điểm: đơn test (biết kết quả thật để đối chiếu) + đơn đang mở
    opened = orders[open_mask(orders, spec)]
    scored = pd.concat([test.assign(scope="holdout", p=primary["p_test"]),
                        opened.assign(scope="open", y=np.nan, p=model.predict_proba(X(opened))[:, 1] if len(opened) else [])])
    scored["level"] = risk_level(scored["p"].to_numpy(), primary["threshold"])

    # Giá trị điển hình (theo tuyến / ngành) từ đơn đã giao giai đoạn train -> công cụ dự đoán đơn giả định
    purchase_day = orders["purchase_ts"].dt.date
    history = orders[orders["delivered_ts"].notna() & (purchase_day >= TRAIN_START) & (purchase_day < TEST_START)]

    os.makedirs(MODELS_DIR, exist_ok=True)
    joblib.dump({"model": model, "spec": spec, "categories": categories, "threshold": primary["threshold"],
                 "version": PRIMARY_VERSION, "profile": build_profile(history)}, model_path(spec.name))

    persist(spec, train, test, metric_rows, importance, scored)
    return {
        "model": spec.name,
        "n_train": len(train),
        "n_test": len(test),
        "n_open_scored": len(opened),
        "metrics": {r["version"]: {k: round(v, 4) for k, v in r.items() if isinstance(v, float)} for r in metric_rows},
        "top_features": [{"feature": f, "importance": round(float(v), 4)} for f, v in importance[:8]],
    }


def persist(spec, train, test, metric_rows, importance, scored):
    metric_values = [
        (spec.name, r["version"], r["version"] == PRIMARY_VERSION, TRAIN_START, TEST_START, TEST_START, TEST_END,
         len(train), len(test), r["positive_rate"], r["roc_auc"], r["pr_auc"], r["threshold"], r["precision"],
         r["recall"], r["f1"], r["threshold_valid"], r["f1_valid_threshold"], r["precision_top10"], r["recall_top10"])
        for r in metric_rows
    ]
    importance_values = [(spec.name, PRIMARY_VERSION, f, float(v)) for f, v in importance]
    score_values = [
        (r.order_id, spec.name, PRIMARY_VERSION, float(r.p), r.level, r.scope, None if pd.isna(r.y) else bool(r.y))
        for r in scored.itertuples()
    ]
    bulk_execute([
        ("DELETE FROM dw.model_metrics WHERE model_name = %s", (spec.name,)),
        ("""INSERT INTO dw.model_metrics (model_name, model_version, is_primary, train_start, train_end, test_start,
                test_end, n_train, n_test, positive_rate, roc_auc, pr_auc, threshold, precision, recall, f1,
                threshold_valid, f1_valid_threshold, precision_top10, recall_top10) VALUES %s""", metric_values),
        ("DELETE FROM dw.model_feature_importance WHERE model_name = %s", (spec.name,)),
        ("INSERT INTO dw.model_feature_importance (model_name, model_version, feature, importance) VALUES %s",
         importance_values),
        ("DELETE FROM dw.order_risk_score WHERE model_name = %s", (spec.name,)),
        ("""INSERT INTO dw.order_risk_score (order_id, model_name, model_version, risk_score, risk_level, scope,
                actual_label) VALUES %s""", score_values),
    ])


def run_all() -> list[dict]:
    orders = load_orders()
    return [train_one(spec, orders) for spec in SPECS.values()]


if __name__ == "__main__":
    import json

    print(json.dumps(run_all(), indent=2, default=str))
