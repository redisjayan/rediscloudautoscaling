"""
Scale a Redis Cloud Pro database to a named profile (PEAK / OFF_PEAK).

Usage (one-shot, e.g. from cron / EventBridge / Kubernetes CronJob):
    python scaler.py PEAK
    python scaler.py OFF_PEAK
    python scaler.py PEAK --dry-run

The long-running scheduler in scheduler.py calls scale_to_profile() directly.
"""
import argparse
import logging
import sys
import time
from typing import Dict

import config
from redis_cloud_api import DB_ACTIVE, RedisCloudAPI, RedisCloudAPIError

log = logging.getLogger("scaler")


def _current_shape(db: Dict) -> Dict:
    tp = db.get("throughputMeasurement") or {}
    return {
        "dataset_size_gb": float(db.get("datasetSizeInGb") or 0),
        "throughput_ops_sec": int(tp.get("value") or 0),
        "memory_used_gb": float(db.get("memoryUsedInMb") or 0) / 1024.0,
        "status": db.get("status"),
    }


def _health_check() -> None:
    """Optional PING over the private endpoint after scaling."""
    if not config.REDIS_URL:
        log.info("REDIS_URL not set; skipping health check")
        return
    try:
        import redis  # redis-py
    except ImportError:
        log.warning("redis-py not installed; skipping health check")
        return
    client = redis.Redis.from_url(config.REDIS_URL, socket_timeout=config.HEALTH_CHECK_TIMEOUT_SEC)
    t0 = time.perf_counter()
    assert client.ping(), "PING returned falsy"
    log.info("health check OK: PING in %.2f ms", (time.perf_counter() - t0) * 1000)


def scale_to_profile(profile_name: str, dry_run: bool = config.DRY_RUN) -> bool:
    """
    Returns True if a change was applied (or validated in dry-run), False if skipped.
    Raises on API error, timeout, or safety-rail violation.
    """
    if profile_name not in config.PROFILES:
        raise ValueError(f"unknown profile '{profile_name}'; choose from {list(config.PROFILES)}")
    target = config.PROFILES[profile_name]
    api = RedisCloudAPI()

    db = api.get_database(config.SUBSCRIPTION_ID, config.DATABASE_ID)
    cur = _current_shape(db)
    log.info("current: %s", cur)
    log.info("target [%s]: %s", profile_name, target)

    # Rail 1: only one change at a time; wait if a previous operation is still provisioning.
    if cur["status"] != DB_ACTIVE:
        log.warning("database status is '%s', waiting for it to become active before scaling", cur["status"])
        api.wait_for_database_active(config.SUBSCRIPTION_ID, config.DATABASE_ID)

    # Rail 2: idempotency; nothing to do if already at target.
    same_size = abs(cur["dataset_size_gb"] - target["dataset_size_gb"]) < 1e-6
    same_tp = cur["throughput_ops_sec"] == target["throughput_ops_sec"]
    if same_size and same_tp:
        log.info("already at profile %s; nothing to do", profile_name)
        return False

    # Rail 3: never shrink below what is actually stored (plus headroom).
    floor_gb = cur["memory_used_gb"] * config.MIN_HEADROOM_FACTOR
    if target["dataset_size_gb"] < floor_gb:
        raise RuntimeError(
            f"refusing to scale dataset to {target['dataset_size_gb']} GB: "
            f"{cur['memory_used_gb']:.2f} GB in use, floor with headroom is {floor_gb:.2f} GB"
        )

    # Only send the dials that change.
    kwargs = {}
    if not same_size:
        kwargs["dataset_size_gb"] = target["dataset_size_gb"]
    if not same_tp:
        kwargs["throughput_ops_sec"] = target["throughput_ops_sec"]

    t_start = time.time()
    task = api.update_database(config.SUBSCRIPTION_ID, config.DATABASE_ID, dry_run=dry_run, **kwargs)
    task_id = task.get("taskId")
    log.info("submitted task %s (dry_run=%s)", task_id, dry_run)

    result = api.wait_for_task(task_id)
    if dry_run:
        # Dry-run returns validation + pricing in the task response, no provisioning.
        log.info("dry-run result: %s", result.get("response"))
        return True

    final = api.wait_for_database_active(config.SUBSCRIPTION_ID, config.DATABASE_ID)
    elapsed = time.time() - t_start
    log.info("scaled to %s in %.0f s; now %s", profile_name, elapsed, _current_shape(final))

    _health_check()
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scale a Redis Cloud Pro database to a profile.")
    parser.add_argument("profile", choices=sorted(config.PROFILES))
    parser.add_argument("--dry-run", action="store_true", help="validate and price the change without applying it")
    args = parser.parse_args(argv)

    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        scale_to_profile(args.profile, dry_run=args.dry_run or config.DRY_RUN)
        return 0
    except (RedisCloudAPIError, TimeoutError, RuntimeError, ValueError) as exc:
        log.error("scaling failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
