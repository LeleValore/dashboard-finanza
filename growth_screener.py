#!/usr/bin/env python3
"""
Seleziona le aziende in forte crescita all'interno dei temi seguiti. Ogni azienda
riceve un punteggio da crescita dei ricavi, crescita degli utili, margini e momentum
del prezzo. I temi e le aziende citati nei PDF dei Giganti compaiono come segnalazione
ma non alterano il punteggio, così col tempo si potrà misurare se aggiungono valore.

Ogni esecuzione salva una fotografia in growth/AAAA-MM-GG.csv: Yahoo non offre uno
storico dei fondamentali, quindi è questo archivio a permettere verifiche future.
Il risultato è una lista di candidate da studiare, non una raccomandazione d'acquisto.
"""

from __future__ import annotations

import csv
import datetime
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import yfinance as yf

import market_history

DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).parent)
GROWTH_DIR = DATA_DIR / "growth"

# Tema: parole chiave per riconoscerlo nei report e aziende {ticker: nome cercato nei PDF}.
THEMES = {
    "Robotica e attuatori": {
        "keywords": ["robot", "automazione", "attuator"],
        "companies": {
            "ROK": "Rockwell", "PH": "Parker", "6954.T": "Fanuc", "6324.T": "Harmonic Drive",
            "6268.T": "Nabtesco", "MOG-A": "Moog", "TER": "Teradyne", "SYM": "Symbotic",
            "ISRG": "Intuitive Surgical", "ABBN.SW": "ABB",
        },
    },
    "Semiconduttori e IA": {
        "keywords": ["semiconduttor", "chip", "intelligenza artificiale", "ia"],
        "companies": {
            "NVDA": "Nvidia", "AVGO": "Broadcom", "TSM": "TSMC", "ASML": "ASML", "PLTR": "Palantir",
        },
    },
    "Energia e reti per data center": {
        "keywords": ["energia", "rete", "data center", "elettric"],
        "companies": {
            "VRT": "Vertiv", "ETN": "Eaton", "GEV": "GE Vernova", "PWR": "Quanta", "PRY.MI": "Prysmian",
            "SU.PA": "Schneider", "CEG": "Constellation", "CCJ": "Cameco",
        },
    },
    "Cybersicurezza": {
        "keywords": ["cyber", "sicurezza informatica"],
        "companies": {"CRWD": "CrowdStrike", "PANW": "Palo Alto"},
    },
    "Difesa e spazio": {
        "keywords": ["difesa", "spazio", "aerospaz"],
        "companies": {"RHM.DE": "Rheinmetall", "LDO.MI": "Leonardo", "RKLB": "Rocket Lab"},
    },
    "Farmaceutica": {
        "keywords": ["sanità", "farmac", "obesità"],
        "companies": {"LLY": "Eli Lilly", "NVO": "Novo Nordisk"},
    },
}

STRONG_REVENUE_GROWTH = 0.25
MAX_DEBT_TO_EQUITY = 150
MOMENTUM_DAYS = 126
TREND_DAYS = 200
MAX_WORKERS = 4


def tracked_companies() -> dict[str, str]:
    """{nome: ticker} delle aziende seguite, per lo storico prezzi."""
    return {name: ticker for theme in THEMES.values() for ticker, name in theme["companies"].items()}


def _fetch_fundamentals(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        print(f"⚠️  fondamentali non disponibili per {ticker} ({exc}), salto")
        return {}
    return {
        "name": info.get("shortName") or ticker,
        "currency": info.get("currency"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "gross_margin": info.get("grossMargins"),
        "debt_to_equity": info.get("debtToEquity"),
    }


def _price_signals(ticker: str) -> dict:
    path = market_history.PRICES_DIR / market_history._ticker_filename(ticker)
    if not path.exists():
        return {}
    close = pd.read_csv(path, index_col="Date", parse_dates=True)["Close"]
    if len(close) <= TREND_DAYS:
        return {}
    return {
        "momentum_6m": close.iloc[-1] / close.iloc[-MOMENTUM_DAYS] - 1,
        "above_trend": bool(close.iloc[-1] > close.iloc[-TREND_DAYS:].mean()),
    }


def _mentioning_banks(search_name: str, documents: list[dict]) -> list[str]:
    pattern = re.compile(rf"\b{re.escape(search_name)}\b", flags=re.IGNORECASE)
    return sorted({doc["bank"] for doc in documents if pattern.search(doc.get("text", ""))})


def _theme_banks(keywords: list[str], report_themes: list[dict]) -> list[str]:
    patterns = [re.compile(rf"\b{re.escape(word)}", flags=re.IGNORECASE) for word in keywords]
    return sorted({
        theme["bank"] for theme in report_themes
        if any(p.search(theme["theme_it"]) for p in patterns)
    })


def screen_companies(report_themes: list[dict], documents: list[dict]) -> list[dict]:
    """Ritorna le aziende ordinate per punteggio, dalla più forte."""
    entries = [
        (theme_name, theme, ticker, name)
        for theme_name, theme in THEMES.items() for ticker, name in theme["companies"].items()
    ]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        fundamentals = list(executor.map(_fetch_fundamentals, [entry[2] for entry in entries]))

    rows = []
    for (theme_name, theme, ticker, search_name), fundamental in zip(entries, fundamentals):
        if not fundamental:
            continue
        rows.append({
            "ticker": ticker,
            "theme": theme_name,
            **fundamental,
            **_price_signals(ticker),
            "banks_company": _mentioning_banks(search_name, documents),
            "banks_theme": _theme_banks(theme["keywords"], report_themes),
        })
    if not rows:
        return []

    frame = pd.DataFrame(rows)
    factors = [c for c in ("revenue_growth", "earnings_growth", "gross_margin", "momentum_6m") if c in frame]
    frame["score"] = frame[factors].apply(pd.to_numeric, errors="coerce").rank(pct=True).mean(axis=1) * 100
    debt = pd.to_numeric(frame["debt_to_equity"], errors="coerce").fillna(0)
    above_trend = frame.get("above_trend", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
    revenue = pd.to_numeric(frame["revenue_growth"], errors="coerce").fillna(0)
    earnings = pd.to_numeric(frame["earnings_growth"], errors="coerce").fillna(0)
    frame["strong_growth"] = (
        (revenue >= STRONG_REVENUE_GROWTH) & (earnings > 0) & above_trend & (debt <= MAX_DEBT_TO_EQUITY)
    )
    frame = frame.sort_values("score", ascending=False)
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def save_snapshot(rows: list[dict]) -> None:
    if not rows:
        return
    GROWTH_DIR.mkdir(parents=True, exist_ok=True)
    path = GROWTH_DIR / f"{datetime.date.today().isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
