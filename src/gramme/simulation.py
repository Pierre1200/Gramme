"""Une année de factures pour l'établissement fictif.

Quatre étapes, chacune une fonction pure : ventes du jour, besoins bruts en
ingrédients, commandes à chaque livraison, factures. Les factures sont donc
cohérentes entre elles et avec les recettes, et l'on sait d'avance ce que le
rapport doit montrer.

Ce module calcule des montants en Python. Ce n'est pas une entorse à la
décision 2 de CLAUDE.md : il joue le logiciel de facturation des fournisseurs,
extérieur au produit. Les calculs de Gramme, eux, restent en SQL.

Tout le hasard vient de la graine : la même graine et la même date de fin
redonnent exactement les mêmes factures.
"""

import math
import random
import re
from calendar import monthrange
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from gramme.truth import MappingTruth, SourceQuality, TruthInvoice, TruthLine, VatBasis
from gramme.world import WEEKDAYS, Product, Supplier, World

CENT = Decimal("0.01")
GRAM = Decimal("0.001")

# Commander 10 % au-dessus du besoin : personne ne tombe en rupture un samedi.
SAFETY_STOCK = Decimal("1.10")
# Sous 150 g, une pièce pesée ne se commande pas : elle attend la livraison suivante.
MIN_WEIGHED_QTY = Decimal("0.150")
# Les ventes du jour s'écartent de la moyenne, le cours du jour aussi.
SALES_NOISE = 0.12
MARKET_NOISE = 0.025
# Fichier déposé à la racine d'un jeu factice généré : c'est lui qui distingue
# un dossier de factures inventées d'un dossier de vraies factures.
CORPUS_MARKER = ".generated-by-gramme"

# Libellé de la ligne qui rembourse une consigne au retour des casiers.
RETURN_PREFIX = "RETOUR "

# La simulation déborde de la période, puis l'excédent est écarté : sans cela
# la dernière commande n'aurait pas de « livraison suivante » pour borner ce
# qu'elle couvre.
LOOKAHEAD = timedelta(days=14)

_HIDDEN_PACK = re.compile(r"\d+\s?X\s?\d")


def percent(rate: Decimal) -> str:
    """Un taux tel qu'on l'imprime : 2 %, 5,5 %."""
    return format(rate.normalize(), "f").replace(".", ",") + " %"


def money(amount: Decimal) -> Decimal:
    """Arrondi commercial au centime : 105,875 s'imprime 105,88."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class InvoiceLine:
    label: str
    supplier_ref: str | None
    qty: Decimal
    unit: str
    unit_price: Decimal
    vat_rate: Decimal
    # Ce que le générateur sait et que la facture ne dit pas : la vérité du
    # rapprochement et du colisage.
    ingredient: str | None = None
    pack_qty: Decimal | None = None
    base_unit: str | None = None

    @property
    def total(self) -> Decimal:
        return money(self.qty * self.unit_price)


@dataclass(frozen=True)
class Adjustment:
    """Remise (montant négatif) ou frais (montant positif) de pied de facture."""

    label: str
    amount: Decimal
    vat_rate: Decimal


@dataclass(frozen=True)
class Invoice:
    supplier: str
    supplier_name: str
    supplier_siren: str
    number: str
    issued_on: date
    kind: Literal["invoice", "credit_note"]
    lines: tuple[InvoiceLine, ...]
    adjustments: tuple[Adjustment, ...] = ()
    pitfalls: tuple[str, ...] = ()
    # Pour un avoir : le numéro de la facture qu'il corrige.
    credits: str | None = None

    @property
    def lines_total(self) -> Decimal:
        return sum((line.total for line in self.lines), Decimal(0))

    @property
    def total_ht(self) -> Decimal:
        return self.lines_total + sum((a.amount for a in self.adjustments), Decimal(0))

    @property
    def tax_bases(self) -> dict[Decimal, Decimal]:
        """Base hors taxes de chaque taux de TVA, remises et frais compris."""
        bases: dict[Decimal, Decimal] = defaultdict(Decimal)
        for line in self.lines:
            bases[line.vat_rate] += line.total
        for adjustment in self.adjustments:
            bases[adjustment.vat_rate] += adjustment.amount
        return dict(sorted(bases.items()))

    @property
    def vat_by_rate(self) -> dict[Decimal, Decimal]:
        """TVA par taux, calculée sur la base nette de chaque taux."""
        return {rate: money(base * rate / 100) for rate, base in self.tax_bases.items()}

    @property
    def total_ttc(self) -> Decimal:
        return self.total_ht + sum(self.vat_by_rate.values(), Decimal(0))


@dataclass(frozen=True)
class Corpus:
    start: date
    end: date
    # Portions vendues par jour et par recette.
    sales: dict[date, dict[str, int]]
    invoices: tuple[Invoice, ...]
    # Numéros des factures envoyées une seconde fois, à l'identique.
    resent: tuple[str, ...]


def _stream(seed: int, *parts: str) -> random.Random:
    """Un générateur par usage, pour qu'ajouter un produit ne décale pas les autres."""
    return random.Random(":".join((str(seed), *parts)))


def _round_at_random(value: float, rng: random.Random) -> int:
    """Arrondi dont la moyenne reste juste : 2,3 donne 2, et 3 trois fois sur dix."""
    whole = math.floor(value)
    return whole + (rng.random() < value - whole)


def is_closed(world: World, day: date) -> bool:
    month_day = f"{day:%m-%d}"
    for first, last in world.simulation.closures:
        if first <= last:
            if first <= month_day <= last:
                return True
        # La fermeture enjambe le nouvel an.
        elif month_day >= first or month_day <= last:
            return True
    return False


# --------------------------------------------------------------------- ventes


def simulate_sales(world: World, start: date, end: date, seed: int) -> dict[date, dict[str, int]]:
    """Portions vendues chaque jour, par recette."""
    sales: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]

    for key, recipe in world.recipes.items():
        if not recipe.is_sellable:
            continue
        activity = world.activities[recipe.activity]
        rng = _stream(seed, "sales", key)
        mean_month = sum(activity.months) / 12
        week_weight = sum(activity.weekdays)

        def weekly(day: date, recipe=recipe, activity=activity, mean_month=mean_month) -> float:
            return float(recipe.per_week) * activity.months[day.month - 1] / mean_month

        if activity.lumpy:
            # Une commande traiteur est un lot entier, posé sur un jour.
            for monday in (day for day in days if day.weekday() == 0):
                batches = _round_at_random(weekly(monday) / recipe.portions, rng)
                for offset in rng.choices(range(7), weights=activity.weekdays, k=batches):
                    day = monday + timedelta(days=offset)
                    if day <= end and not is_closed(world, day):
                        sales[day][key] += recipe.portions
        else:
            for day in days:
                share = activity.weekdays[day.weekday()] / week_weight
                if share == 0 or is_closed(world, day):
                    continue
                expected = weekly(day) * share * rng.gauss(1, SALES_NOISE)
                sales[day][key] += _round_at_random(max(expected, 0), rng)

    return {day: dict(by_recipe) for day, by_recipe in sorted(sales.items())}


# -------------------------------------------------------------------- besoins


def gross_needs(world: World) -> dict[str, dict[str, Decimal]]:
    """Quantité BRUTE de chaque ingrédient pour une portion vendue.

    Le brut, c'est ce qu'il faut acheter : la quantité nette divisée par le
    rendement, propagée à travers les sous-recettes.
    """
    cache: dict[str, dict[str, Decimal]] = {}

    def batch_needs(key: str) -> dict[str, Decimal]:
        if key not in cache:
            needs: dict[str, Decimal] = defaultdict(Decimal)
            for component in world.recipes[key].components:
                gross = component.qty / component.yield_ratio
                if component.ingredient is not None:
                    needs[component.ingredient] += gross
                else:
                    child = world.recipes[component.recipe]
                    for ingredient, qty in batch_needs(component.recipe).items():
                        needs[ingredient] += qty * gross / child.batch.qty
            cache[key] = dict(needs)
        return cache[key]

    return {
        key: {ingredient: qty / recipe.portions for ingredient, qty in batch_needs(key).items()}
        for key, recipe in world.recipes.items()
        if recipe.is_sellable
    }


def daily_consumption(
    world: World, sales: dict[date, dict[str, int]]
) -> dict[date, dict[str, Decimal]]:
    """Ce que la cuisine consomme chaque jour, en unité de base."""
    needs = gross_needs(world)
    consumption: dict[date, dict[str, Decimal]] = {}
    for day, by_recipe in sales.items():
        used: dict[str, Decimal] = defaultdict(Decimal)
        for recipe, portions in by_recipe.items():
            for ingredient, qty in needs[recipe].items():
                used[ingredient] += qty * portions
        consumption[day] = dict(used)
    return consumption


# ----------------------------------------------------------------------- prix


def _interpolate(points: list[tuple[float, float]], x: float) -> float:
    """Valeur entre deux points d'une trajectoire, figée hors de ses bornes."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def _season_factor(months: list[float], day: date) -> float:
    """Multiplicateur de saison, lissé entre le milieu de deux mois."""
    position = (day.month - 1) + (day.day - 15) / 30
    lower = math.floor(position)
    blend = position - lower
    return months[lower % 12] * (1 - blend) + months[(lower + 1) % 12] * blend


def price_on(
    world: World, supplier: Supplier, product: Product, day: date, end: date, seed: int
) -> Decimal:
    """Prix HT d'une unité facturée ce jour-là.

    Le prix du fichier est le prix réel relevé : il vaut tel quel la dernière
    semaine. Trajectoire et saison décrivent le chemin pour y arriver.
    """
    if (end - day).days < 7:
        return product.price

    # Un tarif est révisé le premier du mois et vaut tout le mois ; un cours
    # bouge chaque jour.
    reference = day
    if supplier.pricing == "list":
        month_end = day.replace(day=monthrange(day.year, day.month)[1])
        reference = min(month_end, end)

    factor = 1.0
    if product.trend:
        points = world.trends[product.trend]
        weeks_before_end = -(end - reference).days / 7
        factor *= _interpolate(points, weeks_before_end) / points[-1][1]
    if product.season:
        months = world.seasons[product.season]
        factor *= _season_factor(months, reference) / _season_factor(months, end)
    if supplier.pricing == "market":
        rng = _stream(seed, "price", supplier.prefix, product.label, day.isoformat())
        factor *= 1 + rng.uniform(-MARKET_NOISE, MARKET_NOISE)

    return money(product.price * Decimal(str(factor)))


# ------------------------------------------------------------------ commandes


def delivery_days(
    world: World, supplier: Supplier, start: date, end: date, until: date | None = None
) -> list[date]:
    """Jours de livraison du fournisseur, de `start` à `until` (par défaut `end`)."""
    days: list[date] = []
    for n in range(((until or end) - start).days + 1):
        day = start + timedelta(days=n)
        # Semaines comptées depuis la fin : un fournisseur qui livre une
        # semaine sur deux livre toujours la dernière.
        week = (end - day).days // 7
        if (
            WEEKDAYS[day.weekday()] in supplier.delivery
            and week % supplier.every == 0
            and not is_closed(world, day)
        ):
            days.append(day)
    return days


def _daily_need(
    world: World, product: Product, day: date, consumption: dict[date, dict[str, Decimal]]
) -> Decimal:
    """Besoin du jour, en unités facturées de ce produit."""
    if product.ingredient is None:
        return Decimal(0) if is_closed(world, day) else product.per_week / 7
    used = consumption.get(day, {}).get(product.ingredient, Decimal(0))
    return used * product.share / product.contains


def _orders(
    world: World,
    supplier: Supplier,
    consumption: dict[date, dict[str, Decimal]],
    deliveries: list[date],
    seed: int,
) -> list[tuple[date, list[tuple[Product, Decimal]]]]:
    """Quantité commandée de chaque produit, à chaque livraison.

    Une livraison couvre le besoin jusqu'à la suivante, stock restant déduit.
    Un colis ne se coupe pas : la commande est arrondie au colis supérieur, et
    le reliquat sert la fois d'après.
    """
    stock: dict[str, Decimal] = defaultdict(Decimal)
    orders: list[tuple[date, list[tuple[Product, Decimal]]]] = []

    for day, next_day in zip(deliveries, deliveries[1:], strict=False):
        period = [day + timedelta(days=n) for n in range((next_day - day).days)]
        ordered: list[tuple[Product, Decimal]] = []

        for product in supplier.products:
            need = sum((_daily_need(world, product, d, consumption) for d in period), Decimal(0))
            missing = need * SAFETY_STOCK - stock[product.label]
            qty = Decimal(0)
            if missing > 0 and product.weight == "variable":
                # Une pièce pesée ne tombe jamais sur le poids demandé.
                rng = _stream(seed, "weight", supplier.prefix, product.label, day.isoformat())
                weighed = (missing * Decimal(str(rng.uniform(0.97, 1.04)))).quantize(GRAM)
                qty = weighed if weighed >= MIN_WEIGHED_QTY else Decimal(0)
            elif missing > 0:
                qty = Decimal(math.ceil(missing))

            stock[product.label] = max(stock[product.label] + qty - need, Decimal(0))
            if qty > 0:
                ordered.append((product, qty))

        if ordered:
            orders.append((day, ordered))
    return orders


# ------------------------------------------------------------------- factures


def _label_on(product: Product, day: date, end: date) -> tuple[str, bool]:
    """Libellé imprimé ce jour-là, et s'il vient de changer."""
    if product.relabel is None:
        return product.label, False
    switch = end - timedelta(weeks=product.relabel.weeks_before_end)
    if day < switch:
        return product.relabel.was, False
    return product.label, True


def _build(
    supplier_key: str,
    supplier: Supplier,
    number: str,
    day: date,
    kind: Literal["invoice", "credit_note"],
    lines: list[InvoiceLine],
    pitfalls: list[str],
    credits: str | None = None,
) -> Invoice:
    """Ajoute le pied de facture : remise par taux de TVA, puis port."""
    adjustments: list[Adjustment] = []
    pitfalls = list(pitfalls)

    rates = sorted({line.vat_rate for line in lines})
    if len(rates) > 1:
        pitfalls.append("TVA mixte : " + ", ".join(percent(rate) for rate in rates))

    if supplier.footer_discount:
        # La remise suit le taux de TVA de ce qu'elle réduit, sinon la TVA
        # serait calculée sur une base fausse.
        for rate in rates:
            base = sum((line.total for line in lines if line.vat_rate == rate), Decimal(0))
            adjustments.append(
                Adjustment(
                    f"REMISE {percent(supplier.footer_discount)} SUR TVA {percent(rate)}",
                    -money(base * supplier.footer_discount / 100),
                    rate,
                )
            )
        pitfalls.append(
            f"remise de pied de facture de {percent(supplier.footer_discount)}, "
            "appliquée après le total des lignes"
        )

    goods = sum((line.total for line in lines), Decimal(0))
    shipping = supplier.shipping
    if shipping and kind == "invoice" and goods < shipping.free_above:
        adjustments.append(Adjustment(shipping.label, shipping.fee, shipping.vat))
        pitfalls.append(f"frais de port de {shipping.fee} € en pied de facture, hors lignes")

    return Invoice(
        supplier=supplier_key,
        supplier_name=supplier.name,
        supplier_siren=supplier.siren,
        number=number,
        issued_on=day,
        kind=kind,
        lines=tuple(lines),
        adjustments=tuple(adjustments),
        pitfalls=tuple(pitfalls),
        credits=credits,
    )


def _supplier_invoices(
    world: World,
    key: str,
    supplier: Supplier,
    consumption: dict[date, dict[str, Decimal]],
    start: date,
    end: date,
    seed: int,
) -> list[Invoice]:
    deliveries = delivery_days(world, supplier, start, end, until=end + LOOKAHEAD)
    orders = [
        (day, ordered)
        for day, ordered in _orders(world, supplier, consumption, deliveries, seed)
        if day <= end
    ]
    rng = _stream(seed, "credit", key)

    invoices: list[Invoice] = []
    relabel_announced: set[str] = set()
    crates_out: dict[str, Decimal] = {}
    credit_notes = 0

    for sequence, (day, ordered) in enumerate(orders, start=1):
        lines: list[InvoiceLine] = []
        pitfalls: list[str] = []
        for product, qty in ordered:
            label, relabelled = _label_on(product, day, end)
            if relabelled and product.label not in relabel_announced:
                relabel_announced.add(product.label)
                pitfalls.append(f"libellé modifié : « {product.relabel.was} » devient « {label} »")
            ingredient = world.ingredients.get(product.ingredient)
            lines.append(
                InvoiceLine(
                    label=label,
                    supplier_ref=product.ref,
                    qty=qty,
                    unit=product.sold_by,
                    unit_price=price_on(world, supplier, product, day, end, seed),
                    vat_rate=product.vat,
                    ingredient=product.ingredient,
                    pack_qty=product.contains if ingredient else None,
                    base_unit=ingredient.unit if ingredient else None,
                )
            )
            if product.deposit:
                # La consigne n'est pas une vente : hors TVA, et rendue au retour.
                deposit = product.deposit
                lines.append(
                    InvoiceLine(deposit.label, None, qty, "PCE", deposit.amount, Decimal(0))
                )
                returned = crates_out.get(product.label)
                if returned:
                    lines.append(
                        InvoiceLine(
                            f"{RETURN_PREFIX}{deposit.label}",
                            None,
                            -returned,
                            "PCE",
                            deposit.amount,
                            Decimal(0),
                        )
                    )
                crates_out[product.label] = qty
                pitfalls.append("consigne facturée à la livraison, retour de consigne déduit")

        number = f"{supplier.prefix}-{day:%y%m}-{sequence:04d}"
        invoices.append(_build(key, supplier, number, day, "invoice", lines, pitfalls))

        # Un avoir reprend une ligne de la facture, en quantité négative.
        credited_on = day + timedelta(days=rng.randint(2, 6))
        if rng.random() < supplier.credit_note_rate and credited_on <= end:
            credit_notes += 1
            line = rng.choice([line for line in lines if line.ingredient])
            whole = line.qty if line.qty != line.qty.to_integral_value() else Decimal(1)
            credit = InvoiceLine(
                line.label,
                line.supplier_ref,
                -whole,
                line.unit,
                line.unit_price,
                line.vat_rate,
                line.ingredient,
                line.pack_qty,
                line.base_unit,
            )
            invoices.append(
                _build(
                    key,
                    supplier,
                    f"{supplier.prefix}-AV-{credited_on:%y%m}-{credit_notes:03d}",
                    credited_on,
                    "credit_note",
                    [credit],
                    [f"avoir sur la facture {number}"],
                    credits=number,
                )
            )
    return invoices


def simulate(world: World, end: date, seed: int | None = None) -> Corpus:
    """Simule l'établissement jusqu'à `end` et rend ses factures."""
    seed = world.simulation.seed if seed is None else seed
    start = end - timedelta(weeks=round(world.simulation.months * 52 / 12)) + timedelta(days=1)

    sales = simulate_sales(world, start, end + LOOKAHEAD, seed)
    consumption = daily_consumption(world, sales)

    invoices: list[Invoice] = []
    for key, supplier in world.suppliers.items():
        invoices.extend(_supplier_invoices(world, key, supplier, consumption, start, end, seed))
    invoices.sort(key=lambda invoice: (invoice.issued_on, invoice.supplier, invoice.number))

    originals = [invoice.number for invoice in invoices if invoice.kind == "invoice"]
    resent = _stream(seed, "resent").sample(
        originals, round(len(originals) * world.simulation.duplicate_rate)
    )

    return Corpus(
        start=start,
        end=end,
        sales={day: sold for day, sold in sales.items() if day <= end},
        invoices=tuple(invoices),
        resent=tuple(sorted(resent)),
    )


def revenue_ht(world: World, corpus: Corpus) -> Decimal:
    """Chiffre d'affaires HT simulé, pour situer les achats."""
    total = Decimal(0)
    for by_recipe in corpus.sales.values():
        for key, portions in by_recipe.items():
            recipe = world.recipes[key]
            total += portions * recipe.price_ttc / (1 + recipe.vat / 100)
    return money(total)


# --------------------------------------------------------------------- vérité


def to_truth(
    invoice: Invoice, source_file: str, unit_of: Callable[[str], str] = str
) -> TruthInvoice:
    """L'annotation de la facture, écrite par celui qui l'a émise.

    C'est tout l'intérêt d'un jeu factice : la vérité est exacte et ne coûte
    rien, là où une vraie facture demande une annotation à la main.

    `unit_of` traduit l'unité imprimée dans celle que porte le document lu :
    un Factur-X écrit « XCT » là où le papier imprime « CT ».
    """
    lines = []
    for line in invoice.lines:
        notes = None
        if line.pack_qty and _HIDDEN_PACK.search(line.label):
            notes = (
                f"colisage dans le libellé : {line.pack_qty} {line.base_unit} "
                f"par unité facturée ({line.unit})"
            )
        lines.append(
            TruthLine(
                raw_label=line.label,
                supplier_ref=line.supplier_ref,
                qty=line.qty,
                unit_price_ht=line.unit_price,
                line_total_ht=line.total,
                raw_unit=unit_of(line.unit),
                pack_qty=line.pack_qty,
                base_unit=line.base_unit,
                notes=notes,
            )
        )
    return TruthInvoice(
        source_file=source_file,
        quality=SourceQuality.PDF,
        vat_basis=VatBasis.EXCLUDING,
        supplier_name=invoice.supplier_name,
        supplier_siren=invoice.supplier_siren,
        invoice_number=invoice.number,
        invoice_date=invoice.issued_on,
        total_ht=invoice.total_ht,
        total_ttc=invoice.total_ttc,
        lines=lines,
        pitfalls=list(invoice.pitfalls),
    )


def mapping_truth(world: World) -> list[MappingTruth]:
    """Le bon rapprochement de chaque produit que les factures font apparaître.

    Le catalogue de chaque fournisseur, plus ce que les factures créent sans
    qu'il y soit : la consigne et son retour, qui ne sont pas des ingrédients,
    et l'ancien libellé d'un produit sans référence, qui devient en base un
    produit distinct du même ingrédient.
    """
    truths: list[MappingTruth] = []
    for supplier in world.suppliers.values():

        def entry(
            label: str, ref: str | None, ingredient_key: str | None, contains, supplier=supplier
        ):
            ingredient = world.ingredients.get(ingredient_key)
            return MappingTruth(
                supplier_siren=supplier.siren,
                supplier_name=supplier.name,
                supplier_ref=ref,
                raw_label=label,
                ingredient=ingredient.name if ingredient else None,
                pack_qty=contains if ingredient else None,
                base_unit=ingredient.unit if ingredient else None,
            )

        for product in supplier.products:
            truths.append(entry(product.label, product.ref, product.ingredient, product.contains))
            # Avec une référence, l'ancien libellé reste le même produit ; sans,
            # il en devient un autre.
            if product.relabel and not product.ref:
                truths.append(
                    entry(product.relabel.was, None, product.ingredient, product.contains)
                )
            if product.deposit:
                for label in (product.deposit.label, RETURN_PREFIX + product.deposit.label):
                    truths.append(entry(label, None, None, None))
    return truths
