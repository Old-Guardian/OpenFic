# -*- coding: utf-8 -*-
"""Literal text normalization shared by the knowledge retrieval layers."""

from __future__ import annotations

_ASCII_UPPER_TO_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)


def normalize_literal(value: str) -> str:
    """Apply the phase-one literal matching normalization.

    Only ASCII letters are folded.  This deliberately does not promise full
    Unicode case folding, simplified/traditional conversion, or tokenization.
    Leading and trailing whitespace is not removed here; callers that accept
    user input trim it before normalizing.
    """

    return value.translate(_ASCII_UPPER_TO_LOWER)
