# claude-trading-agents

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
