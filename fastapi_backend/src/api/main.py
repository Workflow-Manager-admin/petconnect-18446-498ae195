from typing import List, Optional
from enum import Enum
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    status,
    UploadFile,
    File,
    Form,
    Body,
)
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    Float,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Enum as SQLAEnum,
    text,  # Added import for SQL text construct
)
from sqlalchemy.orm import relationship, sessionmaker, declarative_base, Session
from jose import JWTError, jwt  # ensure python-jose (not generic 'jose')
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

import os

# Ensure Python 3+
import sys
if sys.version_info < (3, 7):
    raise RuntimeError("This application requires Python 3.7 or newer.")

# Constants and Config
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./petconnect.db")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "changeme_supersecret")  # In production, use an environment variable
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# RBAC Roles
class UserRole(str, Enum):
    ADOPTER = "adopter"
    RESCUER = "rescuer"
    ADMIN = "admin"

# Database Setup
Base = declarative_base()
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ====================
#      MODELS
# ====================

# SQLAlchemy models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String)
    role = Column(SQLAEnum(UserRole), default=UserRole.ADOPTER, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    pets = relationship("Pet", back_populates="owner")
    messages_sent = relationship("Message", back_populates="sender", foreign_keys="Message.sender_id")
    messages_received = relationship("Message", back_populates="receiver", foreign_keys="Message.receiver_id")


class Pet(Base):
    __tablename__ = "pets"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    species = Column(String, nullable=False)
    breed = Column(String)
    age = Column(Float)
    description = Column(Text)
    location_lat = Column(Float)
    location_lng = Column(Float)
    available = Column(Boolean, default=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    photos = relationship("PetPhoto", back_populates="pet", cascade="all, delete")
    owner = relationship("User", back_populates="pets")


class PetPhoto(Base):
    __tablename__ = "pet_photos"
    id = Column(Integer, primary_key=True, index=True)
    image_url = Column(String, nullable=False)
    pet_id = Column(Integer, ForeignKey("pets.id"))
    pet = relationship("Pet", back_populates="photos")
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"))
    receiver_id = Column(Integer, ForeignKey("users.id"))
    content = Column(String)
    sent_at = Column(DateTime, default=datetime.utcnow)
    flagged = Column(Boolean, default=False)

    sender = relationship("User", back_populates="messages_sent", foreign_keys=[sender_id])
    receiver = relationship("User", back_populates="messages_received", foreign_keys=[receiver_id])


class AdminAnalytics(Base):
    __tablename__ = "admin_analytics"
    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False, unique=True)
    value = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class FlaggedContent(Base):
    __tablename__ = "flagged_content"
    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)  # e.g. 'pet', 'message', 'user'
    item_id = Column(Integer, nullable=False)
    flagged_reason = Column(String)
    flagged_by = Column(Integer, ForeignKey("users.id"))
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ==============
#   SCHEMAS
# ==============
class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None


class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=6, description="User password (min 6 chars)")
    role: UserRole = Field(default=UserRole.ADOPTER)


class UserOut(UserBase):
    id: int
    role: UserRole

    class Config:
        orm_mode = True


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class PetBase(BaseModel):
    name: str
    species: str
    breed: Optional[str] = None
    age: Optional[float] = None
    description: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None


class PetCreate(PetBase):
    pass


class PetUpdate(PetBase):
    available: Optional[bool] = None


class PetOut(PetBase):
    id: int
    available: bool
    photos: List[str]
    owner_id: int
    created_at: datetime

    class Config:
        orm_mode = True


class MessageOut(BaseModel):
    id: int
    sender_id: int
    receiver_id: int
    content: str
    sent_at: datetime

    class Config:
        orm_mode = True


# ======================
#   APP SETUP & UTILS
# ======================
app = FastAPI(
    title="PetConnect API",
    description="Backend API for Pet Adoption/Rescue Platform",
    version="1.0.0",
    openapi_tags=[
        {"name": "auth", "description": "Authentication and user registration"},
        {"name": "users", "description": "User management/Role management"},
        {"name": "pets", "description": "Pet listings management"},
        {"name": "messages", "description": "Messaging endpoints"},
        {"name": "admin", "description": "Admin endpoints (analytics/moderation)"},
        {"name": "files", "description": "Photo upload"},
        {"name": "health", "description": "Health check endpoints"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

# ========================
#     AUTH FUNCTIONALITY
# ========================
def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user

# PUBLIC_INTERFACE
async def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)
) -> User:
    """Gets current user from JWT access token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email: str = payload.get("sub")
        role: str = payload.get("role")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email, role=role)
    except JWTError:
        raise credentials_exception
    user = get_user_by_email(db, token_data.email)
    if user is None:
        raise credentials_exception
    return user

# PUBLIC_INTERFACE
def require_role(required_roles: List[UserRole]):
    """Dependency function for enforcing role-based access."""
    def role_checker(
        current_user: User = Depends(get_current_user),
    ):
        if current_user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires one of the following roles: {required_roles}",
            )
        return current_user
    return role_checker

# ==============
#    ENDPOINTS
# ==============

# ---- Health ----
@app.get("/health/db", tags=["health"], summary="Database health check")
def db_health_check():
    """Check database connectivity."""
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return {"status": "ok"}
    except Exception as e:
        import traceback
        return JSONResponse(status_code=503, content={"status": "unhealthy", "error": str(e), "trace": traceback.format_exc()})

# -------- AUTH --------
@app.post("/users/register", tags=["auth"], summary="User registration", response_model=UserOut)
def register_user(user: UserCreate = Body(...), db: Session = Depends(get_db)):
    """Register a new user (with input validation). Captcha required (stub).

    Handles unique email constraint violations gracefully and returns
    friendly errors if email is already registered.
    """
    from sqlalchemy.exc import IntegrityError

    # CAPTCHA check could go here (stub)
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    new_user = User(
        email=user.email,
        full_name=user.full_name,
        hashed_password=get_password_hash(user.password),
        role=user.role,
    )
    db.add(new_user)
    try:
        db.commit()
        db.refresh(new_user)
    except IntegrityError:
        db.rollback()
        # Detect specifically unique constraint violation for email field
        # Optionally, you can examine e.orig for DB-specific error codes/messages
        raise HTTPException(status_code=409, detail="Email already registered")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")
    return new_user

@app.post("/auth/token", tags=["auth"], summary="JWT login", response_model=Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    """User login and JWT token issuance."""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )
    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer"}

# --- User Role Management ---
@app.get("/users/me", tags=["users"], response_model=UserOut)
def read_users_me(current_user: User = Depends(get_current_user)):
    """Get details of logged-in user."""
    return current_user

@app.put("/users/{user_id}/role", tags=["users"], summary="Change user role")
def update_user_role(
    user_id: int,
    new_role: UserRole = Body(..., embed=True),
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.ADMIN])),
):
    """Change a user's role. Admin only."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = new_role
    db.commit()
    return {"message": "User role updated", "user_id": user_id, "new_role": new_role}

# --- Pets CRUD ---
@app.post("/pets/", tags=["pets"], response_model=PetOut)
def create_pet(
    name: str = Form(...),
    species: str = Form(...),
    breed: str = Form(""),
    age: float = Form(None),
    description: str = Form(""),
    location_lat: float = Form(None),
    location_lng: float = Form(None),
    files: List[UploadFile] = File([]),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.RESCUER, UserRole.ADMIN])),
):
    """Create new pet listing. Multi-photo upload. CAPTCHA required (stub)."""
    new_pet = Pet(
        name=name,
        species=species,
        breed=breed,
        age=age,
        description=description,
        location_lat=location_lat,
        location_lng=location_lng,
        owner_id=current_user.id,
    )
    db.add(new_pet)
    db.commit()
    db.refresh(new_pet)
    photo_urls = []
    # Handle uploads (stub for Cloudinary URL; SAVES locally and generates dummy URL for now)
    for file in files:
        filename = f"uploads/{datetime.utcnow().timestamp()}_{file.filename}"
        with open(filename, "wb") as image_file:
            contents = file.file.read()
            image_file.write(contents)
        # TODO: Replace below with actual Cloudinary integration
        photo_url = f"/static/{filename}"  # Placeholder
        new_photo = PetPhoto(image_url=photo_url, pet_id=new_pet.id)
        db.add(new_photo)
        photo_urls.append(photo_url)
    db.commit()
    return PetOut(
        id=new_pet.id,
        name=new_pet.name,
        species=new_pet.species,
        breed=new_pet.breed,
        age=new_pet.age,
        available=new_pet.available,
        description=new_pet.description,
        location_lat=new_pet.location_lat,
        location_lng=new_pet.location_lng,
        owner_id=new_pet.owner_id,
        photos=photo_urls,
        created_at=new_pet.created_at,
    )

@app.get("/pets/", tags=["pets"], response_model=List[PetOut])
def list_pets(
    species: Optional[str] = None,
    breed: Optional[str] = None,
    age: Optional[float] = None,
    location_lat: Optional[float] = None,
    location_lng: Optional[float] = None,
    available: Optional[bool] = True,
    db: Session = Depends(get_db),
):
    """List/search pets with optional filters."""
    pets_query = db.query(Pet)
    if available is not None:
        pets_query = pets_query.filter(Pet.available == available)
    if species:
        pets_query = pets_query.filter(Pet.species.ilike(f"%{species}%"))
    if breed:
        pets_query = pets_query.filter(Pet.breed.ilike(f"%{breed}%"))
    if age:
        pets_query = pets_query.filter(Pet.age == age)
    # Location filtering is basic; for real geo use, consider radius/Haversine
    pets = pets_query.all()
    results = []
    for pet in pets:
        results.append(
            PetOut(
                id=pet.id,
                name=pet.name,
                species=pet.species,
                breed=pet.breed,
                age=pet.age,
                available=pet.available,
                description=pet.description,
                location_lat=pet.location_lat,
                location_lng=pet.location_lng,
                owner_id=pet.owner_id,
                photos=[ph.image_url for ph in pet.photos],
                created_at=pet.created_at,
            )
        )
    return results

@app.get("/pets/{pet_id}", tags=["pets"], response_model=PetOut)
def get_pet(pet_id: int, db: Session = Depends(get_db)):
    """Get pet listing by ID."""
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found")
    return PetOut(
        id=pet.id,
        name=pet.name,
        species=pet.species,
        breed=pet.breed,
        age=pet.age,
        available=pet.available,
        description=pet.description,
        location_lat=pet.location_lat,
        location_lng=pet.location_lng,
        owner_id=pet.owner_id,
        photos=[ph.image_url for ph in pet.photos],
        created_at=pet.created_at,
    )

@app.put("/pets/{pet_id}", tags=["pets"], response_model=PetOut)
def update_pet(
    pet_id: int,
    pet_update: PetUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update pet listing (owner or admin only)."""
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found")
    if pet.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not allowed")
    for attr, value in pet_update.dict(exclude_unset=True).items():
        setattr(pet, attr, value)
    db.commit()
    db.refresh(pet)
    return PetOut(
        id=pet.id,
        name=pet.name,
        species=pet.species,
        breed=pet.breed,
        age=pet.age,
        available=pet.available,
        description=pet.description,
        location_lat=pet.location_lat,
        location_lng=pet.location_lng,
        owner_id=pet.owner_id,
        photos=[ph.image_url for ph in pet.photos],
        created_at=pet.created_at,
    )

@app.delete("/pets/{pet_id}", tags=["pets"], summary="Delete a pet listing")
def delete_pet(
    pet_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete pet listing. Allowed for owner or admin only."""
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found")
    if pet.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not allowed")
    db.delete(pet)
    db.commit()
    return {"status": "deleted", "pet_id": pet_id}

# --- Location Endpoints (Map Integration) ---
@app.put("/pets/{pet_id}/location", tags=["pets"], summary="Update pet location")
def update_pet_location(
    pet_id: int,
    location_lat: float = Body(...),
    location_lng: float = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update pet's map location."""
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found")
    if pet.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not allowed")
    pet.location_lat = location_lat
    pet.location_lng = location_lng
    db.commit()
    return {"pet_id": pet_id, "location_lat": location_lat, "location_lng": location_lng}

# --- Messaging and Interest Contact ---
@app.post("/messages/send", tags=["messages"], response_model=MessageOut)
def send_message(
    receiver_id: int = Body(...),
    content: str = Body(..., min_length=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send message to another user—a contact/interest inquiry."""
    receiver = db.query(User).filter(User.id == receiver_id).first()
    if not receiver:
        raise HTTPException(status_code=404, detail="Receiver not found")
    message = Message(
        sender_id=current_user.id, receiver_id=receiver_id, content=content
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message

@app.get("/messages/inbox", tags=["messages"], response_model=List[MessageOut])
def get_inbox(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get user's inbox messages."""
    inbox = (
        db.query(Message)
        .filter(Message.receiver_id == current_user.id)
        .order_by(Message.sent_at.desc())
        .all()
    )
    return inbox

@app.get("/messages/flagged", tags=["admin"], response_model=List[MessageOut])
def get_flagged_messages(
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.ADMIN])),
):
    """Get flagged messages. Admin only."""
    flagged = db.query(Message).filter(Message.flagged.is_(True)).all()
    return flagged

# --- Admin Analytics/Moderation Endpoints ---
@app.get("/admin/analytics", tags=["admin"])
def get_analytics(
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.ADMIN])),
):
    """Get admin dashboard analytics summary (simple count stats)."""
    users_count = db.query(User).count()
    pets_count = db.query(Pet).count()
    adoptions = db.query(Pet).filter(Pet.available.is_(False)).count()
    return {
        "users_count": users_count,
        "pets_count": pets_count,
        "adoptions": adoptions,
    }

@app.post("/admin/flag_content", tags=["admin"])
def flag_content(
    type: str = Body(...),  # e.g., 'pet', 'message', 'user'
    item_id: int = Body(...),
    flagged_reason: str = Body(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Flag a pet/listing/message for admin review."""
    flagged = FlaggedContent(
        type=type, item_id=item_id, flagged_reason=flagged_reason, flagged_by=current_user.id
    )
    db.add(flagged)
    db.commit()
    return {"msg": "Flag submitted"}

@app.get("/admin/flagged", tags=["admin"])
def flagged_content(
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.ADMIN])),
):
    """Get flagged content (all types)."""
    flagged_items = db.query(FlaggedContent).filter(FlaggedContent.resolved.is_(False)).all()
    return jsonable_encoder(flagged_items)

# Placeholder endpoint for Google OAuth setup (MVP: stub only)
@app.get("/auth/google_oauth", tags=["auth"])
def setup_google_oauth():
    """Google OAuth not implemented in MVP; stub for frontend integration."""
    return {"msg": "Google OAuth setup endpoint; not enabled in MVP."}

# ===============================
#       INIT ON FIRST RUN
# ===============================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run at startup
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    admin_email = "admin@petconnect.local"
    if not db.query(User).filter(User.email == admin_email).first():
        admin = User(
            email=admin_email,
            hashed_password=get_password_hash("admin123"),
            full_name="Administrator",
            role=UserRole.ADMIN,
        )
        db.add(admin)
        db.commit()
    db.close()
    yield
    # Run at shutdown, nothing to do

app.router.lifespan_context = lifespan

# ===============================
#      RUN INSTRUCTIONS
# ===============================
"""
To run: 
uvicorn src.api.main:app --reload

Serving static / uploaded photos is left as an exercise;
Map integration expects storing lat/lng on pets;
Image uploads handled as dumb files - replace with cloud storage in production.
Add input validation and CAPTCHA integration for production security.
"""
