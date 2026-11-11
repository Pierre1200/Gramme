"""La seule porte vers la bibliothèque `factur-x`.

Un Factur-X est un PDF lisible par un humain qui porte, en pièce jointe, les
mêmes données en XML au format de la norme européenne EN 16931. C'est le XML
qui fait foi : le PDF n'est que sa représentation.

Tout ce qui touche à ce format passe par ce module, comme tout appel à un
modèle passera par `llm.py` : le jour où la bibliothèque change, un seul
fichier bouge.
"""

import io
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import facturx
from pypdf.errors import PyPdfError

from gramme.models import Extracted, FieldStatus, RawAdjustment, RawInvoice, RawInvoiceLine

if TYPE_CHECKING:
    # Le lecteur fait partie du produit, le simulateur non : l'écriture ne
    # doit pas le faire charger à chaque facture lue.
    from gramme.simulation import Invoice
    from gramme.world import World

# Le profil EN 16931 porte le détail des lignes, dont Gramme ne peut pas se
# passer. La bibliothèque ne sait pas émettre le profil BASIC.
LEVEL = "en16931"

NOTICE = "Document fictif — jeu d'essai Gramme"

INVOICE, CREDIT_NOTE = "380", "381"
STANDARD_RATE, EXEMPT = "S", "E"
DEPOSIT_EXEMPTION = "Emballage consigné, hors base d'imposition"

# Le XML ne porte pas l'abréviation imprimée mais un code UN/ECE : unités de
# mesure (recommandation 20) ou types d'emballage préfixés d'un X
# (recommandation 21). Tous figurent dans la liste de codes de la norme.
UNIT_CODES = {
    "KG": "KGM",
    "L": "LTR",
    "PCE": "C62",
    "CT": "XCT",  # carton
    "SAC": "XSA",
    "COLIS": "XPK",
    "BTE": "XBX",  # boîte
    "BIB": "XBX",  # la norme n'a pas de code propre à l'outre en carton
    "SEAU": "XBJ",
    "BID": "XJY",  # bidon
    "CS": "XCR",  # casier
}


class NotFacturX(Exception):
    """Le document n'est pas un Factur-X : aucun XML de facture à l'intérieur.

    Ce n'est pas une panne. C'est le signal qu'un document relève de la
    lecture papier, pas de celle-ci.
    """


def unit_code(unit: str) -> str:
    """Code UN/ECE de l'unité imprimée. Une unité inconnue est une erreur."""
    return UNIT_CODES[unit]


def _amount(value: Decimal) -> str:
    return f"{value:.2f}"


def _vat_category(rate: Decimal) -> str:
    return STANDARD_RATE if rate else EXEMPT


def _vat_number(siren: str) -> str:
    """Numéro de TVA au format français, dérivé du SIREN fictif."""
    return f"FR{(12 + 3 * (int(siren) % 97)) % 97:02d}{siren}"


def _party(party) -> dict:
    return {
        "name": party.name,
        "legal_identifier": party.siren,
        "legal_identifier_schemeid": "0002",  # 0002 : le répertoire SIREN
        "vat_identifier": _vat_number(party.siren),
        "addr_l1": party.street,
        "postcode": party.zip,
        "city": party.city,
        "country_code": "FR",
    }


def invoice_data(world: "World", invoice: "Invoice") -> dict:
    """Les données de la facture, sous les codes de la norme (BT-1, BG-25...).

    Un avoir s'écrit en montants POSITIFS : c'est son code de type (381) qui
    dit qu'il se soustrait. Le lecteur devra donc refaire le chemin inverse.
    """
    is_credit_note = invoice.kind == "credit_note"
    sign = -1 if is_credit_note else 1

    lines = []
    for number, line in enumerate(invoice.lines, start=1):
        entry = {
            "BT-126": str(number),
            "BT-153": line.label,
            "BT-146": _amount(line.unit_price),
            "BT-129": format(sign * line.qty, "f"),
            "BT-130": unit_code(line.unit),
            "BT-151": _vat_category(line.vat_rate),
            "BT-152": _amount(line.vat_rate),
            "BT-131": _amount(sign * line.total),
        }
        if line.supplier_ref:
            entry["BT-155"] = line.supplier_ref
        lines.append(entry)

    allowances, charges = [], []
    for adjustment in invoice.adjustments:
        amount = sign * adjustment.amount
        (allowances if amount < 0 else charges).append(
            {
                "amount": _amount(abs(amount)),
                "reason": adjustment.label,
                "vat_category_code": _vat_category(adjustment.vat_rate),
                "vat_rate": _amount(adjustment.vat_rate),
            }
        )

    breakdown = []
    for rate, base in invoice.tax_bases.items():
        entry = {
            "BT-116": _amount(sign * base),
            "BT-117": _amount(sign * invoice.vat_by_rate[rate]),
            "BT-118": _vat_category(rate),
            "BT-119": _amount(rate),
        }
        if not rate:
            entry["BT-120"] = DEPOSIT_EXEMPTION
        breakdown.append(entry)

    data = {
        "BT-1": invoice.number,
        "BT-2": invoice.issued_on,
        "BT-3": CREDIT_NOTE if is_credit_note else INVOICE,
        "BT-5": "EUR",
        "BT-20": world.suppliers[invoice.supplier].terms,
        "BT-72": invoice.issued_on,
        "BG-1": [{"BT-22": NOTICE}],
        "BG-4": _party(world.suppliers[invoice.supplier]),
        "BG-7": _party(world.establishment),
        "BG-25": lines,
        "BG-23": breakdown,
        "BT-106": _amount(sign * invoice.lines_total),
        "BT-109": _amount(sign * invoice.total_ht),
        "BT-110": _amount(sign * (invoice.total_ttc - invoice.total_ht)),
        "BT-112": _amount(sign * invoice.total_ttc),
        "BT-115": _amount(sign * invoice.total_ttc),
    }
    if allowances:
        data["BG-20"] = allowances
        data["BT-107"] = _amount(sum(Decimal(a["amount"]) for a in allowances))
    if charges:
        data["BG-21"] = charges
        data["BT-108"] = _amount(sum(Decimal(c["amount"]) for c in charges))
    if invoice.credits:
        data["BG-3"] = [{"BT-25": invoice.credits}]
    return data


def build_xml(world: "World", invoice: "Invoice") -> bytes:
    """Le XML de la facture, contrôlé contre le schéma officiel.

    Le schéma vérifie la structure. Les règles de gestion de la norme (sommes,
    TVA) demandent un serveur de validation que le projet n'a pas : leur
    cohérence est vérifiée par nos tests.
    """
    return facturx.generate_cii_xml(
        invoice_data(world, invoice), level=LEVEL, check_xsd=True, check_schematron=False
    )


def embed(pdf: bytes, xml: bytes, title: str, author: str) -> bytes:
    """Joint le XML au PDF : c'est ce qui fait d'un PDF un Factur-X."""
    return facturx.generate_from_binary(
        pdf,
        xml,
        flavor="factur-x",
        level=LEVEL,
        check_xsd=False,  # déjà contrôlé à la construction du XML
        pdf_metadata={"title": title, "author": author, "subject": NOTICE, "keywords": "fictif"},
        lang="fr-FR",
    )


def extract_xml(pdf: bytes) -> bytes:
    """Ressort le XML d'un PDF Factur-X, contrôlé contre le schéma officiel.

    Un XML présent mais refusé par le schéma lève l'erreur de la bibliothèque,
    à ne pas confondre avec `NotFacturX` : le premier est un document cassé, le
    second un document d'une autre nature.
    """
    try:
        _, xml = facturx.get_xml_from_pdf(io.BytesIO(pdf), check_xsd=True)
    except PyPdfError as e:
        raise NotFacturX("le fichier n'est pas un PDF lisible") from e
    if not xml:
        raise NotFacturX("le PDF ne contient pas de XML Factur-X")
    return xml


def _read[T](type_: type[T], text: str | None, convert=None, sign: int = 1) -> Extracted[T]:
    """Un champ du XML : lu avec certitude, ou absent. Jamais deviné."""
    if text is None or text == "":
        return Extracted[type_](status=FieldStatus.ABSENT)
    value = (convert or type_)(text)
    if sign < 0:
        value = -value
    return Extracted[type_](
        status=FieldStatus.READ, value=value, raw_text=str(text), confidence=1.0
    )


def read_xml(xml: bytes) -> RawInvoice:
    """Le XML d'une facture électronique, rendu dans le contrat de lecture.

    Aucun montant n'est déduit : le total d'une ligne est celui que porte le
    XML, pas quantité × prix. C'est ce qui garde le recoupement arithmétique
    utile, même sur un document structuré.
    """
    data = facturx.parse_ubl_cii_xml(xml, check_xsd=False)  # contrôlé à l'extraction

    # Un avoir s'écrit en positif avec un code de type ; l'historique de prix
    # l'attend en négatif.
    sign = -1 if facturx.is_credit_note(data) else 1

    lines = [
        RawInvoiceLine(
            line_no=number,
            raw_label=entry["BT-153"],
            supplier_ref=entry.get("BT-155"),
            qty=_read(Decimal, entry.get("BT-129"), sign=sign),
            raw_unit=_read(str, entry.get("BT-130")),
            unit_price_ht=_read(Decimal, entry.get("BT-146")),
            line_total_ht=_read(Decimal, entry.get("BT-131"), sign=sign),
        )
        for number, entry in enumerate(data.get("BG-25") or [], start=1)
    ]

    # BG-20 : remises, à soustraire. BG-21 : frais, à ajouter.
    adjustments = [
        RawAdjustment(
            label=entry.get("reason") or default_label,
            amount_ht=_read(Decimal, entry.get("amount"), sign=sign * direction),
        )
        for group, direction, default_label in (("BG-20", -1, "Remise"), ("BG-21", 1, "Frais"))
        for entry in data.get(group) or []
    ]

    issued_on = data.get("BT-2")
    return RawInvoice(
        supplier_name=_read(str, (data.get("BG-4") or {}).get("name")),
        invoice_number=_read(str, data.get("BT-1")),
        invoice_date=_read(
            date,
            issued_on.isoformat() if isinstance(issued_on, date) else issued_on,
            convert=date.fromisoformat,
        ),
        total_ht=_read(Decimal, data.get("BT-109"), sign=sign),
        total_ttc=_read(Decimal, data.get("BT-112"), sign=sign),
        lines=lines,
        adjustments=adjustments,
    )


def read_invoice(pdf: bytes) -> RawInvoice:
    """Lit un PDF Factur-X. Lève `NotFacturX` si ce n'en est pas un."""
    return read_xml(extract_xml(pdf))
