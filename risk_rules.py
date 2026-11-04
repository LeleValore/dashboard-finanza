#!/usr/bin/env python3
"""
Regole di rischio e semaforo del tutor. Tutti i limiti stanno in risk_profile.json.
Il semaforo valuta un'operazione e calcola l'importo massimo, ma è solo un avviso: la
decisione resta sempre di chi opera. Non dipende da nessuna interfaccia, così report,
terminale e futuro sito usano la stessa logica.

Uso:
    ./semaforo.sh NVDA --prezzo 180 --stop 170 --obiettivo 210
    ./semaforo.sh BTC-USD --prezzo 70000 --stop 66000 --importo 100 --orizzonte breve
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
from pathlib import Path

import pandas as pd

import market_history

BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR)
PROFILE = json.loads((BASE_DIR / "risk_profile.json").read_text(encoding="utf-8"))
HISTORY_FILE = DATA_DIR / "paper" / "history.csv"

GREEN, YELLOW, RED = "verde", "giallo", "rosso"
LIGHTS = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴"}
LOSS_PERIOD_DAYS = {"giornaliera": 1, "settimanale": 7, "mensile": 30}
TREND_DAYS = 200
VOLATILITY_DAYS = 20
MIN_STOP_IN_DAILY_MOVES = 1.5
MAX_COST_SHARE_OF_RISK = 0.40


def is_crypto(ticker: str) -> bool:
    return ticker.upper().endswith("-USD")


def round_trip_cost(ticker: str, amount: float) -> float:
    """Costo di acquisto più vendita in euro."""
    costs = PROFILE["costs"]
    return amount * costs["crypto_commission"] * 2 if is_crypto(ticker) else costs["commission_eur"] * 2


def max_amount(capital: float, entry: float, stop: float, horizon: str) -> float:
    """Importo massimo: il minore tra quello che a stop perde risk_per_trade del
    patrimonio e il peso massimo consentito per l'orizzonte."""
    stop_distance = (entry - stop) / entry
    by_risk = capital * PROFILE["risk_per_trade"] / stop_distance
    return min(by_risk, capital * PROFILE["max_position"][horizon])


def _load_close(ticker: str) -> pd.Series | None:
    path = market_history.PRICES_DIR / market_history._ticker_filename(ticker)
    if not path.exists():
        return None
    return pd.read_csv(path, index_col="Date", parse_dates=True)["Close"].dropna()


def market_warnings(ticker: str, entry: float, stop: float) -> list[str]:
    """Avvisi dal contesto di mercato, se ci sono i dati del titolo."""
    warnings = []
    close = _load_close(ticker)
    if close is None or len(close) <= TREND_DAYS:
        warnings.append(f"Non ho abbastanza storico per {ticker}: contesto di mercato non controllato.")
    else:
        if close.iloc[-1] < close.iloc[-TREND_DAYS:].mean():
            warnings.append("Il prezzo è sotto la media a 200 giorni: stai comprando contro il trend.")
        daily_move = close.pct_change().iloc[-VOLATILITY_DAYS:].std()
        stop_distance = (entry - stop) / entry
        if stop_distance < daily_move * MIN_STOP_IN_DAILY_MOVES:
            warnings.append(
                f"Lo stop è al {stop_distance:.1%} ma il titolo si muove di circa {daily_move:.1%} al giorno: "
                "rischia di scattare per il normale rumore."
            )
    vix = _load_close("^VIX")
    if vix is not None and len(vix) and vix.iloc[-1] > PROFILE["alert_vix"]:
        warnings.append(f"Il VIX è a {vix.iloc[-1]:.0f}: mercato molto nervoso, le oscillazioni sono ampie.")
    return warnings


def read_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    with HISTORY_FILE.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def current_capital() -> float:
    history = read_history()
    return float(history[-1]["value_eur"]) if history else float(PROFILE["starting_capital_eur"])


def loss_limit_breaches(history: list[dict]) -> list[str]:
    """I limiti superati sul valore del portafoglio, dal più breve al calo massimo."""
    if len(history) < 2:
        return []
    values = [(datetime.date.fromisoformat(r["date"]), float(r["value_eur"])) for r in history]
    last_date, last_value = values[-1]
    breaches = []
    for period, limit in PROFILE["loss_limits"].items():
        cutoff = last_date - datetime.timedelta(days=LOSS_PERIOD_DAYS[period])
        reference = [value for date, value in values if date <= cutoff]
        start_value = reference[-1] if reference else values[0][1]
        change = last_value / start_value - 1
        if change <= -limit:
            breaches.append(f"Perdita {period} del {abs(change):.1%}, oltre il limite del {limit:.0%}.")
    drawdown = last_value / max(value for _, value in values) - 1
    if drawdown <= -PROFILE["max_drawdown"]:
        breaches.append(f"Calo dal massimo del {abs(drawdown):.1%}, oltre il limite del {PROFILE['max_drawdown']:.0%}.")
    return breaches


def evaluate_trade(ticker: str, entry: float, stop: float, target: float | None = None, amount: float | None = None,
                   horizon: str = "medio", capital: float | None = None) -> dict:
    """Valuta un acquisto. Ritorna colore, importo massimo, perdita prevista e motivi."""
    capital = current_capital() if capital is None else capital
    if stop >= entry:
        return {"color": RED, "max_amount": 0.0, "amount": amount, "risk_eur": None, "risk_share": None,
                "reward_risk": None, "reasons": ["Lo stop deve stare sotto il prezzo di acquisto."]}

    limit = max_amount(capital, entry, stop, horizon)
    size = limit if amount is None else amount
    stop_distance = (entry - stop) / entry
    cost = round_trip_cost(ticker, size)
    risk_eur = size * stop_distance + cost
    reward_risk = (target - entry) / (entry - stop) if target is not None else None

    red = loss_limit_breaches(read_history())
    if amount is not None and amount * stop_distance > capital * PROFILE["risk_per_trade"]:
        red.append(f"A stop perderesti {amount * stop_distance:.2f} €, oltre il limite di "
                   f"{capital * PROFILE['risk_per_trade']:.2f} €.")
    if amount is not None and amount > capital * PROFILE["max_position"][horizon]:
        red.append(f"{amount:.2f} € superano il peso massimo per l'orizzonte {horizon} "
                   f"({capital * PROFILE['max_position'][horizon]:.2f} €).")

    yellow = market_warnings(ticker, entry, stop)
    if target is None:
        yellow.append("Manca l'obiettivo: senza non si può misurare se il guadagno vale il rischio.")
    elif target <= entry:
        red.append("L'obiettivo deve stare sopra il prezzo di acquisto.")
    elif reward_risk < PROFILE["min_reward_risk"]:
        yellow.append(f"Potenziale guadagno {reward_risk:.1f} volte il rischio: la soglia è {PROFILE['min_reward_risk']:.1f}.")
    if cost > size * stop_distance * MAX_COST_SHARE_OF_RISK:
        yellow.append(f"Le commissioni ({cost:.2f} €) pesano molto sul rischio dell'operazione.")

    return {
        "color": RED if red else YELLOW if yellow else GREEN,
        "max_amount": limit,
        "amount": size,
        "risk_eur": risk_eur,
        "risk_share": risk_eur / capital,
        "reward_risk": reward_risk,
        "reasons": red + yellow,
    }


def format_result(ticker: str, result: dict) -> str:
    lines = [f"{LIGHTS[result['color']]} {ticker}: {result['color'].upper()}"]
    if result["risk_eur"] is not None:
        lines.append(f"Importo massimo consigliato: {result['max_amount']:.2f} €")
        lines.append(f"Se scatta lo stop con {result['amount']:.2f} € perdi circa {result['risk_eur']:.2f} € "
                     f"({result['risk_share']:.1%} del patrimonio, commissioni incluse)")
    if result["reward_risk"] is not None:
        lines.append(f"Guadagno potenziale / rischio: {result['reward_risk']:.1f}")
    lines += [f"  • {reason}" for reason in result["reasons"]]
    lines.append("È un avviso: la decisione resta tua.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Semaforo del tutor: valuta un acquisto prima di farlo.")
    parser.add_argument("ticker", help="ticker Yahoo Finance, es. NVDA o BTC-USD")
    parser.add_argument("--prezzo", type=float, required=True, help="prezzo di acquisto")
    parser.add_argument("--stop", type=float, required=True, help="prezzo a cui uscire in perdita")
    parser.add_argument("--obiettivo", type=float, help="prezzo a cui prendere il guadagno")
    parser.add_argument("--importo", type=float, help="euro che vuoi investire")
    parser.add_argument("--orizzonte", choices=sorted(PROFILE["max_position"]), default="medio")
    args = parser.parse_args()
    result = evaluate_trade(args.ticker, args.prezzo, args.stop, args.obiettivo, args.importo, args.orizzonte)
    print(format_result(args.ticker, result))


if __name__ == "__main__":
    main()
