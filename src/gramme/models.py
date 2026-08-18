"""Contrats d'extraction : ce qu'un modèle est autorisé à renvoyer.

Frontière stricte (CLAUDE.md, décision 2) : aucun champ de ce module ne
porte un montant calculé. Tout ce qui est ici a été LU sur un document.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldStatus(StrEnum):
    READ = "read"              # lu sur le document
    UNREADABLE = "unreadable"  # présent mais illisible -> validation humaine
    ABSENT = "absent"          # le document ne porte pas ce champ


class Extracted[T](BaseModel):
    """Un champ extrait, avec son état de lecture.

    `value` n'a de sens que si `status is READ`. Les invariants ci-dessous
    rendent impossible de porter une valeur sans l'assumer, ou d'annoncer
    une confiance sur un champ qu'on n'a pas lu.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: FieldStatus
    value: T | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Ce que le modèle affirme avoir lu, caractère pour caractère.
    # Sert au recoupement (« 219,00 € » vs Decimal("219.00")) et à la file
    # de validation : un humain corrige plus vite s'il voit la lecture brute.
    raw_text: str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.status is FieldStatus.READ:
            if self.value is None:
                raise ValueError("status=read exige une valeur")
            if self.raw_text is None:
                raise ValueError("status=read exige raw_text (traçabilité)")
        else:
            if self.value is not None:
                raise ValueError(f"status={self.status} interdit une valeur")
            if self.confidence != 0.0:
                raise ValueError(f"status={self.status} impose confidence=0")
        return self

    def needs_review(self, threshold: float) -> bool:
        """Vrai si ce champ doit passer par un humain.

        ABSENT ne déclenche rien : un champ que le document ne porte pas
        n'est pas une erreur d'extraction.
        """
        if self.status is FieldStatus.ABSENT:
            return False
        return self.status is FieldStatus.UNREADABLE or self.confidence < threshold
