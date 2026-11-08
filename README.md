# E-commerce DW & DSS — Giao hàng đúng hạn & Mức độ hài lòng của khách

Đồ án môn **CO4031 - Kho dữ liệu & Hệ hỗ trợ Quyết định** (HK261, ĐH Bách Khoa TPHCM).

DSS gồm 2 mô hình trên dữ liệu Olist thật: **rủi ro giao trễ hạn** (chấm điểm lúc đơn được bàn giao vận
chuyển) và **rủi ro nhận review thấp ≤ 2 sao** (chấm điểm lúc đơn vừa giao tới khách), kèm what-if và campaign
chăm sóc khách. Xem thiết kế đầy đủ tại [`docs/dw-design.md`](docs/dw-design.md) và [`docs/dss-design.md`](docs/dss-design.md).

## Cấu trúc thư mục

| Thư mục | Nội dung |
|---|---|
| `docs/` | Yêu cầu nghiệp vụ, thiết kế DW, thiết kế DSS |
| `data/` | Dataset gốc (`raw/`), danh sách campaign chăm sóc (`campaigns/care_campaigns.csv`) |
| `infra/` | Docker Compose cho toàn bộ hạ tầng (Postgres, Airflow, services) |
| `etl/` | Airflow DAGs + extract/transform/load scripts |
| `warehouse/` | DDL cho staging + DW (star schema), OLAP views |
| `dss/` | Model service (FastAPI): mô hình rủi ro giao trễ + rủi ro review thấp (`dss/risk/`); notebook trình bày quá trình train: `dss/notebooks/risk_models.ipynb` |
| `backend-api/` | BFF (FastAPI) — API cho frontend gọi vào |
| `frontend/` | 1 app React (Vite), giao diện tiếng Anh, thanh tab trên cùng chọn 4 màn hình: Manager Dashboard, Ops Console, Campaign, Risk Predictor |
| `scripts/` | `seed_db.sh`: nạp dữ liệu lần đầu bằng script trên máy |
| `report/` | Báo cáo / slide nộp bài |

## Chạy dự án (development)

### 1. Chuẩn bị

- Tải [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce),
  giải nén **8 file** `olist_*.csv` (kể cả `olist_geolocation_dataset.csv` — dùng để tính khoảng cách giao hàng)
  + `product_category_name_translation.csv` vào `data/raw/` (không cần giữ file `archive.zip`).
- Tạo file env cho Docker và điền `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`:

```bash
cp infra/.env.example infra/.env.local
```


### 2. Khởi động hạ tầng (Postgres, Airflow, DSS, backend-api)

```bash
cd infra
docker compose --env-file .env.local up -d
docker compose --env-file .env.local ps   # db (healthy), airflow-init Exited (0), còn lại Up
```

Postgres trong Docker được map ra **`localhost:5433`** (bên trong mạng Docker, các service vẫn dùng `db:5432`).
Nếu máy đang chạy Postgres local ở cổng 5433 (vd `brew services list` thấy `postgresql@16 started`),
hãy tắt nó (`brew services stop postgresql@16`), nếu không thì script chạy trên máy sẽ kết nối nhầm vào DB local.

### 3. Nạp dữ liệu lần đầu (bắt buộc, DB mới tạo sẽ trống)

**Cách 1 — bằng Airflow (khuyến nghị, không cần cài Python trên máy):**

```bash
S=infra-airflow-scheduler-1
for d in staging_load dw_load dss_batch; do docker exec $S airflow dags unpause $d; done
docker exec $S airflow dags trigger staging_load
```

`staging_load` → tự trigger `dw_load` → tự trigger `dss_batch` (train 2 mô hình rủi ro).
Tổng thời gian khoảng 1 phút, theo dõi tại http://localhost:8080 (`airflow`/`airflow`).

**Cách 2 — bằng script chạy trên máy:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r etl/requirements.txt
set -a; . infra/.env.local; set +a
export DW_DB_PORT=5433 DW_DB_USER=$POSTGRES_USER DW_DB_PASSWORD=$POSTGRES_PASSWORD DW_DB_NAME=$POSTGRES_DB
bash scripts/seed_db.sh     # DDL -> staging -> DW -> OLAP views -> pytest DQ (~20s)
curl -X POST localhost:8001/risk/retrain   # train trong container dss (~50s) — không train trên máy, xem mục Lỗi
```

### 4. Frontend (1 app, 1 cổng duy nhất)

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Manager Dashboard ở `http://localhost:5173/#/manager`, Ops Console ở `#/ops`, Campaign ở `#/campaigns`, công cụ dự đoán
rủi ro ở `#/predict` (chuyển qua thanh tab trên cùng). Giao diện hiển thị bằng tiếng Anh; tiền và số theo định dạng
tiếng Anh (`R$13,494,401`, trục rút gọn `R$1.2M`, số đếm `98,199`); mã bang / ngành vẫn gửi nguyên giá trị gốc khi gọi API.
Cổng cố định 5173 (`strictPort`): nếu cổng đang bị chiếm, Vite báo lỗi chứ không tự nhảy sang cổng khác.

Frontend gọi backend ở `http://localhost:8000` (đổi bằng biến `VITE_API_BASE_URL`).

Cấu trúc `frontend/src/`:

| File | Vai trò |
|---|---|
| `App.tsx` | Thanh tab trên cùng + danh sách màn hình `PAGES` (route dạng hash), footer |
| `api.ts` | Toàn bộ type và lời gọi tới `backend-api` (dùng chung 1 hàm `getJSON`, timeout 20 giây) — nơi duy nhất frontend gọi backend |
| `styles.css` | Toàn bộ CSS giao diện |
| `utils/display.ts` | Định dạng tiền BRL, %, số đếm, tháng/ngày theo kiểu tiếng Anh (`en-US`) |
| `utils/format.ts` | Tên đủ 27 bang Brazil, tên ngành (`cool_stuff` → `Cool Stuff`), dịch nhãn tiếng Việt do backend trả về (mức trễ, nguồn giá trị, vùng) sang tiếng Anh, tên đặc trưng mô hình |
| `components/Dashboard.tsx` | Khối giao diện dùng chung: tiêu đề trang/mục/panel, chú giải, thông báo, trạng thái tải/lỗi/trống, ô chọn, bảng sắp xếp được, nhãn mức rủi ro |
| `components/ListTools.tsx` | Ô chọn số dòng `[n] / tổng` và nút tải `.csv` / `.xlsx` (tải các dòng đang hiển thị) |
| `pages/ManagerDashboard.tsx` | KPI, doanh thu theo tháng / theo bang (lọc năm/ngành/vùng, nút Export report ra CSV), tỷ lệ giao trễ & review thấp theo tháng, review thấp theo mức trễ, 10 tuyến giao trễ nhiều nhất, 10 sản phẩm doanh thu thấp nhất |
| `pages/OpsConsole.tsx` | Đơn đang vận chuyển có rủi ro giao trễ + nút Simulate action (thử dời ngày hẹn thêm N ngày, gọi API what-if), đơn rủi ro review thấp, gắn khách rủi ro vào campaign chăm sóc. Chất lượng mô hình không hiển thị trên giao diện (xem báo cáo, Mục 6.3 Kết quả hai mô hình DSS) |
| `pages/CampaignManager.tsx` | Danh sách campaign; nút Create campaign mở form tạo; sửa / xoá trong bảng; nút Details hiện mô tả + danh sách khách đã gắn |
| `pages/RiskPredictor.tsx` | Công cụ dự đoán cho 1 đơn tự nhập (khoảng cách tự điền khi chọn 2 bang, sửa tay được): % rủi ro giao trễ và % rủi ro review thấp, kèm ngưỡng, lịch sử tuyến và giá trị mô hình đã dùng; đổi đầu vào thì xoá kết quả cũ |

`frontend/tests/frontend.spec.js` là bộ test Playwright của giao diện (mô phỏng API trong trình duyệt, không đụng
backend thật). Chạy bằng `npx playwright install chromium && npm test` — test tự bật Vite ở cổng **5180** trong lúc chạy.

Thêm màn hình mới: tạo file trong `pages/`, thêm 1 dòng vào `PAGES` trong `App.tsx`,
thêm endpoint vào `api.ts`. Không cần mở thêm cổng hay sửa CORS.

### Notebook trình bày mô hình (tuỳ chọn)

Code mô hình: `dss/risk/features.py` (đọc dữ liệu, feature, nhãn), `train.py` (train + đánh giá + chấm điểm, ghi kết quả
vào DW), `predict.py` (dùng model đã train: what-if và dự đoán đơn giả định).

`dss/notebooks/risk_models.ipynb` đi lại từng bước của `dss/risk/train.py` (đọc dữ liệu, EDA, train HGB vs LogReg,
ngưỡng theo chi phí, PR curve, feature importance, what-if) bằng chính các hàm của project. Notebook đã lưu sẵn output
nên mở ra xem/present được ngay. Muốn chạy lại: cần DB (bước 2–3) và môi trường có `jupyter`, `pandas`, `scikit-learn`
(nên cùng bản với container, 1.9.1), `sqlalchemy`, `psycopg2-binary`, `matplotlib`. Notebook chỉ **đọc** kho, không ghi
đè model hay bảng kết quả.

### Cấu hình hạn bàn giao mặc định của sàn

Công cụ dự đoán (`#/predict`) dùng "hạn bàn giao" = số ngày sau khi mua mà người bán phải giao hàng cho đơn vị vận
chuyển. Mặc định lấy trung vị dữ liệu thật (~6 ngày). Muốn đặt theo quy định của sàn, thêm vào `infra/.env.local`:

```
DEFAULT_SHIPPING_LIMIT_DAYS=3
```

rồi `docker compose --env-file .env.local up -d dss`. Trên form vẫn sửa được số ngày cho từng lần dự đoán.

### Địa chỉ các dịch vụ

| Dịch vụ | URL |
|---|---|
| Airflow UI | http://localhost:8080 (`airflow`/`airflow`) |
| DSS service | http://localhost:8001/docs |
| Backend API | http://localhost:8000/docs |
| Frontend | http://localhost:5173 (`#/manager`, `#/ops`, `#/campaigns`, `#/predict`) |
| Postgres | `localhost:5433` (user/password/db theo `infra/.env.local`) |

### Lỗi

- **Frontend báo `API error 500 … — UndefinedTable: relation "dw.mv_..." does not exist`**: DB còn trống
  (thường gặp sau `down -v`), hãy chạy lại bước 3. Backend luôn trả lỗi 500 dạng JSON `{"detail": ...}` kèm header CORS;
  traceback đầy đủ xem bằng `docker logs infra-backend-api-1`.
- **Frontend báo `TypeError: Failed to fetch`**: backend không chạy, hoặc frontend mở ở cổng khác 5173
  (backend chỉ cho phép origin `http://localhost:5173`, xem `allow_origins` trong `backend-api/app/main.py`).
  Kiểm tra bằng `curl localhost:8000/health` (xem bước 2). Sửa code backend thì phải build lại container:
  `docker compose --env-file .env.local up -d --build backend-api`.
- **`npm run dev` báo `Port 5173 is already in use`**: còn 1 tiến trình Vite cũ đang chạy, hãy tắt nó
  (`lsof -i :5173` để tìm PID) rồi chạy lại.
- **Đổi DDL sau khi DB đã tạo**: `docker compose --env-file .env.local down -v` (xoá cả dữ liệu),
  sau đó làm lại bước 2 và bước 3 (DDL dùng `IF NOT EXISTS` nên không tự sửa bảng cũ).
  Không muốn mất metadata Airflow thì chỉ xoá 2 schema dữ liệu:
  `DROP SCHEMA dw CASCADE; DROP SCHEMA staging CASCADE;` rồi làm lại bước 3. DDL chỉ gồm lệnh tạo bảng, không chứa
  bước nâng cấp từ các phiên bản schema cũ — DB tạo từ phiên bản cũ cần làm theo cách này (mất danh sách khách
  đã gán campaign).
- **Ops Console / Risk Predictor báo `409 Model is not trained yet`**: chưa có file `dss/models/*.pkl`, chạy `POST /risk/retrain`
  (bước 3). File model lưu kèm bảng giá trị điển hình (profile) cho công cụ dự đoán.
- **Frontend báo `API error 404 on /risk/...`**: container `backend-api`/`dss` đang chạy image cũ.
  `up -d` không tự build lại khi code đổi, phải dùng `docker compose --env-file .env.local up -d --build dss backend-api`.
  Kiểm tra: `curl localhost:8000/openapi.json` phải có các path `/risk/...`.
- **What-if báo 500, log `dss` có `ModuleNotFoundError: No module named '_loss'`**: file `dss/models/*.pkl` được train
  bằng phiên bản scikit-learn khác với trong container (ví dụ train trên máy). Train lại trong container:
  `curl -X POST localhost:8001/risk/retrain` (~50 giây).
- **Sửa code `dss/`**: build lại `docker compose --env-file .env.local up -d --build dss`.

## Trạng thái

Xem `check.md` để biết tiến độ chi tiết.

## Tuỳ chỉnh danh sách campaign

Danh sách ban đầu nằm ở `data/campaigns/care_campaigns.csv` (cột `campaign_code`, `campaign_name`, `description`).

- **Thêm campaign bằng file:** thêm 1 dòng với mã mới (đừng dùng dạng `CMP0001`, vì UI tự sinh mã dạng này), rồi chạy lại ETL
  (`scripts/seed_db.sh` hoặc DAG `staging_load`).
- **Thay đổi hằng ngày:** dùng màn hình Campaign. Thay đổi lưu trong DB (`dw.care_campaign`), không ghi ngược vào file.
- ETL chỉ thêm **mã mới**: sửa tên của mã đã có trong file sẽ không có tác dụng (hãy sửa trên UI), và campaign đã xoá trên UI
  không tự xuất hiện lại.
