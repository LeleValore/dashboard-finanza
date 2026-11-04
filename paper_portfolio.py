#!/usr/bin/env python3
"""
Portafoglio virtuale da 1.000 € che applica ogni giorno le regole del backtest con
prezzi veri e commissioni simulate, senza toccare denaro reale. Lo stato resta in
paper/: portfolio.json (posizioni), trades.csv (operazioni), history.csv (valore).

Regole: nucleo 70% e crescita 20% si rivedono una volta al mese, BTC 10% una volta a
settimana, solo se sopra la media a 200 giorni (altrimenti quella quota resta liquida).
Un'operazione parte solo se sposta più di REBALANCE_BAND del patrimonio: con importi
così piccoli la commissione fissa consumerebbe il rendimento. Si assume la possibilità
di comprare frazioni di quota, che non tutti i broker offrono.
"""

from __future__ import annotations

import csv
import datetime
import json
import os
from pathlib import Path

import pandas as pd
import yfinance as yf

import market_history

DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent)
PAPER_DIR = DATA_DIR / "paper"
STATE_FILE = PAPER_DIR / "portfolio.json"
TRADES_FILE = PAPER_DIR / "trades.csv"
HISTORY_FILE = PAPER_DIR / "history.csv"

STARTING_CAPITAL = 1000.0
COMMISSION_EUR = 1.0
CRYPTO_COMMISSION = 0.0025
REBALANCE_BAND = 0.02

CORE_WEIGHT = 0.70
GROWTH_WEIGHT = 0.20
CRYPTO_WEIGHT = 0.10
CORE_POSITIONS = {"VWCE.DE": 0.60, "EUNH.DE": 0.25, "4GLD.DE": 0.15}
CRYPTO_TICKER = "BTC-USD"
BENCHMARK = "VWCE.DE"
GROWTH_POSITIONS = 5
GROWTH_KEEP_RANK = 10
TREND_DAYS = 200


def _last_close(ticker: str) -> float | None:
    path = market_history.PRICES_DIR / market_history._ticker_filename(ticker)
    if not path.exists():
        return None
    close = pd.read_csv(path)["Close"].dropna()
    return float(close.iloc[-1]) if len(close) else None


def _above_trend(ticker: str) -> bool:
    path = market_history.PRICES_DIR / market_history._ticker_filename(ticker)
    close = pd.read_csv(path)["Close"].dropna()
    return bool(close.iloc[-1] > close.iloc[-TREND_DAYS:].mean())


def _eur_rate(currency: str) -> float | None:
    if currency == "EUR":
        return 1.0
    try:
        close = yf.Ticker(f"{currency}EUR=X").history(period="5d")["Close"].dropna()
        return float(close.iloc[-1])
    except Exception as exc:
        print(f"⚠️  cambio {currency}/EUR non disponibile ({exc})")
        return None


def _prices_eur(currencies: dict[str, str]) -> dict[str, float] | None:
    """Prezzi in euro per {ticker: valuta}, o None se ne manca anche uno solo:
    meglio saltare un giorno che valorizzare il portafoglio con dati parziali."""
    rates = {currency: _eur_rate(currency) for currency in set(currencies.values())}
    prices = {}
    for ticker, currency in currencies.items():
        close = _last_close(ticker)
        if close is None or rates[currency] is None:
            print(f"⚠️  prezzo non disponibile per {ticker}, portafoglio virtuale non aggiornato")
            return None
        prices[ticker] = close * rates[currency]
    return prices


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "started": datetime.date.today().isoformat(),
        "cash": STARTING_CAPITAL,
        "positions": {},
        "currencies": {},
        "benchmark_units": None,
        "last_monthly": None,
        "last_weekly": None,
        "commissions": 0.0,
    }


def _portfolio_value(state: dict, prices: dict[str, float]) -> float:
    return state["cash"] + sum(units * prices[t] for t, units in state["positions"].items())


def _commission(ticker: str, amount: float) -> float:
    return abs(amount) * CRYPTO_COMMISSION if ticker == CRYPTO_TICKER else COMMISSION_EUR


def _trade(state: dict, ticker: str, amount: float, price: float, today: str, reason: str) -> None:
    """Esegue una compravendita di amount euro (negativo per vendere) e la registra."""
    commission = _commission(ticker, amount)
    if amount > 0:
        amount = min(amount, state["cash"] - commission)
        if amount <= 0:
            return
    units = amount / price
    state["positions"][ticker] = state["positions"].get(ticker, 0.0) + units
    if state["positions"][ticker] <= 1e-9:
        del state["positions"][ticker]
    state["cash"] -= amount + commission
    state["commissions"] += commission
    new_file = not TRADES_FILE.exists()
    with TRADES_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(["date", "ticker", "side", "amount_eur", "price_eur", "commission_eur", "reason"])
        writer.writerow([today, ticker, "buy" if amount > 0 else "sell", round(abs(amount), 2),
                         round(price, 4), round(commission, 2), reason])


def _apply_targets(state: dict, targets: dict[str, float], managed: set[str], prices: dict[str, float],
                   today: str, reason: str) -> None:
    """Porta i titoli in managed ai pesi di targets. Prima le vendite, per avere liquidità."""
    total = _portfolio_value(state, prices)
    orders = {}
    for ticker in managed:
        current = state["positions"].get(ticker, 0.0) * prices[ticker]
        delta = targets.get(ticker, 0.0) * total - current
        closing = ticker not in targets and current > 0
        if closing or abs(delta) >= REBALANCE_BAND * total:
            orders[ticker] = -current if closing else delta
    for ticker, amount in sorted(orders.items(), key=lambda item: item[1]):
        _trade(state, ticker, amount, prices[ticker], today, reason)


def _select_growth(state: dict, screened: list[dict]) -> list[str]:
    """Tiene le posizioni ancora tra le prime GROWTH_KEEP_RANK, così un piccolo
    cambio di classifica non genera commissioni, e completa con le migliori candidate."""
    candidates = [row["ticker"] for row in screened if row["strong_growth"]]
    keep_zone = set(candidates[:GROWTH_KEEP_RANK])
    selected = [t for t in state["positions"] if t in state.get("growth", []) and t in keep_zone]
    for ticker in candidates:
        if len(selected) >= GROWTH_POSITIONS:
            break
        if ticker not in selected:
            selected.append(ticker)
    return selected


def update_portfolio(screened: list[dict]) -> dict | None:
    """Aggiornamento giornaliero. Rilanciarlo nello stesso giorno non ripete le operazioni."""
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    today = datetime.date.today()
    month, week = today.strftime("%Y-%m"), today.strftime("%G-W%V")

    state["currencies"].update({row["ticker"]: row["currency"] for row in screened if row.get("currency")})
    state["currencies"].update({ticker: "EUR" for ticker in CORE_POSITIONS})
    state["currencies"][CRYPTO_TICKER] = "USD"
    held = set(state["positions"]) | set(CORE_POSITIONS) | {CRYPTO_TICKER}
    candidates = {row["ticker"] for row in screened if row["strong_growth"]}
    prices = _prices_eur({t: state["currencies"][t] for t in held | candidates if t in state["currencies"]})
    if prices is None:
        return None

    if state["benchmark_units"] is None:
        state["benchmark_units"] = STARTING_CAPITAL / prices[BENCHMARK]

    if state["last_monthly"] != month and screened:
        growth = _select_growth(state, screened)
        previous_growth = set(state.get("growth", []))
        targets = {t: w * CORE_WEIGHT for t, w in CORE_POSITIONS.items()}
        targets.update({t: GROWTH_WEIGHT / len(growth) for t in growth})
        _apply_targets(state, targets, set(targets) | previous_growth, prices, today.isoformat(), "revisione mensile")
        state["growth"] = growth
        state["last_monthly"] = month

    if state["last_weekly"] != week:
        target = {CRYPTO_TICKER: CRYPTO_WEIGHT} if _above_trend(CRYPTO_TICKER) else {}
        _apply_targets(state, target, {CRYPTO_TICKER}, prices, today.isoformat(), "revisione settimanale BTC")
        state["last_weekly"] = week

    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    value = _portfolio_value(state, prices)
    benchmark = state["benchmark_units"] * prices[BENCHMARK]
    _record_history(today.isoformat(), value, benchmark)
    return _summary(state, prices, value, benchmark)


def _record_history(date: str, value: float, benchmark: float) -> None:
    rows = []
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open(newline="", encoding="utf-8") as handle:
            rows = [row for row in csv.DictReader(handle) if row["date"] != date]
    rows.append({"date": date, "value_eur": round(value, 2), "benchmark_eur": round(benchmark, 2)})
    with HISTORY_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "value_eur", "benchmark_eur"])
        writer.writeheader()
        writer.writerows(rows)


def _summary(state: dict, prices: dict[str, float], value: float, benchmark: float) -> dict:
    positions = [
        {"ticker": ticker, "value": units * prices[ticker], "weight": units * prices[ticker] / value}
        for ticker, units in state["positions"].items()
    ]
    trades = []
    if TRADES_FILE.exists():
        with TRADES_FILE.open(newline="", encoding="utf-8") as handle:
            trades = list(csv.DictReader(handle))[-8:]
    return {
        "started": state["started"],
        "value": value,
        "benchmark": benchmark,
        "cash": state["cash"],
        "commissions": state["commissions"],
        "positions": sorted(positions, key=lambda p: p["value"], reverse=True),
        "trades": trades[::-1],
    }
