from __future__ import annotations
from typing import Any
from sqlalchemy.orm import Session
from axiom_server.ledger import Fact, FactStatus
from axiom_server import verification_engine

class VerificationManager:
    """Handles the orchestration of fact verification logic."""
    
    @staticmethod
    def run_audit(session: Session, fact_hash: str) -> dict[str, Any]:
        """Performs a deep audit on a specific fact."""
        fact = session.query(Fact).filter(Fact.hash == fact_hash).one_or_none()
        if not fact:
            return {"error": "Fact not found"}
            
        corroborations = verification_engine.find_corroborating_claims(fact, session)
        return {
            "fact_hash": fact_hash,
            "status": fact.status.value,
            "corroboration_count": len(corroborations),
            "is_verified": fact.status == FactStatus.EMPIRICALLY_VERIFIED
        }