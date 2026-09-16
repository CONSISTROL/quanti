"""FastAPI application for the Quanti Web Console.

Run from repository root:
    uvicorn backend.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
FRONTEND_DIST = ROOT / "frontend" / "dist"
REPORT_RE = re.compile(r"^report_.+\.(html?|txt|json)$")
REPORTS_DIR = ROOT / "reports"

from backend import runners  # noqa: E402
from backend.intraday_t_monitor import intraday_monitor  # noqa: E402
from backend.jobs import JobManager  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the watchlist intraday-T monitor with the web service."""
    intraday_monitor.start()
    _warm_market_cache()
    try:
        yield
    finally:
        intraday_monitor.stop()


def _warm_market_cache() -> None:
    """后台预热指数K线缓存。

    「缠论看盘」概览要覆盖 7 个指数 × 3 个级别,冷启动要串行拉 20 多组数据、十几秒;
    放在启动时后台预热,用户点开页面时通常已命中缓存。取不到数据不影响服务运行。
    """
    def run():
        try:
            from concurrent.futures import ThreadPoolExecutor
            from backend.market import INDICES, PERIOD, fetch_index_df

            jobs = [(i["code"], iv) for i in INDICES for iv in PERIOD]

            def one(kv):
                try:
                    fetch_index_df(kv[0], kv[1])
                except Exception:
                    return None
                return None

            with ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(one, jobs))
        except Exception:
            pass

    try:
        threading.Thread(target=run, name="market-cache-warm", daemon=True).start()
    except Exception:
        pass


app = FastAPI(title="Quanti Web Console", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

job_manager = JobManager()


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise HTTPException(status_code=400, detail="config.json 不存在，请先创建")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 config.json 失败: {e}")


class ConfigUpdate(BaseModel):
    config: dict


class JobCreate(BaseModel):
    kind: str  # watchlist | stock | test
    module: Optional[str] = None
    stock: Optional[str] = None
    strategy: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    watchlist: Optional[list[str]] = None
    top: Optional[int] = None


class PortfolioOptimizeRequest(BaseModel):
    watchlist: Optional[list[str]] = None
    gamma: float = 2.0
    periods: int = 252


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/stock-name/{code}")
def stock_name(code: str):
    try:
        from backend.stock_name import get_stock_name
        return get_stock_name(code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/portfolio-optimize")
def portfolio_optimize(body: PortfolioOptimizeRequest):
    try:
        from backend.portfolio_optimizer import run_portfolio_optimization
        config = _load_config()
        params = {
            "watchlist": body.watchlist,
            "gamma": body.gamma,
            "periods": body.periods,
        }
        return run_portfolio_optimization(config, params)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/intraday-monitor/status")
def intraday_monitor_status():
    return intraday_monitor.status()


@app.post("/api/intraday-monitor/start")
def intraday_monitor_start():
    ok = intraday_monitor.start()
    return {"ok": ok, **intraday_monitor.status()}


@app.post("/api/intraday-monitor/stop")
def intraday_monitor_stop():
    was_running = intraday_monitor.stop()
    return {"was_running": was_running, **intraday_monitor.status()}


@app.get("/api/intraday-t/{code}")
def intraday_t(code: str, max_days: int = Query(30, ge=1, le=120)):
    try:
        from backend.intraday_t_decision import intraday_t_decision
        return intraday_t_decision(code, max_days=max_days)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



@app.get("/api/intraday-t/{code}/detail")
def intraday_t_detail(code: str, date: str):
    try:
        from backend.intraday_t_decision import intraday_day_detail
        return intraday_day_detail(code, date)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/config")
def get_config():
    return _load_config()


@app.put("/api/config")
def put_config(body: ConfigUpdate):
    cfg = body.config
    # Light validation: must be object and contain expected top-level keys? Keep lenient.
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=400, detail="配置必须是 JSON 对象")
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入 config.json 失败: {e}")
    return {"ok": True, "config": cfg}


@app.get("/api/meta")
def meta():
    cfg = _load_config()
    trading = cfg.get("trading", {})
    data = cfg.get("data", {})
    reports = list_reports()
    return {
        "strategy": trading.get("strategy", ""),
        "watchlist": cfg.get("watchlist", []),
        "source": data.get("source", ""),
        "start_date": cfg.get("backtest", {}).get("start_date", ""),
        "report_count": len(reports),
        "latest_report": reports[0] if reports else None,
        "tests": runners.list_test_modules(),
        "strategies": [
            {"name": name, "label": getattr(cls, "label", name)}
            for name, cls in _strategies().items()
        ],
        "backtest_strategies": [
            {"name": name, "label": info.get("label", name), "description": info.get("desc", "")}
            for name, info in _backtest_strategies().items()
        ],
        "data_sources": [
            {"name": name, "label": getattr(cls, "label", name)}
            for name, cls in _data_sources().items()
        ],
    }


def _strategies():
    from quantlab.strategies import STRATEGIES
    return STRATEGIES


def _backtest_strategies():
    from quantlab.backtest import STRATEGIES
    return STRATEGIES


def _data_sources():
    from quantlab.data_sources import DATA_SOURCES
    return DATA_SOURCES


@app.get("/api/kline/{code}")
def kline(code: str, strategy: Optional[str] = None, max_bars: int = Query(500, ge=100, le=5000),
          interval: str = Query("1d", pattern="^(1d|1w|1M|1m|5m|15m|30m|60m)$"),
          refresh: bool = False):
    try:
        from backend.kline import get_kline_data
        return get_kline_data(code, strategy=strategy, max_bars=max_bars,
                              interval=interval, refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/market/indices")
def market_indices():
    """可用于缠论看盘的宽基指数 + 自选股清单。"""
    from backend.market import list_indices
    return {"indices": list_indices(), "watchlist": market_watchlist()}


@app.get("/api/market/watchlist")
def market_watchlist():
    try:
        from backend.market import watchlist
        return watchlist()
    except Exception:
        return []


@app.get("/api/market/overview")
def market_overview(refresh: bool = False):
    """全部宽基指数 × 日/周/月 的缠论结构摘要与强弱对比(第106课板块强弱指标)。"""
    try:
        from backend.market import market_overview as _overview
        return _overview(refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/market/chan/{code}")
def market_chan(code: str, interval: str = Query("1d", pattern="^(1d|1w|1M)$"),
                refresh: bool = False):
    """单个标的的缠论结构分析:分型/笔/线段/中枢/背驰/三类买卖点/均线九分类。

    code 命中宽基指数注册表时按指数处理,否则按个股/ETF 走前复权通路。
    """
    try:
        from backend.market import analyze_target
        return analyze_target(code, interval=interval, refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/market/chan/{code}/simulate")
def market_chan_simulate(code: str, interval: str = Query("1d", pattern="^(1d|1w|1M)$"),
                         capital: float = Query(100000.0, gt=0), refresh: bool = False):
    """缠论买卖点驱动的交易模拟:两种仓位策略 + 买入持有基准,含交易记录与绩效。"""
    try:
        from backend.market import simulate_target
        return simulate_target(code, interval=interval, capital=capital, refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/tests")
def tests():
    return runners.list_test_modules()


@app.get("/api/strategies")
def strategies():
    return [
        {"name": name, "label": getattr(cls, "label", name)}
        for name, cls in _strategies().items()
    ]


@app.get("/api/data-sources")
def data_sources():
    return [
        {"name": name, "label": getattr(cls, "label", name)}
        for name, cls in _data_sources().items()
    ]


def list_reports():
    items = []
    if not REPORTS_DIR.is_dir():
        return []
    for p in REPORTS_DIR.glob("report_*"):
        if not p.is_file():
            continue
        if not REPORT_RE.match(p.name):
            continue
        stat = p.stat()
        items.append({
            "name": p.name,
            "path": p.name,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "url": f"/api/reports/{p.name}",
        })
    items.sort(key=lambda x: x["name"], reverse=True)
    return items


@app.get("/api/reports")
def reports():
    return list_reports()


@app.get("/api/reports/{filename}")
def get_report(filename: str):
    if not REPORT_RE.match(filename) or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = REPORTS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return FileResponse(path)


@app.delete("/api/reports/{filename}")
def delete_report(filename: str):
    if not REPORT_RE.match(filename) or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = REPORTS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    try:
        path.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    return {"ok": True}


@app.post("/api/jobs")
def create_job(body: JobCreate):
    kind = body.kind
    if kind not in ("watchlist", "stock", "test", "selection"):
        raise HTTPException(status_code=400, detail="kind 必须是 watchlist/stock/test/selection")
    params = body.model_dump(exclude_none=True)
    params.pop("kind", None)
    try:
        job = job_manager.submit(kind, params)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return job_manager._public(job)


@app.get("/api/jobs")
def jobs():
    return job_manager.list_jobs()


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job_manager._public(job)


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str, after: int = Query(0, ge=0)):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job.logs(after)


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != "success":
        raise HTTPException(status_code=409, detail=f"任务未完成，当前状态: {job.status}")
    return job.result


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    ok = job_manager.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    return {"ok": True}


# Serve built Vue frontend if available.
if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # API 404s should still be JSON, not fallback to index.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
