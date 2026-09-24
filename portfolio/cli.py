"""Command line: python -m portfolio <command>

Commands only read data, analyse and write reports. None of them trade.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

from .analytics import analyse
from .build import PROFILES, model_targets
from .config import RISK_PROFILES, SUPPORTED_BASES, ConfigError, load_config, load_targets
from .holdings import rebuild_holdings
from .prices import PriceBook, YahooSource
from .report import status_text, write_report
from .rules import evaluate
from .snapshots import save_snapshot
from .transactions import ACTIONS, COLUMNS, TransactionError, append_transaction, load_transactions, validate_row
from .valuation import value_portfolio

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def default_data_dir() -> Path:
    """Your real data in ./data if it exists, otherwise the bundled example."""
    data = Path("data")
    return data if (data / "config.yaml").exists() else EXAMPLES


def analyse_portfolio(data_dir: Path, as_of=None, offline: bool = False):
    cfg = load_config(data_dir / "config.yaml")
    targets = load_targets(data_dir / "targets.yaml")
    tx = load_transactions(data_dir / "transactions.csv")
    source = None if (offline or cfg.offline) else YahooSource()
    book = PriceBook(cfg.cache_dir, cfg.base_currency, source=source, max_age_hours=cfg.max_age_hours)
    val = value_portfolio(tx, targets, cfg, book, as_of=as_of)
    an = analyse(val, targets, cfg)
    suggestions = evaluate(val, an, targets, cfg)
    return cfg, targets, val, an, suggestions


def cmd_run(args) -> int:
    cfg, targets, val, an, suggestions = analyse_portfolio(args.data_dir, args.as_of, args.offline)
    print(status_text(val, an, cfg, suggestions))
    report = write_report(args.data_dir / "reports", val, an, cfg, suggestions)
    snap = save_snapshot(args.data_dir / "history", val, an)
    print(f"\nReport:   {report}\nSnapshot: {snap}")
    return 0


def cmd_status(args) -> int:
    cfg, targets, val, an, suggestions = analyse_portfolio(args.data_dir, args.as_of, args.offline)
    print(status_text(val, an, cfg, suggestions))
    return 0


def cmd_add_transaction(args) -> int:
    path = args.data_dir / "transactions.csv"
    if args.data_dir.resolve() == EXAMPLES.resolve() and not args.allow_example:
        print("Refusing to edit the example data. Run `python -m portfolio init` first, "
              "or pass --allow-example.", file=sys.stderr)
        return 2
    cfg = load_config(args.data_dir / "config.yaml")
    row = {
        "date": args.date, "ticker": args.ticker or "", "action": args.action, "quantity": args.quantity,
        "price": args.price, "currency": args.currency or cfg.base_currency, "fees": args.fees, "note": args.note,
    }
    # Check the new row against the existing history before writing anything.
    clean = validate_row(row, line="(new)")
    tx = pd.concat([load_transactions(path), pd.DataFrame([clean])], ignore_index=True)
    rebuild_holdings(tx.sort_values("date", kind="stable"))
    append_transaction(path, row)
    print(f"Recorded in {path}: {args.action} {args.quantity:g} {args.ticker or ''} @ {args.price} {row['currency']}")
    print("(This only records a trade you already made. Nothing was sent to any broker.)")
    return 0


def cmd_init(args) -> int:
    data = args.data_dir
    if (data / "config.yaml").exists():
        print(f"{data}/config.yaml already exists; not overwriting.", file=sys.stderr)
        return 2
    data.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load((EXAMPLES / "config.yaml").read_text())
    cfg["base_currency"] = args.base
    cfg["risk_profile"] = args.profile
    cfg["prices"].update({"offline": False, "cache_dir": "prices_cache"})
    (data / "config.yaml").write_text(
        "# Your portfolio settings. Percentages are written as percent (5 = 5%).\n"
        "# See examples/config.yaml for comments on every setting.\n" + yaml.safe_dump(cfg, sort_keys=False))
    (data / "targets.yaml").write_text(
        f"# Starter targets for a '{args.profile}' profile in {args.base}. Edit freely; weights must sum to 100.\n"
        "# These funds are examples, not recommendations: check each KID/factsheet before investing.\n"
        + yaml.safe_dump(model_targets(args.profile, args.base), sort_keys=False))
    (data / "transactions.csv").write_text(",".join(COLUMNS) + "\n")
    print(f"Created {data}/config.yaml, targets.yaml and an empty transactions.csv (all gitignored).")
    return 0


def cmd_build(args) -> int:
    lo, hi = PROFILES[args.profile]["equity_range"]
    print(f"# Starter targets for a '{args.profile}' profile ({lo:.0%}-{hi:.0%} equity), base {args.base}.")
    print("# Example funds only; check each KID/factsheet. Paste into targets.yaml and edit.\n")
    print(yaml.safe_dump(model_targets(args.profile, args.base), sort_keys=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m portfolio",
                                description="Analyse your portfolio and suggest changes. Never trades.")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--data-dir", type=Path, default=None,
                        help="folder with config.yaml, targets.yaml, transactions.csv (default: ./data or examples/)")
        return sp

    for name, fn, help_ in [("run", cmd_run, "full check, write report and snapshot"),
                            ("status", cmd_status, "quick summary in the terminal")]:
        sp = common(sub.add_parser(name, help=help_))
        sp.add_argument("--as-of", default=None, help="value the portfolio as of this date (YYYY-MM-DD)")
        sp.add_argument("--offline", action="store_true", help="use cached prices only")
        sp.set_defaults(func=fn)

    sp = common(sub.add_parser("add-transaction", help="record a trade/deposit you already made"))
    sp.add_argument("--date", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    sp.add_argument("--action", required=True, choices=ACTIONS)
    sp.add_argument("--ticker", default="")
    sp.add_argument("--quantity", type=float, required=True, help="shares, or the cash amount for deposits")
    sp.add_argument("--price", type=float, default=1.0, help="price per share (1 for deposits/withdrawals)")
    sp.add_argument("--currency", default=None, help="e.g. GBP, GBp (pence), USD, EUR; default base currency")
    sp.add_argument("--fees", type=float, default=0.0)
    sp.add_argument("--note", default="")
    sp.add_argument("--allow-example", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_add_transaction)

    for name, fn, help_ in [("init", cmd_init, "create your private data/ folder with starter files"),
                            ("build", cmd_build, "print a starter targets.yaml for a risk profile")]:
        sp = common(sub.add_parser(name, help=help_))
        sp.add_argument("--profile", choices=RISK_PROFILES, default="balanced")
        sp.add_argument("--base", choices=SUPPORTED_BASES, default="GBP")
        sp.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_dir is None:
        args.data_dir = Path("data") if args.command == "init" else default_data_dir()
    try:
        return args.func(args)
    except (ConfigError, TransactionError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
