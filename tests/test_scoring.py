"""Le barème est la seule chose que le banc ne mesure pas.

Si `comparer_champ` se trompe, tous les scores futurs sont faux et rien ne le
signalera. La matrice 3×3 est donc verrouillée cas par cas, explicitement.
"""

from datetime import date
from decimal import Decimal

import pytest

from gramme.models import Extracted, FieldStatus, RawInvoice, RawInvoiceLine
from gramme.scoring import (
    CHAMPS_LIGNE,
    Verdict,
    agreger,
    apparier_lignes,
    comparer_champ,
    scorer_facture,
)
from gramme.truth import Regime, SourceQuality, TruthInvoice, TruthLine


def lu(valeur, confiance: float = 0.95) -> Extracted:
    """Un champ lu par le modèle, avec la valeur donnée."""
    return Extracted[type(valeur)](
        status=FieldStatus.READ, value=valeur, raw_text=str(valeur), confidence=confiance
    )


ILLISIBLE = Extracted[Decimal](status=FieldStatus.UNREADABLE, raw_text="8,4?")
ABSENT = Extracted[Decimal](status=FieldStatus.ABSENT)


class TestMatriceVerite:
    """Les neuf cas de vérité × réponse, un test chacun."""

    # --- Le document porte une valeur lisible -----------------------------
    def test_valeur_bien_lue(self):
        assert comparer_champ(Decimal("8.47"), False, lu(Decimal("8.47"))) is Verdict.CORRECT

    def test_valeur_mal_lue_est_fausse(self):
        # Le cas qui corrompt price_point, qui est append-only.
        assert comparer_champ(Decimal("8.74"), False, lu(Decimal("8.47"))) is Verdict.FAUX

    def test_valeur_non_lue_est_une_abstention(self):
        # Le modèle ne sait pas et le dit : ça part en validation humaine.
        assert comparer_champ(Decimal("8.47"), False, ILLISIBLE) is Verdict.ABSTENTION

    def test_valeur_niee_est_fausse(self):
        # ABSENT ne déclenche aucune relecture : la valeur est perdue en
        # silence. C'est pire qu'une abstention, donc c'est un faux.
        assert comparer_champ(Decimal("8.47"), False, ABSENT) is Verdict.FAUX

    # --- Le champ est présent mais illisible ------------------------------
    def test_illisible_reconnu(self):
        assert comparer_champ(None, True, ILLISIBLE) is Verdict.CORRECT

    def test_illisible_devine_est_faux(self):
        # Le pire cas au regard de la décision 5 : il ne peut pas savoir, et
        # il affirme quand même.
        assert comparer_champ(None, True, lu(Decimal("8.47"))) is Verdict.FAUX

    def test_illisible_nie_est_faux(self):
        assert comparer_champ(None, True, ABSENT) is Verdict.FAUX

    # --- Le document ne porte pas ce champ --------------------------------
    def test_absent_reconnu(self):
        assert comparer_champ(None, False, ABSENT) is Verdict.CORRECT

    def test_absent_halluciné_est_faux(self):
        assert comparer_champ(None, False, lu(Decimal("8.47"))) is Verdict.FAUX

    def test_absent_signale_illisible_est_une_abstention(self):
        # Coûte du temps humain pour rien, mais ne corrompt rien.
        assert comparer_champ(None, False, ILLISIBLE) is Verdict.ABSTENTION


class TestComparaisonDeValeurs:
    def test_decimal_compare_par_valeur(self):
        assert comparer_champ(Decimal("2"), False, lu(Decimal("2.00"))) is Verdict.CORRECT

    def test_texte_insensible_a_la_casse_et_aux_espaces(self):
        assert comparer_champ("BEURRE  DOUX", False, lu("beurre doux")) is Verdict.CORRECT

    def test_accents_significatifs(self):
        # « CREME » lu au lieu de « CRÈME » est une vraie faiblesse de lecture :
        # la masquer reviendrait à saboter la mesure.
        assert comparer_champ("CRÈME", False, lu("CREME")) is Verdict.FAUX

    def test_dates(self):
        assert comparer_champ(date(2026, 8, 12), False, lu(date(2026, 8, 12))) is Verdict.CORRECT


class TestAppariementDesLignes:
    def _v(self, label: str) -> TruthLine:
        return TruthLine(raw_label=label)

    def _o(self, label: str) -> RawInvoiceLine:
        return RawInvoiceLine(
            raw_label=label,
            qty=lu(Decimal("1")),
            raw_unit=lu("KG"),
            unit_price_ht=lu(Decimal("1")),
            line_total_ht=lu(Decimal("1")),
        )

    def test_ordre_different_sans_incidence(self):
        paires, manquantes, inventees = apparier_lignes(
            [self._v("BEURRE"), self._v("CREME")], [self._o("CREME"), self._o("BEURRE")]
        )
        assert len(paires) == 2 and not manquantes and not inventees

    def test_une_ligne_ratee_ne_decale_pas_les_suivantes(self):
        # Le vrai intérêt de l'appariement par libellé : en positionnel, ce cas
        # produirait trois erreurs au lieu d'une seule ligne manquante.
        paires, manquantes, inventees = apparier_lignes(
            [self._v("A"), self._v("B"), self._v("C"), self._v("D")],
            [self._o("A"), self._o("C"), self._o("D")],
        )
        assert len(paires) == 3
        assert [ligne.raw_label for ligne in manquantes] == ["B"]
        assert not inventees

    def test_libelles_en_double(self):
        # Deux lignes du même article à deux prix : courant sur une facture.
        paires, manquantes, inventees = apparier_lignes(
            [self._v("BEURRE"), self._v("BEURRE")], [self._o("BEURRE"), self._o("BEURRE")]
        )
        assert len(paires) == 2 and not manquantes and not inventees

    def test_ligne_inventee(self):
        _, manquantes, inventees = apparier_lignes([self._v("A")], [self._o("A"), self._o("Z")])
        assert not manquantes
        assert [ligne.raw_label for ligne in inventees] == ["Z"]


def _facture_attendue(**surcharges) -> TruthInvoice:
    base = dict(
        source_file="f.jpg",
        quality=SourceQuality.PHOTO,
        regime=Regime.HT,
        supplier_name="METRO",
        invoice_number="FA-1187",
        invoice_date=date(2026, 8, 12),
        total_ht=Decimal("100.00"),
        total_ttc=Decimal("110.00"),
        lines=[
            TruthLine(
                raw_label="BEURRE",
                qty=Decimal("2"),
                raw_unit="KG",
                unit_price_ht=Decimal("8.47"),
                line_total_ht=Decimal("16.94"),
            )
        ],
    )
    return TruthInvoice(**(base | surcharges))


def _facture_obtenue(**surcharges) -> RawInvoice:
    base = dict(
        supplier_name=lu("METRO"),
        invoice_number=lu("FA-1187"),
        invoice_date=lu(date(2026, 8, 12)),
        total_ht=lu(Decimal("100.00")),
        total_ttc=lu(Decimal("110.00")),
        lines=[
            RawInvoiceLine(
                raw_label="BEURRE",
                qty=lu(Decimal("2")),
                raw_unit=lu("KG"),
                unit_price_ht=lu(Decimal("8.47")),
                line_total_ht=lu(Decimal("16.94")),
            )
        ],
    )
    return RawInvoice(**(base | surcharges))


class TestScorerFacture:
    def test_extraction_parfaite(self):
        rapport = scorer_facture(_facture_attendue(), _facture_obtenue())
        assert rapport.verdicts[Verdict.FAUX] == 0
        assert rapport.taux_faux == 0.0
        assert rapport.champs_evalues == 9  # 5 d'en-tête + 4 sur l'unique ligne
        assert not rapport.erreurs

    def test_un_prix_faux_est_signale_avec_les_valeurs(self):
        obtenue = _facture_obtenue(
            lines=[
                RawInvoiceLine(
                    raw_label="BEURRE",
                    qty=lu(Decimal("2")),
                    raw_unit=lu("KG"),
                    unit_price_ht=lu(Decimal("8.74")),
                    line_total_ht=lu(Decimal("16.94")),
                )
            ]
        )
        rapport = scorer_facture(_facture_attendue(), obtenue)
        assert rapport.verdicts[Verdict.FAUX] == 1
        (erreur,) = rapport.erreurs
        assert erreur.champ == "ligne[0].unit_price_ht"
        assert (erreur.attendu, erreur.obtenu) == ("8.47", "8.74")

    def test_ligne_manquante_comptee_a_part(self):
        attendue = _facture_attendue(
            lines=[TruthLine(raw_label="BEURRE"), TruthLine(raw_label="CREME")]
        )
        rapport = scorer_facture(attendue, _facture_obtenue())
        assert rapport.lignes_manquantes == ["CREME"]

    def test_champs_peu_confiants_comptes_comme_cout(self):
        # Corrects mais sous le seuil : justes, donc pas d'erreur — mais ils
        # coûtent du temps humain, compté séparément.
        obtenue = _facture_obtenue(total_ht=lu(Decimal("100.00"), confiance=0.4))
        rapport = scorer_facture(attendue := _facture_attendue(), obtenue, seuil_validation=0.90)
        assert rapport.verdicts[Verdict.FAUX] == 0
        assert rapport.a_valider == 1
        assert attendue.total_ht == Decimal("100.00")


class TestAgreger:
    def test_ventilation_par_qualite(self):
        photo = scorer_facture(_facture_attendue(), _facture_obtenue())
        pdf = scorer_facture(
            _facture_attendue(source_file="g.pdf", quality=SourceQuality.PDF), _facture_obtenue()
        )
        bilan = agreger([photo, pdf])
        assert set(bilan.par_qualite) == {SourceQuality.PHOTO, SourceQuality.PDF}
        assert bilan.taux_correct == 1.0
        assert bilan.taux_faux == 0.0

    def test_bilan_vide_ne_divise_pas_par_zero(self):
        bilan = agreger([])
        assert bilan.taux_faux == 0.0 and bilan.taux_correct == 0.0


@pytest.mark.parametrize("seuil", [0.0, 0.5, 0.9, 1.0])
def test_le_seuil_ne_change_pas_les_verdicts(seuil):
    """Justesse et coût sont orthogonaux : le seuil ne bouge que le second."""
    rapport = scorer_facture(_facture_attendue(), _facture_obtenue(), seuil_validation=seuil)
    assert rapport.verdicts[Verdict.FAUX] == 0
    assert rapport.verdicts[Verdict.CORRECT] == 9


class TestLignesRatees:
    """Une ligne perdue ou inventée doit peser dans le taux de faux.

    Régression corrigée : elles étaient comptées à côté du taux, qui restait
    donc flatteur pendant que l'extraction perdait la facture.
    """

    def _attendue_10_lignes(self) -> TruthInvoice:
        return _facture_attendue(
            lines=[
                TruthLine(
                    raw_label=f"ARTICLE {i}",
                    qty=Decimal("1"),
                    raw_unit="KG",
                    unit_price_ht=Decimal("10"),
                    line_total_ht=Decimal("10"),
                )
                for i in range(10)
            ]
        )

    def _obtenue_n_lignes(self, n: int) -> RawInvoice:
        return _facture_obtenue(
            lines=[
                RawInvoiceLine(
                    raw_label=f"ARTICLE {i}",
                    qty=lu(Decimal("1")),
                    raw_unit=lu("KG"),
                    unit_price_ht=lu(Decimal("10")),
                    line_total_ht=lu(Decimal("10")),
                )
                for i in range(n)
            ]
        )

    def test_perdre_neuf_lignes_sur_dix_ne_peut_pas_paraitre_bon(self):
        rapport = scorer_facture(self._attendue_10_lignes(), self._obtenue_n_lignes(1))
        assert len(rapport.lignes_manquantes) == 9
        assert rapport.taux_faux > 0.5  # avant correction : 11 %

    def test_lignes_hallucinees_penalisees(self):
        rapport = scorer_facture(self._attendue_10_lignes(), self._obtenue_n_lignes(15))
        assert len(rapport.lignes_inventees) == 5
        assert rapport.verdicts[Verdict.FAUX] >= 5 * len(CHAMPS_LIGNE)

    def test_extraction_parfaite_reste_a_zero(self):
        # Le correctif ne doit pas inventer de faux quand tout va bien.
        rapport = scorer_facture(self._attendue_10_lignes(), self._obtenue_n_lignes(10))
        assert rapport.taux_faux == 0.0
        assert rapport.faux_critiques == 0

    def test_une_ligne_ratee_apparait_dans_les_erreurs(self):
        rapport = scorer_facture(self._attendue_10_lignes(), self._obtenue_n_lignes(9))
        motifs = [e.champ for e in rapport.erreurs]
        assert "ligne manquante" in motifs


class TestChampsCritiques:
    def test_un_prix_faux_est_critique(self):
        obtenue = _facture_obtenue(
            lines=[
                RawInvoiceLine(
                    raw_label="BEURRE",
                    qty=lu(Decimal("2")),
                    raw_unit=lu("KG"),
                    unit_price_ht=lu(Decimal("8.74")),
                    line_total_ht=lu(Decimal("16.94")),
                )
            ]
        )
        rapport = scorer_facture(_facture_attendue(), obtenue)
        assert rapport.faux_critiques == 1

    def test_un_numero_de_facture_faux_ne_l_est_pas(self):
        # Rattrapable : il ne part pas dans price_point.
        obtenue = _facture_obtenue(invoice_number=lu("FA-AUTRE"))
        rapport = scorer_facture(_facture_attendue(), obtenue)
        assert rapport.verdicts[Verdict.FAUX] == 1
        assert rapport.faux_critiques == 0
