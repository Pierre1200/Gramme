"""Banc d'essai : état du corpus, puis résultats de l'extraction.

    make eval

Tant que `data/truth/` est vide, seul l'état du corpus s'affiche : il dit ce
qu'il manque pour qu'une mesure veuille dire quelque chose. Le banc s'active
dès que `data/extractions/` contient des extractions.

CLI mince : toute la logique est dans `gramme.bench`, pour être testable sans
lancer le script.
"""

import sys
from pathlib import Path

from gramme.bench import EXTRACTIONS_DIR, corpus_status, load_extractions, run_bench
from gramme.scoring import Verdict
from gramme.truth import TRUTH_DIR, load_truth

MISMATCHES_SHOWN = 8


def main() -> int:
    try:
        invoices = load_truth()
    except FileNotFoundError:
        print(f"Aucune annotation dans {TRUTH_DIR}.\n")
        print("  1. ranger les documents :  uv run python scripts/store_documents.py <dossier>")
        print("  2. annoter un document   :  voir COLLECTE.md, « Format d'annotation »")
        return 0
    except ValueError as e:  # annotation invalide : le fichier fautif est nommé
        print(e, file=sys.stderr)
        return 1

    _print_corpus(invoices)
    _print_bench(invoices)
    return 0


def _print_corpus(invoices) -> None:
    status = corpus_status(invoices)
    qualities = ", ".join(f"{n} {q}" for q, n in status.by_quality.most_common())

    print(f"CORPUS — {TRUTH_DIR}\n")
    print(f"  {status.invoices} document(s) annoté(s) : {qualities}")
    if status.longest_series_supplier:
        print(
            f"  série la plus longue : {status.longest_series} date(s) distincte(s) "
            f"chez {status.longest_series_supplier}"
        )
    print(
        f"  {status.with_pitfalls} facture(s) tordue(s) · "
        f"{status.unreadable_fields} champ(s) illisible(s)"
    )

    if status.ready:
        print("\n  Le corpus remplit les critères de sortie de COLLECTE.md.")
    else:
        print("\n  Il manque encore :")
        for gap in status.missing:
            print(f"    - {gap}")


def _print_bench(invoices) -> None:
    extractions = load_extractions()
    print(f"\nBANC D'ESSAI — {EXTRACTIONS_DIR}\n")

    if not extractions:
        print("  Aucune extraction : le banc s'activera quand extract.py en produira.")
        return

    corpus, not_extracted = run_bench(invoices, extractions)
    scored = len(corpus.invoices)
    print(f"  {scored} document(s) noté(s), {len(not_extracted)} non extrait(s)")

    total = sum(corpus.verdicts.values())
    if not total:
        return

    # Le taux de faux d'abord : c'est le seul qui corrompt des données.
    print(
        f"\n  taux de faux      {corpus.wrong_rate:6.1%}   dont {corpus.critical_wrong} critique(s)"
    )
    print(f"  correct           {corpus.correct_rate:6.1%}")
    print(f"  abstentions       {corpus.verdicts[Verdict.ABSTAINED] / total:6.1%}")
    print(f"  lignes manquantes {corpus.missing_lines:6}   inventées {corpus.invented_lines}")
    print(f"  champs à valider  {corpus.to_review:6}")

    print("\n  par qualité de source :")
    for quality, verdicts in sorted(corpus.by_quality.items()):
        counted = sum(verdicts.values())
        print(f"    {quality:<12} {verdicts[Verdict.WRONG] / counted:6.1%} de faux")

    mismatches = [m for score in corpus.invoices for m in score.mismatches]
    if mismatches:
        print(f"\n  divergences ({min(len(mismatches), MISMATCHES_SHOWN)} sur {len(mismatches)}) :")
        for mismatch in mismatches[:MISMATCHES_SHOWN]:
            print(
                f"    [{mismatch.verdict:<9}] {mismatch.field:<28} "
                f"attendu {mismatch.expected:<14} obtenu {mismatch.got}"
            )

    if not_extracted:
        print(f"\n  non extraits : {', '.join(Path(f).name for f in not_extracted)}")


if __name__ == "__main__":
    raise SystemExit(main())
