"""L'établissement fictif du jeu d'essai, lu depuis `corpus/restaurant.toml`.

Ce module ne simule rien : il charge le fichier et refuse de le charger s'il
est incohérent. Une référence cassée ou une unité qui ne concorde pas
produirait des factures fausses sans le moindre avertissement, et tout le jeu
de vérité avec elles.
"""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

WORLD_FILE = Path("corpus/restaurant.toml")

BaseUnit = Literal["g", "ml", "unit"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class _Strict(BaseModel):
    """Base commune : immuable, et une clé inconnue est une faute de frappe."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


def luhn_valid(number: str) -> bool:
    """Contrôle de Luhn, celui que passe tout vrai numéro SIREN."""
    total = 0
    for position, char in enumerate(reversed(number)):
        digit = int(char) * (2 if position % 2 else 1)
        total += digit - 9 if digit > 9 else digit
    return total % 10 == 0


class _Identified(_Strict):
    """Une entité qui porte une adresse et un SIREN fictifs."""

    name: str
    legal: str
    street: str
    zip: str
    city: str
    siren: str = Field(pattern=r"^\d{9}$")
    phone: str
    email: str

    @model_validator(mode="after")
    def _check_siren_is_fictional(self) -> Self:
        # Un SIREN qui échoue à Luhn ne peut désigner aucune entreprise réelle :
        # c'est ce qui garantit qu'une facture factice ne vise personne.
        if luhn_valid(self.siren):
            raise ValueError(f"le SIREN {self.siren} est valide : il doit échouer à Luhn")
        return self


class Establishment(_Identified):
    tagline: str


class Settings(_Strict):
    seed: int
    months: int = Field(gt=0)
    duplicate_rate: float = Field(ge=0, le=1)
    # Bornes incluses, au format mois-jour ; une fermeture peut enjamber le
    # nouvel an ("12-24" → "01-02").
    closures: list[tuple[str, str]] = Field(default_factory=list)


class Activity(_Strict):
    name: str
    weekdays: list[float] = Field(min_length=7, max_length=7)
    months: list[float] = Field(min_length=12, max_length=12)
    lumpy: bool


class Ingredient(_Strict):
    name: str
    unit: BaseUnit


class Relabel(_Strict):
    weeks_before_end: int = Field(gt=0)
    was: str


class Deposit(_Strict):
    label: str
    amount: Decimal = Field(gt=0)


class Shipping(_Strict):
    label: str
    fee: Decimal = Field(gt=0)
    free_above: Decimal
    vat: Decimal


class Product(_Strict):
    label: str
    ref: str | None = None
    ingredient: str | None = None
    sold_by: str
    contains: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    vat: Decimal
    weight: Literal["fixed", "variable"] = "fixed"
    trend: str | None = None
    season: str | None = None
    share: Decimal = Decimal(1)
    relabel: Relabel | None = None
    per_week: Decimal | None = None
    deposit: Deposit | None = None
    src: str
    proxy: str | None = None

    @model_validator(mode="after")
    def _check_demand_source(self) -> Self:
        # La demande vient des recettes OU d'un rythme d'achat, jamais des deux :
        # sinon le même produit serait commandé deux fois.
        if (self.ingredient is None) == (self.per_week is None):
            raise ValueError(f"{self.label} : exactement un de `ingredient` ou `per_week`")
        return self


class Supplier(_Identified):
    color: str
    prefix: str
    delivery: list[Weekday] = Field(min_length=1)
    every: int = Field(ge=1)
    pricing: Literal["market", "list"]
    terms: str
    credit_note_rate: float = Field(default=0, ge=0, le=1)
    footer_discount: Decimal | None = None
    shipping: Shipping | None = None
    products: list[Product] = Field(min_length=1)


class Batch(_Strict):
    qty: Decimal = Field(gt=0)
    unit: BaseUnit


class Component(_Strict):
    ingredient: str | None = None
    recipe: str | None = None
    qty: Decimal = Field(gt=0)
    unit: BaseUnit
    # `yield` est un mot réservé de Python : le champ garde le nom du schéma SQL.
    yield_ratio: Decimal = Field(default=Decimal(1), alias="yield", gt=0, le=1)
    note: str | None = None

    @model_validator(mode="after")
    def _check_exactly_one_source(self) -> Self:
        # Même règle que le CHECK de `recipe_component` : ingrédient XOR recette.
        if (self.ingredient is None) == (self.recipe is None):
            raise ValueError("un composant est un ingrédient OU une sous-recette")
        return self


class Recipe(_Strict):
    name: str
    batch: Batch
    components: list[Component] = Field(min_length=1)
    activity: str | None = None
    portions: int | None = Field(default=None, gt=0)
    price_ttc: Decimal | None = Field(default=None, gt=0)
    vat: Decimal | None = None
    per_week: Decimal | None = Field(default=None, gt=0)

    @property
    def is_sellable(self) -> bool:
        return self.activity is not None

    @model_validator(mode="after")
    def _check_selling_fields(self) -> Self:
        selling = (self.activity, self.portions, self.price_ttc, self.vat, self.per_week)
        if any(field is None for field in selling) and any(field is not None for field in selling):
            raise ValueError(f"{self.name} : une recette vendue porte tous ses champs de vente")
        return self


class World(_Strict):
    establishment: Establishment
    simulation: Settings
    activities: dict[str, Activity]
    ingredients: dict[str, Ingredient]
    trends: dict[str, list[tuple[float, float]]]
    seasons: dict[str, list[float]]
    sources: dict[str, str]
    suppliers: dict[str, Supplier]
    recipes: dict[str, Recipe]

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        errors = [*self._catalog_errors(), *self._recipe_errors()]
        if errors:
            raise ValueError("fichier incohérent :\n  - " + "\n  - ".join(errors))
        return self

    def _catalog_errors(self) -> list[str]:
        errors: list[str] = []
        shares: dict[str, Decimal] = {}
        for key, supplier in self.suppliers.items():
            for product in supplier.products:
                if product.src not in self.sources:
                    errors.append(f"{key} : source inconnue pour {product.label}")
                if product.trend and product.trend not in self.trends:
                    errors.append(f"{key} : trajectoire inconnue {product.trend}")
                if product.season and product.season not in self.seasons:
                    errors.append(f"{key} : saison inconnue {product.season}")
                if product.ingredient is None:
                    continue
                if product.ingredient not in self.ingredients:
                    errors.append(f"{key} : ingrédient inconnu {product.ingredient}")
                shares[product.ingredient] = shares.get(product.ingredient, 0) + product.share

        for name, months in self.seasons.items():
            if len(months) != 12:
                errors.append(f"saison {name} : douze valeurs attendues, {len(months)} lues")
        for name in self.ingredients:
            if name not in shares:
                errors.append(f"ingrédient sans fournisseur : {name}")
            # Un seul fournisseur prend tout ; à plusieurs, les parts font 1.
            elif shares[name] != 1:
                errors.append(f"les parts de {name} font {shares[name]}, pas 1")
        return errors

    def _recipe_errors(self) -> list[str]:
        errors: list[str] = []
        used: set[str] = set()
        for key, recipe in self.recipes.items():
            if recipe.activity and recipe.activity not in self.activities:
                errors.append(f"{key} : activité inconnue {recipe.activity}")
            for component in recipe.components:
                if component.ingredient is not None:
                    ingredient = self.ingredients.get(component.ingredient)
                    if ingredient is None:
                        errors.append(f"{key} : ingrédient inconnu {component.ingredient}")
                    elif component.unit != ingredient.unit:
                        errors.append(
                            f"{key} : {component.ingredient} se compte en {ingredient.unit}"
                        )
                    used.add(component.ingredient)
                else:
                    child = self.recipes.get(component.recipe)
                    if child is None:
                        errors.append(f"{key} : sous-recette inconnue {component.recipe}")
                    elif component.unit != child.batch.unit:
                        errors.append(f"{key} : {component.recipe} se compte en {child.batch.unit}")

        errors.extend(
            f"ingrédient jamais utilisé : {name}" for name in self.ingredients if name not in used
        )
        if not errors:
            errors.extend(self._cycle_errors())
        return errors

    def _cycle_errors(self) -> list[str]:
        """Une recette qui se contient elle-même ferait boucler tout calcul de coût."""

        def visit(key: str, path: tuple[str, ...]) -> list[str]:
            if key in path:
                return [f"cycle de recettes : {' → '.join((*path, key))}"]
            found: list[str] = []
            for component in self.recipes[key].components:
                if component.recipe is not None:
                    found.extend(visit(component.recipe, (*path, key)))
            return found

        errors: list[str] = []
        for key in self.recipes:
            errors.extend(visit(key, ()))
        return errors[:1]


def load_world(path: Path = WORLD_FILE) -> World:
    """Charge et valide l'établissement fictif.

    Les nombres sont lus en `Decimal` : un prix passé par un flottant ne
    retombe plus au centime.
    """
    with path.open("rb") as file:
        return World.model_validate(tomllib.load(file, parse_float=Decimal))
