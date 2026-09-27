# Kickbacks AI

A referral and rewards ("kickbacks") tracker with a built-in Claude assistant.

- **Referral codes**: every member gets a code. When someone joins with it, both people get a signup bonus ($5 by default).
- **Kickbacks**: a referrer earns a share (5% by default) of every purchase their referred members make.
- **Payouts**: members can cash out their full balance once it reaches the minimum ($10 by default).
- **Leaderboard**: top earners and referrers.
- **AI assistant**: members ask questions about their earnings in plain language ("How much did Bob's purchases earn me?"). Claude answers using that member's account data.

The rules live in `RewardRules` in `kickbacks/service.py`.

## Run it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # only needed for the assistant
uvicorn kickbacks.app:app --reload
```

Open http://localhost:8000. Data is stored in SQLite (`kickbacks.db`; set `KICKBACKS_DB` to change the path).
Set `KICKBACKS_MODEL` to use a Claude model other than the default `claude-opus-5`.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/rules` | Current reward rules |
| POST | `/api/members` | Join (`name`, `email`, optional `referral_code`) |
| GET | `/api/members` / `/api/members/{id}` | Members with balance, lifetime earnings, referral count |
| GET | `/api/members/{id}/ledger` | Bonuses, kickbacks and payouts |
| POST | `/api/members/{id}/purchases` | Record a purchase (`amount_cents`), paying the referrer's kickback |
| POST | `/api/members/{id}/payouts` | Cash out the full balance |
| GET | `/api/leaderboard` | Top members by lifetime earnings |
| POST | `/api/members/{id}/assistant` | Ask the AI assistant (`question`, optional `history`) |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests stub out the Claude client, so they don't need an API key.
