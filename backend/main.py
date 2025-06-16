import os
import datetime
from typing import List, Optional, Dict, Generator
from sqlalchemy import BigInteger
from fastapi import FastAPI, HTTPException, Depends, status
from pydantic import BaseModel, Field
import json
from fastapi.encoders import jsonable_encoder

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    JSON,
    DateTime,
    ForeignKey,
    select,
)
from sqlalchemy.orm import (
    declarative_base,
    relationship,
    sessionmaker,
    Session,
)

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

Base = declarative_base()

# Константы статусов
STATUS_NEW = "\U0001F195 Новая заявка"  # 🆕
STATUS_PENDING_APPROVAL = "\u23F3 На проверке у диспетчера"  # ⏳
STATUS_APPROVED_BY_DISPATCHER = "\u2705 Одобрена, поиск водителя"  # ✅
STATUS_DECLINED_BY_DISPATCHER = "\u274C Отклонена диспетчером"  # ❌
STATUS_ASSIGNED_TO_DRIVER = "\U0001F69A Водитель найден"  # 🚚
STATUS_PICKED = "\U0001F504 Заказ забран"  # 🔄
STATUS_DELIVERING = "\U0001F69A В пути"  # 🚚 
STATUS_COMPLETED = "\U0001F3C1 Выполнена"  # 🏁


# SQLAlchemy модели

class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    role = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)

    orders = relationship(
        "OrderDB", back_populates="customer", foreign_keys="OrderDB.customer_id"
    )
    deliveries = relationship(
        "OrderDB", back_populates="driver", foreign_keys="OrderDB.driver_id"
    )


class OrderDB(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)

    customer_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    customer_telegram_id = Column(BigInteger, index=True, nullable=False)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    status = Column(String, default=STATUS_NEW, index=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    customer = relationship("UserDB", foreign_keys=[customer_id], back_populates="orders")
    driver = relationship("UserDB", foreign_keys=[driver_id], back_populates="deliveries")


# Pydantic схемы (response models)
class User(BaseModel):
    id: int
    telegram_id: int
    role: str
    name: str
    phone: str

    class Config:
        orm_mode = True


class Order(BaseModel):
    id: int
    customer_id: int
    customer_telegram_id: int
    driver_id: Optional[int]
    status: str
    payload: Dict
    created_at: datetime.datetime

    class Config:
        orm_mode = True
# --- Схема payload для создания заявки --- #

class ContactInfo(BaseModel):
    name: str
    phone: str
    address: Optional[str] = None


class CargoInfo(BaseModel):
    name: str
    weight: int
    count: int
    size: str


class PayloadSchema(BaseModel):
    cargo: CargoInfo
    documents: str
    get_from: ContactInfo
    pickup_contact: ContactInfo
    docs_contact: Optional[ContactInfo] = None
    deliver_to: ContactInfo
    address_from: str
    need_payment: bool
    lead_time: datetime.datetime
    extra_info: Optional[str] = None

# FastAPI и зависимость для сессии
app = FastAPI(title="Logistics Bot Backend (PostgreSQL)")


@app.on_event("startup")
def _startup() -> None:
    """Создаём таблицы (если они ещё не созданы)"""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/users/", response_model=List[User])
def get_all_users(db: Session = Depends(get_db)):
    return db.execute(select(UserDB)).scalars().all()

# Получить пользователя по ID
@app.get("/users/{user_id}", response_model=User)
def get_user_by_id(user_id: int, db: Session = Depends(get_db)):
    user = db.get(UserDB, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# Получить пользователя по Telegram ID
@app.get("/users/by_telegram/{telegram_id}", response_model=Optional[User])
def get_user_by_telegram_id(telegram_id: int, db: Session = Depends(get_db)):
    return (
        db.execute(select(UserDB).where(UserDB.telegram_id == telegram_id))
        .scalars()
        .first()
    )

# Получить всех пользователей по роли
@app.get("/users/by_role/{role}", response_model=List[User])
def get_users_by_role(role: str, db: Session = Depends(get_db)):
    return db.execute(select(UserDB).where(UserDB.role == role)).scalars().all()
# Endpoints для заявок
@app.post("/orders/", response_model=Order, status_code=status.HTTP_201_CREATED)
def create_order(customer_telegram_id: int, payload: PayloadSchema, db: Session = Depends(get_db)):
    customer = db.execute(select(UserDB).where(UserDB.telegram_id == customer_telegram_id)).scalars().first()
    if not customer or customer.role != "customer":
        raise HTTPException(status_code=403, detail="Только заказчик может создавать заявки")

    # 🔧 Гарантируем сериализацию вложенных моделей и datetime
    encoded_payload = jsonable_encoder(payload.dict())

    order_db = OrderDB(
        customer_id=customer.id,
        customer_telegram_id=customer.telegram_id,
        payload=encoded_payload,
        status=STATUS_PENDING_APPROVAL,
    )
    db.add(order_db)
    db.commit()
    db.refresh(order_db)
    return order_db

@app.get("/orders/customer/{telegram_id}", response_model=List[Order])
def get_customer_orders(telegram_id: int, db: Session = Depends(get_db)):
    customer = (
        db.execute(select(UserDB).where(UserDB.telegram_id == telegram_id))
        .scalars()
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return (
        db.execute(select(OrderDB).where(OrderDB.customer_id == customer.id))
        .scalars()
        .all()
    )


@app.get("/orders/pending_approval", response_model=List[Order])
def get_pending_orders_for_dispatcher(db: Session = Depends(get_db)):
    return (
        db.execute(select(OrderDB).where(OrderDB.status == STATUS_PENDING_APPROVAL))
        .scalars()
        .all()
    )


@app.get("/orders/driver/available", response_model=List[Order])
def get_available_orders(db: Session = Depends(get_db)):
    return (
        db.execute(select(OrderDB).where(OrderDB.status == STATUS_APPROVED_BY_DISPATCHER))
        .scalars()
        .all()
    )


@app.post("/orders/{order_id}/assign/{driver_telegram_id}", response_model=Order)
def assign_driver(order_id: int, driver_telegram_id: int, db: Session = Depends(get_db)):
    order: OrderDB | None = db.get(OrderDB, order_id)
    driver: UserDB | None = (
        db.execute(select(UserDB).where(UserDB.telegram_id == driver_telegram_id))
        .scalars()
        .first()
    )
    if not order or not driver or driver.role != "driver":
        raise HTTPException(status_code=404, detail="Заявка или водитель не найдены")

    order.driver_id = driver.id
    order.status = STATUS_ASSIGNED_TO_DRIVER
    db.commit()
    db.refresh(order)
    return order


@app.get("/orders/driver/{telegram_id}/active", response_model=List[Order])
def get_driver_active_orders(telegram_id: int, db: Session = Depends(get_db)):
    driver = (
        db.execute(select(UserDB).where(UserDB.telegram_id == telegram_id))
        .scalars()
        .first()
    )
    if not driver or driver.role != "driver":
        raise HTTPException(status_code=403, detail="Пользователь не водитель")

    active_statuses = [STATUS_ASSIGNED_TO_DRIVER, STATUS_PICKED, STATUS_DELIVERING]
    return (
        db.execute(
            select(OrderDB).where(
                OrderDB.driver_id == driver.id, OrderDB.status.in_(active_statuses)
            )
        )
        .scalars()
        .all()
    )


@app.get("/orders/driver/{telegram_id}/history", response_model=List[Order])
def get_driver_order_history(telegram_id: int, db: Session = Depends(get_db)):
    driver = (
        db.execute(select(UserDB).where(UserDB.telegram_id == telegram_id))
        .scalars()
        .first()
    )
    if not driver or driver.role != "driver":
        raise HTTPException(status_code=403, detail="Пользователь не водитель")

    return (
        db.execute(
            select(OrderDB).where(
                OrderDB.driver_id == driver.id, OrderDB.status == STATUS_COMPLETED
            )
        )
        .scalars()
        .all()
    )


@app.post("/orders/{order_id}/status", response_model=Order)
def update_order_status(order_id: int, status: str, db: Session = Depends(get_db)):
    order: OrderDB | None = db.get(OrderDB, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    order.status = status
    db.commit()
    db.refresh(order)
    return order


@app.get("/orders/{order_id}/customer_phone")
def get_customer_phone(order_id: int, db: Session = Depends(get_db)):
    order: OrderDB | None = db.get(OrderDB, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    customer: UserDB | None = db.get(UserDB, order.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Заказчик не найден")

    return {"phone": customer.phone}


@app.get("/orders/{order_id}", response_model=Order)
def get_order_by_id(order_id: int, db: Session = Depends(get_db)):
    order = db.get(OrderDB, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# Утилита для быстрого наполнения тестовыми данными

TEST_USERS = [
    dict(id=1, telegram_id=7198487225, role="customer", name="Тестовый Заказчик", phone="+79001234567"),
    dict(id=2, telegram_id=7809176251, role="driver", name="Тестовый Водитель", phone="+79007654321"),
    dict(id=3, telegram_id=661832899, role="dispatcher", name="Главный Диспетчер", phone="+79001112233"),
]


@app.on_event("startup")
def _seed_data() -> None:
    """Добавляем тестовых пользователей, если БД пуста (idempotent)."""
    with SessionLocal() as db:
        if db.execute(select(UserDB.id)).first() is None:
            db.add_all(UserDB(**u) for u in TEST_USERS)
            db.commit()
