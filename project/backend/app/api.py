from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from . import schemas, crud, database

router = APIRouter()

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/orders/", response_model=schemas.OrderOut)
def create_order(order: schemas.OrderCreate, db: Session = Depends(get_db)):
    return crud.create_order(db, telegram_id=order.telegram_id, payload=order.payload)
