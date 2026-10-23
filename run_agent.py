"""Gated entry point: the 750-agent engine only starts after the login gate passes."""

from auth_gate import login_required
from trading import engine


@login_required
def main() -> None:
    engine.run()


if __name__ == "__main__":
    main()
