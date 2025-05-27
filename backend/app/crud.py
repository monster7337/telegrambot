from sqlalchemy.orm import Session
from . import models

def create_order(db: Session, telegram_id: int, payload: dict):
    user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
    if not user:
        user = models.User(telegram_id=telegram_id, name=str(telegram_id), role="customer")
        db.add(user)
        db.commit()
        db.refresh(user)

    order = models.Order(user_id=user.id, payload=str(payload))
    db.add(order)
    db.commit()
    db.refresh(order)
    return order
