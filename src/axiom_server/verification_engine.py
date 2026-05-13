# Axiom - verification_engine.py
# Copyright (C) 2025 The Axiom Contributors
# This program is licensed under the Peer Production License (PPL).
# See the LICENSE file for full details.
"""The experimental V4 Verification Engine for Axiom nodes."""

import re
from typing import Any

import numpy as np
import requests
from sqlalchemy.orm import Session

from axiom_server.common import NLP_MODEL
from axiom_server.ledger import Fact


def find_corroborating_claims(
    fact_to_verify: Fact,
    session: Session,
    min_similarity: float = 0.85,
) -> list[dict[str, Any]]:
    """Find facts that corroborate the given fact.

    This is a **vector-based** similarity search. It looks for other facts
    that are semantically similar in meaning, not just string matches.

    Args:
        fact_to_verify: The Fact object to find corroboration for.
        session: The database session.
        min_similarity: The minimum cosine similarity score (0.0 to 1.0).
                        Defaults to 0.85 (very high similarity).

    Returns:
        A list of dictionaries, each representing a corroborating fact.

    """
    corroborations = []
    from axiom_server.ledger import FactVector

    # --- CLEANED UP SECTION ---
    target_vector_obj = (
        session.query(FactVector)
        .filter(FactVector.fact_id == fact_to_verify.id)
        .one_or_none()
    )

    if target_vector_obj:
        target_vector = np.frombuffer(
            target_vector_obj.vector,
            dtype=np.float32,
        )
    else:
        # Fallback to NLP model if not in DB
        target_doc = NLP_MODEL(fact_to_verify.content)
        target_vector = np.array(target_doc.vector, dtype=np.float32)

    target_norm = float(np.linalg.norm(target_vector))
    if target_norm == 0:
        return []

    target_domains = {s.domain for s in fact_to_verify.sources}

    # Get all potential corroborators
    all_vectors = session.query(FactVector).all()

    for fact_vec in all_vectors:
        if fact_vec.fact_id == fact_to_verify.id:
            continue

        other = fact_vec.fact
        if other.disputed:
            continue

        if any(s.domain in target_domains for s in other.sources):
            continue

        # Ensure we are passing bytes to frombuffer
        other_vector = np.frombuffer(fact_vec.vector, dtype=np.float32)
        other_norm = float(np.linalg.norm(other_vector))

        if other_norm == 0:
            continue

        similarity = float(
            np.dot(target_vector, other_vector) / (target_norm * other_norm),
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
