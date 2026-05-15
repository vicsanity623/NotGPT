- [ ] **Implement Strict Fact Requirements (`crucible.py`)**
  - Add a dedicated `Check` in the `SENTENCE_CHECKS` pipeline.
  - The check will verify that a fact contains a subject (who/what), a time/date reference (when), a location (where), and contextual depth (how/why). 
  - Utilize spaCy Entity Recognition (`PERSON`, `ORG`, `GPE`, `DATE`, `TIME`) to enforce presence of "who/what", "where", and "when".
  - Use NLI or dependency parsing to ensure "how/why" elements (e.g. causal relationships, elaborative clauses) exist before accepting the fact.
- [ ] **Aggressive Pruning Phase**
  - Allocate a percentage of node work cycles to scanning the *existing* database for facts that fail these strict checks.
  - Transition failed facts out of the main database to preserve space and improve search quality.

- [ ] **Database File Size Monitoring (`node.py` & `ledger.py`)**
  - Introduce an automated check that measures `ledger.db` size at the start of a block sealing cycle.
- [ ] **Automatic DB Sharding (`ledger.py`)**
  - When `ledger.db` exceeds 50MB, rename it to `ledger_{timestamp}.db` or `ledger_1.db`.
  - Instantiate a fresh `ledger.db` for new block entries.
- [ ] **Unified Query Engine across Shards**
  - Update `api_query.py` and `ledger.py` search logic to bind to multiple SQLite files or use SQLite `ATTACH DATABASE` to run queries across all existing ledger shards transparently.


- [ ] **Fix Global Session Conflict (`hasher.py` & `node.py`)**
  - Modify `FactIndexer` so it does not maintain a separate long-lived `session`.
  - Pass the active transaction `session` from the `node.py` work loop directly to `fact_indexer.add_fact(session, fact)`.
  - Ensure `FactVector` insertions happen within the exact same database transaction as the `Fact` insertion, eliminating lock contention between threads.
