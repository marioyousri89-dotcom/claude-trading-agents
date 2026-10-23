"""Example entry point: nothing below runs until the login gate passes."""

from auth_gate import login_required


@login_required
def main() -> None:
    # Load broker API keys and start the trading agents here.
    print("Trading agents starting...")


if __name__ == "__main__":
    main()
