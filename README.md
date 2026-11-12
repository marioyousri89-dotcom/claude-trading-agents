# claude-trading-agents

A team of **1,400 paper-trading agents** in four departments, run by three managers, spread over several
containers, with live logs and a 3D view, behind a login gate.

## The agent team

| Department | Agents | Manager | What it does |
|---|---|---|---|
| Trend Trading | 350 | **Atlas**, Head of Trading | Momentum, moving-average cross and breakout traders |
| Reversion Trading | 350 | **Atlas** | Mean-reversion traders who fade stretched moves |
| Research | 350 | **Sage**, Head of Research | Regime analysts: they decide whether the desk trusts trend or reversion traders |
| Risk | 350 | **Sentinel**, Head of Risk | Volatility monitors who vote to shrink overall exposure |

The departments are defined in `trading/org.py`. Agents are spread across 10 symbols with varied look-back
windows. `AGENT_COUNT` splits evenly across the departments.

**Every agent has its own brain** (`trading/brain.py`) and its own workspace:
- After each tick the brain scores the agent's last call against what the simulated market did, and updates
  its trust in its own strategy.
- Trusted agents press their signal harder. Doubtful ones soften it. An agent that keeps losing **sits out
  on its own**, but keeps scoring its signal in the background until it earns its way back.
- Each brain has its own learning speed and sit-out level, and its learning rate slows with experience.
- Traders learn from their own P&L. Analysts learn from whether trends continued. Risk monitors learn from
  whether they cut size before big moves.

**Every agent is trained for its job before it starts** (`trading/academy.py`). Each department has its own
curriculum:

| Department | Courses |
|---|---|
| Research (analysts) | **Politics** (elections, tariffs, trade deals, sanctions, wars, stimulus, tax cuts), **Economy** (inflation, interest rates, jobs, GDP, unemployment, PMI, consumer data), Market regimes |
| Trend and Reversion (decision makers) | **Reading numbers** (read a chart's return, moving-average gap and slope), **Making decisions** (act only when the edge pays for the cost), **How to trade** (take a trade only when win rate x payoff beats the loss) |
| Risk | Volatility (when to cut size), Drawdowns (de-risk after a big drop or a losing streak) |

**The masters** (`trading/masters.py`): ten great traders' documented rules, each its own course and exam,
taught to the department whose style fits:

| Department | Master | Rule they learn |
|---|---|---|
| Trend | Jesse Livermore | Buy only when price breaks to a new high on heavy volume |
| Trend | Richard Dennis (Turtles) | Enter when price breaks its 20-day high or low |
| Trend | Ed Seykota | Cut losses, ride winners: hold while the trend holds and the stop isn't hit |
| Trend | Stanley Druckenmiller | Bet big only when the trend and liquidity agree |
| Reversion | George Soros | Reflexivity: fade a boom once price is far above value and buying is tiring |
| Reversion | Jim Simons | Fade a stretched price, but only if it usually snaps back fast |
| Reversion | Warren Buffett | Be greedy when others are fearful, with a margin of safety |
| Risk | Bruce Kovner | Stop where you're proven wrong, size so it risks about 1% of capital |
| Risk | Paul Tudor Jones | Take only 5:1 reward to risk, never trade long below the 200-day average |
| Risk | Ed Thorp | Never bet more than the Kelly fraction says the edge is worth |

Plus the chart methods from the classic books (`trading/methods.py`), read straight off the chart:

| Department | Master | Method they learn |
|---|---|---|
| Trend | W.D. Gann: angles | A trend is strong while price rides above the 1x1 (45-degree) line from the swing low |
| Trend | Charles Dow | An uptrend makes higher highs and higher lows |
| Trend | Nicolas Darvas | Buy the break out of the top of a tight box |
| Trend | William O'Neil | Buy leaders trading near their highs in an uptrend (CAN SLIM) |
| Trend | R.N. Elliott (Fibonacci) | Buy the pullback that retraces 38.2% to 61.8% of the last swing |
| Reversion | W.D. Gann: Square of 9 | A stretched move into a Square of 9 level tends to turn |
| Reversion | W.D. Gann: time cycles | Moves tend to turn 7, 14, 21, 30, 45 or 60 bars after a major high or low |
| Reversion | Richard Wyckoff | The spring: a dip under support that closes back above it |
| Reversion | Larry Williams | %R: buy in the bottom 20% of the range, sell in the top 20% |

Like every course, the masters are re-taught and re-examined every practice hour, and the 3D page's Academy
card shows each department's score per master.

**The masters at work.** Each agent applies the rules *as it learned them* (its own trained model, so a weak
student follows them badly) to its own practice trades:

- Trend: Livermore, the Turtles, Druckenmiller and the chart methods turn a call into a full-size position when
  their setup is on the chart; Seykota closes a loser or a broken trend and lets winners run.
- Reversion: Simons skips fades that aren't stretched enough; Soros, Buffett, Gann (Square of 9, time
  cycles), Wyckoff and Williams fade at full size when their setup is there.
- Risk: Kovner shrinks exposure until the stop risks about 1% of capital, Tudor Jones trims it below the long
  average, Thorp trims what the monitor's own record can't justify.

The Academy card lists how many times each master changed a decision. In three 1,200-tick practice runs, Trend
profit rose about 20% and Reversion losses roughly halved versus no masters; total desk P&L was about the same.

Each course is a stream of practice lessons with textbook answers, some of them deliberately wrong because
markets are noisy. Every agent learns each course with its own small model at its own speed, then sits an
exam marked against the true answer. 70% passes, with up to three rounds of lessons. About 98% graduate.

What the training changes at work:
- An agent's exam average sets its starting trust (0.75 to 1.25), so strong students start more confident.
- Trained decision makers **hold** (vote flat) on calls whose edge doesn't cover the cost of the trade.
- Analysts read a simulated **news wire** (`trading/news.py`) with what they learned in politics and economics,
  and their bullish or bearish read tilts the desk's call. The wire hints at where the simulated market is
  leaning, and is right about 70% of the time.

**The reading room: Claude reads the news and the charts** (`trading/reading_room.py`). One shared read of every
symbol, which every trader checks before a trade:
- **News:** each headline's *text* is read for which way it pushes the stock. With `ANTHROPIC_API_KEY` set,
  Claude (`claude-opus-5-5`) reads every new headline wording and gives a direction, strength and reason; each
  wording is read once and remembered. Without a key, a finance lexicon reads the words (hot inflation is
  bearish, a trade deal bullish).
- **Charts:** the price history is read on two time frames (20 and 60 bars). With a key, Claude also reads a
  compact chart of every symbol every 15 minutes (trend, support, resistance, pattern) and is blended in.
- Claude runs on a background thread, so ticks never wait on it. `READING_ROOM_CLAUDE=off` keeps Claude out;
  `READING_ROOM=off` turns the whole room and the filter off.

**The confirmation filter.** A trader opens a trade only when its own signal, the news and the chart agree. It
lets a trade ride through signal wobbles while the news and chart still back it, and gets out when they turn
clearly against it.

**Every trader reviews its own trades every 15 minutes** (`trading/review.py`). It reads its own journal of closed
trades and asks: did I lose fighting the news and chart, on weak signals, or by holding on after they turned?
Each answer tightens its own rules and is written as a lesson in its workspace. **Once a day** the weakest 20% of
each department copy the rules and trust of its best 20%, then keep learning on their own.

**Win rate now means trades won.** Each trader counts its closed trades won and lost; the 3D page shows "trades
won" per department. (The older "right" figure is the brain's per-tick score and counts flat ticks as misses.)

Practice runs, 2,400 ticks, 1,400 agents, three seeds, before vs after:

| Seed | Trend win rate | Trend P&L | Reversion win rate | Reversion P&L | Desk P&L |
|---|---|---|---|---|---|
| 42 | 42.5% → 52.0% | $101k → $110k | 52.7% → 57.7% | −$5.4k → +$4.9k | $274k → $297k |
| 7 | 42.5% → 53.3% | $179k → $194k | 51.8% → 57.0% | −$7.5k → +$8.9k | $279k → $309k |
| 99 | 43.2% → 53.5% | $204k → $211k | 50.2% → 58.5% | −$9.4k → +$14.1k | $333k → $346k |

Almost all of the gain comes from the reading room and the filter; the 15-minute reviews add a little on top in
these short runs. Win rate trades off against profit: requiring stronger agreement lifts Trend to about 56% but
earns less, so the default (`review.TREND_VIEW_NEED = 0.3`) keeps profit. These runs use the built-in readers;
Claude's reads need a key and were not part of the measurement.

**Built like Claude's agent program.** We compared the team with Anthropic's Claude Agent SDK (the agent loop
gather → act → check your work, long-term memory, a lead agent that directs helpers, hooks before every action,
a reason for every action) and added what was missing:

| Claude's agent program | What we added |
|---|---|
| Memory that outlives a session | `trading/memory.py`: every shard saves each agent's brain, rules, rule changes on trial, lessons and trade record to `memory-<shard>.json` every 5 minutes and on stop, and restores them on start. A restarted shard keeps what it learned. (`MEMORY=off` to disable.) |
| Check your own work | `trading/review.py`: every rule change a 15-minute review makes goes on trial. If the next review's win rate is more than 5 points worse, the change is undone and the agent writes down why. |
| A lead agent that directs its helpers | `trading/managers.py`: once an hour **Atlas** benches traders that won under 35% of their trades that hour, **Sage** benches analysts whose calls are mostly wrong, **Sentinel** cuts losing symbols to half size. Benched agents keep practising; no manager benches more than 20% of a department. |
| Hooks before every action | `trading/hooks.py`: every order is checked before risk sees it: no new risk against a confident news-and-chart view, half size after two losing desk trades in a row in that symbol, half size in a symbol Sentinel flagged. Getting out is never blocked. |
| A reason for every action | Every `order` event carries `why` (what each department voted, the news, the chart, the risk size) and the checks that changed it. The desk also keeps its own trade record per symbol. |
| Claude reviewing results | `trading/coach.py`: once an hour Claude reads how each trading department's trades went (losers against the view, losers on weak signals, loss vs win size, losing symbols, what the managers did, its own last advice) and makes small bounded rule changes with a reason. On only with `ANTHROPIC_API_KEY` (`COACH=off` to disable). |

**Second round of upgrades:**
- **Permanent disk** (`render.yaml`): `/events` is a 1 GB Render disk (about $0.25 a month), so the agents'
  memory, JARVIS's passphrase and the logs survive every update. If the folder isn't writable the app falls back
  to `/tmp/events` and keeps running (`cloud.writable_dir`).
- **The coach is a real agent loop**: it gets the hour's summary, then uses tools (`symbol_trades`, `traders`,
  `views`) to look into what it wants before it calls `give_advice` (at most 6 rounds an hour).
- **The coach's advice is on trial**: a department whose win rate falls more than 5 points the hour after the
  advice gets the change undone, and the coach is told in its next report.
- **On screen**: the 3D page has an "Agent program" card (memory, kept/undone review changes, order checks,
  what the managers did, the coach's summary and trial). JARVIS can answer "what does the coach say?" or
  "do they remember?".

Measured on the simulated market (400 agents, 7,200 ticks, seeds 1 to 3, built-in readers, no Claude): trader
win rate 53.2% → 54.2%, desk profit about the same (−0.7%). A full block on Sentinel's losing symbols was
tried first and cost about 6% of profit, so it halves size instead. Memory and the coach can't show up in one
short run: memory pays off across restarts, the coach needs an API key.

**Paper trading on live prices.** `MARKET=live` swaps the simulated market for real prices (Coinbase for BTC
and ETH, Yahoo Finance for stocks), polled every 15 s (`LIVE_POLL_SECONDS`). Orders still go to the paper broker;
real money stays locked. The cloud app runs live by default (`render.yaml`); set `MARKET=sim` to go back.
The simulated news wire is off on live prices (it hints at the simulator's hidden drift), so live views come
from the charts until a real news feed is added.

**Training never stops (practice hours).** Every practice hour (3,600 s of ticks, `PRACTICE_HOUR_TICKS`):
- **Every strategy trader (Trend and Reversion) makes at least 20 practice trades.** The desk keeps each trader
  on pace; one that falls behind takes its signal instead of holding, and a benched trader keeps practising
  on its own virtual book (scored by its brain, never sent to the desk).
- **Every agent is upgraded:** a refresher round in each of its courses and a re-sit of its exams (one retake
  allowed). Benched agents who pass get another chance to trade.
- The engine logs an `hour` event with each department's practice trades (min, average, how many fell short)
  and exam scores before and after the upgrade. The 3D page shows the hour's progress on the Academy card.

The managers also report their departments' training ("336/350 graduated, exam avg 85%"). Training takes
about half a second for 1,400 agents at start-up.

The managers read their departments every 5 ticks and report a call ("favour trend", "choppy market",
"run 96% of normal size"). A coordinator (`trading/desk.py`) combines the votes into one order per symbol.
The hard limits in `trading/risk.py` have the final say: order size, position size, gross exposure, and a
drawdown limit that halts new risk.

**Paper mode only.** The market is simulated and fills are fake. No real orders are ever sent.

## 3D cluster view and agent slider

Open **http://127.0.0.1:8080/3d**:
- Each department is a cluster with one dot per agent. Green means buy, trending or full size. Red means sell,
  choppy or shrink. Grey means warming up or sitting out.
- The managers float above their departments.
- Pulses run along the pipeline as it works: market feed, departments, desk, risk gate, Jev gate, paper broker.
- Drag to rotate and scroll to zoom.
- The side panel shows each manager's call and each department's brains (average trust, share of calls right,
  how many are sitting out), the **Academy** card (each department's courses, exam scores and graduates) and
  the latest news headlines.
- There are TradingView (lightweight-charts) charts for every symbol's price and the session P&L.
- **The slider sets the agents per department, from 350 to 2,500 (up to 10,000 agents in all).** The dashboard
  saves the setting to `control.json` in the shared events volume. Every engine shard resizes within about
  10 ticks without restarting, and agents that stay keep their brains.

On one CPU a tick takes about 35 ms with 1,400 agents and about 105 ms with 10,000, well inside the 1.5 s
loop target.

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
| **Analyst** | The 1,400-agent engine shards (spot markets) |
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

## Whole Matrix desk (paper mode)

`trading/matrix.py` fuses four strategies into one decision, on a simulated cross-asset market
(Gold, AUDUSD, Nasdaq, Bitcoin, Oil, USDCAD) with calm and stormy periods and trending and ranging phases.

| Layer | What it does |
|---|---|
| **Multi-timeframe confluence** | EMA(8) vs EMA(21) on 1-minute, 15-minute, 1-hour and daily bars. A trend trade needs all four to agree. |
| **Regime switching (ML)** | k-means clustering (`trading/regimes.py`) on volatility and trend-efficiency features labels each asset low or high volatility, and trending or ranging. Trend trades run only in trending regimes, high volatility halves size, and new pair trades pause when a leg is in a high-volatility trend. |
| **Cross-asset stat arb** | Pairs Gold/AUDUSD, Nasdaq/Bitcoin and Oil/USDCAD. A rolling one-day hedge ratio and spread z-score: enter at 2, exit at 0.5, stop at 4. Both legs trade together, and negative hedge ratios are supported. |
| **Confluence filter and news exits** | Strong contrary news suppresses a trade and exits an open one; mild contrary news shrinks it. **The news feed isn't connected yet** (it needs network access and API keys), so the live desk uses a neutral feed. The filter is fully tested with a stub feed. |

Orders are sized to order-book depth (slippage within tolerance), then pass the risk limits and Jev.
Brain Alpha re-weights trend vs arbitrage. The desk warms up on 21 simulated days at start (a few seconds)
so the daily timeframe has data. One tick is one simulated minute.

## Run it in the cloud, with an app icon on your phone

`render.yaml` deploys the whole system as one always-on service on [Render](https://render.com)
(Starter plan, about $7/month). `python -m trading.cloud` runs the 3 engine shards and the 8-bot ring
under a supervisor that restarts crashes, and serves the dashboard with password login.

**Deploy (one time):**
1. Sign up at render.com with your GitHub account.
2. Click **New → Blueprint**, pick this repository and click **Apply**. Render builds the Dockerfile and
   gives you a `https://ats-matrix-….onrender.com` address.
3. Open the service's **Environment** tab and copy `DASHBOARD_PASSWORD`. Render generated it; it isn't
   in the repo.

After that, every merge to `main` redeploys automatically.

**Add the app icon:**
- **iPhone (Safari):** open your Render address, sign in, tap **Share → Add to Home Screen**.
- **Android (Chrome):** open the address, sign in, tap **⋮ → Install app** (or **Add to Home screen**).

The icon opens the dashboard full-screen like an app. You stay signed in for 30 days (`SESSION_DAYS`).
Changing `DASHBOARD_PASSWORD` or `SESSION_SECRET` in Render signs every device out.

**Security:**
- The dashboard refuses to start on a public address without a password of at least 12 characters.
- Failed logins are limited to 5 per client per 15 minutes.
- Sessions are signed, `HttpOnly`, `Secure` cookies.
- Every page sends a strict Content-Security-Policy.
- Local Docker Compose runs without a login only because its port is bound to `127.0.0.1`.
- POSTs sent by a browser from another site are refused (Origin check), and JSON APIs need a JSON body.
- Every page sends `Permissions-Policy` (microphone for this site only; no camera, location or payments)
  and cross-origin isolation headers.
- JARVIS needs a second passphrase and rate limits questions (20 a minute) and wrong passphrases.

## JARVIS: your voice assistant with a hologram

Open **/jarvis** (signed in). JARVIS is a hologram of a woman made of light, standing on a projector, who
listens, talks back and knows the whole application.

- **Voice in and out.** Tap **Listen** and talk. With "only when I say Jarvis" ticked she ignores everything
  that doesn't start with her name, so a TV or other people in the room don't set her off. Replies are read
  aloud in a female voice and her lips move while she speaks. You can also type. Voice input works in Chrome,
  Edge and Safari (the browser's own speech recognition).
- **She knows the app.** Departments, Atlas, Sage and Sentinel, every agent's brain, the academy, the ten
  masters, practice hours, the desk, the ring, the Whole Matrix desk, the platforms and the safety rules
  (`trading/jarvis.py`, `handbook()`), plus a live snapshot of the same reports the dashboard shows. Ask
  "how is the desk doing?", "what does Atlas say?", "tell me about Risk", "what does Livermore teach?".
- **Two brains.** Set `ANTHROPIC_API_KEY` and she answers with Claude (`JARVIS_MODEL`, default
  `claude-opus-5-5`, with the server-side refusal fallback on). Without a key she answers from her built-in
  handbook and the live numbers, with no network calls. If Claude is unreachable she falls back on her own.
- **She answers only you.** First the dashboard password, then your passphrase (at least 8 characters), typed
  or spoken. On your first visit to /jarvis you choose it right there (it is stored hashed with PBKDF2 next to
  the events, never in plain text). Set `JARVIS_PASSPHRASE` in Render instead if you want it fixed across
  redeploys: Render's disk is wiped on each deploy, so without it she asks you to choose again after an update. That gives a separate signed cookie for 12 hours (`JARVIS_UNLOCK_HOURS`).
  Five wrong passphrases lock her for 15 minutes. On the internet she answers nothing until a passphrase is
  set, and only someone signed in with the dashboard password can set it, once. **Lock** puts her to sleep again. (A browser can't reliably recognise a voice, so the passphrase is the
  owner check.)
- **She can't trade.** She only reads reports. She can't place orders, change settings or see secrets.

**Switch her on:** sign in, open /jarvis and choose your passphrase. Optional: in Render, service →
**Environment** → add `JARVIS_PASSPHRASE` (fixed passphrase) and/or `ANTHROPIC_API_KEY` (Claude brain) → **Save**.

## Real platforms (paper mode, real money locked)

`trading/platforms.py` has connectors for the platforms we'll trade on. Open **/api/platforms** or run
`python -m trading.platforms` to see each one's mode and which keys are missing.

| Platform | Markets | Prices from | Modes | Keys (environment only) |
|---|---|---|---|---|
| Polymarket | prediction markets | public CLOB order book | paper | none for prices |
| eToro | stocks, crypto, ETFs, FX | eToro Public API rates | paper | `ETORO_API_KEY`, `ETORO_USER_KEY` |
| MetaTrader 5 | FX, CFDs, indices, metals | the MT5 terminal (Windows) | paper, demo | `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` |
| moomoo | US and HK stocks, options | the OpenD gateway | paper, demo | `MOOMOO_HOST`, `MOOMOO_PORT` |

- **paper** (the default): real prices from the platform, fills on our own paper book. Nothing is sent to the
  platform's order endpoint.
- **demo** (`MT5_MODE=demo`, `MOOMOO_MODE=demo`): orders go to the broker's own practice account. MetaTrader
  checks the login really is a demo account and refuses otherwise; moomoo is hard-coded to its `SIMULATE`
  account.
- **Real money is locked.** `LIVE_TRADING_ENABLED = False` in code, `place_order(live=True)` always refuses,
  and `<PLATFORM>_MODE=live` falls back to paper. Turning real money on needs a reviewed code change after 1 to 2
  weeks of paper trading on live prices, not just a setting.
- Keys live only in environment variables, are held by the Vault, and are never shown: status shows key
  *names* only. A test fails the build if anything that looks like a real key is committed.

## Running it

```sh
docker compose up --build        # 3 engines (1,400 agents) + 8-bot ring + Whole Matrix + dashboard
docker compose logs -f           # live logs from every container
open http://127.0.0.1:8080       # live dashboard (bound to localhost only)
open http://127.0.0.1:8080/3d    # 3D cluster view, charts and the agent slider
docker compose down -v
```

Settings (environment variables): `AGENT_COUNT` (default 1400), `TICK_SECONDS` (0.5), `TICKS` (0 = run
forever) and `PYTHON_VERSION` (base image, default 3.13).

Without Docker, run a single process with all agents: `python -m trading.engine`. Run the gated
version with `python run_agent.py`.

## Matrix build (CI)

`.github/workflows/tests.yml` runs on every pull request and every push to `main`:

| Job | Matrix | What it checks |
|---|---|---|
| `test` | Linux, macOS, Windows × Python 3.10–3.14 (15 jobs) | Unit tests, including a full 1,400-agent run, agent brains, the slider, and 3-shard dashboard tests |
| `cloud` | The single-container image Render runs | Refuses to start without a password, then runs all 1,400 agents and the ring in one container and checks login, data, health check, manifest and app icon |
| `containers` | Python 3.12 and 3.13 images | Builds and starts all 5 containers, then checks that all 1,400 agents report, every shard and the ring keep ticking, the live log stream reaches all of them, the heatmap and Jev desk are populated, and the departments, managers and 3D view are served. Container logs are printed at the end of each job. |

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
