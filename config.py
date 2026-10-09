"""
All configurable parameters for the Redis Cloud scheduled scaler.

Secrets are read from environment variables so they never live in source control.
Everything else can be edited here directly.
"""
import os

# ---------------------------------------------------------------------------
# Redis Cloud REST API
# Docs: https://redis.io/docs/latest/operate/rc/api/
# Keys: Account Key  -> header x-api-key        (Account settings > API keys)
#       Secret Key   -> header x-api-secret-key (per-user key)
# ---------------------------------------------------------------------------
API_BASE_URL = os.getenv("REDIS_CLOUD_API_BASE_URL", "https://api.redislabs.com/v1")
API_ACCOUNT_KEY = os.environ["REDIS_CLOUD_API_KEY"]           # required
API_SECRET_KEY = os.environ["REDIS_CLOUD_API_SECRET_KEY"]     # required

SUBSCRIPTION_ID = int(os.environ["REDIS_CLOUD_SUBSCRIPTION_ID"])   # e.g. 1234567
DATABASE_ID = int(os.environ["REDIS_CLOUD_DATABASE_ID"])           # e.g. 12345678

# ---------------------------------------------------------------------------
# Database connection, used only for the post-scale health check (PING).
# Use the PRIVATE endpoint over the VPC peering, e.g.
#   redis://default:<password>@redis-12345.internal.c123.ap-south-1-mz.ec2.cloud.rlrcp.com:12345
#   rediss://...  if TLS is enabled on the database
# Leave empty to skip the health check.
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "")
HEALTH_CHECK_TIMEOUT_SEC = 5

# ---------------------------------------------------------------------------
# Capacity profiles. Redis Cloud Pro has two dials: dataset size (GB) and
# throughput (ops/sec). Set both for each profile.
#   - Scale-up happens at SCALE_UP_TIME on weekdays  -> PEAK profile
#   - Scale-down happens at SCALE_DOWN_TIME on weekdays -> OFF_PEAK profile
#   - Weekends are left untouched (the database stays in whatever profile
#     Friday evening left it in, i.e. OFF_PEAK).
# ---------------------------------------------------------------------------
PROFILES = {
    "PEAK": {
        "dataset_size_gb": 20.0,
        "throughput_ops_sec": 100_000,
    },
    "OFF_PEAK": {
        "dataset_size_gb": 10.0,
        "throughput_ops_sec": 25_000,
    },
}

# ---------------------------------------------------------------------------
# Schedule, interpreted in TIMEZONE.
# ---------------------------------------------------------------------------
TIMEZONE = os.getenv("SCALER_TIMEZONE", "Asia/Kolkata")
SCALE_UP_TIME = {"hour": 8, "minute": 0}     # -> PEAK
SCALE_DOWN_TIME = {"hour": 17, "minute": 0}  # -> OFF_PEAK
SCALE_DAYS = {0, 1, 2, 3, 4}                 # Monday=0 ... Friday=4; weekends excluded
# If the scheduler was down and wakes up within this many seconds of a slot,
# it still runs that slot; later than this it skips to the next one.
MISFIRE_GRACE_SEC = 3600

# ---------------------------------------------------------------------------
# Safety rails
# ---------------------------------------------------------------------------
# Never scale the dataset below (memory currently used * this factor).
# Redis Cloud rejects a dataset size smaller than the data it holds; this keeps
# a margin so a Friday scale-down does not land on a full database.
MIN_HEADROOM_FACTOR = 1.25

# How long to wait for the Redis Cloud task + provisioning to finish.
# Scaling is data-dependent (minutes typically; longer for large moves).
POLL_INTERVAL_SEC = 20
MAX_WAIT_SEC = 60 * 60   # 1 hour

# When True, the API is called with dryRun=true: Redis Cloud validates the
# request and returns the cost delta without changing anything.
DRY_RUN = os.getenv("SCALER_DRY_RUN", "false").lower() == "true"

LOG_LEVEL = os.getenv("SCALER_LOG_LEVEL", "INFO")
