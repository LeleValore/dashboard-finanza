#!/usr/bin/env python3
"""
Knowledge base locale dei bank report (PDF) dei "Giganti" catalogati in
generate_report.py (BANK_SOURCES). Ogni esecuzione dello scraping scopre i
PDF pubblicati sulle pagine insight di ciascuna banca, scarica ed estrae il
testo solo di quelli non ancora noti (tracciati in un manifest per banca) e
li salva come singoli file JSON sotto knowledge_base/<banca>/. Questo storico
si accumula nel tempo (più PDF a settimana per banca restano tutti, non
vengono sovrascritti) insieme al PDF originale, sotto <banca>/pdf/. I documenti
si riconoscono dal contenuto: FIXED_PDF_URLS elenca i PDF che cambiano edizione
restando allo stesso indirizzo. build_search_index() ne ricava un indice leggero che
generate_report.py incorpora nel report per la ricerca lato client (nessuna
chiamata API, gratuita).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from io import BytesIO

BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR)
KB_DIR = DATA_DIR / "knowledge_base"

REQUEST_TIMEOUT = 15
SCRAPING_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

# Pagine aggiuntive in cui cercare PDF, oltre a quelle di BANK_SOURCES: le pagine
# insight delle banche spesso non linkano PDF, quelle dell'asset management sì.
EXTRA_PDF_PAGES = {
    "Goldman Sachs": ["https://am.gs.com/en-us/advisors/insights"],
}

# PDF a indirizzo noto. La Guide to the Markets esce ogni trimestre allo stesso URL.
FIXED_PDF_URLS = {
    "JP Morgan": [
        "https://am.jpmorgan.com/content/dam/jpm-am-aem/global/en/insights/market-insights/"
        "guide-to-the-markets/mi-guide-to-the-markets-us.pdf",
    ],
}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "banca"


def _manifest_path(bank_slug: str) -> Path:
    return KB_DIR / bank_slug / "manifest.json"


def _load_manifest(bank_slug: str) -> dict:
    path = _manifest_path(bank_slug)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(bank_slug: str, manifest: dict) -> None:
    path = _manifest_path(bank_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_pdf_links(page_url: str) -> list[str]:
    """Scarica una pagina insight e ne estrae i link a PDF (assoluti). Cerca
    nell'HTML grezzo e non solo nei tag <a>: molti siti tengono i link nel JSON
    che alimenta la pagina."""
    try:
        response = requests.get(page_url, headers=SCRAPING_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        raw_html = response.text.replace("\\/", "/")
        links = re.findall(r"""[^"'\s<>()]+\.pdf""", raw_html, flags=re.IGNORECASE)
        return sorted({urljoin(page_url, link) for link in links})
    except Exception as exc:
        print(f"⚠️  scoperta PDF fallita per {page_url} ({exc}), salto")
        return []


def download_pdf(url: str) -> bytes | None:
    """Scarica un PDF. Best-effort: ritorna None se il download fallisce o se
    il server risponde con qualcosa che non è un PDF (es. pagina di errore)."""
    try:
        response = requests.get(url, headers=SCRAPING_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            print(f"⚠️  {url} non è un PDF, salto")
            return None
        return response.content
    except Exception as exc:
        print(f"⚠️  download PDF fallito per {url} ({exc}), salto")
        return None


def extract_pdf_text(pdf_bytes: bytes) -> str | None:
    """Estrae il testo con pypdf. Ritorna None se il PDF non produce testo
    leggibile (es. scansione senza OCR)."""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        return " ".join(t.strip() for t in pages_text if t.strip()) or None
    except Exception as exc:
        print(f"⚠️  estrazione testo PDF fallita ({exc}), salto")
        return None


def _ingest_pdf(bank_name: str, bank_slug: str, pdf_url: str, manifest: dict, today: str) -> bool:
    """Salva un PDF e il suo testo se il contenuto non è già nel manifest."""
    pdf_bytes = download_pdf(pdf_url)
    if not pdf_bytes:
        return False
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()
    if content_hash in manifest:
        return False
    text = extract_pdf_text(pdf_bytes)
    if not text:
        return False

    pdf_path = KB_DIR / bank_slug / "pdf" / f"{today}__{content_hash[:8]}.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)
    doc = {
        "bank": bank_name,
        "url": pdf_url,
        "title": pdf_url.rsplit("/", 1)[-1],
        "text": text,
        "first_seen": today,
        "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "sha256": content_hash,
        "pdf_path": str(pdf_path.relative_to(KB_DIR)),
    }
    doc_path = KB_DIR / bank_slug / f"{today}__{content_hash[:8]}.json"
    doc_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest[content_hash] = {"url": pdf_url, "first_seen": today}
    return True


def ingest_new_reports(bank_sources: dict[str, str]) -> dict[str, int]:
    """Per ogni banca in bank_sources, scopre i PDF pubblicati e ingerisce
    nella knowledge base solo quelli non ancora presenti nel manifest. I PDF di
    FIXED_PDF_URLS si riscaricano sempre, per accorgersi delle nuove edizioni.
    Ritorna {banca: numero di nuovi documenti aggiunti}."""
    today = datetime.date.today().isoformat()
    new_docs_per_bank: dict[str, int] = {}

    for bank_name, page_url in bank_sources.items():
        bank_slug = _slugify(bank_name)
        manifest = _load_manifest(bank_slug)
        known_urls = {entry["url"] for entry in manifest.values()}

        pdf_links = []
        for page in [page_url, *EXTRA_PDF_PAGES.get(bank_name, [])]:
            pdf_links.extend(discover_pdf_links(page))
        fixed_links = FIXED_PDF_URLS.get(bank_name, [])
        to_fetch = [url for url in sorted(set(pdf_links)) if url not in known_urls and url not in fixed_links]

        new_count = sum(_ingest_pdf(bank_name, bank_slug, url, manifest, today) for url in to_fetch + fixed_links)

        if new_count:
            _save_manifest(bank_slug, manifest)
        new_docs_per_bank[bank_name] = new_count

    return new_docs_per_bank


def iter_document_paths() -> list[Path]:
    """I JSON dei documenti della knowledge base, esclusi i manifest.json."""
    if not KB_DIR.exists():
        return []
    return [p for p in KB_DIR.glob("*/*.json") if p.name != "manifest.json"]


def load_all_documents(bank_filter: str | None = None) -> list[dict]:
    """Legge tutti i documenti salvati nella knowledge base (esclusi i
    manifest.json), opzionalmente filtrati per nome banca."""
    docs = []
    for doc_path in iter_document_paths():
        try:
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if bank_filter and doc.get("bank", "").lower() != bank_filter.lower():
            continue
        docs.append(doc)
    return docs


def build_search_index(max_docs: int = 80, max_chars_per_doc: int = 12_000) -> list[dict]:
    """Costruisce un indice leggero (banca, data, url, titolo, estratto di
    testo) da incorporare nel report HTML per la ricerca lato client, senza
    nessuna chiamata API: la pagina cerca per parole chiave direttamente nel
    browser. Prende i documenti più recenti fino a max_docs, troncando il
    testo di ciascuno per tenere contenuta la dimensione della pagina."""
    docs = sorted(load_all_documents(), key=lambda d: d.get("first_seen", ""), reverse=True)
    index = []
    for doc in docs[:max_docs]:
        index.append({
            "bank": doc.get("bank", ""),
            "date": doc.get("first_seen", ""),
            "url": doc.get("url", ""),
            "title": doc.get("title", ""),
            "text": doc.get("text", "")[:max_chars_per_doc],
        })
    return index
