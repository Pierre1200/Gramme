"""Le fichier de l'établissement fictif doit refuser de se charger s'il est faux.

Une référence cassée ne lève rien toute seule : elle donne des factures
fausses, puis un jeu de vérité faux, puis des mesures fausses.
"""

import copy
import tomllib
from decimal import Decimal

import pytest
from pydantic import ValidationError

from gramme.world import WORLD_FILE, World, load_world, luhn_valid


@pytest.fixture(scope="module")
def raw() -> dict:
    with WORLD_FILE.open("rb") as file:
        return tomllib.load(file, parse_float=Decimal)


def load_edited(raw: dict, edit) -> World:
    data = copy.deepcopy(raw)
    edit(data)
    return World.model_validate(data)


def test_the_real_file_loads():
    world = load_world()
    assert world.recipes["paella"].is_sellable
    assert not world.recipes["pate_sucree"].is_sellable


def test_a_valid_siren_is_rejected(raw):
    # Un SIREN qui passe Luhn pourrait être celui d'une vraie entreprise.
    assert luhn_valid("000000000")
    with pytest.raises(ValidationError, match="doit échouer à Luhn"):
        load_edited(raw, lambda d: d["establishment"].update(siren="000000000"))


def test_an_unknown_ingredient_is_rejected(raw):
    def edit(data):
        data["recipes"]["cafe"]["components"][0]["ingredient"] = "chicoree"

    with pytest.raises(ValidationError, match="ingrédient inconnu chicoree"):
        load_edited(raw, edit)


def test_a_component_in_the_wrong_unit_is_rejected(raw):
    def edit(data):
        data["recipes"]["beurre_blanc"]["components"][0]["unit"] = "ml"

    with pytest.raises(ValidationError, match="beurre se compte en g"):
        load_edited(raw, edit)


def test_a_recipe_that_contains_itself_is_rejected(raw):
    def edit(data):
        data["recipes"]["pate_sucree"]["components"].append(
            {"recipe": "tarte_citron", "qty": 1, "unit": "unit"}
        )

    with pytest.raises(ValidationError, match="cycle de recettes"):
        load_edited(raw, edit)


def test_shares_of_an_ingredient_must_add_up(raw):
    def edit(data):
        butter = next(p for p in data["suppliers"]["grossiste"]["products"] if p["ref"] == "160090")
        butter["share"] = Decimal("0.5")

    with pytest.raises(ValidationError, match="les parts de beurre"):
        load_edited(raw, edit)


def test_a_misspelt_key_is_rejected(raw):
    with pytest.raises(ValidationError, match="delivry"):
        load_edited(raw, lambda d: d["suppliers"]["cave"].update(delivry=["thu"]))
