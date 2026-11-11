"""La seule porte vers la bibliothèque `factur-x`.

Un Factur-X est un PDF lisible par un humain qui porte, en pièce jointe, les
mêmes données en XML au format de la norme européenne EN 16931. C'est le XML
qui fait foi : le PDF n'est que sa représentation.

Tout ce qui touche à ce format passe par ce module, comme tout appel à un
modèle passera par `llm.py` : le jour où la bibliothèque change, un seul
fichier bouge.
"""

import io
from decimal import Decimal

import facturx

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


def invoice_data(world: World, invoice: Invoice) -> dict:
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


def build_xml(world: World, invoice: Invoice) -> bytes:
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
    """Ressort le XML d'un PDF Factur-X, contrôlé contre le schéma officiel."""
    _, xml = facturx.get_xml_from_pdf(io.BytesIO(pdf), check_xsd=True)
    return xml
