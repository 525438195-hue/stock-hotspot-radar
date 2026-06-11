"""A-share sector market fetcher with manual CSV fallback handled upstream."""

from __future__ import annotations

from datetime import datetime
from multiprocessing import Process, Queue
from queue import Empty
from typing import Any

from .base import BaseFetcher


AKSHARE_TIMEOUT_SECONDS = 20


class MarketFetcher(BaseFetcher):
    name = "A股板块行情"
    source_type = "market_data"

    def safe_fetch(self) -> dict[str, Any]:
        queue: Queue = Queue()
        process = Process(target=_market_worker, args=(queue,))
        process.start()
        process.join(AKSHARE_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(2)
            return {
                "success": False,
                "status": "timeout",
                "source": self.name,
                "items": {"trade_date": "", "sectors": []},
                "warning": "AKShare 行情抓取超过 20 秒，已切换 fallback",
            }
        try:
            result = queue.get_nowait()
        except Empty:
            return {
                "success": False,
                "status": "failed",
                "source": self.name,
                "items": {"trade_date": "", "sectors": []},
                "warning": "AKShare 行情抓取未返回结果",
            }
        return result

    def fetch(self) -> Any:
        try:
            import akshare as ak  # type: ignore
        except Exception as exc:
            raise RuntimeError("缺少 akshare，无法自动读取 A股板块行情") from exc

        candidates = [
            ("stock_board_industry_name_em", {}),
            ("stock_board_concept_name_em", {}),
        ]
        errors: list[str] = []
        for function_name, kwargs in candidates:
            func = getattr(ak, function_name, None)
            if func is None:
                continue
            try:
                data = func(**kwargs)
                if data is not None and not data.empty:
                    return data
            except Exception as exc:
                errors.append(f"{function_name}: {exc}")
        raise RuntimeError("akshare 板块行情读取失败；" + "；".join(errors))

    def normalize(self, raw_data: Any) -> dict[str, Any]:
        sectors: list[dict[str, Any]] = []
        records = raw_data.to_dict("records") if hasattr(raw_data, "to_dict") else []
        for row in records:
            sector = _first_value(row, ["板块名称", "名称", "行业名称", "概念名称"])
            if not sector:
                continue
            sectors.append(
                {
                    "sector": str(sector),
                    "change_pct": _number(_first_value(row, ["涨跌幅", "涨幅", "板块涨幅"])),
                    "limit_up_count": _integer(_first_value(row, ["涨停家数", "涨停数量"])),
                    "turnover_amount_billion": _number(_first_value(row, ["成交额", "成交金额"])) / 100000000,
                    "turnover_change_pct": _number(_first_value(row, ["换手率", "放量幅度", "成交额变化"])),
                    "leading_stock": str(_first_value(row, ["领涨股票", "领涨股"]) or ""),
                    "leading_stock_change_pct": _number(_first_value(row, ["领涨股票-涨跌幅", "领涨股票涨幅"])),
                }
            )
        return {"trade_date": datetime.now().strftime("%Y-%m-%d"), "sectors": sectors}


def _first_value(row: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in row and row[name] not in {None, ""}:
            return row[name]
    return None


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).replace(",", "").replace("%", "").replace("亿元", "").replace("亿", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _integer(value: Any) -> int:
    return int(round(_number(value)))


def _market_worker(queue: Queue) -> None:
    fetcher = MarketFetcher()
    try:
        raw_data = fetcher.fetch()
        items = fetcher.normalize(raw_data)
        count = len(items.get("sectors", []))
        queue.put(
            {
                "success": True,
                "status": "success" if count else "skipped",
                "source": fetcher.name,
                "items": items,
                "warning": "" if count else "数据源返回为空",
            }
        )
    except Exception as exc:
        queue.put(
            {
                "success": False,
                "status": "failed",
                "source": fetcher.name,
                "items": {"trade_date": "", "sectors": []},
                "warning": str(exc),
            }
        )
