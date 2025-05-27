from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import traceback
from .api import router
from .database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.include_router(router)

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )