#!/usr/bin/env bash
# Avvia la Dashboard Finanza con un solo comando: ./avvia.sh
# Crea/aggiorna automaticamente l'ambiente Python la prima volta, poi genera il report.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Errore: python3 non trovato. Installalo da https://www.python.org/downloads/ e riprova." >&2
  exit 1
fi

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "Primo avvio: creo l'ambiente Python in $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

pip install -q --upgrade pip
pip install -q -r requirements.txt

# Carica le variabili da .env se presente (ANTHROPIC_API_KEY, SITE_PASSWORD).
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

python3 generate_report.py "$@"
