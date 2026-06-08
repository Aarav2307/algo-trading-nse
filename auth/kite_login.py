from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import os
from kiteconnect import KiteConnect

load_dotenv()

_TOKEN_FILE = Path(__file__).parent / "access_token.txt"


def get_kite_session() -> KiteConnect:
    api_key    = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")

    kite = KiteConnect(api_key=api_key)

    login_url = kite.login_url()
    print("Login URL:", login_url)
    print("\nOpen this URL in your browser, log in, then copy the request_token")
    print("from the redirect URL:  http://127.0.0.1:5000/?request_token=XXXXXX&...")
    print()

    request_token = input("request_token: ").strip()

    data         = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]
    kite.set_access_token(access_token)

    # Persist token so kite_fetcher.py can load it without re-authenticating
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _TOKEN_FILE.write_text(f"{access_token}\n# saved {timestamp}\n")
    print(f"\nAccess token saved to {_TOKEN_FILE}")

    return kite


if __name__ == "__main__":
    kite = get_kite_session()

    profile = kite.profile()
    print("\nLogged in as:", profile["user_name"])
    print("Email:",         profile["email"])