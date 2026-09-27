"""Claude-powered assistant that answers a member's questions about their kickbacks."""

import json
import os

import anthropic

from .service import KickbacksService

MODEL = os.environ.get("KICKBACKS_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """You are the Kickbacks AI assistant inside a referral rewards app.
You help one member understand their referral earnings, how the program works, and how to earn more.

Program rules:
- Signup bonus: when someone joins with a member's referral code, both people get {signup_bonus}.
- Kickback: a referrer earns {kickback_rate} of every purchase their referred members make.
- Payouts: a member can cash out their full balance once it reaches {min_payout}.

The member's account data is provided as JSON. Base answers about their balance, history and
referrals only on that data; if something isn't in it, say so. Amounts in the data are in cents;
show them to the member as dollars. Keep replies short and friendly, and suggest concrete next
steps (for example, sharing their referral code) when useful. You cannot move money or change
the account; point the member to the app's buttons for that."""


class AssistantUnavailable(Exception):
    pass


class KickbacksAssistant:
    def __init__(self, service: KickbacksService, client: anthropic.AsyncAnthropic | None = None):
        self.service = service
        self._client = client

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    def _system(self) -> str:
        r = self.service.rules
        return SYSTEM_PROMPT.format(
            signup_bonus=f"${r.signup_bonus_cents / 100:.2f}",
            kickback_rate=f"{r.kickback_rate:.0%}",
            min_payout=f"${r.min_payout_cents / 100:.2f}",
        )

    def _account_context(self, member_id: int) -> str:
        member = self.service.get_member(member_id)
        with self.service.db.connect() as conn:
            referrals = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, name, created_at FROM members WHERE referred_by = ? ORDER BY id",
                    (member_id,),
                )
            ]
        return json.dumps(
            {
                "member": member,
                "referred_members": referrals,
                "recent_ledger": self.service.ledger(member_id)[:50],
            },
            indent=2,
        )

    async def ask(self, member_id: int, question: str, history: list[dict] | None = None) -> str:
        """Answer `question` for a member. `history` is prior [{role, content}] turns."""
        context = self._account_context(member_id)
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in (history or [])
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        messages.append(
            {
                "role": "user",
                "content": f"<account_data>\n{context}\n</account_data>\n\n{question}",
            }
        )

        try:
            response = await self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=self._system(),
                messages=messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise AssistantUnavailable("The assistant isn't configured (missing or invalid API key).") from e
        except anthropic.RateLimitError as e:
            raise AssistantUnavailable("The assistant is busy right now. Please try again shortly.") from e
        except anthropic.APIStatusError as e:
            raise AssistantUnavailable(f"The assistant request failed ({e.status_code}).") from e
        except anthropic.APIConnectionError as e:
            raise AssistantUnavailable("Couldn't reach the assistant service.") from e
        except (anthropic.AnthropicError, TypeError) as e:
            # The SDK raises TypeError when no credentials can be resolved.
            raise AssistantUnavailable("The assistant isn't configured (set ANTHROPIC_API_KEY).") from e

        if response.stop_reason == "refusal":
            return "Sorry, I can't help with that one. Try asking about your rewards or referrals."

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or "Sorry, I don't have an answer for that."
