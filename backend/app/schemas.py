from pydantic import BaseModel
from typing import Optional

class OrderCreate(BaseModel):
    telegram_id: int
    payload: dict

class OrderOut(BaseModel):
    id: int
    status: str

    class Config:
        orm_mode = True
