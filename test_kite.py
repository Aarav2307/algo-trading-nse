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

# Test that TOTP is generating codes correctly
totp = pyotp.TOTP(totp_secret)
print("\nCurrent TOTP code:", totp.now())
print("Does it match Google Authenticator? Check your phone.")