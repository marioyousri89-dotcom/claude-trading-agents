# claude-trading-agents

A team of **750 paper-trading agents** running in several containers, with live logs, behind a login gate.

## The agent team

| Role | Count | What it does |
|---|---|---|
| Traders | 600 | Momentum, moving-average cross, breakout and mean-reversion strategies with varied look-back windows, spread across 10 symbols |
| Analysts | 120 | Judge each symbol's regime (trending vs choppy) and decide whether the desk trusts trend-followers or mean-reversion traders |
| Risk monitors | 30 | Watch volatility and vote to shrink overall exposure |

A coordinator (`trading/desk.py`) combines the votes into one order per symbol. The hard limits in
`trading/risk.py` have the final say: order size, position size, gross exposure, and a drawdown limit
that halts new risk. Every agent keeps a virtual P&L, which feeds a leaderboard.

**Paper mode only.** The market is simulated and fills are fake. No real orders are ever sent.

## Running it

```sh
docker compose up --build        # 3 engine containers x 250 agents + dashboard
docker compose logs -f           # live logs from every container
open http://127.0.0.1:8080       # live dashboard (bound to localhost only)
docker compose down -v
```

Settings (environment variables): `AGENT_COUNT` (default 750), `TICK_SECONDS` (0.5), `TICKS` (0 = run
forever) and `PYTHON_VERSION` (base image, default 3.13).

Without Docker, run a single process with all agents: `python -m trading.engine`. Run the gated
version with `python run_agent.py`.

## Matrix build (CI)

`.github/workflows/tests.yml` runs on every pull request and every push to `main`:

| Job | Matrix | What it checks |
|---|---|---|
| `test` | Linux, macOS, Windows × Python 3.10–3.14 (15 jobs) | Unit tests, including a full 750-agent run and a 3-shard dashboard test |
| `containers` | Python 3.12 and 3.13 images | Builds and starts the full multi-container stack, then checks that all 750 agents report, every shard keeps ticking, and the live log stream reaches every shard. Container logs are printed at the end of each job. |

GitHub's macOS and Windows runners can't run Linux containers, so container tests run on Linux. macOS
and Windows cover the same engine through the unit tests.

## Login security gate

Every script must pass the login gate before it runs. You sign in with **GitHub** or **Google** using
the OAuth *device flow*: the CLI prints a short code, you approve it in any browser, and no local web
server or redirect URL is needed. The gate depends only on the Python standard library (3.10+).

### What it enforces

- **Allowlist only.** Only the accounts listed in `TRADING_GATE_ALLOWED` get in. If the list is empty,
  every script refuses to run.
- **Checked on every run.** The token is re-verified with GitHub or Google each time a script starts,
  so revoking the app's access, or removing someone from the allowlist, locks them out immediately.
- **Sessions expire.** A session lasts 12 hours by default (`TRADING_GATE_SESSION_HOURS`). A Google
  session is capped at the token's own lifetime (about 1 hour).
- **Owner-only session file.** `~/.config/claude-trading-agents/session.json` is created with mode
  `0600`. The gate refuses it if anyone else can read it or if another user owns it.
- **Fails closed.** Network errors, unverified Google emails and bad configuration all deny access.
- **Logout revokes the token** where the provider supports it (Google).

### Setup

1. **GitHub**: create an OAuth App (Settings → Developer settings → OAuth Apps). Tick **Enable Device
   Flow** and copy the Client ID. The device flow needs no client secret.
2. **Google**: in Google Cloud Console → APIs & Services → Credentials, create an OAuth client ID of
   type **TVs and Limited Input devices**, then copy its ID and secret.
3. Copy `.env.example` to `.env`, fill it in, and load it:

   ```sh
   set -a; source .env; set +a
   ```

### Usage

```sh
python -m auth_gate login github    # or: login google
python -m auth_gate status
python -m auth_gate logout
python run_agent.py                 # prompts for sign-in if needed
```

To gate any script, add the decorator:

```python
from auth_gate import login_required

@login_required
def main(): ...
```

You can also call `require_login()` directly; it raises `GateError` when access is denied. When stdin
is not a terminal (cron, CI), it never prompts. It fails unless a valid session already exists.

### Notes

- GitHub allowlist entries match the **username**. If an allowed account is renamed, update the list,
  because someone else could register the freed name.
- On a machine you don't control, a local gate is only as strong as the OS account it runs under. Keep
  broker API keys out of the repo, and load them only after `require_login()` succeeds.

### Tests

```sh
python -m unittest discover -s tests -t .
```

CI runs this as a matrix build (`.github/workflows/tests.yml`) on Linux, macOS and Windows with
Python 3.10–3.14, for every pull request and every push to `main`.
