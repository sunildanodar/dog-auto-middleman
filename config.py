
import os
from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(BASE_DIR, ".env")
DOTENV_EXAMPLE_PATH = os.path.join(BASE_DIR, ".env.example")

# Prefer .env; fallback to .env.example to reduce startup issues in local setups.
if os.path.exists(DOTENV_PATH):
	load_dotenv(dotenv_path=DOTENV_PATH)
else:
	load_dotenv(dotenv_path=DOTENV_EXAMPLE_PATH)


def _env_int(name, default):
	try:
		return int(os.getenv(name, default))
	except (TypeError, ValueError):
		return default


def _env_required(name):
	value = os.getenv(name, "").strip()
	if not value:
		raise RuntimeError(f"Missing required environment variable: {name}")
	return value


TOKEN = _env_required("DISCORD_TOKEN")
BLOCKCYPHER_TOKEN = _env_required("BLOCKCYPHER_TOKEN")
MASTER_PRIVATE_KEY = os.getenv("MASTER_PRIVATE_KEY", "")
MASTER_ADDRESS = os.getenv("MASTER_ADDRESS", "")
LOG_CHANNEL_ID = _env_int("LOG_CHANNEL_ID", 0)
CONFIRMATIONS_REQUIRED = _env_int("CONFIRMATIONS_REQUIRED", 2)
FEE_PERCENT = _env_int("FEE_PERCENT", 2)
PAYMENT_TIMEOUT_MINUTES = _env_int("PAYMENT_TIMEOUT_MINUTES", 20)
DB_NAME = os.getenv("DB_NAME", "data.db")

# BEP20 settings
BSC_RPC_URL = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
USDT_CONTRACT_ADDRESS = os.getenv("USDT_CONTRACT_ADDRESS", "0x55d398326f99059fF775485246999027B3197955")

# Encryption
ENCRYPTION_KEY = _env_required("ENCRYPTION_KEY").encode()

# Admin
ADMIN_ID = _env_int("ADMIN_ID", 0)

# Other
TICKET_CATEGORY_ID = _env_int("TICKET_CATEGORY_ID", 0)  # Category for tickets

if ADMIN_ID <= 0:
	raise RuntimeError("ADMIN_ID must be set to a valid Discord user ID")
