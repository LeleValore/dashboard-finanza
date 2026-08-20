#!/usr/bin/env python3
"""
Knowledge base locale dei bank report (PDF) dei "Giganti" catalogati in
generate_report.py (BANK_SOURCES). Ogni esecuzione dello scraping scopre i
PDF pubblicati sulle pagine insight di ciascuna banca, scarica ed estrae il
testo solo di quelli non ancora noti (tracciati in un manifest per banca) e
li salva come singoli file JSON sotto knowledge_base/<banca>/. Questo storico
si accumula nel tempo (più PDF a settimana per banca restano tutti, non
vengono sovrascritti). build_search_index() ne ricava un indice leggero che
generate_report.py incorpora nel report per la ricerca lato client (nessuna
chiamata API, gratuita).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from io import BytesIO

BASE_DIR = Path(__file__).parent
KB_DIR = BASE_DIR / "knowledge_base"

REQUEST_TIMEOUT = 15
SCRAPING_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

PDF_TEXT_MAX_CHARS = 20_000


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
    """Scarica una pagina insight e ne estrae i link a PDF (assoluti)."""
    try:
        response = requests.get(page_url, headers=SCRAPING_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links = set()
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if ".pdf" in href.lower():
                links.add(urljoin(page_url, href))
        return sorted(links)
    except Exception as exc:
        print(f"⚠️  scoperta PDF fallita per {page_url} ({exc}), salto")
        return []


def download_and_extract_pdf(url: str) -> str | None:
    """Scarica un PDF ed estrae il testo con pypdf. Best-effort: ritorna
    None se il download fallisce o il PDF non produce testo leggibile
    (es. scansione senza OCR)."""
    try:
        response = requests.get(url, headers=SCRAPING_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        reader = PdfReader(BytesIO(response.content))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = " ".join(t.strip() for t in pages_text if t.strip())
        if not text:
            return None
        return text[:PDF_TEXT_MAX_CHARS]
    except Exception as exc:
        print(f"⚠️  estrazione PDF fallita per {url} ({exc}), salto")
        return None


def ingest_new_reports(bank_sources: dict[str, str]) -> dict[str, int]:
    """Per ogni banca in bank_sources, scopre i PDF pubblicati e ingerisce
    nella knowledge base solo quelli non ancora presenti nel manifest.
    Ritorna {banca: numero di nuovi documenti aggiunti}."""
    today = datetime.date.today().isoformat()
    new_docs_per_bank: dict[str, int] = {}

    for bank_name, page_url in bank_sources.items():
        bank_slug = _slugify(bank_name)
        manifest = _load_manifest(bank_slug)

        pdf_links = discover_pdf_links(page_url)
        new_count = 0

        for pdf_url in pdf_links:
            url_hash = hashlib.sha256(pdf_url.encode("utf-8")).hexdigest()
            if url_hash in manifest:
                continue

            text = download_and_extract_pdf(pdf_url)
            if not text:
                continue

            title = pdf_url.rsplit("/", 1)[-1]
            doc = {
                "bank": bank_name,
                "url": pdf_url,
                "title": title,
                "text": text,
                "first_seen": today,
                "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            doc_path = KB_DIR / bank_slug / f"{today}__{url_hash[:8]}.json"
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

            manifest[url_hash] = {"url": pdf_url, "first_seen": today}
            new_count += 1

        if pdf_links:
            _save_manifest(bank_slug, manifest)
        new_docs_per_bank[bank_name] = new_count

    return new_docs_per_bank


def load_all_documents(bank_filter: str | None = None) -> list[dict]:
    """Legge tutti i documenti salvati nella knowledge base (esclusi i
    manifest.json), opzionalmente filtrati per nome banca."""
    if not KB_DIR.exists():
        return []
    docs = []
    for doc_path in KB_DIR.glob("*/*.json"):
        if doc_path.name == "manifest.json":
            continue
        try:
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if bank_filter and doc.get("bank", "").lower() != bank_filter.lower():
            continue
        docs.append(doc)
    return docs


def build_search_index(max_docs: int = 80, max_chars_per_doc: int = 3000) -> list[dict]:
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
