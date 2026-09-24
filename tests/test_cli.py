import shutil
from pathlib import Path

import pandas as pd
import yaml

from portfolio.cli import EXAMPLES, main
from portfolio.transactions import load_transactions


def copy_example(tmp_path) -> Path:
    d = tmp_path / "data"
    shutil.copytree(EXAMPLES, d, ignore=shutil.ignore_patterns("reports", "history", "*.py"))
    return d


def test_run_writes_report_and_snapshot(tmp_path, capsys):
    d = copy_example(tmp_path)
    assert main(["run", "--data-dir", str(d), "--as-of", "2026-09-24"]) == 0
    out = capsys.readouterr().out
    assert "Rebalance toward targets" in out and "never trades" in out
    report = (d / "reports" / "report_2026-09-24.md").read_text()
    for section in ["## Summary", "## Suggestions", "## Methods", "Not financial advice", "Sharpe, 'The Sharpe Ratio'"]:
        assert section in report
    assert (d / "reports" / "allocation_2026-09-24.png").exists()
    assert (d / "history" / "holdings_2026-09-24.csv").exists()
    # Running twice on the same day keeps one summary row.
    main(["run", "--data-dir", str(d), "--as-of", "2026-09-24"])
    assert len(pd.read_csv(d / "history" / "summary.csv")) == 1


def test_quiet_when_nothing_to_do(tmp_path, capsys):
    d = tmp_path / "data"
    assert main(["init", "--data-dir", str(d)]) == 0
    cfg = yaml.safe_load((d / "config.yaml").read_text())
    cfg["prices"]["offline"] = True
    cfg["prices"]["cache_dir"] = str(EXAMPLES / "prices_cache")
    (d / "config.yaml").write_text(yaml.safe_dump(cfg))
    (d / "targets.yaml").write_text(yaml.safe_dump({"holdings": {
        "VWRL.L": {"weight": 98, "asset_class": "equity"}, "CASH": {"weight": 2, "asset_class": "cash"}}}))
    cfg["risk_profile"] = "aggressive"
    cfg["max_position_pct"] = 100
    (d / "config.yaml").write_text(yaml.safe_dump(cfg))
    for args in (["--action", "deposit", "--quantity", "10000", "--date", "2026-01-05"],
                 ["--action", "buy", "--ticker", "VWRL.L", "--quantity", "80", "--price", "120", "--date", "2026-01-05"]):
        assert main(["add-transaction", "--data-dir", str(d), *args]) == 0
    capsys.readouterr()
    main(["status", "--data-dir", str(d), "--as-of", "2026-01-05"])
    out = capsys.readouterr().out
    assert "No action needed." in out


def test_add_transaction_rejects_oversell_and_leaves_file_unchanged(tmp_path, capsys):
    d = copy_example(tmp_path)
    before = (d / "transactions.csv").read_text()
    rc = main(["add-transaction", "--data-dir", str(d), "--action", "sell", "--ticker", "IGLT.L",
               "--quantity", "100000", "--price", "1000", "--currency", "GBp"])
    assert rc == 1
    assert "only" in capsys.readouterr().err
    assert (d / "transactions.csv").read_text() == before


def test_add_transaction_refuses_example_dir(capsys):
    rc = main(["add-transaction", "--data-dir", str(EXAMPLES), "--action", "deposit", "--quantity", "1"])
    assert rc == 2


def test_init_and_build(tmp_path, capsys):
    d = tmp_path / "mine"
    assert main(["init", "--data-dir", str(d), "--profile", "growth", "--base", "EUR"]) == 0
    assert load_transactions(d / "transactions.csv").empty
    assert yaml.safe_load((d / "config.yaml").read_text())["base_currency"] == "EUR"
    assert main(["init", "--data-dir", str(d)]) == 2  # never overwrites
    main(["build", "--profile", "cautious"])
    assert "IGLT.L" in capsys.readouterr().out
