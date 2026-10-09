"""
Minimal Redis Cloud REST API client: read a Pro database, update its size or
throughput, and wait for the asynchronous task + provisioning to complete.

API lifecycle (https://redis.io/docs/latest/operate/rc/api/get-started/process-lifecycle/):
  PUT /subscriptions/{sid}/databases/{dbid}  -> returns {"taskId": ...}
  GET /tasks/{taskId}  -> status: received | processing-in-progress |
                                   processing-completed | processing-error
  GET /subscriptions/{sid}/databases/{dbid}  -> status: pending | active | error ...
Only one task per account is *processed* at a time; provisioning may take minutes.
Rate limit: 400 requests/min per account key.
"""
import logging
import time
from typing import Any, Dict, Optional

import requests

import config

log = logging.getLogger(__name__)

TASK_DONE = "processing-completed"
TASK_FAILED = "processing-error"
DB_ACTIVE = "active"
DB_ERROR = "error"


class RedisCloudAPIError(RuntimeError):
    pass


class RedisCloudAPI:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": config.API_ACCOUNT_KEY,
            "x-api-secret-key": config.API_SECRET_KEY,
        })
        self._base = config.API_BASE_URL.rstrip("/")

    # ---- low level -------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = f"{self._base}{path}"
        resp = self._session.request(method, url, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise RedisCloudAPIError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}

    # ---- database --------------------------------------------------------
    def get_database(self, subscription_id: int, database_id: int) -> Dict[str, Any]:
        return self._request("GET", f"/subscriptions/{subscription_id}/databases/{database_id}")

    def update_database(
        self,
        subscription_id: int,
        database_id: int,
        dataset_size_gb: Optional[float] = None,
        throughput_ops_sec: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Issue the update. Returns the task object ({"taskId": ...})."""
        body: Dict[str, Any] = {}
        if dataset_size_gb is not None:
            body["datasetSizeInGb"] = float(dataset_size_gb)
        if throughput_ops_sec is not None:
            body["throughputMeasurement"] = {"by": "operations-per-second", "value": int(throughput_ops_sec)}
        if dry_run:
            body["dryRun"] = True
        if not body:
            raise ValueError("nothing to update")
        log.info("PUT database %s/%s body=%s", subscription_id, database_id, body)
        return self._request("PUT", f"/subscriptions/{subscription_id}/databases/{database_id}", json=body)

    # ---- tasks -----------------------------------------------------------
    def get_task(self, task_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/tasks/{task_id}")

    def wait_for_task(self, task_id: str) -> Dict[str, Any]:
        """Block until the task leaves the processing phase."""
        deadline = time.time() + config.MAX_WAIT_SEC
        while True:
            task = self.get_task(task_id)
            status = task.get("status")
            log.info("task %s status=%s", task_id, status)
            if status == TASK_DONE:
                return task
            if status == TASK_FAILED:
                raise RedisCloudAPIError(f"task {task_id} failed: {task.get('response') or task.get('description')}")
            if time.time() > deadline:
                raise TimeoutError(f"task {task_id} still '{status}' after {config.MAX_WAIT_SEC}s")
            time.sleep(config.POLL_INTERVAL_SEC)

    def wait_for_database_active(self, subscription_id: int, database_id: int) -> Dict[str, Any]:
        """Block until provisioning finishes and the database is 'active' again."""
        deadline = time.time() + config.MAX_WAIT_SEC
        while True:
            db = self.get_database(subscription_id, database_id)
            status = db.get("status")
            log.info("database %s status=%s", database_id, status)
            if status == DB_ACTIVE:
                return db
            if status == DB_ERROR:
                raise RedisCloudAPIError(f"database {database_id} entered error state: {db}")
            if time.time() > deadline:
                raise TimeoutError(f"database {database_id} still '{status}' after {config.MAX_WAIT_SEC}s")
            time.sleep(config.POLL_INTERVAL_SEC)
