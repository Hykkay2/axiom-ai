from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kickbacks.app import create_app
from kickbacks.assistant import KickbacksAssistant
from kickbacks.db import Database
from kickbacks.service import KickbacksService, RewardRules


class FakeMessages:
    def __init__(self, reply="You have $5.00.", stop_reason="end_turn"):
        self.reply = reply
        self.stop_reason = stop_reason
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self.reply)],
        )


@pytest.fixture
def fake_messages():
    return FakeMessages()


@pytest.fixture
def service(tmp_path):
    return KickbacksService(Database(tmp_path / "test.db"), RewardRules())


@pytest.fixture
def client(service, fake_messages):
    fake_client = SimpleNamespace(beta=SimpleNamespace(messages=fake_messages))
    return TestClient(create_app(service, KickbacksAssistant(service, client=fake_client)))


def join(client, name, email, code=None):
    res = client.post("/api/members", json={"name": name, "email": email, "referral_code": code})
    assert res.status_code == 201, res.text
    return res.json()


def test_signup_with_code_pays_both_bonuses(client):
    alice = join(client, "Alice", "alice@example.com")
    bob = join(client, "Bob", "bob@example.com", alice["referral_code"].lower())

    assert bob["referred_by"] == alice["id"]
    assert bob["balance_cents"] == 500
    alice = client.get(f"/api/members/{alice['id']}").json()
    assert alice["balance_cents"] == 500
    assert alice["referral_count"] == 1


def test_purchase_pays_kickback_to_referrer(client):
    alice = join(client, "Alice", "alice@example.com")
    bob = join(client, "Bob", "bob@example.com", alice["referral_code"])

    res = client.post(f"/api/members/{bob['id']}/purchases", json={"amount_cents": 12345})
    assert res.status_code == 201
    assert res.json()["kickback_cents"] == 617  # 5% of $123.45, rounded
    assert res.json()["kickback_to"] == alice["id"]

    ledger = client.get(f"/api/members/{alice['id']}/ledger").json()
    assert [e["kind"] for e in ledger] == ["kickback", "signup_bonus"]


def test_purchase_without_referrer_pays_nothing(client):
    carol = join(client, "Carol", "carol@example.com")
    res = client.post(f"/api/members/{carol['id']}/purchases", json={"amount_cents": 1000})
    assert res.json()["kickback_cents"] == 0
    assert client.get(f"/api/members/{carol['id']}/ledger").json() == []


def test_rejects_bad_input(client):
    alice = join(client, "Alice", "alice@example.com")
    assert client.post("/api/members", json={"name": "A2", "email": "ALICE@example.com"}).status_code == 400
    assert client.post("/api/members", json={"name": "X", "email": "x@example.com", "referral_code": "NOPE"}).status_code == 404
    assert client.post(f"/api/members/{alice['id']}/purchases", json={"amount_cents": 0}).status_code == 422
    assert client.get("/api/members/999").status_code == 404


def test_payout_requires_minimum_then_zeroes_balance(client):
    alice = join(client, "Alice", "alice@example.com")
    bob = join(client, "Bob", "bob@example.com", alice["referral_code"])

    res = client.post(f"/api/members/{alice['id']}/payouts")
    assert res.status_code == 400
    assert "minimum" in res.json()["detail"]

    client.post(f"/api/members/{bob['id']}/purchases", json={"amount_cents": 10000})
    res = client.post(f"/api/members/{alice['id']}/payouts")
    assert res.status_code == 201
    assert res.json()["paid_out_cents"] == 1000
    alice = client.get(f"/api/members/{alice['id']}").json()
    assert alice["balance_cents"] == 0
    assert alice["lifetime_earned_cents"] == 1000


def test_leaderboard_orders_by_earnings(client):
    alice = join(client, "Alice", "alice@example.com")
    bob = join(client, "Bob", "bob@example.com", alice["referral_code"])
    join(client, "Carol", "carol@example.com", alice["referral_code"])
    join(client, "Dan", "dan@example.com", bob["referral_code"])

    board = client.get("/api/leaderboard").json()
    assert board[0]["name"] == "Alice"
    assert board[0]["referrals"] == 2
    assert board[0]["lifetime_earned_cents"] == 1000


def test_assistant_sends_account_context(client, fake_messages):
    alice = join(client, "Alice", "alice@example.com")
    res = client.post(
        f"/api/members/{alice['id']}/assistant",
        json={
            "question": "What's my code?",
            "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        },
    )
    assert res.status_code == 200
    assert res.json() == {"answer": "You have $5.00."}

    call = fake_messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["fallbacks"] == "default"
    assert [m["role"] for m in call["messages"]] == ["user", "assistant", "user"]
    assert alice["referral_code"] in call["messages"][-1]["content"]
    assert "5%" in call["system"]


def test_assistant_handles_refusal(client, fake_messages):
    fake_messages.stop_reason = "refusal"
    alice = join(client, "Alice", "alice@example.com")
    res = client.post(f"/api/members/{alice['id']}/assistant", json={"question": "hmm"})
    assert "can't help" in res.json()["answer"]


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Kickbacks AI" in res.text
