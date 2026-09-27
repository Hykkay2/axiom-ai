"""FastAPI app exposing the Kickbacks AI API and a small web UI."""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .assistant import AssistantUnavailable, KickbacksAssistant
from .db import Database
from .service import KickbacksError, KickbacksService

STATIC_DIR = Path(__file__).parent / "static"


class MemberIn(BaseModel):
    name: str
    email: str
    referral_code: str | None = None


class PurchaseIn(BaseModel):
    amount_cents: int = Field(gt=0)


class ChatTurn(BaseModel):
    role: str
    content: str


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = []


def create_app(
    service: KickbacksService | None = None,
    assistant: KickbacksAssistant | None = None,
) -> FastAPI:
    service = service or KickbacksService(Database(os.environ.get("KICKBACKS_DB", "kickbacks.db")))
    assistant = assistant or KickbacksAssistant(service)

    app = FastAPI(title="Kickbacks AI")

    @app.exception_handler(KickbacksError)
    async def _kickbacks_error(_: Request, exc: KickbacksError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(AssistantUnavailable)
    async def _assistant_error(_: Request, exc: AssistantUnavailable):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.get("/api/rules")
    def rules():
        return service.rules.__dict__

    @app.post("/api/members", status_code=201)
    def create_member(body: MemberIn):
        return service.create_member(body.name, body.email, body.referral_code)

    @app.get("/api/members")
    def list_members():
        return service.list_members()

    @app.get("/api/members/{member_id}")
    def get_member(member_id: int):
        return service.get_member(member_id)

    @app.get("/api/members/{member_id}/ledger")
    def ledger(member_id: int):
        return service.ledger(member_id)

    @app.post("/api/members/{member_id}/purchases", status_code=201)
    def purchase(member_id: int, body: PurchaseIn):
        return service.record_purchase(member_id, body.amount_cents)

    @app.post("/api/members/{member_id}/payouts", status_code=201)
    def payout(member_id: int):
        return service.request_payout(member_id)

    @app.get("/api/leaderboard")
    def leaderboard(limit: int = 10):
        return service.leaderboard(max(1, min(limit, 100)))

    @app.post("/api/members/{member_id}/assistant")
    async def ask(member_id: int, body: AskIn):
        answer = await assistant.ask(
            member_id, body.question, [t.model_dump() for t in body.history[-20:]]
        )
        return {"answer": answer}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
