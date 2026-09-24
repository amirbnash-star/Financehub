"""Write a dated Markdown report with charts, suggestions and cited methods."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # render to files; no display needed
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .analytics import Analytics, drawdown_series  # noqa: E402
from .config import CASH, Config  # noqa: E402
from .methods import METHODS  # noqa: E402
from .rules import ACTION, INFO, REVIEW, Suggestion  # noqa: E402
from .valuation import Valuation  # noqa: E402

DISCLAIMER = (
    "**Not financial advice.** This report is generated automatically from your own data and simple rules. "
    "It may contain errors, uses delayed or cached prices, and does not know your full financial situation or "
    "tax position. It never places trades; every decision is yours. Consider speaking to a regulated adviser."
)

# Light-mode chart colours (reference palette; see the dataviz guidance).
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
BLUE, ORANGE, RED = "#2a78d6", "#eb6834", "#e34948"


# --- formatting helpers -------------------------------------------------------

def pct(x, digits=1, signed=False) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x:+.{digits}%}" if signed else f"{x:.{digits}%}"


def money(x, base) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{base} {x:,.0f}"


def num(x, digits=2) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:,.{digits}f}"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


# --- charts -------------------------------------------------------------------

def _style(ax, title: str):
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def chart_allocation(val: Valuation, path: Path) -> None:
    t = val.table[(val.table["value"] > 0) | (val.table["target"] > 0)].iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 0.45 * len(t) + 1.2), facecolor=SURFACE)
    y = range(len(t))
    ax.barh(y, t["weight"] * 100, height=0.55, color=BLUE, label="Current")
    ax.scatter(t["target"] * 100, y, marker="|", s=260, linewidths=2.5, color=INK, label="Target", zorder=3)
    for yi, (w, tg) in zip(y, zip(t["weight"], t["target"])):
        ax.text(max(w, tg) * 100 + 0.8, yi, f"{w:.1%}", va="center", fontsize=8, color=INK_2)
    ax.set_yticks(list(y), t.index)
    ax.set_xlabel("% of portfolio", color=INK_2, fontsize=9)
    ax.grid(axis="y", visible=False)
    _style(ax, "Allocation vs target")
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_value(val: Valuation, path: Path) -> None:
    d = val.daily
    fig, ax = plt.subplots(figsize=(8, 3.6), facecolor=SURFACE)
    ax.plot(d.index, d["value"], color=BLUE, linewidth=2, label="Portfolio value")
    ax.plot(d.index, d["flow"].cumsum(), color=ORANGE, linewidth=2, label="Net money paid in")
    ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
    ax.set_ylabel(val.base, color=INK_2, fontsize=9)
    _style(ax, "Value over time")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_drawdown(an: Analytics, path: Path) -> None:
    dd = drawdown_series(an.returns) * 100
    fig, ax = plt.subplots(figsize=(8, 2.8), facecolor=SURFACE)
    ax.fill_between(dd.index, dd, 0, color=RED, alpha=0.25, linewidth=0)
    ax.plot(dd.index, dd, color=RED, linewidth=1.5)
    ax.set_ylabel("% below peak", color=INK_2, fontsize=9)
    _style(ax, "Drawdown (time-weighted)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# --- report -------------------------------------------------------------------

SEVERITY_LABEL = {ACTION: "Action", REVIEW: "Review", INFO: "Info"}


def _suggestion_md(i: int, s: Suggestion, base: str) -> str:
    parts = [f"### {i}. {s.title} ({SEVERITY_LABEL[s.severity]})", "", f"- **What:** {s.what}", f"- **Why:** {s.why}"]
    if s.trades:
        parts.append(f"- **Estimated cost:** {base} {s.cost:,.2f} (fees + spread)")
    if s.effect:
        parts += ["", md_table(["Holding", "Weight now", "Weight after"],
                               [[t, pct(b), pct(a)] for t, (b, a) in s.effect.items()])]
    if s.trades:
        parts += ["", md_table(
            ["Side", "Ticker", "Quantity", "Price", "Value", "Est. cost"],
            [[t.side.upper(), t.ticker, f"{t.quantity:g}", f"{t.price:,.2f} {t.currency}",
              money(t.value, base), f"{t.cost:,.2f}"] for t in s.trades])]
    if s.methods:
        parts += ["", "_Method: " + "; ".join(METHODS[m]["name"] for m in s.methods) + " (see Methods)._"]
    return "\n".join(parts)


def _method_findings(val: Valuation, an: Analytics, cfg: Config, suggestions: list[Suggestion]) -> dict[str, str]:
    """One sentence per method saying what it showed on this run."""
    base = cfg.base_currency
    rules = {s.rule for s in suggestions}
    breached = [s for s in suggestions if s.rule == "rebalance"]
    return {
        "average_cost": f"Cost basis of invested holdings is {money(val.table.drop(index=CASH)['cost'].sum(), base)}.",
        "prices": f"Valued {len(val.table) - 1} holdings as of {val.as_of:%Y-%m-%d}"
                  + (f"; {len(val.warnings)} data warning(s), see Data notes." if val.warnings else "; no data issues."),
        "twr": f"TWR {pct(an.twr_total, signed=True)} since inception"
               + (f" ({pct(an.twr_annual, signed=True)} a year)." if an.twr_annual is not None else "."),
        "xirr": f"Your money-weighted return is {pct(an.xirr, signed=True)} a year.",
        "volatility": f"Annualised volatility is {pct(an.volatility)}.",
        "sharpe": f"Sharpe ratio is {num(an.sharpe)} (risk-free rate {pct(cfg.risk_free_rate)}).",
        "drawdown": (f"Worst fall was {pct(an.drawdown.max_drawdown)} from {an.drawdown.peak:%Y-%m-%d} "
                     f"to {an.drawdown.trough:%Y-%m-%d}." if an.drawdown else "Not enough history."),
        "correlation": (f"{len(an.corr_pairs)} pair(s) above {cfg.corr_threshold:.2f}."
                        if not an.corr.empty else "Not enough price history."),
        "hhi": f"HHI {an.hhi:.3f} (≈{num(an.effective_n, 1)} equal positions); largest is "
               + (f"{an.largest[0]} at {pct(an.largest[1])}." if an.largest else "n/a."),
        "drift_band": ("Holdings outside their band: see the rebalance suggestion." if breached
                       else "All holdings are inside their bands."),
        "cash_first": f"Cash is {pct(val.table.loc[CASH, 'weight'])} vs max {pct(cfg.cash_max)}.",
        "period_return": ("A holding lags its peers; see suggestions." if "underperformer" in rules
                          else "No holding lags its asset-class peers beyond the threshold."),
        "risk_profile": ("Targets do not fit the profile; see suggestions." if "risk-profile" in rules
                         else f"Targets fit the '{cfg.risk_profile}' profile."),
    }


def write_report(out_dir: Path, val: Valuation, an: Analytics, cfg: Config, suggestions: list[Suggestion]) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date = f"{val.as_of:%Y-%m-%d}"
    base = cfg.base_currency
    charts = {"allocation": f"allocation_{date}.png", "value": f"value_{date}.png", "drawdown": f"drawdown_{date}.png"}
    chart_allocation(val, out_dir / charts["allocation"])
    if len(val.daily) > 1:
        chart_value(val, out_dir / charts["value"])
    if len(an.returns) > 1:
        chart_drawdown(an, out_dir / charts["drawdown"])

    order = {ACTION: 0, REVIEW: 1, INFO: 2}
    suggestions = sorted(suggestions, key=lambda s: order[s.severity])
    n_action = sum(s.severity == ACTION for s in suggestions)
    t = val.table
    L: list[str] = [f"# Portfolio report: {date}", "", f"> {DISCLAIMER}", ""]

    L += ["## Summary", ""]
    headline = (f"**{n_action} suggested action(s)** and {len(suggestions) - n_action} item(s) to review."
                if suggestions else "**No action needed.** All rules are satisfied.")
    L += [headline, "", md_table(["Measure", "Value"], [
        ["Total value", money(val.total, base)],
        ["Cash", f"{money(val.cash, base)} ({pct(t.loc[CASH, 'weight'])})"],
        ["Time-weighted return (since start)", pct(an.twr_total, signed=True)],
        ["Time-weighted return (annualised)", pct(an.twr_annual, signed=True)],
        ["Money-weighted return (XIRR, annual)", pct(an.xirr, signed=True)],
        ["Volatility (annualised)", pct(an.volatility)],
        ["Sharpe ratio", num(an.sharpe)],
        ["Max drawdown", pct(an.drawdown.max_drawdown) if an.drawdown else "n/a"],
        ["Largest position", f"{an.largest[0]} {pct(an.largest[1])}" if an.largest else "n/a"],
        ["HHI / effective positions", f"{an.hhi:.3f} / {num(an.effective_n, 1)}"],
        ["Risk profile / horizon", f"{cfg.risk_profile} / {cfg.horizon_years:g} years"],
    ]), ""]

    L += ["## Suggestions", ""]
    if suggestions:
        for i, s in enumerate(suggestions, 1):
            L += [_suggestion_md(i, s, base), ""]
    else:
        L += ["Nothing needs doing. Every holding is inside its drift band, no position is above the max weight, "
              "and cash is below its limit.", ""]

    L += ["## Holdings", "", md_table(
        ["Ticker", "Name", "Quantity", "Price", "Value", "Weight", "Target", "Drift", "Gain", "XIRR",
         f"Return {cfg.underperf_lookback_days}d"],
        [[tk, r["name"], f"{r['quantity']:,.4g}" if tk != CASH else "", f"{r['price']:,.2f} {r['currency']}" if tk != CASH else "",
          money(r["value"], base), pct(r["weight"]), pct(r["target"]), f"{r['drift'] * 100:+.1f} pp",
          pct(r["gain_pct"], signed=True) if tk != CASH else "", pct(an.holding_xirr.get(tk), signed=True) if tk != CASH else "",
          pct(an.holding_return.get(tk), signed=True) if tk != CASH else ""]
         for tk, r in t.iterrows()]), ""]

    L += ["### Asset classes", "", md_table(["Class", "Weight", "Target", "Drift"], [
        [c, pct(r["weight"]), pct(r["target"]), f"{r['drift'] * 100:+.1f} pp"] for c, r in an.class_table.iterrows()]), ""]

    L += ["## Charts", "", f"![Allocation vs target]({charts['allocation']})", ""]
    if len(val.daily) > 1:
        L += [f"![Value over time]({charts['value']})", ""]
    if len(an.returns) > 1:
        L += [f"![Drawdown]({charts['drawdown']})", ""]

    if not an.corr.empty:
        cols = list(an.corr.columns)
        L += [f"## Correlation ({cfg.corr_lookback_days} days, daily returns)", "",
              md_table([""] + cols, [[a] + [num(an.corr.loc[a, b]) for b in cols] for a in cols]), ""]

    if val.warnings:
        L += ["## Data notes", ""] + [f"- {w}" for w in dict.fromkeys(val.warnings)] + [""]

    L += ["## Methods", "", "Every figure above comes from one of these methods.", ""]
    findings = _method_findings(val, an, cfg, suggestions)
    for key, m in METHODS.items():
        L += [f"**{m['name']}**: {m['ref']}.  ",
              f"Why: {m['why']}  ", f"How: {m['how']}  ", f"Result: {findings.get(key, '')}", ""]

    L += ["---", "", f"_{DISCLAIMER}_", ""]
    path = out_dir / f"report_{date}.md"
    path.write_text("\n".join(L))
    return path


def status_text(val: Valuation, an: Analytics, cfg: Config, suggestions: list[Suggestion]) -> str:
    """Short plain-text summary for the terminal."""
    base = cfg.base_currency
    lines = [f"Portfolio as of {val.as_of:%Y-%m-%d}: {money(val.total, base)} "
             f"(cash {money(val.cash, base)}, {pct(val.table.loc[CASH, 'weight'])})",
             f"TWR {pct(an.twr_total, signed=True)}  XIRR {pct(an.xirr, signed=True)}  "
             f"vol {pct(an.volatility)}  max DD {pct(an.drawdown.max_drawdown) if an.drawdown else 'n/a'}", "",
             f"{'Ticker':<10}{'Value':>14}{'Weight':>9}{'Target':>9}{'Drift':>10}"]
    for tk, r in val.table.iterrows():
        lines.append(f"{tk:<10}{r['value']:>14,.0f}{r['weight']:>9.1%}{r['target']:>9.1%}{r['drift'] * 100:>+8.1f}pp")
    lines.append("")
    if suggestions:
        lines.append("Suggestions:")
        lines += [f"  [{SEVERITY_LABEL[s.severity]}] {s.title}: {s.what}" for s in suggestions]
    else:
        lines.append("No action needed.")
    lines += [f"  ! {w}" for w in dict.fromkeys(val.warnings)]
    lines += ["", "Not financial advice. This tool never trades; all decisions are yours."]
    return "\n".join(lines)
