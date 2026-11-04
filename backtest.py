#!/usr/bin/env python3
"""
Backtest delle regole di portafoglio sui CSV di prices/. Le regole hanno parametri
fissi e non vengono ottimizzate sui dati. Una decisione presa alla chiusura di un
giorno si applica dal giorno dopo, quindi nessuna strategia vede il futuro. I costi
sono proporzionali a quanto si scambia; le tasse non sono incluse. Per i decenni
senza ETF UCITS si usano gli ETF americani come approssimazione.

Uso:
    python3 backtest.py
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

import market_history

DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent)
RESULTS_FILE = DATA_DIR / "backtest" / "results.csv"

CORE_WEIGHTS = {"SPY": 0.40, "EFA": 0.15, "EEM": 0.05, "IEF": 0.25, "GLD": 0.15}
EQUITIES = ["SPY", "EFA", "EEM"]
ETF_COST = 0.001
CRYPTO_COST = 0.0025
TREND_DAYS = 200
VIX_ALERT = 30
VOL_TARGET = 0.10
OUT_OF_SAMPLE_START = "2018-01-01"
FREQUENCIES = {"giornaliera": "D", "settimanale": "W-FRI", "mensile": "ME"}


def load_prices(tickers: list[str]) -> pd.DataFrame:
    series = {}
    for ticker in tickers:
        path = market_history.PRICES_DIR / market_history._ticker_filename(ticker)
        frame = pd.read_csv(path, index_col="Date", parse_dates=True)
        series[ticker] = frame["Adj Close"]
    return pd.DataFrame(series).sort_index()


def aligned_prices(tickers: list[str]) -> pd.DataFrame:
    """Prezzi sul calendario del primo ticker: mettendo un ETF prima di BTC si
    tolgono i weekend, altrimenti gli ETF avrebbero giorni fittizi a rendimento zero."""
    prices = load_prices(tickers)
    return prices.ffill().loc[prices[tickers[0]].notna()].dropna()


def periods_per_year(index: pd.DatetimeIndex) -> float:
    """Circa 252 per gli ETF e 365 per le crypto, che scambiano anche nel weekend."""
    return len(index) / ((index[-1] - index[0]).days / 365.25)


def load_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Rendimenti giornalieri con la colonna CASH, ricavata dal Treasury a 13 settimane."""
    returns = prices.pct_change().dropna()
    bills = load_prices(["^IRX"])["^IRX"].reindex(returns.index).ffill()
    returns["CASH"] = bills / 100 / periods_per_year(returns.index)
    return returns


def rebalance_dates(index: pd.DatetimeIndex, frequency: str) -> pd.DatetimeIndex:
    """L'ultimo giorno di scambi di ogni periodo."""
    if frequency == "D":
        return index
    marker = pd.Series(index, index=index)
    return pd.DatetimeIndex(marker.resample(frequency).last().dropna())


def simulate(returns: pd.DataFrame, targets: pd.DataFrame, cost: float) -> tuple[pd.Series, float]:
    """Applica i pesi di targets (una riga per data di decisione) lasciandoli
    variare con i prezzi tra una decisione e l'altra. Ritorna il valore del
    portafoglio e il turnover annuo."""
    targets = targets.reindex(columns=returns.columns, fill_value=0.0)
    weights = targets.iloc[0].to_numpy()
    start = targets.index[0]
    decisions = {date: row.to_numpy() for date, row in targets.iloc[1:].iterrows()}
    period = returns.loc[returns.index > start]
    value, values, traded = 1.0, [], 0.0
    for date, daily in zip(period.index, period.to_numpy()):
        growth = 1 + float(weights @ daily)
        value *= growth
        weights = weights * (1 + daily) / growth
        if date in decisions:
            trade = float(np.abs(decisions[date] - weights).sum()) / 2
            value *= 1 - trade * cost * 2
            traded += trade
            weights = decisions[date]
        values.append(value)
    years = len(period) / periods_per_year(period.index)
    return pd.Series(values, index=period.index), traded / years


def metrics(values: pd.Series, cash: pd.Series, turnover: float) -> dict:
    daily = values.pct_change().dropna()
    per_year = periods_per_year(values.index)
    years = len(values) / per_year
    excess = daily - cash.reindex(daily.index)
    return {
        "dal": values.index[0].date().isoformat(),
        "rendimento_annuo": values.iloc[-1] ** (1 / years) - 1,
        "volatilita": daily.std() * per_year ** 0.5,
        "sharpe": excess.mean() / daily.std() * per_year ** 0.5,
        "max_drawdown": (values / values.cummax() - 1).min(),
        "anno_peggiore": values.resample("YE").last().pct_change().min(),
        "turnover_annuo": turnover,
    }


def fixed_targets(index: pd.DatetimeIndex, weights: dict[str, float], frequency: str = "ME") -> pd.DataFrame:
    dates = rebalance_dates(index, frequency)
    return pd.DataFrame([weights] * len(dates), index=dates)


def trend_targets(prices: pd.Series, index: pd.DatetimeIndex, frequency: str, days: int = TREND_DAYS) -> pd.Series:
    """1 se il prezzo è sopra la media mobile, da usare come esposizione."""
    signal = (prices > prices.rolling(days).mean()).astype(float)
    return signal.reindex(rebalance_dates(index, frequency)).dropna()


def core_with_equity_scale(scale: pd.Series) -> pd.DataFrame:
    """Nucleo in cui la parte azionaria è moltiplicata per scale, il resto va in liquidità."""
    frame = pd.DataFrame({ticker: weight * scale if ticker in EQUITIES else weight
                          for ticker, weight in CORE_WEIGHTS.items()}, index=scale.index)
    frame["CASH"] = 1 - frame.sum(axis=1)
    return frame


def single_asset_targets(ticker: str, exposure: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({ticker: exposure, "CASH": 1 - exposure})


def core_strategies(returns: pd.DataFrame, prices: pd.DataFrame, vix: pd.Series) -> dict[str, pd.DataFrame]:
    index = returns.index
    months = rebalance_dates(index, "ME")
    realized_vol = (returns["SPY"].rolling(60).std() * periods_per_year(index) ** 0.5).reindex(months).dropna()
    core_vol = realized_vol * sum(CORE_WEIGHTS[t] for t in EQUITIES)
    return {
        "S&P 500 buy & hold": fixed_targets(index, {"SPY": 1.0}),
        "60/40": fixed_targets(index, {"SPY": 0.6, "IEF": 0.4}),
        "Nucleo": fixed_targets(index, CORE_WEIGHTS),
        "Nucleo + media 200gg": core_with_equity_scale(trend_targets(prices["SPY"], index, "ME")),
        "Nucleo + filtro VIX": core_with_equity_scale(
            (vix.reindex(months).ffill() < VIX_ALERT).astype(float) * 0.5 + 0.5
        ),
        "Nucleo + target volatilità": core_with_equity_scale((VOL_TARGET / core_vol).clip(upper=1.0)),
    }


def timing_strategies(ticker: str, returns: pd.DataFrame, prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    index = returns.index
    strategies = {f"{ticker} buy & hold": fixed_targets(index, {ticker: 1.0})}
    for label, frequency in FREQUENCIES.items():
        exposure = trend_targets(prices[ticker], index, frequency)
        strategies[f"{ticker} media 200gg, {label}"] = single_asset_targets(ticker, exposure)
    return strategies


def run_block(block: str, strategies: dict[str, pd.DataFrame], returns: pd.DataFrame, cost: float) -> list[dict]:
    """Tutte le strategie di un blocco partono dalla stessa data."""
    start = max(targets.dropna().index[0] for targets in strategies.values())
    rows = []
    for name, targets in strategies.items():
        targets = targets.dropna().loc[start:]
        values, turnover = simulate(returns, targets, cost)
        row = {"blocco": block, "strategia": name, **metrics(values, returns["CASH"], turnover)}
        recent = values.loc[OUT_OF_SAMPLE_START:]
        if values.index[0] < pd.Timestamp(OUT_OF_SAMPLE_START):
            years = len(recent) / periods_per_year(recent.index)
            row["rendimento_dal_2018"] = (recent.iloc[-1] / recent.iloc[0]) ** (1 / years) - 1
            row["max_drawdown_dal_2018"] = (recent / recent.cummax() - 1).min()
        rows.append(row)
    return rows


def run_all() -> pd.DataFrame:
    vix = load_prices(["^VIX"])["^VIX"]
    rows = []

    prices = aligned_prices(list(CORE_WEIGHTS))
    returns = load_returns(prices)
    rows += run_block("Nucleo", core_strategies(returns, prices, vix), returns, ETF_COST)

    prices = aligned_prices(["GLD"])
    returns = load_returns(prices)
    rows += run_block("Oro", timing_strategies("GLD", returns, prices), returns, ETF_COST)

    prices = aligned_prices(["BTC-USD"])
    returns = load_returns(prices)
    rows += run_block("Crypto", timing_strategies("BTC-USD", returns, prices), returns, CRYPTO_COST)

    prices = aligned_prices([*CORE_WEIGHTS, "QQQ", "BTC-USD"])
    returns = load_returns(prices)
    index = returns.index
    trend = trend_targets(prices["BTC-USD"], index, "W-FRI")
    core = {ticker: weight * 0.7 for ticker, weight in CORE_WEIGHTS.items()}
    total = pd.DataFrame({**core, "QQQ": 0.2, "BTC-USD": 0.1 * trend, "CASH": 0.1 * (1 - trend)}, index=trend.index)
    portfolios = {
        "Nucleo": fixed_targets(index, CORE_WEIGHTS),
        "70/20/10 sempre investito": fixed_targets(index, {**core, "QQQ": 0.2, "BTC-USD": 0.1}),
        "70/20/10 con filtro BTC": total,
    }
    rows += run_block("Portafoglio", portfolios, returns, ETF_COST)
    return pd.DataFrame(rows)


def main() -> None:
    results = run_all()
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(RESULTS_FILE, index=False)
    with pd.option_context("display.width", 220, "display.max_columns", None, "display.float_format", "{:.3f}".format):
        print(results.to_string(index=False))


if __name__ == "__main__":
    main()
