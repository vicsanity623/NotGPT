# tests/test_verification_engine.py
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from sqlalchemy.orm import Session

from axiom_server import verification_engine
from axiom_server.ledger import Fact, FactVector, Source


def create_mock_vector(values: list[float]) -> bytes:
    """Create a bytes-serialized numpy vector."""
    return np.array(values, dtype=np.float32).tobytes()


class TestVerificationEngine(unittest.TestCase):
    """Tests for the Axiom Verification Engine."""

    def setUp(self) -> None:
        """Set up a mock database session and test data for each test."""
        self.mock_session = MagicMock(spec=Session)

        # Create mock Source objects
        self.source1 = Source(domain="sourceA.com")
        self.source2 = Source(domain="sourceB.com")
        self.source3 = Source(domain="sourceC.com")

        # Standard vectors for similarity control
        # target vs corroborating: 1.0 (identical)
        # target vs unrelated: 0.0 (orthogonal)
        self.vec_target = create_mock_vector([1.0, 0.0])
        self.vec_corroborate = create_mock_vector([1.0, 0.0])
        self.vec_unrelated = create_mock_vector([0.0, 1.0])

    def test_find_corroborating_claims_success(self) -> None:
        """Test that find_corroborating_claims correctly identifies a similar fact from a different source."""
        # --- Arrange ---
        fact_to_verify = Fact(
            id=1,
            content="The sky is blue",
            sources=[self.source1],
        )

        corroborating_fact = Fact(
            id=2,
            content="The color of the sky is blue",
            sources=[self.source2],
        )
        unrelated_fact = Fact(
            id=3,
            content="Grass is green",
            sources=[self.source3],
        )

        # Mock the FactVector rows
        fv_target = MagicMock(
            spec=FactVector,
            fact_id=1,
            vector=self.vec_target,
            fact=fact_to_verify,
        )
        fv_corroborate = MagicMock(
            spec=FactVector,
            fact_id=2,
            vector=self.vec_corroborate,
            fact=corroborating_fact,
        )
        fv_unrelated = MagicMock(
            spec=FactVector,
            fact_id=3,
            vector=self.vec_unrelated,
            fact=unrelated_fact,
        )

        # Configure session to handle:
        # 1. session.query(FactVector).filter(...).one_or_none()
        # 2. session.query(FactVector).all()
        query_mock = self.mock_session.query.return_value
        query_mock.filter.return_value.one_or_none.return_value = fv_target
        query_mock.all.return_value = [fv_target, fv_corroborate, fv_unrelated]

        # --- Act ---
        results = verification_engine.find_corroborating_claims(
            fact_to_verify,
            self.mock_session,
        )

        # --- Assert ---
        assert len(results) == 1
        assert results[0]["content"] == "The color of the sky is blue"
        assert "sourceB.com" in results[0]["sources"]
        assert results[0]["similarity"] > 0.85

    def test_find_corroborating_claims_from_same_source(self) -> None:
        """Test that a similar fact from the SAME source is NOT considered a corroboration."""
        # --- Arrange ---
        fact_to_verify = Fact(
            id=4,
            content="The sky is blue",
            sources=[self.source1],
        )
        similar_fact = Fact(
            id=5,
            content="The sky is indeed blue",
            sources=[self.source1],
        )

        fv_target = MagicMock(
            spec=FactVector,
            fact_id=4,
            vector=self.vec_target,
            fact=fact_to_verify,
        )
        fv_similar = MagicMock(
            spec=FactVector,
            fact_id=5,
            vector=self.vec_corroborate,
            fact=similar_fact,
        )

        query_mock = self.mock_session.query.return_value
        query_mock.filter.return_value.one_or_none.return_value = fv_target
        query_mock.all.return_value = [fv_target, fv_similar]

        # --- Act ---
        results = verification_engine.find_corroborating_claims(
            fact_to_verify,
            self.mock_session,
        )

        # --- Assert ---
        assert (
            len(results) == 0
        )  # Should find no corroborations because sources overlap

    @patch("axiom_server.verification_engine.requests.head")
    def test_verify_citations(self, mock_requests_head: MagicMock) -> None:
        """Test that verify_citations correctly identifies and checks URLs in fact content."""
        # --- Arrange ---
        fact_content = "Check this live link http://good-url.com and this broken one http://bad-url.com."
        fact_to_verify = Fact(content=fact_content)

        def side_effect(url: str, **kwargs: object) -> MagicMock:
            response = MagicMock()
            if url == "http://good-url.com":
                response.status_code = 200
            elif url == "http://bad-url.com":
                response.status_code = 404
            return response

        mock_requests_head.side_effect = side_effect

        # --- Act ---
        results = verification_engine.verify_citations(fact_to_verify)

        # --- Assert ---
        results_map = {item["url"]: item for item in results}
        assert results_map["http://good-url.com"]["status"] == "VALID_AND_LIVE"
        assert results_map["http://bad-url.com"]["status"] == "BROKEN_404"


if __name__ == "__main__":
    unittest.main()
