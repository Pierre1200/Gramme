"""Banc d'essai : état du corpus, résultats de l'extraction, puis du rapprochement.

    make eval                          les vraies factures, dans data/
    make eval CORPUS=data/synthetic    le jeu factice

Tant que `data/truth/` est vide, seul l'état du corpus s'affiche : il dit ce
qu'il manque pour qu'une mesure veuille dire quelque chose. Le banc s'active
dès que `data/extractions/` contient des extractions.

CLI mince : toute la logique est dans `gramme.bench`, pour être testable sans
lancer le script.
"""

import argparse
import sys
from pathlib import Path

from gramme.bench import (
    MAPPING_PROPOSALS,
    corpus_status,
    load_extractions,
    load_mappings,
    run_bench,
)
from gramme.scoring import Verdict, product_spend, score_mappings
from gramme.truth import load_truth

MISMATCHES_SHOWN = 8


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Vraies factures et factures factices ne partagent jamais un dossier :
    # mélangées, elles donneraient une mesure qui ne décrit ni les unes ni les autres.
    parser.add_argument("--corpus", type=Path, default=Path("data"))
    corpus = parser.parse_args().corpus
    truth_dir, extractions_dir = corpus / "truth", corpus / "extractions"

    try:
        invoices = load_truth(truth_dir, corpus / "raw")
    except FileNotFoundError as e:
        if truth_dir.is_dir() and any(truth_dir.glob("*.json")):
            print(e, file=sys.stderr)  # une annotation sans son document source
            return 1
        print(f"Aucune annotation dans {truth_dir}.\n")
        print("  1. ranger les documents :  uv run python scripts/store_documents.py <dossier>")
        print("  2. annoter un document   :  voir COLLECTE.md, « Format d'annotation »")
        return 0
    except ValueError as e:  # annotation invalide : le fichier fautif est nommé
        print(e, file=sys.stderr)
        return 1

    _print_corpus(invoices, truth_dir)
    _print_bench(invoices, extractions_dir)
    try:
        _print_mapping(invoices, corpus)
    except ValueError as e:  # vérité ou réponse invalide : l'entrée fautive est nommée
        print(e, file=sys.stderr)
        return 1
    return 0


def _print_corpus(invoices, truth_dir: Path) -> None:
    status = corpus_status(invoices)
    qualities = ", ".join(f"{n} {q}" for q, n in status.by_quality.most_common())

    print(f"CORPUS — {truth_dir}\n")
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


def _print_bench(invoices, extractions_dir: Path) -> None:
    extractions = load_extractions(extractions_dir)
    print(f"\nBANC D'ESSAI — {extractions_dir}\n")

    if not extractions:
        print("  Aucune extraction : lancer `make read` pour lire les factures.")
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


def _print_mapping(invoices, corpus: Path) -> None:
    mappings = load_mappings(corpus)
    if mappings is None:
        return
    truths, proposals = mappings
    print(f"\nRAPPROCHEMENT — {corpus / MAPPING_PROPOSALS}\n")
    print(f"  {len(truths)} produit(s) dans la vérité, {len(proposals)} réponse(s)")
    if not proposals:
        print("  Aucune proposition : le rapprochement s'activera avec normalize.py.")
        return

    score = score_mappings(truths, proposals, product_spend(invoices))
    # La part des achats d'abord : c'est elle que le rapport du lundi affiche faux.
    print(f"\n  achats mal valorisés  {score.wrong_share:6.1%}")
    print(f"  achats à valider      {score.share(Verdict.ABSTAINED):6.1%}")
    print(f"  achats justes         {score.share(Verdict.CORRECT):6.1%}")
    print(f"  achats non traités    {score.not_processed_share:6.1%}")
    print(
        f"\n  produits : {score.products[Verdict.WRONG]} faux, "
        f"{score.products[Verdict.ABSTAINED]} à valider, "
        f"{score.products[Verdict.CORRECT]} justes, {len(score.not_processed)} non traités"
    )
    if score.unknown:
        print(f"  réponses hors vérité, ignorées : {', '.join(score.unknown)}")
    if score.mismatches:
        shown = min(len(score.mismatches), MISMATCHES_SHOWN)
        print(f"\n  divergences ({shown} sur {len(score.mismatches)}) :")
        for mismatch in score.mismatches[:MISMATCHES_SHOWN]:
            print(
                f"    [{mismatch.verdict:<9}] {mismatch.field:<44} "
                f"attendu {mismatch.expected:<20} obtenu {mismatch.got}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
