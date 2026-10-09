# Redis Cloud Pro scheduled scaler (sample)

Scales a Redis Cloud Pro database on a schedule using the Redis Cloud REST API:

| When (Asia/Kolkata) | Action | Profile |
|---|---|---|
| Mon–Fri 08:00 | scale up | `PEAK` (e.g. 20 GB / 100k ops/sec) |
| Mon–Fri 17:00 | scale down | `OFF_PEAK` (e.g. 10 GB / 25k ops/sec) |
| Sat, Sun | nothing | stays in whatever Friday left (OFF_PEAK) |

All tunables live in `config.py`; secrets come from environment variables.

## Files

- `config.py` – API endpoint, keys, subscription/database IDs, `REDIS_URL`, profiles, cron schedule, safety rails.
- `redis_cloud_api.py` – thin REST client: get database, update size/throughput, poll task and provisioning status.
- `scaler.py` – one-shot `python scaler.py PEAK|OFF_PEAK [--dry-run]`; idempotent, with safety rails and a post-scale `PING`.
- `scheduler.py` – long-running, standard-library-only loop that fires the two weekday slots (no extra dependency).

Requires Python 3.9+ (uses `zoneinfo`).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export REDIS_CLOUD_API_KEY="<account key>"            # x-api-key
export REDIS_CLOUD_API_SECRET_KEY="<user secret key>" # x-api-secret-key
export REDIS_CLOUD_SUBSCRIPTION_ID=1234567
export REDIS_CLOUD_DATABASE_ID=12345678
export REDIS_URL="redis://default:<password>@<private-endpoint>:<port>"   # optional, for the health check
```

Enable the API and create keys in the Redis Cloud console first
(Account settings > API keys). The user whose secret key you use needs Owner or Manager.

## Run

```bash
# Validate and price a change without applying it
python scaler.py PEAK --dry-run

# Apply once (good for cron / EventBridge / Kubernetes CronJob)
python scaler.py PEAK
python scaler.py OFF_PEAK

# Or run the built-in scheduler
python scheduler.py --list   # show the next 5 runs
python scheduler.py          # run forever
```

Equivalent crontab if you prefer cron over `scheduler.py` (server clock in IST):

```
0 8  * * 1-5  cd /opt/scaler && .venv/bin/python scaler.py PEAK     >> scaler.log 2>&1
0 17 * * 1-5  cd /opt/scaler && .venv/bin/python scaler.py OFF_PEAK >> scaler.log 2>&1
```

## How it behaves

1. Reads the database; if it is not `active` (a previous change still provisioning) it waits.
2. Skips if the database is already at the target profile.
3. Refuses a scale-down that would set dataset size below `memory used × MIN_HEADROOM_FACTOR`.
4. Sends only the dials that change (`datasetSizeInGb`, `throughputMeasurement`).
5. Polls the task until `processing-completed`, then polls the database until `active`.
6. Logs wall-clock time for the whole operation and runs a `PING` over `REDIS_URL`.

## Notes

- Scaling is online; the endpoint does not change. Duration is data-dependent (typically minutes; longer for large data moves). Databases on Redis 8.4+ with the default Redis hashing policy use Smooth Scaling automatically.
- Scale-down is available where Smooth Scaling is enabled for the account; if a scale-down is rejected, check eligibility with Redis.
- Only one API task per account is processed at a time; the scheduler is single-threaded and the scaler waits for the database to be `active` before issuing a change, so operations never overlap.
- API rate limit is 400 requests/min per account key; polling every 20 s is far below it.
- For metric-driven (not just scheduled) scaling, see the Redis Field Engineering autoscaler: https://github.com/redis-field-engineering/redis-cloud-autoscaler
