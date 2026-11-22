from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import random
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, Column, Float, Integer, String, Text, create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker
import uvicorn

# Importa funções de impressão direta do printer_service.py
try:
    from printer_service import print_order_now
except ImportError:
    print_order_now = None

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = f"sqlite:///{BASE_DIR / 'lanchonete.db'}"

# Opcional: configure estas variáveis de ambiente para envio real via WhatsApp API
WHATSAPP_API_URL = os.getenv("WHATSAPP_API_URL", "").strip()
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "").strip()
WHATSAPP_INSTANCE = os.getenv("WHATSAPP_INSTANCE", "").strip()

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def normalize_phone(phone: str) -> str:
    digits = "".join(filter(str.isdigit, phone or ""))
    if len(digits) in (10, 11):
        return f"55{digits}"
    return digits


def phone_variants(phone: str) -> list[str]:
    digits = "".join(filter(str.isdigit, phone or ""))
    variants: set[str] = set()
    if digits:
        variants.add(digits)
        if digits.startswith("55") and len(digits) > 11:
            variants.add(digits[2:])
        elif len(digits) in (10, 11):
            variants.add(f"55{digits}")
    return list(variants)


def hash_value(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def send_whatsapp_code(phone: str, code: str, scope: str) -> dict:
    area_label = "da sua conta de cliente" if scope == "customer" else "do painel administrativo"
    message = (
        f"Sua Lanchonete - Código de recuperação de senha ({area_label}): *{code}*\n"
        f"Válido por 10 minutos. Se você não solicitou, ignore esta mensagem."
    )

    if WHATSAPP_API_URL and WHATSAPP_API_TOKEN:
        url = WHATSAPP_API_URL.rstrip("/")
        if WHATSAPP_INSTANCE:
            url = f"{url}/message/sendText/{WHATSAPP_INSTANCE}"
        payload = json.dumps({"number": phone, "text": message}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "apikey": WHATSAPP_API_TOKEN,
                "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                if 200 <= resp.status < 300:
                    return {"sent": True, "mode": "whatsapp_api"}
        except (urllib.error.URLError, TimeoutError):
            pass

    return {"sent": False, "mode": "dev_preview", "dev_code": code}


# --- Modelos ORM (Tabelas do Banco de Dados) ---
class AdminCredentialsModel(Base):
    __tablename__ = "admin_credentials"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), nullable=False)
    password = Column(String(120), nullable=False)
    phone = Column(String(30), default="5519999999999", nullable=False)
    recovery_code = Column(String(80), nullable=False)


class CustomerModel(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(30), unique=True, nullable=False, index=True)
    password = Column(String(120), nullable=False)
    street = Column(String(160), default="", nullable=False)
    number = Column(String(30), default="", nullable=False)
    complement = Column(String(120), default="", nullable=False)
    reference = Column(String(160), default="", nullable=False)


class PasswordResetCodeModel(Base):
    __tablename__ = "password_reset_codes"

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(20), nullable=False, index=True)  # "customer" ou "admin"
    phone = Column(String(30), nullable=False, index=True)
    target_user = Column(String(80), default="", nullable=False)
    code_hash = Column(String(128), nullable=False)
    expires_at = Column(String(40), nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(String(40), nullable=False)


class ProductModel(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=False)
    price = Column(Float, nullable=False)
    image = Column(Text, nullable=False)


class DeliveryFeeModel(Base):
    __tablename__ = "delivery_fees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), unique=True, nullable=False, index=True)
    fee = Column(Float, nullable=False)


class CouponModel(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(40), unique=True, nullable=False, index=True)
    discount_type = Column(String(20), nullable=False)  # "fixed" ou "percent"
    discount_value = Column(Float, nullable=False)
    min_order = Column(Float, default=0.0, nullable=False)
    description = Column(String(180), default="", nullable=False)


class StoreSettingsModel(Base):
    __tablename__ = "store_settings"

    id = Column(Integer, primary_key=True, index=True)
    is_open = Column(Boolean, default=True, nullable=False)
    opening_hours = Column(String(120), default="18:00 às 23:00", nullable=False)


class OrderModel(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String(120), nullable=False)
    client_phone = Column(String(30), nullable=False, index=True)
    delivery_type = Column(String(30), nullable=False)
    address = Column(Text, nullable=False)
    complement = Column(String(120), default="", nullable=False)
    reference = Column(String(160), default="", nullable=False)
    payment = Column(String(120), nullable=False)
    items = Column(Text, nullable=False)
    total = Column(Float, nullable=False)
    status = Column(String(40), default="Pendente", nullable=False, index=True)
    reason = Column(Text, default="", nullable=False)
    printed = Column(Boolean, default=False, nullable=False)
    created_at = Column(String(40), nullable=False)


Base.metadata.create_all(bind=engine)


# Garante migração segura caso você já tenha um lanchonete.db antigo na pasta
def ensure_schema_columns():
    with engine.begin() as conn:
        order_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(orders)")).fetchall()]
        if "complement" not in order_cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN complement VARCHAR(120) DEFAULT '' NOT NULL"))
        if "reference" not in order_cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN reference VARCHAR(160) DEFAULT '' NOT NULL"))
        if "status" not in order_cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN status VARCHAR(40) DEFAULT 'Pendente' NOT NULL"))
        if "reason" not in order_cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN reason TEXT DEFAULT '' NOT NULL"))
        if "printed" not in order_cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN printed BOOLEAN DEFAULT 0 NOT NULL"))
        if "created_at" not in order_cols:
            conn.execute(text("ALTER TABLE orders ADD COLUMN created_at VARCHAR(40) DEFAULT '' NOT NULL"))

        customer_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(customers)")).fetchall()]
        if "complement" not in customer_cols:
            conn.execute(text("ALTER TABLE customers ADD COLUMN complement VARCHAR(120) DEFAULT '' NOT NULL"))
        if "reference" not in customer_cols:
            conn.execute(text("ALTER TABLE customers ADD COLUMN reference VARCHAR(160) DEFAULT '' NOT NULL"))

        admin_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(admin_credentials)")).fetchall()]
        if "phone" not in admin_cols:
            conn.execute(text("ALTER TABLE admin_credentials ADD COLUMN phone VARCHAR(30) DEFAULT '5519999999999' NOT NULL"))


ensure_schema_columns()


# --- Schemas Pydantic ---
class AdminLoginSchema(BaseModel):
    username: str
    password: str


class AdminRecoverSchema(BaseModel):
    recovery_code: str = Field(..., min_length=4)
    new_username: str = Field(..., min_length=3, max_length=80)
    new_password: str = Field(..., min_length=4, max_length=120)


class AdminUpdateCredentialsSchema(BaseModel):
    new_username: str = Field(..., min_length=3, max_length=80)
    new_password: str = Field(..., min_length=4, max_length=120)
    new_phone: str | None = None
    new_whatsapp: str | None = None
    new_recovery_code: str | None = None


class ForgotPasswordRequestSchema(BaseModel):
    scope: str = Field(default="customer", pattern="^(customer|admin)$")
    phone: str = Field(..., min_length=8, max_length=30)
    username: str | None = ""


class ResetPasswordConfirmSchema(BaseModel):
    scope: str = Field(default="customer", pattern="^(customer|admin)$")
    phone: str = Field(..., min_length=8, max_length=30)
    username: str | None = ""
    code: str = Field(..., min_length=4, max_length=8)
    new_password: str = Field(..., min_length=4, max_length=120)


class CustomerRegisterSchema(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    phone: str = Field(..., min_length=8, max_length=30)
    password: str = Field(..., min_length=4, max_length=120)
    street: str = ""
    number: str = ""
    complement: str = ""
    reference: str = ""


class CustomerLoginSchema(BaseModel):
    phone: str
    password: str


class ProductCreate(BaseModel):
    category: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field(..., min_length=2)
    price: float = Field(..., ge=0)
    image: str = Field(..., min_length=1)


class ProductResponse(ProductCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class DeliveryFeeCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    fee: float = Field(..., ge=0)


class DeliveryFeeResponse(DeliveryFeeCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class CouponCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=40)
    discount_type: str = Field(..., pattern="^(fixed|percent)$")
    discount_value: float = Field(..., gt=0)
    min_order: float = Field(default=0.0, ge=0)
    description: str = Field(default="", max_length=180)


class CouponResponse(CouponCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class StoreSettingsSchema(BaseModel):
    is_open: bool
    opening_hours: str = Field(..., min_length=2, max_length=120)
    model_config = ConfigDict(from_attributes=True)


class OrderCreate(BaseModel):
    client_name: str = Field(..., min_length=2, max_length=120)
    client_phone: str = Field(..., min_length=6, max_length=30)
    delivery_type: str = Field(..., min_length=2, max_length=30)
    address: str = Field(..., min_length=2)
    complement: str = ""
    reference: str = ""
    payment: str = Field(..., min_length=2, max_length=120)
    items: str = Field(..., min_length=1)
    total: float = Field(..., ge=0)


class OrderStatusUpdate(BaseModel):
    status: str = Field(..., min_length=2, max_length=40)
    reason: str | None = ""


class OrderResponse(OrderCreate):
    id: int
    status: str
    reason: str = ""
    printed: bool = False
    created_at: str = ""
    model_config = ConfigDict(from_attributes=True)


app = FastAPI(title="Lanchonete API Integrada", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_initial_data():
    db = SessionLocal()
    try:
        if db.query(AdminCredentialsModel).count() == 0:
            db.add(
                AdminCredentialsModel(
                    id=1,
                    username="admin",
                    password="1234",
                    phone="5519999999999",
                    recovery_code="LANCHONETE2026",
                )
            )

        if db.query(StoreSettingsModel).count() == 0:
            db.add(StoreSettingsModel(id=1, is_open=True, opening_hours="18:00 às 23:00"))

        if db.query(ProductModel).count() == 0:
            db.add_all([
                ProductModel(
                    category="lanches",
                    name="X-Bacon Artesanal Supremo",
                    description="Pão brioche selado na manteiga, burger 160g, duplo cheddar e bacon crocante.",
                    price=34.90,
                    image="https://placehold.co/600x400/f43f5e/ffffff?text=X-Bacon",
                ),
                ProductModel(
                    category="bebidas",
                    name="Coca-Cola Lata 350ml",
                    description="Lata bem gelada.",
                    price=6.50,
                    image="https://placehold.co/600x400/ef4444/ffffff?text=Coca-Cola+Lata",
                ),
            ])

        if db.query(DeliveryFeeModel).count() == 0:
            db.add_all([
                DeliveryFeeModel(name="Centro", fee=5.00),
                DeliveryFeeModel(name="Jardim Morada do Sol", fee=8.00),
            ])

        db.commit()
    finally:
        db.close()


seed_initial_data()


def trigger_thermal_print(order: OrderModel, db: Session):
    """Dispara a impressão térmica imediatamente e marca como impresso no banco."""
    order_dict = {
        "id": order.id,
        "client_name": order.client_name,
        "client_phone": order.client_phone,
        "delivery_type": order.delivery_type,
        "address": order.address,
        "complement": order.complement,
        "reference": order.reference,
        "payment": order.payment,
        "items": order.items,
        "total": order.total,
        "status": order.status,
    }
    if print_order_now:
        print_order_now(order_dict)
    order.printed = True
    db.commit()


# --- Páginas HTML (Cardápio e Painel de Pedidos) ---
@app.get("/")
def serve_frontend():
    index_path = BASE_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo index.html não encontrado")
    return FileResponse(index_path)


@app.get("/admin")
def serve_admin_panel():
    admin_path = BASE_DIR / "admin.html"
    if not admin_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo admin.html não encontrado")
    return FileResponse(admin_path)


# --- Endpoints de Clientes (Cadastro, Login e Histórico) ---
@app.post("/api/customers/register")
def register_customer(payload: CustomerRegisterSchema, db: Session = Depends(get_db)):
    clean_phone = normalize_phone(payload.phone)
    if len(clean_phone) < 12:
        raise HTTPException(status_code=400, detail="Informe um número de WhatsApp válido com DDD.")

    variants = phone_variants(clean_phone)
    existing = db.query(CustomerModel).filter(CustomerModel.phone.in_(variants)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Este WhatsApp já possui cadastro.")

    customer = CustomerModel(
        name=payload.name.strip(),
        phone=clean_phone,
        password=payload.password,
        street=payload.street.strip(),
        number=payload.number.strip(),
        complement=payload.complement.strip(),
        reference=payload.reference.strip(),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return {
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "street": customer.street,
        "number": customer.number,
        "complement": customer.complement,
        "reference": customer.reference,
    }


@app.post("/api/customers/login")
def login_customer(payload: CustomerLoginSchema, db: Session = Depends(get_db)):
    variants = phone_variants(payload.phone)
    customer = db.query(CustomerModel).filter(CustomerModel.phone.in_(variants)).first()
    if not customer or customer.password != payload.password:
        raise HTTPException(status_code=401, detail="WhatsApp ou senha inválidos.")
    return {
        "id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "street": customer.street,
        "number": customer.number,
        "complement": customer.complement,
        "reference": customer.reference,
    }


@app.get("/api/customers/{phone}/orders", response_model=list[OrderResponse])
def get_customer_orders(phone: str, db: Session = Depends(get_db)):
    variants = phone_variants(phone)
    return (
        db.query(OrderModel)
        .filter(OrderModel.client_phone.in_(variants))
        .order_by(OrderModel.id.desc())
        .all()
    )


# --- Recuperação Padrão de Senha via Código no WhatsApp ---
@app.post("/api/auth/forgot-password")
def request_password_reset_code(payload: ForgotPasswordRequestSchema, db: Session = Depends(get_db)):
    clean_phone = normalize_phone(payload.phone)
    variants = phone_variants(clean_phone)
    if not variants or len(clean_phone) < 12:
        raise HTTPException(status_code=400, detail="Informe um número de WhatsApp válido com DDD.")

    target_user = ""
    if payload.scope == "customer":
        customer = db.query(CustomerModel).filter(CustomerModel.phone.in_(variants)).first()
        if not customer:
            raise HTTPException(
                status_code=404,
                detail="Não encontramos nenhum cadastro com este número de WhatsApp.",
            )
    else:
        admin = db.query(AdminCredentialsModel).filter(AdminCredentialsModel.id == 1).first()
        typed_user = (payload.username or "").strip()
        if not admin:
            raise HTTPException(status_code=404, detail="Conta administrativa não encontrada.")
        if typed_user and admin.username != typed_user:
            raise HTTPException(status_code=404, detail="Usuário administrativo não encontrado.")
        target_user = admin.username

    code = f"{random.randint(0, 999999):06d}"
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    created_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    db.query(PasswordResetCodeModel).filter(
        PasswordResetCodeModel.scope == payload.scope,
        PasswordResetCodeModel.phone.in_(variants),
        PasswordResetCodeModel.used == False,  # noqa: E712
    ).update({"used": True}, synchronize_session=False)

    reset_entry = PasswordResetCodeModel(
        scope=payload.scope,
        phone=clean_phone,
        target_user=target_user,
        code_hash=hash_value(code),
        expires_at=expires_at,
        used=False,
        created_at=created_at,
    )
    db.add(reset_entry)
    db.commit()

    delivery_info = send_whatsapp_code(clean_phone, code, payload.scope)
    response = {
        "message": "Código de confirmação enviado para o WhatsApp informado.",
        "expires_in_minutes": 10,
        "whatsapp_sent": delivery_info["sent"],
    }
    if not delivery_info["sent"]:
        response["dev_code"] = delivery_info["dev_code"]
    return response


@app.post("/api/auth/reset-password")
def confirm_password_reset_code(payload: ResetPasswordConfirmSchema, db: Session = Depends(get_db)):
    clean_phone = normalize_phone(payload.phone)
    variants = phone_variants(clean_phone)
    code_clean = (payload.code or "").strip()
    if not variants or not code_clean:
        raise HTTPException(status_code=400, detail="Informe o WhatsApp e o código de confirmação.")

    now_iso = datetime.utcnow().isoformat()
    code_digest = hash_value(code_clean)

    token_row = (
        db.query(PasswordResetCodeModel)
        .filter(
            PasswordResetCodeModel.scope == payload.scope,
            PasswordResetCodeModel.phone.in_(variants),
            PasswordResetCodeModel.code_hash == code_digest,
            PasswordResetCodeModel.used == False,  # noqa: E712
        )
        .order_by(PasswordResetCodeModel.id.desc())
        .first()
    )

    if not token_row:
        raise HTTPException(status_code=400, detail="Código de confirmação inválido.")

    if token_row.expires_at < now_iso:
        token_row.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Este código expirou. Solicite um novo código.")

    if payload.scope == "customer":
        customer = db.query(CustomerModel).filter(CustomerModel.phone.in_(variants)).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Conta de cliente não encontrada.")
        customer.password = payload.new_password.strip()
    else:
        admin = db.query(AdminCredentialsModel).filter(AdminCredentialsModel.id == 1).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Conta administrativa não encontrada.")
        if token_row.target_user:
            admin.username = token_row.target_user
        elif (payload.username or "").strip():
            admin.username = payload.username.strip()
        admin.password = payload.new_password.strip()
        admin.phone = clean_phone

    token_row.used = True
    db.commit()
    return {"message": "Senha alterada com sucesso! Faça login com sua nova senha."}


# --- Endpoints de Autenticação e Recuperação Admin ---
@app.post("/api/admin/login")
def admin_login(payload: AdminLoginSchema, db: Session = Depends(get_db)):
    admin = db.query(AdminCredentialsModel).filter(AdminCredentialsModel.id == 1).first()
    if not admin or admin.username != payload.username.strip() or admin.password != payload.password.strip():
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
    return {"status": "authenticated", "username": admin.username}


@app.post("/api/admin/recover")
def admin_recover(payload: AdminRecoverSchema, db: Session = Depends(get_db)):
    admin = db.query(AdminCredentialsModel).filter(AdminCredentialsModel.id == 1).first()
    if not admin or admin.recovery_code != payload.recovery_code.strip():
        raise HTTPException(status_code=403, detail="Código Mestre de Recuperação inválido")
    admin.username = payload.new_username.strip()
    admin.password = payload.new_password.strip()
    db.commit()
    return {"status": "recovered", "username": admin.username}


@app.put("/api/admin/credentials")
def admin_update_credentials(payload: AdminUpdateCredentialsSchema, db: Session = Depends(get_db)):
    admin = db.query(AdminCredentialsModel).filter(AdminCredentialsModel.id == 1).first()
    if not admin:
        admin = AdminCredentialsModel(id=1, recovery_code="LANCHONETE2026", phone="5519999999999")
        db.add(admin)
    admin.username = payload.new_username.strip()
    admin.password = payload.new_password.strip()
    phone_val = payload.new_whatsapp or payload.new_phone
    if phone_val:
        admin.phone = normalize_phone(phone_val)
    if payload.new_recovery_code:
        admin.recovery_code = payload.new_recovery_code.strip()
    db.commit()
    return {"status": "updated", "username": admin.username}


# --- Endpoints de Configurações da Loja ---
@app.get("/api/settings", response_model=StoreSettingsSchema)
def get_store_settings(db: Session = Depends(get_db)):
    settings = db.query(StoreSettingsModel).filter(StoreSettingsModel.id == 1).first()
    if not settings:
        settings = StoreSettingsModel(id=1, is_open=True, opening_hours="18:00 às 23:00")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@app.put("/api/settings", response_model=StoreSettingsSchema)
def update_store_settings(payload: StoreSettingsSchema, db: Session = Depends(get_db)):
    settings = db.query(StoreSettingsModel).filter(StoreSettingsModel.id == 1).first()
    if not settings:
        settings = StoreSettingsModel(id=1)
        db.add(settings)
    settings.is_open = payload.is_open
    settings.opening_hours = payload.opening_hours.strip()
    db.commit()
    db.refresh(settings)
    return settings


# --- Endpoints de Produtos ---
@app.get("/api/products", response_model=list[ProductResponse])
def list_products(category: str | None = None, db: Session = Depends(get_db)):
    query = db.query(ProductModel)
    if category:
        query = query.filter(ProductModel.category == category)
    return query.order_by(ProductModel.id.desc()).all()


@app.post("/api/products", response_model=ProductResponse, status_code=201)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    item = ProductModel(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.delete("/api/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    item = db.query(ProductModel).filter(ProductModel.id == product_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    db.delete(item)
    db.commit()


# --- Endpoints de Taxas por Bairro ---
@app.get("/api/delivery-fees", response_model=list[DeliveryFeeResponse])
def list_delivery_fees(db: Session = Depends(get_db)):
    return db.query(DeliveryFeeModel).order_by(DeliveryFeeModel.name.asc()).all()


@app.post("/api/delivery-fees", response_model=DeliveryFeeResponse, status_code=201)
def upsert_delivery_fee(payload: DeliveryFeeCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(DeliveryFeeModel)
        .filter(DeliveryFeeModel.name.ilike(payload.name.strip()))
        .first()
    )
    if existing:
        existing.name = payload.name.strip()
        existing.fee = payload.fee
        db.commit()
        db.refresh(existing)
        return existing

    item = DeliveryFeeModel(name=payload.name.strip(), fee=payload.fee)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.delete("/api/delivery-fees/{fee_id}", status_code=204)
def delete_delivery_fee(fee_id: int, db: Session = Depends(get_db)):
    item = db.query(DeliveryFeeModel).filter(DeliveryFeeModel.id == fee_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Bairro não encontrado")
    db.delete(item)
    db.commit()


# --- Endpoints de Cupons e Promoções ---
@app.get("/api/coupons", response_model=list[CouponResponse])
def list_coupons(db: Session = Depends(get_db)):
    return db.query(CouponModel).order_by(CouponModel.id.desc()).all()


@app.post("/api/coupons", response_model=CouponResponse, status_code=201)
def upsert_coupon(payload: CouponCreate, db: Session = Depends(get_db)):
    code_clean = payload.code.strip().upper()
    existing = db.query(CouponModel).filter(CouponModel.code.ilike(code_clean)).first()
    if existing:
        existing.code = code_clean
        existing.discount_type = payload.discount_type
        existing.discount_value = payload.discount_value
        existing.min_order = payload.min_order
        existing.description = payload.description.strip()
        db.commit()
        db.refresh(existing)
        return existing

    item = CouponModel(
        code=code_clean,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        min_order=payload.min_order,
        description=payload.description.strip(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.delete("/api/coupons/{coupon_id}", status_code=204)
def delete_coupon(coupon_id: int, db: Session = Depends(get_db)):
    item = db.query(CouponModel).filter(CouponModel.id == coupon_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Cupom não encontrado")
    db.delete(item)
    db.commit()


# --- Endpoints de Pedidos e Impressão Automática ---
@app.get("/api/orders", response_model=list[OrderResponse])
def list_orders(db: Session = Depends(get_db)):
    return db.query(OrderModel).order_by(OrderModel.id.desc()).all()


@app.post("/api/orders", response_model=OrderResponse, status_code=201)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    order_data = payload.model_dump()
    order_data["client_phone"] = normalize_phone(payload.client_phone)
    order = OrderModel(
        **order_data,
        status="Pendente",
        reason="",
        printed=False,
        created_at=now_str,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@app.put("/api/orders/{order_id}/status", response_model=OrderResponse)
def update_order_status(order_id: int, payload: OrderStatusUpdate, db: Session = Depends(get_db)):
    order = db.query(OrderModel).filter(OrderModel.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    order.status = payload.status.strip()
    if payload.reason is not None:
        order.reason = payload.reason.strip()
    db.commit()
    db.refresh(order)

    # Dispara a impressão térmica apenas quando o pedido for confirmado ("Confirmado")
    if order.status == "Confirmado" and not order.printed:
        trigger_thermal_print(order, db)
        db.refresh(order)

    return order


@app.post("/api/orders/{order_id}/print")
def manual_print_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(OrderModel).filter(OrderModel.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    trigger_thermal_print(order, db)
    return {"status": "printed", "order_id": order.id}


def _wait_for_server_and_open(host: str, port: int, url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex((host, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.2)
    webbrowser.open(url)


if __name__ == "__main__":
    app_host = "127.0.0.1"
    app_port = 8000
    app_url = f"http://{app_host}:{app_port}"
    threading.Thread(
        target=_wait_for_server_and_open,
        args=(app_host, app_port, app_url),
        daemon=True,
    ).start()
    uvicorn.run("main:app", host=app_host, port=app_port, reload=False)