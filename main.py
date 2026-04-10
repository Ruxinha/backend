from fastapi import FastAPI, APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timedelta
from bson import ObjectId
import csv
import io
import bcrypt
from jose import JWTError, jwt

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# JWT Configuration
SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-super-secret-key-change-in-production')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

security = HTTPBearer()

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ.get('DB_NAME', 'finance_tracker')]

# Create the main app without a prefix
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== MODELS ====================

# User Authentication Models
class UserRegister(BaseModel):
    name: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class UserSettings(BaseModel):
    dark_mode: bool = True
    language: str = "pt"  # pt, en, es
    currency: str = "EUR"  # EUR, USD, BRL, GBP

class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    email: str
    password_hash: str
    profile_photo: Optional[str] = None  # Base64 encoded image
    settings: UserSettings = Field(default_factory=UserSettings)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class UserUpdate(BaseModel):
    name: Optional[str] = None
    profile_photo: Optional[str] = None
    settings: Optional[UserSettings] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

# Client Models
class ClientBase(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    company: str = ""
    address: str = ""
    notes: str = ""

class ClientCreate(ClientBase):
    pass

class Client(ClientBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    total_revenue: float = 0.0
    transaction_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None

class CategoryBase(BaseModel):
    name: str
    type: str  # 'income' or 'expense'
    color: str = "#4CAF50"
    icon: str = "cash"

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None

class Category(CategoryBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TransactionBase(BaseModel):
    amount: float
    type: str  # 'income' or 'expense'
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    description: str = ""
    date: datetime = Field(default_factory=datetime.utcnow)
    client_id: Optional[str] = None
    client_name: Optional[str] = None

class TransactionCreate(TransactionBase):
    pass

class Transaction(TransactionBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TransactionUpdate(BaseModel):
    amount: Optional[float] = None
    type: Optional[str] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    description: Optional[str] = None
    date: Optional[datetime] = None
    client_id: Optional[str] = None
    client_name: Optional[str] = None

class InvoiceItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    total: float

class InvoiceBase(BaseModel):
    client_id: Optional[str] = None
    client_name: str
    client_email: str = ""
    client_address: str = ""
    items: List[InvoiceItem]
    subtotal: float
    tax_rate: float = 0.0
    tax_amount: float = 0.0
    total: float
    status: str = "draft"  # draft, sent, approved, paid, overdue
    due_date: datetime
    notes: str = ""

class InvoiceCreate(InvoiceBase):
    pass

class Invoice(InvoiceBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    invoice_number: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

class InvoiceUpdate(BaseModel):
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    client_address: Optional[str] = None
    items: Optional[List[InvoiceItem]] = None
    subtotal: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total: Optional[float] = None
    status: Optional[str] = None
    due_date: Optional[datetime] = None
    notes: Optional[str] = None

# ==================== HELPER FUNCTIONS ====================

async def get_next_invoice_number():
    """Generate next invoice number"""
    last_invoice = await db.invoices.find_one(sort=[("created_at", -1)])
    if last_invoice and last_invoice.get("invoice_number"):
        try:
            last_num = int(last_invoice["invoice_number"].replace("INV-", ""))
            return f"INV-{str(last_num + 1).zfill(5)}"
        except:
            pass
    return "INV-00001"

async def seed_default_categories(user_id: str = None):
    """Seed default categories if none exist for user"""
    query = {"user_id": user_id} if user_id else {}
    count = await db.categories.count_documents(query)
    if count == 0:
        default_categories = [
            # Income categories
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Vendas", "type": "income", "color": "#4CAF50", "icon": "cart", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Serviços", "type": "income", "color": "#2196F3", "icon": "briefcase", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Investimentos", "type": "income", "color": "#9C27B0", "icon": "trending-up", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Outros Rendimentos", "type": "income", "color": "#00BCD4", "icon": "cash", "created_at": datetime.utcnow()},
            # Expense categories
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Renda", "type": "expense", "color": "#F44336", "icon": "home", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Utilidades", "type": "expense", "color": "#FF9800", "icon": "flash", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Materiais", "type": "expense", "color": "#795548", "icon": "cube", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Marketing", "type": "expense", "color": "#E91E63", "icon": "megaphone", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Salários", "type": "expense", "color": "#673AB7", "icon": "people", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Viagens", "type": "expense", "color": "#009688", "icon": "airplane", "created_at": datetime.utcnow()},
            {"id": str(uuid.uuid4()), "user_id": user_id, "name": "Outras Despesas", "type": "expense", "color": "#607D8B", "icon": "receipt", "created_at": datetime.utcnow()},
        ]
        await db.categories.insert_many(default_categories)
        logger.info(f"Seeded default categories for user {user_id}")

# ==================== AUTH HELPERS ====================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = await db.users.find_one({"id": user_id})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ==================== ROUTES ====================

@api_router.get("/")
async def root():
    return {"message": "Finance Tracker API", "version": "1.0.0"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy"}

# ==================== AUTH ROUTES ====================

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserRegister):
    # Check if user already exists
    existing_user = await db.users.find_one({"email": user_data.email.lower()})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email já registado")
    
    # Create new user
    user = User(
        name=user_data.name,
        email=user_data.email.lower(),
        password_hash=hash_password(user_data.password),
        settings=UserSettings()
    )
    await db.users.insert_one(user.dict())
    
    # Seed default categories for user
    await seed_default_categories(user.id)
    
    # Create access token
    access_token = create_access_token(data={"sub": user.id})
    
    return TokenResponse(
        access_token=access_token,
        user={
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "settings": user.settings.dict()
        }
    )

@api_router.post("/auth/login", response_model=TokenResponse)
async def login(user_data: UserLogin):
    user = await db.users.find_one({"email": user_data.email.lower()})
    if not user or not verify_password(user_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email ou palavra-passe incorretos")
    
    access_token = create_access_token(data={"sub": user["id"]})
    
    return TokenResponse(
        access_token=access_token,
        user={
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "profile_photo": user.get("profile_photo"),
            "settings": user.get("settings", UserSettings().dict())
        }
    )

@api_router.get("/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "name": current_user["name"],
        "email": current_user["email"],
        "profile_photo": current_user.get("profile_photo"),
        "settings": current_user.get("settings", UserSettings().dict())
    }

@api_router.put("/auth/settings")
async def update_settings(settings: UserSettings, current_user: dict = Depends(get_current_user)):
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"settings": settings.dict()}}
    )
    return {"message": "Definições atualizadas", "settings": settings.dict()}

@api_router.put("/auth/profile")
async def update_profile(updates: UserUpdate, current_user: dict = Depends(get_current_user)):
    update_data = {}
    if updates.name:
        update_data["name"] = updates.name
    if updates.profile_photo is not None:
        update_data["profile_photo"] = updates.profile_photo
    if updates.settings:
        update_data["settings"] = updates.settings.dict()
    
    if update_data:
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": update_data}
        )
    
    updated_user = await db.users.find_one({"id": current_user["id"]})
    return {
        "id": updated_user["id"],
        "name": updated_user["name"],
        "email": updated_user["email"],
        "profile_photo": updated_user.get("profile_photo"),
        "settings": updated_user.get("settings", UserSettings().dict())
    }

# ==================== CATEGORY ROUTES ====================

@api_router.get("/categories", response_model=List[Category])
async def get_categories():
    await seed_default_categories()
    categories = await db.categories.find().to_list(1000)
    return [Category(**cat) for cat in categories]

@api_router.post("/categories", response_model=Category)
async def create_category(category: CategoryCreate):
    category_obj = Category(**category.dict())
    await db.categories.insert_one(category_obj.dict())
    return category_obj

@api_router.delete("/categories/{category_id}")
async def delete_category(category_id: str):
    result = await db.categories.delete_one({"id": category_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"message": "Category deleted"}

@api_router.put("/categories/{category_id}", response_model=Category)
async def update_category(category_id: str, updates: CategoryUpdate):
    update_data = {k: v for k, v in updates.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No updates provided")
    result = await db.categories.update_one({"id": category_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Category not found")
    updated = await db.categories.find_one({"id": category_id})
    return Category(**updated)

# ==================== CLIENT ROUTES ====================

@api_router.get("/clients", response_model=List[Client])
async def get_clients(search: Optional[str] = None, limit: int = 100):
    query = {}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"company": {"$regex": search, "$options": "i"}}
        ]
    clients = await db.clients.find(query).sort("created_at", -1).to_list(limit)
    
    # Calculate stats for each client
    result = []
    for c in clients:
        # Get transactions for this client
        transactions = await db.transactions.find({"client_id": c["id"]}).to_list(10000)
        total_revenue = sum(t["amount"] for t in transactions if t["type"] == "income")
        c["total_revenue"] = total_revenue
        c["transaction_count"] = len(transactions)
        result.append(Client(**c))
    
    return result

@api_router.get("/clients/{client_id}", response_model=Client)
async def get_client(client_id: str):
    client = await db.clients.find_one({"id": client_id})
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Calculate stats
    transactions = await db.transactions.find({"client_id": client_id}).to_list(10000)
    client["total_revenue"] = sum(t["amount"] for t in transactions if t["type"] == "income")
    client["transaction_count"] = len(transactions)
    
    return Client(**client)

@api_router.post("/clients", response_model=Client)
async def create_client(client: ClientCreate):
    client_obj = Client(**client.dict())
    await db.clients.insert_one(client_obj.dict())
    return client_obj

@api_router.put("/clients/{client_id}", response_model=Client)
async def update_client(client_id: str, updates: ClientUpdate):
    update_data = {k: v for k, v in updates.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    result = await db.clients.find_one_and_update(
        {"id": client_id},
        {"$set": update_data},
        return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Calculate stats
    transactions = await db.transactions.find({"client_id": client_id}).to_list(10000)
    result["total_revenue"] = sum(t["amount"] for t in transactions if t["type"] == "income")
    result["transaction_count"] = len(transactions)
    
    return Client(**result)

@api_router.delete("/clients/{client_id}")
async def delete_client(client_id: str):
    result = await db.clients.delete_one({"id": client_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"message": "Client deleted"}

@api_router.get("/clients/{client_id}/transactions", response_model=List[Transaction])
async def get_client_transactions(client_id: str, limit: int = 100):
    """Get all transactions for a specific client"""
    transactions = await db.transactions.find({"client_id": client_id}).sort("date", -1).to_list(limit)
    return [Transaction(**t) for t in transactions]

# ==================== TRANSACTION ROUTES ====================

@api_router.get("/transactions", response_model=List[Transaction])
async def get_transactions(
    type: Optional[str] = None,
    category_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100
):
    query = {}
    if type:
        query["type"] = type
    if category_id:
        query["category_id"] = category_id
    if start_date:
        query["date"] = {"$gte": datetime.fromisoformat(start_date)}
    if end_date:
        if "date" in query:
            query["date"]["$lte"] = datetime.fromisoformat(end_date)
        else:
            query["date"] = {"$lte": datetime.fromisoformat(end_date)}
    
    transactions = await db.transactions.find(query).sort("date", -1).to_list(limit)
    return [Transaction(**t) for t in transactions]

@api_router.post("/transactions", response_model=Transaction)
async def create_transaction(transaction: TransactionCreate):
    transaction_obj = Transaction(**transaction.dict())
    await db.transactions.insert_one(transaction_obj.dict())
    return transaction_obj

@api_router.put("/transactions/{transaction_id}", response_model=Transaction)
async def update_transaction(transaction_id: str, updates: TransactionUpdate):
    update_data = {k: v for k, v in updates.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    result = await db.transactions.find_one_and_update(
        {"id": transaction_id},
        {"$set": update_data},
        return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return Transaction(**result)

@api_router.delete("/transactions/{transaction_id}")
async def delete_transaction(transaction_id: str):
    result = await db.transactions.delete_one({"id": transaction_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"message": "Transaction deleted"}

# ==================== INVOICE ROUTES ====================

@api_router.get("/invoices", response_model=List[Invoice])
async def get_invoices(status: Optional[str] = None, limit: int = 100):
    query = {}
    if status:
        query["status"] = status
    invoices = await db.invoices.find(query).sort("created_at", -1).to_list(limit)
    return [Invoice(**inv) for inv in invoices]

@api_router.get("/invoices/{invoice_id}", response_model=Invoice)
async def get_invoice(invoice_id: str):
    invoice = await db.invoices.find_one({"id": invoice_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return Invoice(**invoice)

@api_router.post("/invoices", response_model=Invoice)
async def create_invoice(invoice: InvoiceCreate):
    invoice_number = await get_next_invoice_number()
    invoice_obj = Invoice(**invoice.dict(), invoice_number=invoice_number)
    await db.invoices.insert_one(invoice_obj.dict())
    return invoice_obj

@api_router.put("/invoices/{invoice_id}", response_model=Invoice)
async def update_invoice(invoice_id: str, updates: InvoiceUpdate):
    update_data = {k: v for k, v in updates.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    result = await db.invoices.find_one_and_update(
        {"id": invoice_id},
        {"$set": update_data},
        return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return Invoice(**result)

@api_router.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: str):
    result = await db.invoices.delete_one({"id": invoice_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"message": "Invoice deleted"}

# ==================== REPORTS ROUTES ====================

@api_router.get("/reports/summary")
async def get_financial_summary(period: str = "month"):
    """Get financial summary for a period (week, month, year, all)"""
    now = datetime.utcnow()
    
    if period == "week":
        start_date = now - timedelta(days=7)
    elif period == "month":
        start_date = now - timedelta(days=30)
    elif period == "year":
        start_date = now - timedelta(days=365)
    else:
        start_date = datetime(2000, 1, 1)
    
    # Get transactions for the period
    transactions = await db.transactions.find({"date": {"$gte": start_date}}).to_list(10000)
    
    total_income = sum(t["amount"] for t in transactions if t["type"] == "income")
    total_expenses = sum(t["amount"] for t in transactions if t["type"] == "expense")
    net_profit = total_income - total_expenses
    
    # Get category breakdown
    income_by_category = {}
    expense_by_category = {}
    
    for t in transactions:
        cat_name = t.get("category_name", "Other")
        if t["type"] == "income":
            income_by_category[cat_name] = income_by_category.get(cat_name, 0) + t["amount"]
        else:
            expense_by_category[cat_name] = expense_by_category.get(cat_name, 0) + t["amount"]
    
    return {
        "period": period,
        "start_date": start_date.isoformat(),
        "end_date": now.isoformat(),
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "transaction_count": len(transactions),
        "income_by_category": income_by_category,
        "expense_by_category": expense_by_category
    }

@api_router.get("/reports/trends")
async def get_spending_trends(period: str = "month"):
    """Get spending trends over time"""
    now = datetime.utcnow()
    
    if period == "week":
        start_date = now - timedelta(days=7)
        group_by = "day"
    elif period == "month":
        start_date = now - timedelta(days=30)
        group_by = "day"
    elif period == "year":
        start_date = now - timedelta(days=365)
        group_by = "month"
    else:
        start_date = now - timedelta(days=30)
        group_by = "day"
    
    transactions = await db.transactions.find({"date": {"$gte": start_date}}).to_list(10000)
    
    # Group by date
    trends = {}
    for t in transactions:
        if group_by == "day":
            key = t["date"].strftime("%Y-%m-%d")
        else:
            key = t["date"].strftime("%Y-%m")
        
        if key not in trends:
            trends[key] = {"income": 0, "expenses": 0, "date": key}
        
        if t["type"] == "income":
            trends[key]["income"] += t["amount"]
        else:
            trends[key]["expenses"] += t["amount"]
    
    # Sort by date and fill in missing dates
    sorted_trends = sorted(trends.values(), key=lambda x: x["date"])
    
    return {
        "period": period,
        "group_by": group_by,
        "trends": sorted_trends
    }

@api_router.get("/reports/monthly")
async def get_monthly_summary(year: int = None):
    """Get monthly financial summary for a year"""
    if year is None:
        year = datetime.utcnow().year
    
    start_date = datetime(year, 1, 1)
    end_date = datetime(year, 12, 31, 23, 59, 59)
    
    transactions = await db.transactions.find({
        "date": {"$gte": start_date, "$lte": end_date}
    }).to_list(10000)
    
    monthly_data = {}
    for month in range(1, 13):
        month_key = f"{year}-{str(month).zfill(2)}"
        monthly_data[month_key] = {"income": 0, "expenses": 0, "net": 0}
    
    for t in transactions:
        month_key = t["date"].strftime("%Y-%m")
        if month_key in monthly_data:
            if t["type"] == "income":
                monthly_data[month_key]["income"] += t["amount"]
            else:
                monthly_data[month_key]["expenses"] += t["amount"]
            monthly_data[month_key]["net"] = monthly_data[month_key]["income"] - monthly_data[month_key]["expenses"]
    
    return {
        "year": year,
        "monthly_data": monthly_data
    }

# ==================== EXPORT ROUTES ====================

@api_router.get("/export/transactions")
async def export_transactions(
    type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Export transactions to CSV"""
    query = {}
    if type:
        query["type"] = type
    if start_date:
        query["date"] = {"$gte": datetime.fromisoformat(start_date)}
    if end_date:
        if "date" in query:
            query["date"]["$lte"] = datetime.fromisoformat(end_date)
        else:
            query["date"] = {"$lte": datetime.fromisoformat(end_date)}
    
    transactions = await db.transactions.find(query).sort("date", -1).to_list(10000)
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Type', 'Category', 'Description', 'Amount'])
    
    for t in transactions:
        writer.writerow([
            t['date'].strftime('%Y-%m-%d') if isinstance(t['date'], datetime) else t['date'],
            t['type'].capitalize(),
            t.get('category_name', 'N/A'),
            t.get('description', ''),
            f"${t['amount']:.2f}"
        ])
    
    output.seek(0)
    filename = f"transactions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@api_router.get("/export/invoices")
async def export_invoices(status: Optional[str] = None):
    """Export invoices to CSV"""
    query = {}
    if status:
        query["status"] = status
    
    invoices = await db.invoices.find(query).sort("created_at", -1).to_list(10000)
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Invoice #', 'Client Name', 'Client Email', 'Status', 'Due Date', 'Subtotal', 'Tax', 'Total', 'Created Date'])
    
    for inv in invoices:
        writer.writerow([
            inv.get('invoice_number', 'N/A'),
            inv.get('client_name', 'N/A'),
            inv.get('client_email', ''),
            inv.get('status', 'draft').capitalize(),
            inv['due_date'].strftime('%Y-%m-%d') if isinstance(inv.get('due_date'), datetime) else inv.get('due_date', 'N/A'),
            f"${inv.get('subtotal', 0):.2f}",
            f"${inv.get('tax_amount', 0):.2f}",
            f"${inv.get('total', 0):.2f}",
            inv['created_at'].strftime('%Y-%m-%d') if isinstance(inv.get('created_at'), datetime) else inv.get('created_at', 'N/A')
        ])
    
    output.seek(0)
    filename = f"invoices_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@api_router.get("/export/report")
async def export_financial_report(period: str = "month"):
    """Export financial report to CSV"""
    now = datetime.utcnow()
    
    if period == "week":
        start_date = now - timedelta(days=7)
    elif period == "month":
        start_date = now - timedelta(days=30)
    elif period == "year":
        start_date = now - timedelta(days=365)
    else:
        start_date = datetime(2000, 1, 1)
    
    transactions = await db.transactions.find({"date": {"$gte": start_date}}).to_list(10000)
    
    total_income = sum(t["amount"] for t in transactions if t["type"] == "income")
    total_expenses = sum(t["amount"] for t in transactions if t["type"] == "expense")
    net_profit = total_income - total_expenses
    
    # Category breakdown
    income_by_category = {}
    expense_by_category = {}
    
    for t in transactions:
        cat_name = t.get("category_name", "Other")
        if t["type"] == "income":
            income_by_category[cat_name] = income_by_category.get(cat_name, 0) + t["amount"]
        else:
            expense_by_category[cat_name] = expense_by_category.get(cat_name, 0) + t["amount"]
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Summary section
    writer.writerow(['FINANCIAL REPORT'])
    writer.writerow([f'Period: {period.capitalize()} ({start_date.strftime("%Y-%m-%d")} to {now.strftime("%Y-%m-%d")})'])
    writer.writerow([])
    writer.writerow(['SUMMARY'])
    writer.writerow(['Total Income', f'${total_income:.2f}'])
    writer.writerow(['Total Expenses', f'${total_expenses:.2f}'])
    writer.writerow(['Net Profit', f'${net_profit:.2f}'])
    writer.writerow(['Total Transactions', len(transactions)])
    writer.writerow([])
    
    # Income breakdown
    writer.writerow(['INCOME BY CATEGORY'])
    writer.writerow(['Category', 'Amount'])
    for cat, amount in income_by_category.items():
        writer.writerow([cat, f'${amount:.2f}'])
    writer.writerow([])
    
    # Expense breakdown
    writer.writerow(['EXPENSES BY CATEGORY'])
    writer.writerow(['Category', 'Amount'])
    for cat, amount in expense_by_category.items():
        writer.writerow([cat, f'${amount:.2f}'])
    
    output.seek(0)
    filename = f"financial_report_{period}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    await seed_default_categories(None)
    logger.info("Application started")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
