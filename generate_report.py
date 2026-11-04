#!/usr/bin/env python3
"""
Genera una pagina HTML statica con indici di mercato, materie prime, notizie
finanziarie e di politica globale, segnali da scraping mirato e un riassunto
operativo dei principali bank report, per avere un quadro d'insieme utile
alle decisioni di investimento. Ogni esecuzione salva anche un resoconto
testuale in days/, così si costruisce uno storico consultabile nel tempo.

Uso:
    python3 generate_report.py            # genera il report e lo apre nel browser
    python3 generate_report.py --no-browser   # genera il report senza aprirlo

Rilancia lo script quando vuoi dati aggiornati (es. ogni mattina).
"""

from __future__ import annotations

import argparse
import base64  # NUOVO: per incorporare salt/iv/ciphertext nell'HTML della password gate
import datetime
import difflib
import html  # NUOVO: per fare l'escape di testo scrappato prima di iniettarlo nell'HTML
import json
import os
import re  # NUOVO: usato per lo scraping mirato del calendario Myfxbook
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import feedparser
import requests
import yfinance as yf
from bs4 import BeautifulSoup  # NUOVO: parsing HTML per scraping mirato e bank report
from cryptography.hazmat.primitives import hashes  # NUOVO: cifratura password gate
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import bank_views
import knowledge_base  # NUOVO: ingestion PDF dei Giganti per l'assistente locale
import market_history

# ----------------------------------------------------------------------
# CONFIGURAZIONE — modifica qui ticker e fonti di notizie
# ----------------------------------------------------------------------

INDICES = {
    "FTSE MIB": "FTSEMIB.MI",
    "Euro Stoxx 50": "^STOXX50E",
    "DAX (Germania)": "^GDAXI",
    "S&P 500": "^GSPC",
    "Nasdaq Composite": "^IXIC",
    "Dow Jones": "^DJI",
    "Nikkei 225": "^N225",
}

# NUOVO: materie prime seguite, con ticker Yahoo Finance verificati.
# L'uranio non ha un future scambiato su Yahoo Finance (UX=F risulta delistato),
# quindi si usa come proxy l'ETF URA (Global X Uranium).
COMMODITIES = {
    "Zucchero": "SB=F",
    "Rame": "HG=F",
    "Oro": "GC=F",
    "Palladio": "PA=F",
    "Uranio (ETF URA)": "URA",
    "Argento": "SI=F",
    "Platino": "PL=F",
    "Caffè": "KC=F",
    "Petrolio (WTI)": "CL=F",
    "Grano": "ZW=F",
}

# NUOVO: principali criptovalute seguite, ticker Yahoo Finance.
DIGITAL_ASSETS = {
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "Ripple (XRP)": "XRP-USD",
    "Solana": "SOL-USD",
    "BNB": "BNB-USD",
    "Cardano": "ADA-USD",
}

# Indicatori di rischio: non compaiono nel report ma finiscono nello storico prezzi
# (market_history.py) come variabili per i modelli: volatilità azionaria e
# obbligazionaria, curva dei tassi USA, dollaro, credito.
RISK_INDICATORS = {
    "VIX": "^VIX",
    "VIX 3 mesi": "^VIX3M",
    "MOVE": "^MOVE",
    "Treasury 13 settimane": "^IRX",
    "Treasury 5 anni": "^FVX",
    "Treasury 10 anni": "^TNX",
    "Treasury 30 anni": "^TYX",
    "Dollar Index": "DX-Y.NYB",
    "EUR/USD": "EURUSD=X",
    "High yield (ETF HYG)": "HYG",
    "Investment grade (ETF LQD)": "LQD",
    "Treasury 20+ anni (ETF TLT)": "TLT",
    "Treasury 7-10 anni (ETF IEF)": "IEF",
}

NEWS_FEEDS = {
    "Finanza": [
        ("Il Sole 24 Ore - Finanza", "https://www.ilsole24ore.com/rss/finanza.xml"),
        ("Investing.com Italia", "https://it.investing.com/rss/news.rss"),
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("MarketWatch Top Stories", "http://feeds.marketwatch.com/marketwatch/topstories/"),
        ("WSJ Markets", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    ],
    "Economia": [
        ("ANSA Economia", "https://www.ansa.it/sito/notizie/economia/economia_rss.xml"),
        ("Il Sole 24 Ore - Economia", "https://www.ilsole24ore.com/rss/economia.xml"),  # NUOVO
    ],
    # NUOVO: le quattro sezioni seguenti coprono le "sezioni di interesse" richieste
    # (mercati per area geografica + macroeconomia globale). Per "Mercati Emergenti"
    # non esiste un feed RSS dedicato affidabile: si usa Asia (componente principale
    # degli emergenti) come miglior approssimazione disponibile via RSS pubblico.
    "Mercati USA": [
        ("MarketWatch MarketPulse", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ],
    "Mercati Emergenti": [
        ("CNBC Asia News", "https://www.cnbc.com/id/19832390/device/rss/rss.html"),
    ],
    "Mercati Europei": [
        ("Euronews Business", "https://www.euronews.com/rss?level=vertical&name=business"),
        ("BCE - Comunicati stampa", "https://www.ecb.europa.eu/rss/press.html"),
    ],
    "Macroeconomia Globale": [
        ("Financial Times - Global Economy", "https://www.ft.com/global-economy?format=rss"),
        ("ING Think", "https://think.ing.com/rss/"),
    ],
    "Eventi Climatici": [
        ("The Guardian - Climate Crisis", "https://www.theguardian.com/environment/climate-crisis/rss"),
    ],
    "Politica Globale": [
        ("ANSA Mondo", "https://www.ansa.it/sito/notizie/mondo/mondo_rss.xml"),
        ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ],
}

# NUOVO: categoria "sintetica" popolata via scraping mirato (non RSS) invece che
# tramite NEWS_FEEDS, ma con la stessa struttura dati delle altre news così da
# riusare score_news_item / dedup_news / render_news_item senza modifiche.
SCRAPED_NEWS_CATEGORY = "Calendario Economico"
MYFXBOOK_CALENDAR_URL = "https://www.myfxbook.com/forex-economic-calendar"
SCRAPING_LIMIT = 8

# NUOVO: header "da browser" usati per lo scraping mirato (Myfxbook,
# pagine insight delle banche). Alcuni siti rispondono 403 alle richieste con un
# User-Agent generico; con questi header quasi tutti i target rispondono 200.
# NOTA: verificare periodicamente Termini di Servizio e robots.txt dei siti scrappati
# prima di un uso continuativo/automatizzato — qui lo scraping è best-effort, fallisce
# in silenzio (solo un warning in console) se un sito cambia struttura o blocca l'accesso.
SCRAPING_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

# NUOVO: pagine pubbliche di market outlook/insight per il riassunto "Bank Report".
# UBS e Citadel bloccano (403) le richieste dirette al momento della stesura: restano
# in lista per il fallback silenzioso richiesto, verranno semplicemente omesse dalla
# sezione finché non si userà un fetch autenticato o un browser reale.
BANK_SOURCES = {
    "BlackRock": "https://www.blackrock.com/us/individual/insights/blackrock-investment-institute/weekly-commentary",
    "JP Morgan": "https://www.jpmorgan.com/insights/global-research/outlook/market-outlook",
    "Morgan Stanley": "https://www.morganstanley.com/insights",
    "Goldman Sachs": "https://www.goldmansachs.com/insights/",
    "UBS": "https://www.ubs.com/global/en/wealth-management/insights.html",
    "Citadel": "https://www.citadel.com/news/",
    "Berkshire Hathaway": "https://www.berkshirehathaway.com/letters/letters.html",
    "Ripple": "https://ripple.com/insights/",
}
BANK_TEXT_MAX_CHARS = 2500

NEWS_PER_FEED = 6
REQUEST_TIMEOUT = 10
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_WORKERS = 8

# Ticker/società seguiti con attenzione: ogni match nel titolo di una
# notizia vale un punto nel punteggio di rilevanza.
WATCHLIST = [
    "Nvidia",
    "Apple",
    "Tesla",
    "ENI",
    "Enel",
    "Intesa Sanpaolo",
    "Unicredit",
    "Stellantis",
]

# Parole chiave che segnalano notizie potenzialmente rilevanti per le
# decisioni di trading. Case-insensitive, match su sottostringa del titolo.
PRIORITY_KEYWORDS = [
    "rate cut",
    "tassi",
    "earnings",
    "trimestrale",
    "guidance",
    "downgrade",
    "upgrade",
    "sanzioni",
    "conflitto",
    "guerra",
    "Fed",
    "BCE",
    "inflazione",
    "recessione",
    "dazi",
    "tariffe",
]

# Soglia di punteggio (numero di match keyword/watchlist) oltre la quale
# una notizia entra nella sezione "Alta priorità" in cima alla pagina.
PRIORITY_THRESHOLD = 1

# Soglia di similarità (0-1) tra due titoli oltre la quale sono
# considerati duplicati e solo il primo viene mostrato.
DEDUP_SIMILARITY_THRESHOLD = 0.85

# Se True e la variabile d'ambiente ANTHROPIC_API_KEY è impostata, chiede
# all'API Anthropic un sentiment sintetico per le notizie ad alta priorità.
ENABLE_SENTIMENT = True
# NUOVO: come sopra, ma per il riassunto operativo dei bank report.
ENABLE_BANK_SUMMARY = True
SENTIMENT_MODEL = "claude-haiku-5-5"

BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "output" / "report.html"
# DATA_DIR è la cartella dello storico: su GitHub Actions punta al repository
# privato dei dati (vedi pages.yml), in locale di default è la cartella del progetto.
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR)
DAYS_DIR = DATA_DIR / "days"

# NUOVO: se impostata (variabile d'ambiente SITE_PASSWORD), il contenuto del
# report viene cifrato (AES-GCM, chiave derivata via PBKDF2) e mostrato solo a
# chi inserisce questa password nella pagina. Senza SITE_PASSWORD il report
# viene generato in chiaro come prima, con un avviso a console.
SITE_PASSWORD = os.environ.get("SITE_PASSWORD")
PBKDF2_ITERATIONS = 250_000


# ----------------------------------------------------------------------
# RACCOLTA DATI: INDICI E MATERIE PRIME
# ----------------------------------------------------------------------

def _fetch_one_index(name: str, ticker: str) -> dict:
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty or len(hist) < 2:
            print(f"⚠️  {name} ({ticker}): dati insufficienti, salto")
            return {"name": name, "ticker": ticker, "error": True}
        last = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2]
        change = last - prev
        change_pct = (change / prev) * 100
        return {
            "name": name,
            "ticker": ticker,
            "price": last,
            "change": change,
            "change_pct": change_pct,
            "error": False,
        }
    except Exception as exc:
        print(f"⚠️  {name} ({ticker}): non raggiungibile ({exc}), salto")
        return {"name": name, "ticker": ticker, "error": True}


def fetch_quotes(mapping: dict[str, str]) -> list[dict]:
    """Scarica prezzo e variazione per un gruppo di ticker Yahoo Finance.

    Generica su INDICES e COMMODITIES: NUOVO rispetto alla versione originale,
    che aveva una fetch_indices() dedicata solo agli indici.
    """
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_fetch_one_index, name, ticker) for name, ticker in mapping.items()]
        return [f.result() for f in futures]


# ----------------------------------------------------------------------
# RACCOLTA DATI: NOTIZIE RSS
# ----------------------------------------------------------------------

def _fetch_one_feed(source_name: str, url: str) -> list[dict]:
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=REQUEST_HEADERS)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        return [
            {
                "source": source_name,
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", "#"),
                "published": entry.get("published", ""),
            }
            for entry in parsed.entries[:NEWS_PER_FEED]
        ]
    except Exception as exc:
        print(f"⚠️  feed {source_name} non raggiungibile ({exc}), salto")
        return []


def fetch_news() -> dict:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            category: [executor.submit(_fetch_one_feed, source_name, url) for source_name, url in feeds]
            for category, feeds in NEWS_FEEDS.items()
        }
        sections = {
            category: [item for future in futures for item in future.result()]
            for category, futures in future_map.items()
        }
    return sections


# ----------------------------------------------------------------------
# NUOVO — SCRAPING MIRATO: CALENDARIO ECONOMICO MYFXBOOK
# ----------------------------------------------------------------------

def fetch_myfxbook_calendar(limit: int = SCRAPING_LIMIT) -> list[dict]:
    """Scraping del calendario economico di Myfxbook (solo eventi a impatto
    alto, per tenere la sezione essenziale: solo le news macro che contano
    davvero).

    myfxbook.com/robots.txt non esclude questa pagina, ma resta comunque
    scraping non ufficiale: fallisce in silenzio se il sito cambia struttura
    o blocca l'accesso (già capitato con header troppo minimali, da cui gli
    header "da browser" in SCRAPING_HEADERS).
    """
    try:
        response = requests.get(MYFXBOOK_CALENDAR_URL, headers=SCRAPING_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        results = []
        for row in soup.select("tr"):
            link = row.select_one("a.calendar-event-link")
            if not link:
                continue
            impact_el = row.select_one("[class*='impact_']")
            impact = impact_el.get_text(strip=True) if impact_el else ""
            if impact.lower() != "high":
                continue
            title = link.get_text(strip=True)
            if title.lower().startswith("myfxbook") and "sentiment" in title.lower():
                continue  # indicatore ricorrente del sito, non un vero evento di calendario

            country_el = row.select_one("i[title]")
            country = country_el.get("title") if country_el else ""
            cells = [c.get_text(strip=True) for c in row.select("td.calendarToggleCell")]
            currency = next((c for c in cells if re.fullmatch(r"[A-Z]{3}", c)), "")
            href = link.get("href", "")
            label = f"{country} {currency}".strip()

            results.append({
                "source": "Myfxbook - Calendario economico",
                "title": f"{label}: {title} — impatto alto",
                "link": href if href.startswith("http") else f"https://www.myfxbook.com{href}",
                "published": "",
                "force_priority": True,
            })
            if len(results) >= limit:
                break
        return results
    except Exception as exc:
        print(f"⚠️  Myfxbook non raggiungibile ({exc}), salto")
        return []


def fetch_scraped_news() -> list[dict]:
    return fetch_myfxbook_calendar()


# ----------------------------------------------------------------------
# NUOVO — BANK REPORT: SCRAPING + RIASSUNTO OPERATIVO
# ----------------------------------------------------------------------

def fetch_bank_page(name: str, url: str) -> dict | None:
    """Scarica una pagina pubblica di insight/outlook ed estrae un testo
    grezzo (titolo + paragrafi/titoletti più lunghi di 40 caratteri) da
    passare al riassunto. Nessun contenuto dietro login o paywall."""
    try:
        response = requests.get(url, headers=SCRAPING_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else name
        blocks = [tag.get_text(" ", strip=True) for tag in soup.find_all(["p", "h2", "h3"])]
        text = " ".join(b for b in blocks if len(b) > 40)[:BANK_TEXT_MAX_CHARS]
        if not text:
            print(f"⚠️  {name}: pagina raggiunta ma nessun testo utile estratto, salto")
            return None
        return {"title": title, "text": text}
    except Exception as exc:
        print(f"⚠️  {name} non raggiungibile ({exc}), salto")
        return None


def _response_text(response) -> str:
    """Testo della risposta: il modello può far precedere blocchi di thinking,
    che contano anche in max_tokens."""
    return next((b.text for b in response.content if b.type == "text"), "").strip()


def summarize_bank_report(bank_name: str, text: str) -> str | None:
    """Chiede all'API Anthropic un riassunto operativo sintetico (2-3 frasi,
    in italiano) del contenuto scrappato dalla pagina insight della banca."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not ENABLE_BANK_SUMMARY or not api_key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=SENTIMENT_MODEL,
            max_tokens=2048,
            output_config={"effort": "low"},
            messages=[{
                "role": "user",
                "content": (
                    f"Il testo seguente è stato estratto dalla pagina pubblica di market outlook/insight "
                    f"di {bank_name}. Scrivi un riassunto operativo in italiano di 2-3 frasi, utile per "
                    "decisioni di investimento (view di mercato, asset class citate, rischi principali). "
                    "Se il testo non contiene un vero outlook di mercato, rispondi esattamente: "
                    "'Nessun outlook di mercato rilevante nel contenuto disponibile.'\n\n"
                    f"Testo:\n{text}"
                ),
            }],
        )
        return _response_text(response) or None
    except Exception as exc:
        print(f"⚠️  riassunto non disponibile per {bank_name} ({exc})")
        return None


def build_bank_reports() -> list[dict]:
    """Scraping + riassunto per ogni banca in BANK_SOURCES. Le banche non
    raggiungibili (pagina cambiata, blocco anti-bot, ecc.) vengono omesse
    dalla sezione senza bloccare il resto dello script."""
    names = list(BANK_SOURCES.keys())
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_bank_page, name, BANK_SOURCES[name]) for name in names]
        pages = [f.result() for f in futures]

    reports = []
    for name, page in zip(names, pages):
        if not page:
            continue
        summary = summarize_bank_report(name, page["text"])
        if not summary:
            # Senza ANTHROPIC_API_KEY (o se la chiamata fallisce): mostra comunque
            # un estratto grezzo del testo scrappato, invece di saltare la banca.
            summary = page["text"][:280].rsplit(" ", 1)[0] + "…"
        reports.append({"bank": name, "summary": summary, "link": BANK_SOURCES[name]})
    return reports


# ----------------------------------------------------------------------
# RILEVANZA, DEDUPLICA E SENTIMENT
# ----------------------------------------------------------------------

def score_news_item(item: dict) -> dict:
    """Assegna un punteggio di rilevanza in base a match con watchlist/keyword."""
    title_lower = item["title"].lower()
    matched = [
        term for term in (WATCHLIST + PRIORITY_KEYWORDS)
        if term.lower() in title_lower
    ]
    item["relevance_score"] = len(matched)
    item["matched_terms"] = matched
    return item


def dedup_news(items: list[dict]) -> list[dict]:
    """Rimuove notizie con titolo troppo simile a una già vista (feed diversi)."""
    kept = []
    for item in items:
        title_norm = item["title"].strip().lower()
        is_duplicate = any(
            difflib.SequenceMatcher(None, title_norm, other["title"].strip().lower()).ratio()
            >= DEDUP_SIMILARITY_THRESHOLD
            for other in kept
        )
        if not is_duplicate:
            kept.append(item)
    return kept


def fetch_sentiment(title: str) -> str | None:
    """Chiede all'API Anthropic un sentiment sintetico (positivo/neutro/negativo)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not ENABLE_SENTIMENT or not api_key:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=SENTIMENT_MODEL,
            max_tokens=1024,
            output_config={"effort": "low"},
            messages=[{
                "role": "user",
                "content": (
                    "Classifica il sentiment di questo titolo di notizia finanziaria "
                    "con una sola parola tra: positivo, neutro, negativo.\n\n"
                    f"Titolo: {title}"
                ),
            }],
        )
        text = _response_text(response).lower()
        for label in ("positivo", "neutro", "negativo"):
            if label in text:
                return label
        return None
    except Exception as exc:
        print(f"⚠️  sentiment non disponibile per '{title[:40]}...' ({exc})")
        return None


def process_news(news: dict) -> tuple[dict, list[dict]]:
    """Deduplica, assegna punteggio e separa la sezione ad alta priorità."""
    processed = {}
    high_priority = []
    for category, items in news.items():
        items = dedup_news(items)
        items = [score_news_item(i) for i in items]
        processed[category] = items
        high_priority.extend(
            i for i in items
            if i["relevance_score"] >= PRIORITY_THRESHOLD or i.get("force_priority")  # NUOVO: force_priority
        )

    high_priority.sort(key=lambda i: i["relevance_score"], reverse=True)

    if ENABLE_SENTIMENT and os.environ.get("ANTHROPIC_API_KEY"):
        for item in high_priority:
            item["sentiment"] = fetch_sentiment(item["title"])
    else:
        for item in high_priority:
            item["sentiment"] = None

    return processed, high_priority


# ----------------------------------------------------------------------
# STORICO
# ----------------------------------------------------------------------

def _format_quote_line(q: dict) -> str:
    if q["error"]:
        return f"  {q['name']}: dati non disponibili"
    sign = "+" if q["change"] >= 0 else ""
    return (
        f"  {q['name']}: {q['price']:,.2f} "
        f"({sign}{q['change']:,.2f}, {sign}{q['change_pct']:.2f}%)"
    )


def save_history(
    indices: list[dict],
    commodities: list[dict],  # NUOVO
    digital_assets: list[dict],  # NUOVO
    news: dict,
    high_priority: list[dict],
    bank_reports: list[dict],  # NUOVO
) -> tuple[Path, Path]:
    """Salva un resoconto della run odierna in days/AAAA-MM-GG.txt e .json.

    Una run al giorno sovrascrive i file di quel giorno, così lo storico
    resta un file per data anche rilanciando lo script più volte. Il
    .txt è per lettura rapida, il .json per riguardare lo storico in modo
    strutturato (es. filtrare per punteggio di rilevanza) in futuro.
    """
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    txt_path = DAYS_DIR / f"{today}.txt"
    json_path = DAYS_DIR / f"{today}.json"

    lines = [f"Resoconto DisciplinedCapital — {today}", "=" * 50, ""]

    lines.append("INDICI DI MERCATO")
    lines.append("-" * 17)
    for idx in indices:
        lines.append(_format_quote_line(idx))
    lines.append("")

    lines.append("MATERIE PRIME")  # NUOVO
    lines.append("-" * 13)
    for c in commodities:
        lines.append(_format_quote_line(c))
    lines.append("")

    lines.append("DIGITAL ASSET")  # NUOVO
    lines.append("-" * 13)
    for d in digital_assets:
        lines.append(_format_quote_line(d))
    lines.append("")

    if bank_reports:  # NUOVO
        lines.append("GIGANTI")
        lines.append("-" * 7)
        for b in bank_reports:
            lines.append(f"  - [{b['bank']}] {b['summary']}")
            lines.append(f"    {b['link']}")
        lines.append("")

    for category, items in news.items():
        lines.append(category.upper())
        lines.append("-" * len(category))
        if not items:
            lines.append("  (nessuna notizia disponibile)")
        for item in items:
            lines.append(f"  - [{item['source']}] {item['title']}")
            lines.append(f"    {item['link']}")
        lines.append("")

    txt_path.write_text("\n".join(lines), encoding="utf-8")

    json_data = {
        "date": today,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "indices": [{k: v for k, v in idx.items()} for idx in indices],
        "commodities": [{k: v for k, v in c.items()} for c in commodities],  # NUOVO
        "digital_assets": [{k: v for k, v in d.items()} for d in digital_assets],  # NUOVO
        "news": news,
        "high_priority": high_priority,
        "bank_report": bank_reports,  # NUOVO
    }
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return txt_path, json_path


# ----------------------------------------------------------------------
# RENDER HTML
# ----------------------------------------------------------------------

def render_index_card(idx: dict) -> str:
    if idx["error"]:
        return f"""
        <div class="card">
          <div class="card-name">{html.escape(idx['name'])}</div>
          <div class="card-error">Dati non disponibili</div>
        </div>"""

    direction = "up" if idx["change"] >= 0 else "down"
    arrow = "▲" if idx["change"] >= 0 else "▼"
    sign = "+" if idx["change"] >= 0 else ""
    return f"""
        <div class="card">
          <div class="card-name">{html.escape(idx['name'])}</div>
          <div class="card-price">{idx['price']:,.2f}</div>
          <div class="card-change {direction}"><span class="arrow">{arrow}</span>{sign}{idx['change']:,.2f} ({sign}{idx['change_pct']:.2f}%)</div>
        </div>"""


SENTIMENT_LABELS = {
    "positivo": ("badge-positive", "Positivo"),
    "neutro": ("badge-neutral", "Neutro"),
    "negativo": ("badge-negative", "Negativo"),
}


def render_news_item(item: dict) -> str:
    badges = ""
    sentiment = item.get("sentiment")
    if sentiment in SENTIMENT_LABELS:
        css_class, label = SENTIMENT_LABELS[sentiment]
        badges += f'<span class="badge {css_class}">{label}</span>'

    return f"""
        <li>
          <a href="{html.escape(item['link'])}" target="_blank" rel="noopener noreferrer">{html.escape(item['title'])}</a>
          <span class="news-meta"><span class="news-source">{html.escape(item['source'])}</span>{badges}</span>
        </li>"""


def render_bank_card(item: dict) -> str:  # NUOVO
    return f"""
        <div class="bank-card">
          <div class="bank-name">{html.escape(item['bank'])}</div>
          <p class="bank-summary">{html.escape(item['summary'])}</p>
          <a class="bank-link" href="{html.escape(item['link'])}" target="_blank" rel="noopener noreferrer">Fonte →</a>
        </div>"""


def render_stance_cell(view: dict | None) -> str:
    if view is None:
        return '<td class="stance-empty">–</td>'
    score = view["score"]
    tone = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    previous = view["previous"]
    change = ""
    if previous is not None and previous != score:
        change = f' <span class="stance-change">{"▲" if score > previous else "▼"}</span>'
    return f'<td><span class="stance {tone}">{score:+d}</span>{change}</td>'


def render_views_section(consensus: dict) -> str:
    """Tabella delle view per asset class e schede degli ultimi report analizzati."""
    if not consensus["documents"]:
        return ""
    table_html = ""
    if consensus["assets"]:
        header = "".join(f"<th>{html.escape(bank)}</th>" for bank in consensus["banks"])
        rows = ""
        for asset in consensus["assets"]:
            cells = "".join(render_stance_cell(asset["views"].get(bank)) for bank in consensus["banks"])
            rows += f"""
          <tr><th scope="row">{html.escape(asset['label'])}</th><td class="stance-average">{asset['average']:+.1f}</td>{cells}</tr>"""
        table_html = f"""
      <p class="search-hint">Da -2 (forte sottopeso) a +2 (forte sovrappeso), solo view esplicite degli ultimi {bank_views.CONSENSUS_MAX_AGE_DAYS} giorni. ▲▼ indicano il cambio rispetto alla view precedente.</p>
      <div class="views-scroll">
        <table class="views-table">
          <thead><tr><th>Asset class</th><th>Media</th>{header}</tr></thead>
          <tbody>{rows}
          </tbody>
        </table>
      </div>"""

    cards_html = ""
    for doc in consensus["documents"]:
        analysis = doc["analysis"]
        risks = sorted(analysis["risks"], key=lambda r: r["severity"] != "high")[:3]
        risks_html = "".join(f"<li>{html.escape(r['risk_it'])}</li>" for r in risks)
        date = analysis.get("published_date") or doc.get("first_seen", "")
        cards_html += f"""
        <div class="bank-card">
          <div class="bank-name">{html.escape(doc['bank'])} &middot; {html.escape(date)}</div>
          <p class="bank-summary">{html.escape(analysis['summary_it'])}</p>
          <ul class="bank-risks">{risks_html}</ul>
          <a class="bank-link" href="{html.escape(doc['url'])}" target="_blank" rel="noopener noreferrer">PDF →</a>
        </div>"""
    return f"""
    <section>
      <h2>📊 View dei Giganti</h2>{table_html}
      <div class="bank-grid">{cards_html}
      </div>
    </section>"""


def encrypt_for_password(plaintext: str, password: str) -> dict:
    """Cifra plaintext con AES-256-GCM, chiave derivata dalla password via
    PBKDF2-HMAC-SHA256. Salt/iv/iterazioni cambiano ad ogni run. Il formato
    (salt, iv, ciphertext in base64 + numero iterazioni) è pensato per essere
    decifrato lato browser con la Web Crypto API (vedi render_lock_screen)."""
    salt = os.urandom(16)
    iv = os.urandom(12)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS)
    key = kdf.derive(password.encode("utf-8"))
    ciphertext = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)
    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "iterations": PBKDF2_ITERATIONS,
    }


def render_lock_screen(wrap_html: str, password: str) -> str:
    """Avvolge il contenuto del report (già renderizzato in wrap_html) dietro
    una schermata con password. Il contenuto viene cifrato qui in Python e
    decifrato solo nel browser, dopo password corretta, con crypto.subtle
    (PBKDF2 + AES-GCM): senza password l'HTML pubblicato non contiene il
    report in chiaro, solo testo cifrato."""
    payload = encrypt_for_password(wrap_html, password)
    return f"""
  <div id="lock-screen" class="lock-screen">
    <form id="lock-form" class="lock-card">
      <div class="eyebrow">Accesso riservato</div>
      <h1>DisciplinedCapital</h1>
      <p class="lock-hint">Inserisci la password per vedere il report.</p>
      <input type="password" id="lock-password" class="lock-input" autocomplete="current-password" autofocus required>
      <button type="submit" class="lock-button">Accedi</button>
      <p id="lock-error" class="lock-error" hidden>Password errata, riprova.</p>
    </form>
  </div>
  <div id="app" hidden></div>
  <script>
  (function () {{
    const SALT_B64 = {json.dumps(payload["salt"])};
    const IV_B64 = {json.dumps(payload["iv"])};
    const CIPHERTEXT_B64 = {json.dumps(payload["ciphertext"])};
    const ITERATIONS = {payload["iterations"]};

    function b64ToBytes(b64) {{
      return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    }}

    async function decrypt(password) {{
      const keyMaterial = await crypto.subtle.importKey(
        "raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]
      );
      const key = await crypto.subtle.deriveKey(
        {{ name: "PBKDF2", salt: b64ToBytes(SALT_B64), iterations: ITERATIONS, hash: "SHA-256" }},
        keyMaterial,
        {{ name: "AES-GCM", length: 256 }},
        false,
        ["decrypt"]
      );
      const plainBuf = await crypto.subtle.decrypt(
        {{ name: "AES-GCM", iv: b64ToBytes(IV_B64) }},
        key,
        b64ToBytes(CIPHERTEXT_B64)
      );
      return new TextDecoder().decode(plainBuf);
    }}

    const form = document.getElementById("lock-form");
    const errorEl = document.getElementById("lock-error");
    const lockScreen = document.getElementById("lock-screen");
    const app = document.getElementById("app");

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      errorEl.hidden = true;
      const password = document.getElementById("lock-password").value;
      try {{
        app.innerHTML = await decrypt(password);
        lockScreen.remove();
        app.hidden = false;
      }} catch (err) {{
        errorEl.hidden = false;
      }}
    }});
  }})();
  </script>"""


def render_html(
    indices: list[dict],
    commodities: list[dict],  # NUOVO
    digital_assets: list[dict],  # NUOVO
    news: dict,
    high_priority: list[dict],
    bank_reports: list[dict],  # NUOVO
    search_index: list[dict],  # NUOVO
    consensus: dict,
) -> str:
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    indices_html = "\n".join(render_index_card(i) for i in indices)
    commodities_html = "\n".join(render_index_card(c) for c in commodities)  # NUOVO
    digital_assets_html = "\n".join(render_index_card(d) for d in digital_assets)  # NUOVO

    bank_html = ""  # NUOVO
    if bank_reports:
        cards_html = "\n".join(render_bank_card(b) for b in bank_reports)
        bank_html = f"""
    <section>
      <h2>🏦 Giganti</h2>
      <div class="bank-grid">{cards_html}
      </div>
    </section>"""

    views_html = render_views_section(consensus)

    search_index_json = json.dumps(search_index, ensure_ascii=False).replace("</", "<\\/")
    search_html = f"""
    <section>
      <h2>🔍 Ricerca Giganti</h2>
      <p class="search-hint">Cerca per parola chiave nei bank report PDF catalogati nella knowledge base locale (nessuna API, ricerca nel browser).</p>
      <form id="giganti-search-form" class="search-form">
        <input type="text" id="giganti-search-query" class="search-input" placeholder="Es. tassi, inflazione, Goldman Sachs…" autocomplete="off">
        <button type="submit" class="search-button">Cerca</button>
      </form>
      <div id="giganti-search-results" class="search-results"></div>
      <script type="application/json" id="giganti-search-data">{search_index_json}</script>
    </section>"""

    news_sections_html = ""
    for category, items in news.items():
        if not items:
            continue
        items_html = "\n".join(render_news_item(i) for i in items)
        news_sections_html += f"""
        <section class="news-section">
          <h2>{html.escape(category)}</h2>
          <ul class="news-list">{items_html}
          </ul>
        </section>"""

    wrap_html = f"""
  <div class="wrap">
    <header>
      <div class="eyebrow">Report giornaliero</div>
      <h1>DisciplinedCapital</h1>
      <div class="updated">Aggiornato il {now}</div>
    </header>
    <section>
      <h2>Indici di mercato</h2>
      <div class="grid">{indices_html}
      </div>
    </section>

    <section>
      <h2>Materie Prime</h2>
      <div class="grid">{commodities_html}
      </div>
    </section>

    <section>
      <h2>Digital Asset</h2>
      <div class="grid">{digital_assets_html}
      </div>
    </section>
{views_html}
{bank_html}
{search_html}
{news_sections_html}
    <footer>Generato localmente da generate_report.py &middot; dati indici/materie prime/digital asset via Yahoo Finance, notizie via RSS e scraping mirato &middot; storico giornaliero in days/</footer>
  </div>"""

    body_html = render_lock_screen(wrap_html, SITE_PASSWORD) if SITE_PASSWORD else wrap_html

    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>DisciplinedCapital</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #f5f6f8;
    --card-bg: #ffffff;
    --text: #1a1d23;
    --muted: #6b7280;
    --border: #e5e7eb;
    --up: #16a34a;
    --down: #dc2626;
    --accent: #2563eb;
    --accent-soft: #eef3ff;
    --positive-bg: #dcfce7;
    --positive-text: #15803d;
    --negative-bg: #fee2e2;
    --negative-text: #b91c1c;
    --neutral-bg: #e5e7eb;
    --neutral-text: #4b5563;
    --shadow: 0 1px 2px rgba(16, 24, 40, 0.04), 0 1px 3px rgba(16, 24, 40, 0.06);
    --shadow-hover: 0 6px 14px rgba(16, 24, 40, 0.09), 0 2px 4px rgba(16, 24, 40, 0.06);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14161a;
      --card-bg: #1e2127;
      --text: #e8e9eb;
      --muted: #9ca3af;
      --border: #2d313a;
      --up: #4ade80;
      --down: #f87171;
      --accent: #60a5fa;
      --accent-soft: #1c2433;
      --positive-bg: #14321f;
      --positive-text: #4ade80;
      --negative-bg: #3a1a1a;
      --negative-text: #f87171;
      --neutral-bg: #2d313a;
      --neutral-text: #9ca3af;
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
      --shadow-hover: 0 8px 18px rgba(0, 0, 0, 0.4);
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 2.5rem 1.5rem 4rem;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 1040px; margin: 0 auto; }}
  header {{ margin-bottom: 2.5rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); }}
  .eyebrow {{
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.72rem;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 0.45rem;
  }}
  h1 {{ margin: 0 0 0.35rem; font-size: 1.85rem; letter-spacing: -0.02em; }}
  .updated {{ color: var(--muted); font-size: 0.88rem; }}
  h2 {{ font-size: 1.05rem; font-weight: 700; margin: 0 0 1rem; padding-bottom: 0.5rem; border-bottom: 2px solid var(--border); }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.9rem;
    margin-bottom: 2.75rem;
  }}
  .card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.1rem 1.2rem;
    box-shadow: var(--shadow);
    transition: box-shadow 0.15s ease, transform 0.15s ease;
  }}
  .card:hover {{ box-shadow: var(--shadow-hover); transform: translateY(-1px); }}
  .card-name {{ font-size: 0.82rem; color: var(--muted); margin-bottom: 0.45rem; font-weight: 500; }}
  .card-price {{ font-size: 1.45rem; font-weight: 700; letter-spacing: -0.01em; }}
  .card-change {{ font-size: 0.88rem; margin-top: 0.3rem; font-weight: 600; display: flex; align-items: center; gap: 0.3rem; }}
  .card-change .arrow {{ font-size: 0.7rem; }}
  .card-change.up {{ color: var(--up); }}
  .card-change.down {{ color: var(--down); }}
  .card-error {{ color: var(--muted); font-size: 0.88rem; }}
  .bank-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 0.9rem;
    margin-bottom: 2.75rem;
  }}
  .bank-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.1rem 1.2rem;
    box-shadow: var(--shadow);
    transition: box-shadow 0.15s ease, transform 0.15s ease;
  }}
  .bank-card:hover {{ box-shadow: var(--shadow-hover); transform: translateY(-1px); }}
  .bank-name {{ font-weight: 700; margin-bottom: 0.5rem; }}
  .bank-summary {{ font-size: 0.9rem; color: var(--text); margin: 0 0 0.6rem; line-height: 1.5; }}
  .bank-link {{ font-size: 0.82rem; color: var(--accent); text-decoration: none; font-weight: 600; }}
  .bank-link:hover {{ text-decoration: underline; }}
  .bank-risks {{ font-size: 0.82rem; color: var(--muted); margin: 0 0 0.6rem; padding-left: 1.1rem; line-height: 1.45; }}
  .views-scroll {{ overflow-x: auto; margin-bottom: 1.2rem; }}
  .views-table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  .views-table th, .views-table td {{ padding: 0.45rem 0.7rem; border-bottom: 1px solid var(--border); text-align: center; white-space: nowrap; }}
  .views-table thead th {{ color: var(--muted); font-weight: 600; }}
  .views-table th:first-child {{ text-align: left; font-weight: 600; }}
  .stance {{ display: inline-block; min-width: 2rem; padding: 0.1rem 0.35rem; border-radius: 6px; font-weight: 600; }}
  .stance.positive {{ background: var(--positive-bg); color: var(--positive-text); }}
  .stance.negative {{ background: var(--negative-bg); color: var(--negative-text); }}
  .stance.neutral {{ background: var(--neutral-bg); color: var(--neutral-text); }}
  .stance-average {{ font-weight: 700; }}
  .stance-empty {{ color: var(--muted); }}
  .stance-change {{ font-size: 0.7rem; color: var(--muted); }}
  .news-section {{ margin-bottom: 2.4rem; }}
  .news-list {{ list-style: none; margin: 0; padding: 0; }}
  .news-list li {{
    padding: 0.75rem 0.5rem;
    margin: 0 -0.5rem;
    border-bottom: 1px solid var(--border);
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    transition: background 0.15s ease;
  }}
  .news-list li:hover {{ background: var(--accent-soft); }}
  .news-list li:last-child {{ border-bottom: none; }}
  .news-list a {{
    color: var(--text);
    text-decoration: none;
    font-size: 0.97rem;
    font-weight: 500;
  }}
  .news-list a:hover {{ color: var(--accent); text-decoration: underline; }}
  .news-meta {{ display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
  .news-source {{ color: var(--muted); font-size: 0.78rem; }}
  .badge {{
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    letter-spacing: 0.01em;
  }}
  .badge-positive {{ background: var(--positive-bg); color: var(--positive-text); }}
  .badge-negative {{ background: var(--negative-bg); color: var(--negative-text); }}
  .badge-neutral {{ background: var(--neutral-bg); color: var(--neutral-text); }}
  footer {{
    color: var(--muted);
    font-size: 0.8rem;
    margin-top: 3.5rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--border);
    text-align: center;
  }}
  .lock-screen {{
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;
  }}
  .lock-card {{
    width: 100%;
    max-width: 320px;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.75rem 1.5rem;
    box-shadow: var(--shadow);
    text-align: center;
  }}
  .lock-card h1 {{ font-size: 1.3rem; margin: 0 0 0.6rem; }}
  .lock-hint {{ color: var(--muted); font-size: 0.88rem; margin: 0 0 1.2rem; }}
  .lock-input {{
    width: 100%;
    padding: 0.6rem 0.75rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--bg);
    color: var(--text);
    font-size: 0.95rem;
    margin-bottom: 0.9rem;
  }}
  .lock-button {{
    width: 100%;
    padding: 0.65rem 0.75rem;
    border: none;
    border-radius: 8px;
    background: var(--accent);
    color: #fff;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
  }}
  .lock-button:hover {{ opacity: 0.9; }}
  .lock-error {{ color: var(--negative-text); font-size: 0.85rem; margin: 0.9rem 0 0; }}
  .search-hint {{ color: var(--muted); font-size: 0.85rem; margin: 0 0 0.9rem; }}
  .search-form {{ display: flex; gap: 0.6rem; margin-bottom: 1rem; }}
  .search-input {{
    flex: 1;
    padding: 0.6rem 0.75rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--card-bg);
    color: var(--text);
    font-size: 0.92rem;
    font-family: inherit;
  }}
  .search-button {{
    padding: 0.6rem 1.1rem;
    border: none;
    border-radius: 8px;
    background: var(--accent);
    color: #fff;
    font-size: 0.92rem;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
  }}
  .search-button:hover {{ opacity: 0.9; }}
  .search-results {{ font-size: 0.92rem; }}
  .search-results-empty {{ color: var(--muted); }}
  .search-result {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0.9rem 1.1rem;
    margin-bottom: 0.7rem;
  }}
  .search-result-meta {{ font-weight: 700; margin-bottom: 0.4rem; }}
  .search-result-meta .search-result-date {{ color: var(--muted); font-weight: 500; font-size: 0.82rem; margin-left: 0.4rem; }}
  .search-result-excerpt {{ color: var(--text); line-height: 1.5; margin: 0 0 0.5rem; }}
  .search-result-excerpt mark {{ background: var(--priority-bg, var(--accent-soft)); color: inherit; border-radius: 3px; }}
  .search-result-link {{ font-size: 0.82rem; color: var(--accent); text-decoration: none; font-weight: 600; }}
  .search-result-link:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{body_html}
<script>
(function () {{
  function getIndex() {{
    const dataEl = document.getElementById("giganti-search-data");
    if (!dataEl) return [];
    try {{
      return JSON.parse(dataEl.textContent || "[]");
    }} catch (err) {{
      return [];
    }}
  }}

  function buildExcerpt(text, terms) {{
    const lower = text.toLowerCase();
    let matchAt = -1;
    for (const term of terms) {{
      const idx = lower.indexOf(term);
      if (idx !== -1 && (matchAt === -1 || idx < matchAt)) matchAt = idx;
    }}
    const start = Math.max(0, (matchAt === -1 ? 0 : matchAt) - 120);
    const end = Math.min(text.length, start + 320);
    let excerpt = (start > 0 ? "…" : "") + text.slice(start, end) + (end < text.length ? "…" : "");
    terms.forEach(function (term) {{
      const re = new RegExp("(" + term.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&") + ")", "gi");
      excerpt = excerpt.replace(re, "<mark>$1</mark>");
    }});
    return excerpt;
  }}

  function escapeHtml(str) {{
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }}

  function renderResults(container, docs, terms) {{
    if (!docs.length) {{
      container.innerHTML = '<p class="search-results-empty">Nessun risultato per questa ricerca.</p>';
      return;
    }}
    container.innerHTML = docs.map(function (doc) {{
      const excerptHtml = buildExcerpt(doc.text || "", terms);
      return (
        '<div class="search-result">' +
        '<div class="search-result-meta">' + escapeHtml(doc.bank) +
        '<span class="search-result-date">' + escapeHtml(doc.date || "") + "</span></div>" +
        '<p class="search-result-excerpt">' + excerptHtml + "</p>" +
        '<a class="search-result-link" href="' + encodeURI(doc.url) + '" target="_blank" rel="noopener noreferrer">Fonte →</a>' +
        "</div>"
      );
    }}).join("");
  }}

  document.addEventListener("submit", function (event) {{
    if (!event.target || event.target.id !== "giganti-search-form") return;
    event.preventDefault();

    const queryEl = document.getElementById("giganti-search-query");
    const resultsEl = document.getElementById("giganti-search-results");
    const query = (queryEl.value || "").trim().toLowerCase();
    if (!resultsEl) return;

    const terms = query.split(/\s+/).filter(function (t) {{ return t.length > 1; }});
    if (!terms.length) {{
      resultsEl.innerHTML = '<p class="search-results-empty">Scrivi almeno una parola da cercare.</p>';
      return;
    }}

    const index = getIndex();
    if (!index.length) {{
      resultsEl.innerHTML = '<p class="search-results-empty">Nessun bank report catalogato ancora nella knowledge base.</p>';
      return;
    }}

    const scored = index
      .map(function (doc) {{
        const haystack = ((doc.title || "") + " " + (doc.text || "")).toLowerCase();
        const score = terms.reduce(function (acc, term) {{
          return acc + haystack.split(term).length - 1;
        }}, 0);
        return {{ doc: doc, score: score }};
      }})
      .filter(function (entry) {{ return entry.score > 0; }})
      .sort(function (a, b) {{ return b.score - a.score; }})
      .slice(0, 8)
      .map(function (entry) {{ return entry.doc; }});

    renderResults(resultsEl, scored, terms);
  }});
}})();
</script>
</body>
</html>"""


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Genera DisciplinedCapital (indici, materie prime, digital asset, notizie, Giganti)."
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="non aprire automaticamente il report nel browser",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("DisciplinedCapital")
    print("-" * 40)

    print("[1/9] Scarico indici di mercato, materie prime e digital asset...")
    indices = fetch_quotes(INDICES)
    commodities = fetch_quotes(COMMODITIES)
    digital_assets = fetch_quotes(DIGITAL_ASSETS)  # NUOVO
    ok_indices = sum(1 for i in indices if not i["error"])
    ok_commodities = sum(1 for c in commodities if not c["error"])
    ok_digital_assets = sum(1 for d in digital_assets if not d["error"])
    print(
        f"      {ok_indices}/{len(indices)} indici, {ok_commodities}/{len(commodities)} materie prime, "
        f"{ok_digital_assets}/{len(digital_assets)} digital asset aggiornati"
    )

    print("[2/9] Scarico notizie (feed RSS)...")
    news_raw = fetch_news()
    total_raw = sum(len(items) for items in news_raw.values())
    for category, items in news_raw.items():
        print(f"      {category}: {len(items)} notizie")

    print("[3/9] Calendario economico (Myfxbook, solo impatto alto)...")
    scraped_items = fetch_scraped_news()
    if scraped_items:
        news_raw.setdefault(SCRAPED_NEWS_CATEGORY, []).extend(scraped_items)
    total_raw += len(scraped_items)
    print(f"      {len(scraped_items)} eventi ad alto impatto")

    print("[4/9] Deduplico, calcolo rilevanza e sentiment...")
    news, high_priority = process_news(news_raw)
    total_news = sum(len(items) for items in news.values())
    duplicates_removed = total_raw - total_news
    print(f"      {total_news} notizie uniche ({duplicates_removed} duplicati rimossi)")
    print(f"      {len(high_priority)} notizie ad alta priorità")

    print("[5/9] Giganti: scraping e riassunto operativo...")
    bank_reports = build_bank_reports()
    print(f"      {len(bank_reports)}/{len(BANK_SOURCES)} banche disponibili")

    print("[6/9] Knowledge base: scraping nuovi PDF dei Giganti...")
    kb_summary = knowledge_base.ingest_new_reports(BANK_SOURCES)
    total_new_docs = sum(kb_summary.values())
    search_index = knowledge_base.build_search_index()
    print(f"      {total_new_docs} nuovi documenti aggiunti, {len(search_index)} nell'indice di ricerca")

    print("[7/9] Analisi strutturata dei PDF...")
    analyzed = bank_views.analyze_pending_documents()
    view_rows = bank_views.export_views_csv()
    consensus = bank_views.build_consensus()
    print(f"      {analyzed} documenti analizzati, {view_rows} view nello storico")

    print("[8/9] Storico prezzi e indicatori di rischio...")
    tracked = {**INDICES, **COMMODITIES, **DIGITAL_ASSETS, **RISK_INDICATORS}
    saved_tickers = market_history.update_price_history(tracked)
    print(f"      {saved_tickers}/{len(tracked)} ticker aggiornati")

    print("[9/9] Genero report e storico...")
    if SITE_PASSWORD:
        print("      🔒 Report protetto da password (SITE_PASSWORD impostata)")
    else:
        print("      ⚠️  SITE_PASSWORD non impostata: il report NON è protetto da password")
    html_output = render_html(
        indices, commodities, digital_assets, news, high_priority, bank_reports, search_index, consensus
    )
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(html_output, encoding="utf-8")
    txt_file, json_file = save_history(indices, commodities, digital_assets, news, high_priority, bank_reports)

    print("-" * 40)
    print(f"Report:  {OUTPUT_FILE}")
    print(f"Storico: {txt_file}")
    print(f"         {json_file}")
    print(
        f"Totale:  {ok_indices} indici, {ok_commodities} materie prime, {ok_digital_assets} digital asset, "
        f"{total_news} notizie ({len(high_priority)} alta priorità), {len(bank_reports)} Giganti"
    )

    if not args.no_browser:
        webbrowser.open(f"file://{OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.RequestException:
        print("Errore di rete: controlla la connessione a Internet e riprova.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrotto dall'utente.", file=sys.stderr)
        sys.exit(1)
