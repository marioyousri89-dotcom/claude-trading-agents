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

## ATS Matrix V2: Dual-Brain and the 8-bot ring

This implements the ATS Matrix V2 blueprint in paper mode. Risk limits, thresholds and fee constants come
from `config/ats_matrix.json`, which is validated at startup (a bad value stops the system before it
trades).

| Blueprint | Code |
|---|---|
| **Brain Alpha** (planner) | `trading/brains.py`: the self-adaptive learning loop, every `interval_hours` (1h). It reweights strategies by the P&L each made since the last update. Weights are bounded so nothing is switched fully off. In the engines it learns how much to trust trend vs mean-reversion traders. In the ring it allocates between the four strategies. |
| **Brain Beta** (reactive) | Times every loop against `execution_loop_target_seconds` (1.5s) and resets Jev's per-loop path budget (`max_categorical_paths`, 255). |
| **Jev** (typed-JSON gate) | `trading/jev.py`: every proposal from every bot must match a fixed contract. That means exact fields and types, finite positive values, the $100k trade cap, 0.5% slippage tolerance and $50k Polymarket depth. Only trading bots may propose. Rejections carry reason codes. |
| **Watcher** | Reads the simulated Polymarket books and token launchpad |
| **Analyst** | The 750-agent engine shards (spot markets) |
| **Risk Gate** | 5% global drawdown halt: after a breach, only exits are allowed |
| **Executor** | Sends every leg through Jev. A multi-leg trade executes only if every leg is approved. |
| **Reporter** | JSON event stream, signal heatmap, Jev desk |
| **Vault** | `trading/vault.py`: secrets only from environment variables, never logged, and locked in paper mode |
| **Crypto Predictor** | `trading/predictor.py`: scores new token pools (liquidity, holder growth, volume velocity, dev-wallet share), skips rug risk, and exits in chunks under the slippage limit |
| **Polymarket bot** | `trading/polymarket.py`: negative-risk squeeze (ε = 0.04), cross-book arbitrage (min spread $0.01) and maker-rebate hunting (0.40–0.60 band). Arbitrage must clear fees, slippage and the 3% oracle-dispute buffer. |
| **HUD** | The dashboard shows the 8-bot signal heatmap and the Jev decision desk (requests, approval rate, session P&L) |

**Capital:** the ring has $1M of paper capital. Every buy must fit in free capital, and arbitrage cost stays
locked until the market resolves (simulated as 600 ticks). P&L is realized only at resolution, and
refused trades are logged as `insufficient free capital`.

**Logs:** event files rotate at `EVENTS_MAX_MB` (default 50 MB, keeping one backup), and Docker's own
container logs are capped at 3 × 20 MB per container, so a 24/7 run can't fill the disk.

**Assumptions** (the config gives rates, not formulas; all are documented in `trading/polymarket.py`):
- The taker fee per share is `rate × p × (1 − p)`.
- Makers earn 25% of the taker fee as a rebate.
- Expected slippage is notional ÷ 2%-depth.

**Not built:**
- Live execution on Solana or Polymarket
- The GPU cluster
- Grok
- The ImGui/WASM HUD

`MODE=live` is refused, and any live path must go through the login gate. The simulated P&L shows the
plumbing works. It is **not** evidence that the strategies would make money.

## Running it

```sh
docker compose up --build        # 3 engines x 250 agents + 8-bot ring + dashboard
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
| `containers` | Python 3.12 and 3.13 images | Builds and starts all 5 containers, then checks that all 750 agents report, every shard and the ring keep ticking, the live log stream reaches all of them, and the heatmap and Jev desk are populated. Container logs are printed at the end of each job. |

GitHub's macOS and Windows runners can't run Linux containers, so container tests run on Linux. macOS
and Windows cover the same engine through the unit tests.

## Login security gate

`run_agent.py`, and any script you add with `@login_required`, must pass the login gate before it runs. Paper-mode Docker containers (`python -m trading.engine`) and the localhost-only dashboard run without the gate, because they can't place real orders. Any future live-trading path must go through the gate. You sign in with **GitHub** or **Google** using
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
- **Windows:** the session file (including one set with `TRADING_GATE_SESSION_FILE` or `XDG_CONFIG_HOME`)
  must be inside your user profile folder, because
  the Unix permission checks don't apply there.
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
