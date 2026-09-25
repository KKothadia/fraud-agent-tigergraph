"""
Agent configuration — thresholds, LLM settings, TigerGraph connection.

All values are overridable via environment variables (see .env.example).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = Path(os.getenv("DATASET_DIR", str(PROJECT_ROOT.parent / "TASK 4" / "HHGOA_IEEE")))
CASES_OUTPUT_DIR = PROJECT_ROOT / "benchmark" / "cases"
CASES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── LLM ────────────────────────────────────────────────────────────────────────# LLM Settings
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

# ── TigerGraph ─────────────────────────────────────────────────────────────────
TG_HOST = os.getenv("TG_HOST", "http://localhost")
TG_REST_PORT = os.getenv("TG_REST_PORT", "9000")
TG_GS_PORT = os.getenv("TG_GS_PORT", "14240")
TG_USERNAME = os.getenv("TG_USERNAME", "tigergraph")
TG_PASSWORD = os.getenv("TG_PASSWORD", "tigergraph")
TG_GRAPH_NAME = os.getenv("TG_GRAPH_NAME", "FraudGraph")
TG_SECRET = os.getenv("TG_SECRET", "")

# ── Agent thresholds ───────────────────────────────────────────────────────────
# Policy §6 — stop conditions
MAX_EVIDENCE_LOOPS = int(os.getenv("MAX_EVIDENCE_LOOPS", "3"))
CONFIDENCE_THRESHOLD_HIGH = float(os.getenv("CONFIDENCE_THRESHOLD_HIGH", "0.85"))
CONFIDENCE_THRESHOLD_LOW = float(os.getenv("CONFIDENCE_THRESHOLD_LOW", "0.15"))
MIN_INDEPENDENT_EVIDENCE = int(os.getenv("MIN_INDEPENDENT_EVIDENCE", "2"))

# Policy §3a — case vs report thresholds
CASE_CREATION_THRESHOLD = 0.30          # Open case when fraud_probability >= this
SAR_EXPOSURE_THRESHOLD = 1000.0         # FILE_REPORT when exposure > this
ESCALATION_EXPOSURE_THRESHOLD = 500.0   # ESCALATE_TO_ANALYST when uncertain and exposure > this
BLOCK_CARD_L1_THRESHOLD = 2500.0        # BLOCK_CARD → L1 if exposure ≤ this, else L2

# Pattern detection thresholds
CARD_TESTING_SMALL_TXN_LIMIT = 5.0      # Max amount for "small" test txns
CARD_TESTING_MIN_COUNT = 3              # Min test txns in window
CARD_TESTING_WINDOW_MINUTES = 60        # Window for test txns
CARD_TESTING_CLEARED_THRESHOLD = 100.0  # Purchase over this → BLOCK_CARD per R5
VELOCITY_BURST_WINDOW_HOURS = 48        # CNP burst window
VELOCITY_BURST_MIN_COUNT = 2            # Min txns in burst

# ── Data file names ────────────────────────────────────────────────────────────
TRANSACTIONS_FILE = "transactions.csv"
IDENTITY_FILE = "identity.csv"
CLOSED_CASES_FILE = "closed_cases_history.csv"
CASE_PACK_FILE = "case_pack.csv"
