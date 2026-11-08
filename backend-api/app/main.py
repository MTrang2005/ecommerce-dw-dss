import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.modules import campaigns, dashboard, risk

logger = logging.getLogger("backend-api")

app = FastAPI(title="E-commerce BFF API", version="0.1.0")


def _root_cause(exc: BaseException) -> BaseException:
    # pandas DatabaseError -> SQLAlchemy error -> psycopg2 error (thông báo gọn nhất, vd "relation ... does not exist")
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return getattr(exc, "orig", None) or exc


# Bắt mọi exception chưa xử lý -> JSON 500. Phải đăng ký TRƯỚC CORSMiddleware để CORS bọc ngoài,
# nếu không response 500 sẽ thiếu header CORS và trình duyệt chỉ báo "Failed to fetch".
@app.middleware("http")
async def catch_unhandled_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        cause = _root_cause(exc)
        message = (str(cause).strip().splitlines() or [""])[0]
        return JSONResponse(status_code=500, content={"detail": f"{type(cause).__name__}: {message}"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # frontend (1 app, cổng cố định 5173)
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router)
app.include_router(risk.router)
app.include_router(campaigns.router)


@app.get("/health")
def health():
    return {"status": "ok"}
