#!/usr/bin/env bash
# Valuta un'operazione con i dati più recenti: ./semaforo.sh NVDA --prezzo 180 --stop 170 --obiettivo 210
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

export DATA_DIR="${DATA_DIR:-$HOME/dashboard-finanza-data}"
git -C "$DATA_DIR" pull -q --ff-only || echo "⚠️  storico non aggiornato, uso i dati locali" >&2

# L'ambiente sta fuori dal Desktop: iCloud toglie i file dal disco e rende Python lentissimo.
VENV_DIR="$HOME/.venvs/dashboard-finanza"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Primo avvio: creo l'ambiente Python in $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install -q -r requirements.txt
fi

"$VENV_DIR/bin/python" risk_rules.py "$@"
