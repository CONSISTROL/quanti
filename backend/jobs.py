"""Simple in-memory background job manager for long-running backtests/tests."""
from __future__ import annotations

import io
import os
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend import runners  # noqa: E402


class LogBuffer(io.StringIO):
    """StringIO that also feeds the job's log store line by line."""

    def __init__(self, sink: "Job"):
        super().__init__()
        self._sink = sink

    def write(self, s: str):
        super().write(s)
        if s:
            self._sink.append_log(s)
        return len(s)

    def flush(self):
        super().flush()


class Job:
    def __init__(self, job_id: str, kind: str, params: dict):
        self.id = job_id
        self.kind = kind
        self.params = params or {}
        self.status = "queued"  # queued | running | success | error | canceled
        self.error: str | None = None
        self.result: Any = None
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self._logs: list[str] = []
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

    def append_log(self, text: str):
        with self._lock:
            for line in text.splitlines():
                if line.strip():
                    self._logs.append(line)
            # Keep memory bounded
            if len(self._logs) > 4000:
                del self._logs[: len(self._logs) - 4000]

    def logs(self, after: int = 0) -> dict:
        with self._lock:
            return {
                "total": len(self._logs),
                "logs": self._logs[after:],
            }

    def request_cancel(self):
        self._cancel_event.set()

    def cancelled(self) -> bool:
        return self._cancel_event.is_set()


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active_lock = threading.Lock()

    def list_jobs(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [self._public(job) for job in sorted(jobs, key=lambda j: j.created_at, reverse=True)]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(self, kind: str, params: dict | None = None) -> Job:
        # Only one running job at a time: many legacy modules write to
        # sys.stdout/sys.stderr globally, so concurrent capture would mix logs.
        if not self._active_lock.acquire(blocking=False):
            raise RuntimeError("已有任务正在运行，请等待当前任务完成后再启动新任务")

        job = Job(uuid.uuid4().hex[:12], kind, params)
        with self._lock:
            self._jobs[job.id] = job

        thread = threading.Thread(
            target=self._worker,
            args=(job,),
            name=f"job-{job.id}",
            daemon=True,
        )
        thread.start()
        return job

    def cancel(self, job_id: str):
        job = self.get(job_id)
        if job is None:
            return False
        if job.status in ("queued", "running"):
            job.request_cancel()
            job.status = "canceling"
            return True
        return False

    def _public(self, job: Job) -> dict:
        info = {
            "id": job.id,
            "kind": job.kind,
            "params": job.params,
            "status": job.status,
            "error": job.error,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "has_result": bool(job.result),
            "log_count": job.logs()["total"],
        }
        return info

    def _worker(self, job: Job):
        job.status = "running"
        job.started_at = datetime.now().isoformat(timespec="seconds")
        job.append_log(f"[{job.started_at}] 任务开始: {job.kind} {job.params}")

        old_out, old_err = sys.stdout, sys.stderr
        buffer = LogBuffer(job)
        sys.stdout = buffer
        sys.stderr = buffer
        try:
            try:
                result = self._dispatch(job)
                if job.cancelled():
                    job.status = "canceled"
                else:
                    job.status = "success"
                    job.result = result
                    job.append_log("[完成] 任务成功")
            except SystemExit as e:
                # Some legacy modules call sys.exit(n). Treat as finished with code.
                if job.cancelled():
                    job.status = "canceled"
                else:
                    job.status = "success"
                    job.append_log(f"[完成] 模块主动退出 code={e.code}")
            except Exception as e:
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
                job.append_log(f"[错误] {job.error}")
                job.append_log(traceback.format_exc())
            finally:
                job.finished_at = datetime.now().isoformat(timespec="seconds")
                job.append_log(f"[{job.finished_at}] 任务结束: {job.status}")
        finally:
            sys.stdout = old_out
            sys.stderr = old_err
            self._active_lock.release()

    def _dispatch(self, job: Job):
        kind = job.kind
        params = job.params

        # Load config once. Always use repository config.json as base.
        from quantlab.cli.main import load_config
        try:
            config = load_config()
        except SystemExit:
            config_path = os.path.join(ROOT, "config.json")
            raise FileNotFoundError(f"未找到 {config_path}，请先创建 config.json")

        # Apply transient test overrides.
        test_cfg = dict(config.get("test", {}))
        if params.get("strategy"):
            test_cfg["strategy"] = params["strategy"]
        if params.get("stock"):
            test_cfg["stock"] = str(params["stock"]).zfill(6)
        config["test"] = test_cfg
        if params.get("module"):
            config["test"]["module"] = params["module"]

        if kind == "test":
            module = params.get("module") or config["test"].get("module", "watchlist_backtest")
            import importlib
            mod = importlib.import_module(f"tests.{module}")
            rc = mod.main(config)
            # Structured test modules that return a dict are kept as result.
            if isinstance(rc, dict):
                return rc
            return {"mode": "test", "module": module, "return_code": rc}
        if kind == "watchlist":
            return runners.run_watchlist_backtest(config, params)
        if kind == "stock":
            return runners.run_stock_backtest(config, params)
        if kind == "selection":
            from backend.selection import run_stock_selection
            return run_stock_selection(config, top=params.get("top"))
        raise ValueError(f"未知任务类型: {kind}")
