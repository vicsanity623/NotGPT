# Axiom - verification_engine.py
# Copyright (C) 2025 The Axiom Contributors
# This program is licensed under the Peer Production License (PPL).
# See the LICENSE file for full details.
"""The experimental V4 Verification Engine for Axiom nodes."""

import re
from typing import Any, cast

import numpy as np
import requests
from sqlalchemy.orm import Session

from axiom_server.common import NLP_MODEL
from axiom_server.ledger import Fact


# --- The "Simple, Smart, Genius" Corroboration Engine ---
def find_corroborating_claims(
    fact_to_verify: Fact,
    session: Session,
    min_similarity: float = 0.85,
) -> list[dict[str, Any]]:
    """Perform a vector similarity search across the ledger to find corroboration.

    Args:
        fact_to_verify: The Fact object to be verified.
        session: The SQLAlchemy session to use.
        min_similarity: The threshold for considering a fact as corroborating.

    Returns:
        A list of dictionaries representing corroborating facts from different sources.

    """
    corroborations = []
    target_doc = NLP_MODEL(fact_to_verify.content)
    target_vector = cast("Any", target_doc.vector)
    target_norm = float(np.linalg.norm(target_vector))

    if target_norm == 0:
        return []

    # Get the domain of the target fact to ensure multi-source corroboration
    target_domains = {s.domain for s in fact_to_verify.sources}

    # Efficiently query for other facts (excluding disputed ones)
    # In a real production system, this would use the FactIndexer or a vector DB.
    other_facts = (
        session.query(Fact)
        .filter(
            Fact.id != fact_to_verify.id,
            Fact.disputed == False,  # noqa: E712
        )
        .all()
    )

    for other in other_facts:
        # Skip if from the same source domain
        if any(s.domain in target_domains for s in other.sources):
            continue

        other_doc = NLP_MODEL(other.content)
        other_vector = cast("Any", other_doc.vector)
        other_norm = float(np.linalg.norm(other_vector))

        if other_norm == 0:
            continue

        similarity = float(
            np.dot(cast("Any", target_vector), other_vector)
            / (target_norm * other_norm),
        )

        if similarity >= min_similarity:
            corroborations.append(
                {
                    "fact_id": other.id,
                    "content": other.content,
                    "similarity": round(similarity, 4),
                    "sources": [s.domain for s in other.sources],
                },
            )

    return corroborations


# --- The "Simple, Smart, Genius" Citation Engine ---
def verify_citations(fact_to_verify: Fact) -> list[dict[str, str]]:
    """Find and verify all hyperlinks within a fact's content.

    This is the first step towards the V5 "Citation Engine" for primary
    source verification.

    Args:
        fact_to_verify: The Fact object to be analyzed.

    Returns:
        A list of dictionaries, each representing a found citation and its status.

    """
    citations = []

    # Use a regex to find all URLs, ensuring we don't capture trailing punctuation.
    urls_found = re.findall(
        r'(https?://[^\s<>"\'()]+)(?<![.,:;!?])',
        fact_to_verify.content,
    )

    if not urls_found:
        return []

    for url in set(
        urls_found,
    ):  # Use set() to avoid checking the same URL twice
        try:
            # We use a HEAD request with a timeout to be a "Good Neighbor".
            # We only care if the page exists, we don't need to download it.
            response = requests.head(url, timeout=5, allow_redirects=True)
            if response.status_code == 200:
                status = "VALID_AND_LIVE"
            else:
                status = f"BROKEN_{response.status_code}"
        except requests.RequestException:
            status = "UNREACHABLE"

        citations.append({"url": url, "status": status})

    return citations
