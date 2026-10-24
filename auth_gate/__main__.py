import argparse
import sys

from .config import PROVIDERS, Config
from .errors import GateError
from .gate import login, logout, verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m auth_gate", description="Trading agents login gate")
    sub = parser.add_subparsers(dest="command", required=True)
    login_cmd = sub.add_parser("login", help="sign in with GitHub or Google")
    login_cmd.add_argument("provider", choices=PROVIDERS)
    sub.add_parser("logout", help="sign out and revoke the session token")
    sub.add_parser("status", help="show who is signed in")
    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
        if args.command == "login":
            identity = login(config, args.provider)
            print(f"Signed in as {identity.display} ({identity.provider})")
        elif args.command == "logout":
            print("Signed out" if logout(config) else "No active session")
        else:
            identity = verify(config)
            if identity is None:
                print("Not signed in")
                return 1
            print(f"Signed in as {identity.display} ({identity.provider})")
    except GateError as err:
        print(f"Access denied: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
