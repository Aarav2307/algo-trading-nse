from dotenv import load_dotenv
import os
import pyotp

load_dotenv()

# Test that all env variables loaded
api_key = os.getenv("KITE_API_KEY")
api_secret = os.getenv("KITE_API_SECRET")
user_id = os.getenv("ZERODHA_USER_ID")
password = os.getenv("ZERODHA_PASSWORD")
totp_secret = os.getenv("ZERODHA_TOTP_SECRET")

print("API Key loaded:", "YES" if api_key else "NO")
print("API Secret loaded:", "YES" if api_secret else "NO")
print("User ID loaded:", "YES" if user_id else "NO")
print("Password loaded:", "YES" if password else "NO")
print("TOTP Secret loaded:", "YES" if totp_secret else "NO")

# Test that TOTP is generating codes correctly.
# The code is MASKED: this script's whole output used to be echoed into pytest
# logs (it was collected as a test until the root conftest.py excluded it), and
# a full 6-digit code is a live, usable second factor for ~30 seconds. Showing
# the last 2 digits still lets a human confirm the generator matches their
# authenticator app, without putting a usable credential on screen or in a log.
totp = pyotp.TOTP(totp_secret)
_code = totp.now()
print("\nTOTP generation: ✓ OK  (code redacted — ends in ****%s)" % _code[-2:])
print("Do the last 2 digits match Google Authenticator? Check your phone.")