# Axiom Engine v4.0.0 Summary

Axiom has undergone a major architectural upgrade to transform from a noisy news aggregator into a **High-Signal Atomic Fact Network**. This version introduces advanced P2P consensus, deep semantic verification, and robust metadata tracking.

## 🚀 Key Improvements

### 1. Atomic Fact Ingestion (The "Crucible" Upgrade)
- **High-Signal Discovery**: RSS feeds are now prioritized by signal strength (Reuters, AP, BBC, Fact-checkers).
- **Full-Text Fetching**: Axiom now retrieves the complete article text from source URLs, enabling deeper analysis than RSS summaries.
- **Zero-Shot Claim Scoring**: Integrated BART-MNLI model to classify sentences as "verifiable claims," "opinions," or "background." Axiom only ingests the strongest claims.
- **Enhanced Heuristics**: Requirements for core entities (PERSON, ORG, GPE) and atomic data (Dates, Numbers, Percentages) ensure facts are objective and specific.

### 2. Decentralized Consensus & P2P Protocols
- **P2P Fact Proposals**: Nodes now proactively broadcast their newly discovered facts to the network.
- **On-Chain Voting**: Peers automatically validate and vote on incoming fact proposals.
- **Consensus Upgrade**: Facts that receive 3+ independent votes are upgraded to `EMPIRICALLY_VERIFIED` status.
- **Vector-Based Corroboration**: The verification engine now uses fast vector similarity math to find corroborating evidence across different source domains.

### 3. Metadata & Provenance
- **Rich Schema**: Facts now store `published_date`, `extraction_confidence`, `primary_source_url`, and `source_domain_reputation`.
- **Domain Reputation**: A curated map of trusted news organizations provides weighted signals for fact validity.
- **JSONL Export**: Added utility to export high-quality ledger subsets for model training or external analysis.

## 🛠 Technical Details
- **Version**: 4.0.0
- **Language**: Python 3.9+ (Strict Mypy compliant)
- **NLP Model**: spaCy Large (`en_core_web_lg`)
- **NLI Model**: `facebook/bart-large-mnli`
- **Search**: Hybrid Keyword + Vector Similarity

## 📈 Roadmap Status
- [x] Phase 1: Quick Wins (Clean logic & tighter RSS)
- [x] Phase 2: Medium Term (Full-text & Claim scoring)
- [x] Phase 3: Advanced (P2P Proposals & Voting)

---
*Axiom 4.0.0: The foundation for a decentralized, objective truth.*
