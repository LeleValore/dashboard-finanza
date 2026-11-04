#!/usr/bin/env python3
"""
Storico giornaliero dei prezzi per tutti i ticker seguiti, uno CSV per ticker
sotto prices/. A ogni esecuzione riscarica l'intera serie disponibile (Yahoo
rivede dividendi e split), così i modelli di previsione partono da decenni di
dati invece che dai soli giorni in cui il report è stato generato. Se un ticker
non risponde il suo CSV precedente resta intatto.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yfinance as yf

DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent)
PRICES_DIR = DATA_DIR / "prices"

PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
MAX_WORKERS = 8


def _ticker_filename(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", ticker).strip("_") + ".csv"


def _save_one_ticker(name: str, ticker: str) -> bool:
    try:
        hist = yf.Ticker(ticker).history(period="max", auto_adjust=False)
        if hist.empty:
            print(f"⚠️  {name} ({ticker}): storico vuoto, tengo il CSV precedente")
            return False
        hist = hist[[c for c in PRICE_COLUMNS if c in hist.columns]].round(6)
        hist.index = hist.index.strftime("%Y-%m-%d")
        hist.index.name = "Date"
        hist.to_csv(PRICES_DIR / _ticker_filename(ticker))
        return True
    except Exception as exc:
        print(f"⚠️  {name} ({ticker}): storico non scaricato ({exc}), tengo il CSV precedente")
        return False


def update_price_history(mapping: dict[str, str]) -> int:
    """Aggiorna i CSV per {nome: ticker}. Ritorna quanti ticker sono andati a buon fine."""
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_save_one_ticker, name, ticker) for name, ticker in mapping.items()]
        return sum(1 for f in futures if f.result())
