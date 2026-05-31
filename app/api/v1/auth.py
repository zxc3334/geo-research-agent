"""Authentication API: register, login, API key management."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
    require_user,
)
from app.models.user import ApiKey, User
from app.schemas.research import (
    ApiKeyCreateRequest,
    ApiKeyResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter()


@router.post("/auth/register", response_model=TokenResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user and return a JWT token."""
    # Check if email already exists
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
    )
    db.add(user)
    await db.flush()  # Get the user.id

    token = create_access_token({"sub": user.id, "email": user.email})
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with email/password and return a JWT token."""
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    token = create_access_token({"sub": user.id, "email": user.email})
    return TokenResponse(access_token=token)


@router.post("/auth/api-keys", response_model=ApiKeyResponse, status_code=201)
async def create_api_key(
    req: ApiKeyCreateRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new API key for the authenticated user."""
    raw_key = f"geo_{secrets.token_urlsafe(32)}"
    key_hash = hash_password(raw_key)

    api_key = ApiKey(
        user_id=user.id,
        key_hash=key_hash,
        name=req.name,
    )
    db.add(api_key)
    await db.flush()

    # Return the raw key ONLY this once — it cannot be recovered later.
    return ApiKeyResponse(
        key=raw_key,
        name=api_key.name,
        created_at=api_key.created_at,
    )


@router.get("/auth/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """List the user's API key names (keys are not returned)."""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user.id, ApiKey.is_active == True)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [
        ApiKeyResponse(key="****", name=k.name, created_at=k.created_at)
        for k in keys
    ]


@router.get("/auth/me")
async def get_me(user: User = Depends(require_user)):
    """Return the current user's profile."""
    return {
        "id": user.id,
        "email": user.email,
        "plan": user.plan,
        "credits": user.credits,
        "created_at": user.created_at.isoformat(),
    }
