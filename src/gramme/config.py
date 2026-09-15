"""Configuration, lue depuis `.env`.

Aucun secret en dur (CLAUDE.md, interdits) : tout vient de l'environnement.
Le fichier `.env` est cherché dans le dossier courant — les commandes du
Makefile se lancent depuis la racine du projet.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" : `.env` porte aussi les clés MiniMax, qui serviront à
    # llm.py (étape 3). Elles n'ont pas à être déclarées avant d'être utilisées.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Base principale : celle qui recevra les vraies factures.
    database_url: str

    # Base de démonstration, recréée de zéro par `make demo-db` avec des
    # chiffres fictifs. Séparée de la principale pour qu'un seed ne puisse
    # jamais toucher une donnée client.
    demo_database_url: str


@lru_cache
def get_settings() -> Settings:
    """Instance unique, en cache : `.env` n'est pas relu à chaque requête."""
    return Settings()
