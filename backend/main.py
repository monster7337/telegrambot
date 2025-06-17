
import os
import datetime
from enum import Enum
from typing import List, Optional, Generator

from fastapi import FastAPI, HTTPException, Depends, status
from pydantic import BaseModel
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    BigInteger,
    String,
    DateTime,
    Boolean,
    Text,
    JSON,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# --- Database setup ------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
Base = declarative_base()

# --- Enumerations --------------------------------------------------------

class UserRole(str, Enum):
    customer = "customer"
    dispatcher = "dispatcher"
    driver = "driver"


class OrderStatus(str, Enum):
    created = "created"
    planning = "planning"
    driver_assigned = "driver_assigned"
    in_progress = "in_progress"
    completed = "completed"
    archived = "archived"

# --- SQLAlchemy models ---------------------------------------------------

class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    role = Column(String, nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)

    # One‑to‑many: user -> orders created
    orders = relationship(
        "OrderDB", back_populates="customer", foreign_keys="OrderDB.customer_id"
    )
    # One‑to‑many: user -> orders delivered
    deliveries = relationship(
        "OrderDB", back_populates="driver", foreign_keys="OrderDB.driver_id"
    )


class ItemDB(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    weight = Column(Integer, nullable=False)
    count = Column(Integer, nullable=False)
    size = Column(String, nullable=False)
    documents = Column(Text)
    get_from = Column(JSON, nullable=False)      # {"name":"...", "phone": "...", "address": "..."}
    deliver_to = Column(JSON, nullable=False)    # same structure
    need_payment = Column(Boolean, default=False)
    lead_time = Column(DateTime, nullable=False)
    comments = Column(Text)

    # One‑to‑one back‑ref from OrderDB
    order = relationship("OrderDB", back_populates="item", uselist=False)


class OrderDB(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    item_id = Column(Integer, ForeignKey("items.id"), unique=True, nullable=False)

    status = Column(String, default=OrderStatus.created.value, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    customer = relationship("UserDB", foreign_keys=[customer_id], back_populates="orders")
    driver = relationship("UserDB", foreign_keys=[driver_id], back_populates="deliveries")
    item = relationship("ItemDB", back_populates="order", uselist=False)

# --- Pydantic schemas ----------------------------------------------------

class ContactInfo(BaseModel):
    name: str
    phone: str
    address: Optional[str] = None

class Item(BaseModel):
    id: int
    name: str
    weight: int
    count: int
    size: str
    documents: Optional[str] = None
    get_from: ContactInfo
    deliver_to: ContactInfo
    need_payment: bool
    lead_time: datetime.datetime
    comments: Optional[str] = None

    class Config:
        orm_mode = True


class ItemCreate(Item):
    id: Optional[int] = None

    class Config:
        orm_mode = True

class User(BaseModel):
    id: int
    telegram_id: int
    role: UserRole
    name: str
    phone: str

    class Config:
        orm_mode = True
class Order(BaseModel):
    id: int
    customer_id: int
    driver_id: Optional[int] = None
    status: OrderStatus
    created_at: datetime.datetime
    item: Item
    customer: User

    class Config:
        orm_mode = True




# --- FastAPI initialisation ---------------------------------------------

app = FastAPI(title="Logistics Bot Backend (new schema)")

@app.on_event("startup")
def _startup() -> None:
    """Create tables automatically on first start (dev only)."""
    Base.metadata.create_all(bind=engine)

# --- Dependency ---------------------------------------------------------

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- User endpoints -----------------------------------------------------

@app.get("/users/", response_model=List[User])
def get_all_users(db: Session = Depends(get_db)):
    return db.query(UserDB).all()


@app.get("/users/{user_id}", response_model=User)
def get_user_by_id(user_id: int, db: Session = Depends(get_db)):
    user = db.get(UserDB, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/users/by_telegram/{telegram_id}", response_model=User)
def get_user_by_telegram(telegram_id: int, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.telegram_id == telegram_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# --- Order endpoints ----------------------------------------------------

class ItemPayload(BaseModel):
    """Incoming payload for order creation (without id)."""
    name: str
    weight: int
    count: int
    size: str
    documents: Optional[str] = None
    get_from: ContactInfo
    deliver_to: ContactInfo
    need_payment: bool
    lead_time: datetime.datetime
    comments: Optional[str] = None


@app.post("/orders/", response_model=Order, status_code=status.HTTP_201_CREATED)
def create_order(
    customer_telegram_id: int,
    item: ItemPayload,
    db: Session = Depends(get_db),
):
    """Create new order together with its Item."""
    customer = (
        db.query(UserDB).filter(UserDB.telegram_id == customer_telegram_id).first()
    )
    if not customer or customer.role != UserRole.customer.value:
        raise HTTPException(
            status_code=403, detail="Only customers can create orders"
        )

    item_db = ItemDB(**item.dict())
    db.add(item_db)
    db.flush()  # to get generated item id

    order_db = OrderDB(
        customer_id=customer.id,
        item_id=item_db.id,
        status=OrderStatus.created.value,
    )
    db.add(order_db)
    db.commit()
    db.refresh(order_db)
    return order_db


from fastapi import Query

@app.get("/orders/", response_model=List[Order])
def get_orders(status: Optional[OrderStatus] = Query(None), db: Session = Depends(get_db)):
    query = db.query(OrderDB)
    if status:
        query = query.filter(OrderDB.status == status.value)
    return query.all()



@app.post("/orders/{order_id}/assign/{driver_telegram_id}", response_model=Order)
def assign_driver(order_id: int, driver_telegram_id: int, db: Session = Depends(get_db)):
    order = db.get(OrderDB, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    driver = (
        db.query(UserDB).filter(UserDB.telegram_id == driver_telegram_id).first()
    )
    if not driver or driver.role != UserRole.driver.value:
        raise HTTPException(status_code=404, detail="Driver not found")

    order.driver_id = driver.id
    order.status = OrderStatus.driver_assigned.value
    db.commit()
    db.refresh(order)
    return order


from sqlalchemy.orm import joinedload

@app.post("/orders/{order_id}/status", response_model=Order)
def update_order_status(order_id: int, status: OrderStatus, db: Session = Depends(get_db)):
    order = db.query(OrderDB).options(joinedload(OrderDB.customer)).get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = status.value
    db.commit()
    db.refresh(order)
    return order

@app.get("/orders/customer/{telegram_id}", response_model=List[Order])
def get_customer_orders(telegram_id: int, db: Session = Depends(get_db)):
    customer = (
        db.query(UserDB).filter(UserDB.telegram_id == telegram_id).first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return db.query(OrderDB).filter(OrderDB.customer_id == customer.id).all()


@app.get("/orders/driver/{telegram_id}/active", response_model=List[Order])
def get_driver_active_orders(telegram_id: int, db: Session = Depends(get_db)):
    driver = db.query(UserDB).filter(UserDB.telegram_id == telegram_id).first()
    if not driver or driver.role != UserRole.driver.value:
        raise HTTPException(status_code=403, detail="Not a driver")

    active_statuses = [
        OrderStatus.driver_assigned.value,
        OrderStatus.in_progress.value,
    ]
    return (
        db.query(OrderDB)
        .filter(OrderDB.driver_id == driver.id, OrderDB.status.in_(active_statuses))
        .all()
    )


@app.get("/orders/driver/{telegram_id}/history", response_model=List[Order])
def get_driver_order_history(telegram_id: int, db: Session = Depends(get_db)):
    driver = db.query(UserDB).filter(UserDB.telegram_id == telegram_id).first()
    if not driver or driver.role != UserRole.driver.value:
        raise HTTPException(status_code=403, detail="Not a driver")

    return (
        db.query(OrderDB)
        .filter(OrderDB.driver_id == driver.id, OrderDB.status == OrderStatus.completed.value)
        .all()
    )
