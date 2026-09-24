"""Every analytical method used, with its reference, why it is used and how it is applied.

The report prints these in its "Methods" section, adding what each one showed on
this run. When you add a method to the code, add it here too.
"""

METHODS = {
    "average_cost": {
        "name": "Average-cost (pooled) cost basis",
        "ref": "HMRC Capital Gains Manual CG51560 (section 104 pooling)",
        "why": "Matches how UK tax treats shares and gives a single, easy-to-read cost per share.",
        "how": "Buys add shares and cost (incl. fees) to a pool; sells remove cost in proportion to shares sold.",
    },
    "prices": {
        "name": "Market data",
        "ref": "Yahoo Finance via the yfinance library (github.com/ranaroussi/yfinance)",
        "why": "Free daily closes and FX rates for most ETFs and stocks worldwide.",
        "how": "Unadjusted daily closes (dividends are recorded as cash), pence converted to pounds, "
               "FX pairs like USDGBP=X convert to the base currency; results are cached locally.",
    },
    "twr": {
        "name": "Time-weighted return (TWR)",
        "ref": "CFA Institute, GIPS Standards 2020; Bacon, Practical Portfolio Performance Measurement (2008), ch. 2",
        "why": "Measures how the investments performed regardless of when and how much money you added or withdrew.",
        "how": "Daily returns r = (V_t - F_t) / V_{t-1} - 1, with deposits/withdrawals F_t at end of day, chain-linked.",
    },
    "xirr": {
        "name": "Money-weighted return (XIRR)",
        "ref": "CFA Institute, Investment Performance Measurement (MWR/IRR); Microsoft Excel XIRR definition",
        "why": "Measures the return YOU actually earned, including the effect of your timing of deposits.",
        "how": "Solve sum CF_i / (1+r)^(days_i/365) = 0 over deposits, withdrawals and today's value (bisection).",
    },
    "volatility": {
        "name": "Annualised volatility",
        "ref": "Hull, Options, Futures and Other Derivatives, ch. 15 (square-root-of-time scaling)",
        "why": "A standard measure of how much the portfolio's value swings.",
        "how": "Standard deviation of daily TWR returns x sqrt(252).",
    },
    "sharpe": {
        "name": "Sharpe ratio",
        "ref": "Sharpe, 'The Sharpe Ratio', Journal of Portfolio Management 21(1), 1994",
        "why": "Return earned per unit of risk taken, above a risk-free rate.",
        "how": "Mean daily excess return over the risk-free rate / std of daily excess returns x sqrt(252).",
    },
    "drawdown": {
        "name": "Maximum drawdown",
        "ref": "Magdon-Ismail & Atiya, 'Maximum Drawdown', Risk Magazine, 2004",
        "why": "The worst peak-to-trough fall, the loss you would have had to sit through.",
        "how": "Computed on the TWR growth index so deposits and withdrawals do not distort it.",
    },
    "correlation": {
        "name": "Correlation of holdings",
        "ref": "Markowitz, 'Portfolio Selection', Journal of Finance 7(1), 1952; pandas DataFrame.corr (Pearson)",
        "why": "Holdings that move together add little diversification and may be redundant.",
        "how": "Pearson correlation of daily base-currency returns over the lookback window; pairs above the threshold are flagged.",
    },
    "hhi": {
        "name": "Concentration (HHI, largest position)",
        "ref": "Herfindahl (1950); Hirschman (1964); Adelman, JASA 64, 1969 (1/HHI as 'numbers equivalent')",
        "why": "Shows whether the portfolio depends on a few positions.",
        "how": "HHI = sum of squared weights of invested positions; 1/HHI = effective number of equal positions; "
               "any position above the max weight is flagged for trimming.",
    },
    "drift_band": {
        "name": "Drift-band (tolerance-band) rebalancing, 5/25 rule",
        "ref": "Jaconetti, Kinniry & Zilbering, 'Best practices for portfolio rebalancing', Vanguard, 2010; "
               "Swedroe & Kizer (2008) '5/25' rule",
        "why": "Controls risk drift while trading only when it matters, keeping costs and taxes low.",
        "how": "A holding is out of band when |weight - target| exceeds the absolute threshold or "
               "|weight - target| / target exceeds the relative threshold.",
    },
    "cash_first": {
        "name": "Cash-flow-first rebalancing",
        "ref": "Jaconetti, Kinniry & Zilbering, Vanguard 2010 (using cash flows to rebalance)",
        "why": "Investing new or spare cash in underweights costs less than selling and avoids realising gains.",
        "how": "Spare cash above the cash target is split across underweights in proportion to their shortfall; "
               "sells are suggested only for holdings still out of band; whole shares; trades under the minimum size dropped.",
    },
    "period_return": {
        "name": "Peer-relative return",
        "ref": "Simple holding-period return (Bodie, Kane & Marcus, Investments, ch. 5)",
        "why": "Spots a holding that lags others of the same asset class, a prompt to review costs or thesis.",
        "how": "Price return in base currency over the lookback, compared with the average of same-class holdings.",
    },
    "risk_profile": {
        "name": "Risk-profile equity bands",
        "ref": "Vanguard, 'Vanguard's framework for constructing globally diversified portfolios', 2015; "
               "LifeStrategy 20/40/60/80/100 model mixes",
        "why": "Checks that the target mix matches the stated risk profile and horizon.",
        "how": "Target equity share is compared with the profile's usual range; short horizons with high equity are flagged.",
    },
}
