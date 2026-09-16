import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .auth import create_invite, get_current_user, is_admin, redeem_invite
from .config import settings
from .db import get_db
from .mirror import mirror_loop
from .routes_attachments import router as attachments_router
from .routes_docs import router as docs_router
from .routes_linker import router as linker_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(mirror_loop())
    yield
    task.cancel()


app = FastAPI(title="Transcriber Goblin", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(docs_router)
app.include_router(attachments_router)
app.include_router(linker_router)


class InviteCreate(BaseModel):
    days_valid: int | None = 7


class RedeemBody(BaseModel):
    code: str
    name: str


@app.get("/health")
def health():
    return {"ok": True, "service": "transcriber-goblin"}


@app.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user["id"], "name": user["name"]}


@app.post("/invites")
def new_invite(body: InviteCreate, _admin=Depends(is_admin)):
    return create_invite(get_db(), body.days_valid)


@app.post("/auth/redeem")
def redeem(body: RedeemBody):
    return redeem_invite(get_db(), body.code, body.name)
