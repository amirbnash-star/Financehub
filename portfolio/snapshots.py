"""Save a dated snapshot of holdings and a one-line summary to history/."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

SNAPSHOT_COLUMNS = ["name", "asset_class", "quantity", "currency", "price", "price_base", "value",
                    "cost", "weight", "target", "drift"]


def save_snapshot(history_dir: Path, val, an) -> Path:
    history_dir = Path(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    date = val.as_of.strftime("%Y-%m-%d")
    path = history_dir / f"holdings_{date}.csv"
    val.table[SNAPSHOT_COLUMNS].round(6).to_csv(path, index_label="ticker")

    summary_path = history_dir / "summary.csv"
    row = {
        "date": date, "base_currency": val.base, "total_value": round(val.total, 2), "cash": round(val.cash, 2),
        "twr_total": round(an.twr_total, 6), "xirr": None if an.xirr is None else round(an.xirr, 6),
        "volatility": None if an.volatility is None else round(an.volatility, 6),
        "max_drawdown": None if an.drawdown is None else round(an.drawdown.max_drawdown, 6),
        "hhi": round(an.hhi, 6),
    }
    old = pd.read_csv(summary_path, dtype={"date": str}) if summary_path.exists() else pd.DataFrame()
    if not old.empty:
        old = old[old["date"] != date]  # one row per day; re-runs overwrite
    pd.concat([old, pd.DataFrame([row])], ignore_index=True).sort_values("date").to_csv(summary_path, index=False)
    return path
