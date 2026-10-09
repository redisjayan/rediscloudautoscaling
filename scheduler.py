"""
Long-running scheduler (standard library only): scale up at 08:00 and down at
17:00, Monday to Friday, in the configured timezone. Saturday and Sunday are
untouched.

    python scheduler.py            # run forever
    python scheduler.py --list     # print the next 5 runs and exit

Run it as a systemd service, a container, or a Kubernetes Deployment with one
replica. If you prefer cron / EventBridge, skip this file and call
`python scaler.py PEAK` / `python scaler.py OFF_PEAK` directly.
"""
import argparse
import logging
import sys
import time
from datetime import datetime, time as dtime, timedelta
from typing import Callable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import config
from scaler import scale_to_profile

log = logging.getLogger("scheduler")

# (local time of day, profile to apply). Weekday filter comes from config.
SLOTS: List[Tuple[dtime, str]] = [
    (dtime(config.SCALE_UP_TIME["hour"], config.SCALE_UP_TIME["minute"]), "PEAK"),
    (dtime(config.SCALE_DOWN_TIME["hour"], config.SCALE_DOWN_TIME["minute"]), "OFF_PEAK"),
]


def next_run(after: datetime) -> Tuple[datetime, str]:
    """First (fire_time, profile) strictly after `after`, on an allowed weekday."""
    tz = after.tzinfo
    day = after.date()
    for _ in range(14):  # at most two weeks ahead covers any weekday/weekend pattern
        if day.weekday() in config.SCALE_DAYS:
            candidates = [(datetime.combine(day, t, tzinfo=tz), p) for t, p in SLOTS]
            due = sorted(c for c in candidates if c[0] > after)
            if due:
                return due[0]
        day += timedelta(days=1)
    raise RuntimeError("no scheduled slot found; check SCALE_DAYS")


def upcoming(n: int, now: Optional[datetime] = None) -> List[Tuple[datetime, str]]:
    now = now or datetime.now(ZoneInfo(config.TIMEZONE))
    out: List[Tuple[datetime, str]] = []
    cursor = now
    for _ in range(n):
        fire, profile = next_run(cursor)
        out.append((fire, profile))
        cursor = fire
    return out


def run_forever(
    action: Callable[[str], object] = scale_to_profile,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(ZoneInfo(config.TIMEZONE)),
) -> None:
    """Loop: sleep until the next slot, run it, repeat. Never exits on a job error."""
    while True:
        now = clock()
        fire, profile = next_run(now)
        wait = (fire - now).total_seconds()
        log.info("next: %s at %s (in %.0f min)", profile, fire.isoformat(), wait / 60)
        # Sleep in short chunks so clock changes / long suspends are noticed.
        while wait > 0:
            sleep(min(wait, 60))
            now = clock()
            wait = (fire - now).total_seconds()
        if (now - fire).total_seconds() > config.MISFIRE_GRACE_SEC:
            log.warning("missed %s slot at %s by more than grace period; skipping", profile, fire)
            continue
        log.info("firing: scale to %s", profile)
        try:
            action(profile)
        except Exception as exc:  # keep the scheduler alive; the next slot will try again
            log.exception("scale to %s failed: %s", profile, exc)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="print the next 5 runs and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.list:
        for fire, profile in upcoming(5):
            print(f"{fire:%a %Y-%m-%d %H:%M %Z}  ->  {profile}")
        return 0

    log.info("starting scheduler tz=%s up=%s down=%s days=%s dry_run=%s",
             config.TIMEZONE, config.SCALE_UP_TIME, config.SCALE_DOWN_TIME, sorted(config.SCALE_DAYS), config.DRY_RUN)
    try:
        run_forever()
    except KeyboardInterrupt:
        log.info("scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
