"""Écrit en base les factures électroniques d'un dossier.

    make synthetic-db                                  le jeu factice, base recréée
    uv run python scripts/store_invoices.py --corpus data

Lit chaque PDF de `raw/`, puis de `resent/` (les fichiers renvoyés une seconde
fois), et compte ce que chacun est devenu. La base cible découle du dossier :
un jeu factice va dans `gramme_synthetic`, de vraies factures dans la base
principale. Le script refuse d'écrire ailleurs.

CLI mince : toute la logique est dans `gramme.db` et `gramme.facturx`.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

import psycopg

from gramme.config import get_settings
from gramme.db import StoreOutcome, missing_fields, store_invoice
from gramme.facturx import NotFacturX, read_invoice
from gramme.intake import fingerprint
from gramme.simulation import CORPUS_MARKER

SYNTHETIC_DATABASE = "gramme_synthetic"
# Bases de chiffres inventés : de vraies factures n'y entrent jamais.
FICTIONAL_DATABASES = frozenset({SYNTHETIC_DATABASE, "gramme_demo"})


def target(corpus: Path) -> tuple[str, bool]:
    """L'adresse de la base où écrire ce dossier, et s'il est factice."""
    settings = get_settings()
    if (corpus / CORPUS_MARKER).exists():
        if not settings.synthetic_database_url:
            raise SystemExit("SYNTHETIC_DATABASE_URL manque dans .env (voir .env.example).")
        return settings.synthetic_database_url, True
    return settings.database_url, False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, default=Path("data"))
    corpus = parser.parse_args().corpus

    url, synthetic = target(corpus)
    documents = [
        document
        for folder in ("raw", "resent")
        for document in sorted((corpus / folder).glob("*.pdf"))
    ]
    if not documents:
        print(f"Aucun PDF dans {corpus}/raw ni {corpus}/resent.")
        return 0

    outcomes: Counter[StoreOutcome] = Counter()
    noted: list[str] = []
    not_facturx: list[str] = []

    with psycopg.connect(url, connect_timeout=5) as conn:
        database = conn.execute("SELECT current_database()").fetchone()[0]
        # Le même garde-fou que le seed de démo : des factures inventées dans la
        # base principale, ou de vraies factures dans une base de démonstration,
        # fausseraient tout chiffre qui en sort.
        if synthetic and database != SYNTHETIC_DATABASE:
            raise SystemExit(f"jeu factice : refus d'écrire dans « {database} »")
        if not synthetic and database in FICTIONAL_DATABASES:
            raise SystemExit(f"vraies factures : refus d'écrire dans « {database} »")

        for document in documents:
            try:
                invoice = read_invoice(document.read_bytes())
            except NotFacturX:
                not_facturx.append(document.name)
                continue
            outcome = store_invoice(
                conn,
                invoice,
                source_uri=str(document),
                source_hash=fingerprint(document),
                extracted_by="factur-x",
            )
            outcomes[outcome] += 1
            if outcome is StoreOutcome.TO_REVIEW:
                noted.append(f"{document.name} : à relire ({', '.join(missing_fields(invoice))})")
            elif outcome is StoreOutcome.DUPLICATE_NUMBER:
                noted.append(f"{document.name} : numéro déjà reçu dans un autre fichier")

    print(f"Base « {database} » :")
    print(f"  {outcomes[StoreOutcome.STORED]} facture(s) écrite(s)")
    print(f"  {outcomes[StoreOutcome.ALREADY_STORED]} fichier(s) déjà reçu(s), ignoré(s)")
    print(f"  {outcomes[StoreOutcome.DUPLICATE_NUMBER]} numéro(s) en double")
    print(f"  {outcomes[StoreOutcome.TO_REVIEW]} facture(s) à relire")
    for line in noted:
        print(f"    - {line}")
    if not_facturx:
        print(f"  {len(not_facturx)} document(s) sans XML, à lire autrement")
    return 1 if noted else 0


if __name__ == "__main__":
    sys.exit(main())
