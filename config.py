
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


def _env_bool(name, default=False):
	value = str(os.getenv(name, str(default))).strip().lower()
	return value in ("1", "true", "yes", "on")


def _env_required(name):
	value = os.getenv(name, "").strip()
	if not value:
		raise RuntimeError(f"Missing required environment variable: {name}")
	return value

# Deployment version identifier - update after each code push
CODE_VERSION = "84cced7-free-panel-usdt-eth"


TOKEN = _env_required("DISCORD_TOKEN")
BLOCKCYPHER_TOKEN = _env_required("BLOCKCYPHER_TOKEN")
MASTER_PRIVATE_KEY = os.getenv("MASTER_PRIVATE_KEY", "")
MASTER_ADDRESS = os.getenv("MASTER_ADDRESS", "")
LOG_CHANNEL_ID = _env_int("LOG_CHANNEL_ID", 0)
PROOF_CHANNEL_ID = _env_int("PROOF_CHANNEL_ID", 0)
CONFIRMATIONS_REQUIRED = _env_int("CONFIRMATIONS_REQUIRED", 2)
FEE_PERCENT = _env_int("FEE_PERCENT", 2)
PAYMENT_TIMEOUT_MINUTES = _env_int("PAYMENT_TIMEOUT_MINUTES", 20)
DB_NAME = os.getenv("DB_NAME", "data.db")
DB_BACKUP_DIR = os.getenv("DB_BACKUP_DIR", "db_backups")
DB_BACKUP_INTERVAL_MINUTES = _env_int("DB_BACKUP_INTERVAL_MINUTES", 30)
DB_BACKUP_RETENTION_DAYS = _env_int("DB_BACKUP_RETENTION_DAYS", 14)
DB_BACKUP_MAX_FILES = _env_int("DB_BACKUP_MAX_FILES", 300)
BACKUP_EXPORT_DIR = os.getenv("BACKUP_EXPORT_DIR", os.path.join(DB_BACKUP_DIR, "exports"))
BACKUP_EXPORT_MAX_FILES = _env_int("BACKUP_EXPORT_MAX_FILES", 120)
BACKUP_ALERT_MAX_AGE_MINUTES = _env_int("BACKUP_ALERT_MAX_AGE_MINUTES", 120)
BACKUP_STARTUP_MAX_AGE_MINUTES = _env_int("BACKUP_STARTUP_MAX_AGE_MINUTES", 0)
BACKUP_ENCRYPTION_KEY = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip().encode()
STRICT_KEY_FINGERPRINT = _env_bool("STRICT_KEY_FINGERPRINT", True)
REQUIRE_PERSISTENT_DB = _env_bool("REQUIRE_PERSISTENT_DB", False)

# BEP20 settings
BSC_RPC_URL = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
USDT_CONTRACT_ADDRESS = os.getenv("USDT_CONTRACT_ADDRESS", "0x55d398326f99059fF775485246999027B3197955")
ETH_RPC_URL = os.getenv("ETH_RPC_URL", "https://rpc.ankr.com/eth")
USDT_ETH_CONTRACT_ADDRESS = os.getenv("USDT_ETH_CONTRACT_ADDRESS", "0xdAC17F958D2ee523a2206206994597C13D831ec7")

# Encryption
ENCRYPTION_KEY = _env_required("ENCRYPTION_KEY").encode()

# Admin
ADMIN_ID = _env_int("ADMIN_ID", 0)

# Other
TICKET_CATEGORY_ID = _env_int("TICKET_CATEGORY_ID", 0)  # Category for tickets

if ADMIN_ID <= 0:
	raise RuntimeError("ADMIN_ID must be set to a valid Discord user ID")
