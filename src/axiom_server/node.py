"""Node - Implementation of a single, P2P-enabled node of the Axiom fact network."""

from __future__ import annotations

# Copyright (C) 2025 The Axiom Contributors
# This program is licensed under the Peer Production License (PPL).
# See the LICENSE file for full details.
import argparse
import json
import logging
import os
import secrets
import sys
import threading
import time
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from axiom_server import (
    article_fetcher,
    crucible,
    discovery_rss,
    merkle,
    verification_engine,
    zeitgeist_engine,
)
from axiom_server.api_query import semantic_search_ledger
from axiom_server.crucible import _extract_dates
from axiom_server.hasher import FactIndexer
from axiom_server.ledger import (
    Block,
    Fact,
    FactLink,
    FactStatus,
    Proposal,
    SerializedFact,
    Source,
    SyncRequiredError,
    add_block_from_peer_data,
    create_genesis_block,
    get_engine,
    get_latest_block,
    get_session_maker,
    initialize_database,
)
from axiom_server.log_config import configure_logging
from axiom_server.p2p.constants import (
    BOOTSTRAP_IP_ADDR,
    BOOTSTRAP_PORT,
)
from axiom_server.p2p.node import (
    ApplicationData,
    Message,
    Node as P2PBaseNode,
    PeerLink,
)

__version__ = "4.0.0"

logger = logging.getLogger("axiom-node")
background_thread_logger = logging.getLogger("axiom-node.background-thread")


CORROBORATION_THRESHOLD = 2

# Phase 2: Trusted Domain Reputation
TRUSTED_DOMAINS: Final[dict[str, float]] = {
    "reuters.com": 0.95,
    "apnews.com": 0.95,
    "bbc.com": 0.90,
    "bbc.co.uk": 0.90,
    "nytimes.com": 0.85,
    "aljazeera.com": 0.80,
    "politifact.com": 0.98,
    "bellingcat.com": 0.95,
    "propublica.org": 0.95,
    "ft.com": 0.90,
    "theguardian.com": 0.85,
}

# This lock ensures only one thread can access the database at a time.
db_lock = threading.Lock()

# This lock ensures only one thread can read from or write to the fact indexer at a time.
fact_indexer_lock = threading.Lock()


def detect_environment() -> str:
    """Detect if the application is running on GitHub Actions or locally."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "GitHub Actions (Cloud)"
    return "Local Environment"


# --- NEW: We create a single class that combines Axiom logic and P2P networking ---
class AxiomNode(P2PBaseNode):
    """A class representing a single Axiom node, inheriting P2P capabilities."""

    def __init__(
        self,
        host: str,
        port: int,
        bootstrap_peer: str | None,
        db_name: str = "axiom_ledger.db",
        limit_cycles: int | None = None,
        limit_time: int | None = None,  # Duration in seconds
        heartbeat_file: str | None = None,
    ) -> None:
        """Initialize both the P2P layer and the Axiom logic layer."""
        logger.info(f"Initializing Axiom Node on {host}:{port}")
        self.start_time = time.time()
        self.limit_time = limit_time
        self.heartbeat_file = heartbeat_file

        # 1. We must call the parent constructor from the P2P library first.
        # This allows it to correctly handle both local and public connections.
        temp_p2p = P2PBaseNode.start(
            ip_address=host,
            port=port,
        )

        super().__init__(
            ip_address=temp_p2p.ip_address,
            port=temp_p2p.port,
            serialized_port=temp_p2p.serialized_port,
            private_key=temp_p2p.private_key,
            public_key=temp_p2p.public_key,
            serialized_public_key=temp_p2p.serialized_public_key,
            peer_links=temp_p2p.peer_links,
            server_socket=temp_p2p.server_socket,
        )

        self.active_proposals: dict[int, Proposal] = {}
        self.fact_votes: dict[
            str,
            set[str],
        ] = {}  # fact_hash -> set of peer_ids
        self.limit_cycles = limit_cycles
        self.cycle_count = 0
        # The unused ThreadPoolExecutor has been correctly removed.

        # 2. Perform Axiom-specific database initialization.
        self.db_engine = get_engine(db_name)
        self.session_maker = get_session_maker(self.db_engine)
        initialize_database(self.db_engine)
        with self.session_maker() as session:
            create_genesis_block(session)

        # 3. If a bootstrap peer URL is provided, connect in the background.
        if bootstrap_peer:
            parsed_url = urlparse(bootstrap_peer)
            bootstrap_host = parsed_url.hostname or BOOTSTRAP_IP_ADDR
            bootstrap_port = parsed_url.port or BOOTSTRAP_PORT

            threading.Thread(
                target=self.bootstrap,
                args=(bootstrap_host, bootstrap_port),
                daemon=True,
            ).start()

    def broadcast_application_message(self, data: str) -> None:
        """Broadcast an application-level message to all connected peers."""
        message = Message.application_data(data)
        peer_count = len(self.peer_links)
        logger.info(
            f"Broadcasting {message.message_type} to {peer_count} peers.",
        )
        self._send_message_to_peers(message)

    def _handle_application_message(
        self,
        _link: PeerLink,
        content: ApplicationData,
    ) -> None:
        """Handle application data.

        Offloaded to a background thread to prevent blocking the main P2P networking loop
        during long-running database or AI operations.
        """

        def process_message() -> None:
            try:
                message = json.loads(content.data)
                msg_type = message.get("type")
                msg_data = message.get("data")

                if msg_type == "new_block_header":
                    # This is a short, thread-safe database operation.
                    with db_lock:
                        with self.session_maker() as session:
                            try:
                                add_block_from_peer_data(session, msg_data)
                            except SyncRequiredError:
                                background_thread_logger.info(
                                    f"Sync required from peer at height {msg_data['height']}",
                                )
                                self._trigger_sync(_link, msg_data["height"])

                elif msg_type == "sync_request":
                    # Handle block sync request from peer
                    since = msg_data.get("since", 0)
                    background_thread_logger.info(
                        f"Received sync_request from {_link.fmt_addr()} (since height: {since})",
                    )
                    with self.session_maker() as session:
                        blocks = (
                            session.query(Block)
                            .filter(Block.height > since)
                            .all()
                        )
                        background_thread_logger.info(
                            f"Sending sync_response to {_link.fmt_addr()} with {len(blocks)} blocks.",
                        )
                        response = {
                            "type": "sync_response",
                            "data": {"blocks": [b.to_dict() for b in blocks]},
                        }
                        self._send_application_message(_link, response)

                elif msg_type == "sync_response":
                    # Handle block sync response from peer
                    peer_blocks = msg_data.get("blocks", [])
                    background_thread_logger.info(
                        f"Received sync_response from {_link.fmt_addr()} with {len(peer_blocks)} blocks.",
                    )
                    with db_lock:
                        with self.session_maker() as session:
                            for b_data in peer_blocks:
                                try:
                                    add_block_from_peer_data(session, b_data)
                                except Exception as e:
                                    background_thread_logger.warning(
                                        f"Failed to add synced block #{b_data.get('height')}: {e}",
                                    )
                                    continue

                elif msg_type == "fact_proposal":
                    self._handle_fact_proposal(_link, msg_data)

                elif msg_type == "fact_vote":
                    self._handle_fact_vote(_link, msg_data)

            except Exception as exc:
                background_thread_logger.error(
                    f"Error processing peer message: {exc}",
                )

        threading.Thread(target=process_message, daemon=True).start()

    def _send_application_message(
        self,
        link: PeerLink,
        data: dict[str, Any],
    ) -> None:
        """Send an application-level message to a specific peer."""
        message: Message = Message.application_data(json.dumps(data))
        self._send_message(link, message)

    def _proactive_sync(self) -> None:
        """Broadcast a sync request to the entire network based on local height."""
        with self.session_maker() as session:
            latest = get_latest_block(session)
            current_height = latest.height if latest else 0

        sync_req = {
            "type": "sync_request",
            "data": {"since": current_height},
        }
        background_thread_logger.info(
            f"Proactively requesting sync from network (current height: {current_height})",
        )
        self.broadcast_application_message(json.dumps(sync_req))

    def _trigger_sync(self, link: PeerLink, _target_height: int) -> None:
        """Request missing blocks from a specific peer."""
        with self.session_maker() as session:
            latest = get_latest_block(session)
            current_height = latest.height if latest else 0

        sync_req = {
            "type": "sync_request",
            "data": {"since": current_height},
        }
        background_thread_logger.info(
            f"Triggering sync from {link.fmt_addr()} since height {current_height}",
        )
        self._send_application_message(link, sync_req)

    def _handle_fact_proposal(
        self,
        link: PeerLink,
        data: dict[str, Any],
    ) -> None:
        """Handle a fact proposal from a peer."""
        fact_data = data.get("fact")
        if not fact_data:
            return

        # Simple validation: if we don't have it, we might vote for it if it looks good
        # For now, we just auto-vote if it has high confidence
        if fact_data.get("extraction_confidence", 0) > 0.8:
            background_thread_logger.info(
                f"Auto-voting for high-confidence fact from {link.fmt_addr()}: {fact_data['hash'][:8]}",
            )
            vote = {
                "type": "fact_vote",
                "data": {"fact_hash": fact_data["hash"]},
            }
            self._send_application_message(link, vote)

    def _handle_fact_vote(self, link: PeerLink, data: dict[str, Any]) -> None:
        """Handle a vote for a fact we proposed or know about."""
        fact_hash = data.get("fact_hash")
        if not fact_hash:
            return

        peer_id = link.fmt_addr()
        if fact_hash not in self.fact_votes:
            self.fact_votes[fact_hash] = set()

        self.fact_votes[fact_hash].add(peer_id)
        background_thread_logger.info(
            f"Received vote for {fact_hash[:8]} from {peer_id}. Total: {len(self.fact_votes[fact_hash])}",
        )

        # If enough votes, upgrade status
        if len(self.fact_votes[fact_hash]) >= 3:
            with db_lock:
                with self.session_maker() as session:
                    fact = (
                        session.query(Fact)
                        .filter(Fact.hash == fact_hash)
                        .one_or_none()
                    )
                    if fact and fact.status != FactStatus.EMPIRICALLY_VERIFIED:
                        fact.status = FactStatus.EMPIRICALLY_VERIFIED
                        background_thread_logger.info(
                            f"Fact {fact_hash[:8]} has reached consensus! Status -> EMPIRICALLY_VERIFIED",
                        )
                        session.commit()

    def _broadcast_fact_proposals(self, facts: list[Fact]) -> None:
        """Broadcast new facts to the network for validation and voting."""
        for fact in facts:
            # Only propose high-confidence facts
            if fact.extraction_confidence > 0.7:
                proposal = {
                    "type": "fact_proposal",
                    "data": {
                        "fact": SerializedFact.from_fact(fact).model_dump(),
                    },
                }
                self.broadcast_application_message(json.dumps(proposal))

    def _background_work_loop(self) -> None:
        """Handle Fact-gathering and block-sealing."""
        # Use SystemRandom for security-compliant randomness
        rng = secrets.SystemRandom()

        # Stagger the startup of the background work to prevent thundering herd on shared resources.
        startup_delay = rng.uniform(5, 15)
        background_thread_logger.info(
            f"Background thread starting in {startup_delay:.1f}s...",
        )
        time.sleep(startup_delay)

        # --- NEW: PROACTIVE SYNC ---
        # Before starting the first work cycle, ask the network for its current state.
        self._proactive_sync()
        # Wait a few seconds for potential sync responses to be processed.
        time.sleep(5)

        background_thread_logger.info("Starting continuous Axiom work cycle.")
        while True:
            if self.limit_cycles and self.cycle_count >= self.limit_cycles:
                background_thread_logger.info(
                    f"Reached cycle limit ({self.limit_cycles}). Process exiting.",
                )
                # Use os._exit to ensure all threads (including Flask) stop immediately.
                os._exit(0)

            # Check time limit at the start of every cycle
            if (
                self.limit_time
                and (time.time() - self.start_time) >= self.limit_time
            ):
                background_thread_logger.info(
                    f"Reached time limit ({self.limit_time}s). Process exiting.",
                )
                os._exit(0)

            self.cycle_count += 1

            # --- Heartbeat ---
            if self.heartbeat_file:
                try:
                    with self.session_maker() as session:
                        latest = get_latest_block(session)
                        status = {
                            "status": "online",
                            "last_update": datetime.now().isoformat(),
                            "block_height": latest.height if latest else 0,
                            "version": __version__,
                            "cycle_count": self.cycle_count,
                            "uptime_seconds": int(
                                time.time() - self.start_time,
                            ),
                        }
                        with open(self.heartbeat_file, "w") as f:
                            json.dump(status, f)
                except Exception as e:
                    background_thread_logger.error(
                        f"Failed to write heartbeat: {e}",
                    )
            # Periodically re-sync to ensure we haven't missed any headers during quiet times.
            if rng.random() < 0.2:  # 20% chance each cycle to check for drift
                self._proactive_sync()
            background_thread_logger.info("Axiom engine cycle start")

            # --- PHASE 1: Fact Gathering & Sealing ---
            # Acquire the lock only for the duration of this phase.
            with db_lock:
                with self.session_maker() as session:
                    try:
                        # Log trending topics for diagnostics, but do NOT gate on them.
                        # Every node must always try to contribute facts.
                        try:
                            topics = zeitgeist_engine.get_trending_topics(
                                top_n=1,
                            )
                            if topics:
                                background_thread_logger.info(
                                    f"Trending topic this cycle: {topics[0]}",
                                )
                        except Exception:
                            # Zeitgeist failure must not abort the work cycle.
                            background_thread_logger.warning(
                                "Zeitgeist trending topics unavailable.",
                            )

                        content_list = (
                            discovery_rss.get_content_from_prioritized_feed()
                        )

                        if not content_list:
                            background_thread_logger.debug(
                                "No new content found. Proceeding to verification phase.",
                            )
                        else:
                            facts_for_sealing: list[Fact] = []
                            adder = crucible.CrucibleFactAdder(
                                session,
                                fact_indexer,
                            )
                            for item in content_list:
                                source_url = item["source_url"]
                                domain = urlparse(source_url).netloc

                                # --- Phase 2: Domain Reputation ---
                                # Extract base domain (e.g., news.bbc.co.uk -> bbc.co.uk)
                                # Simple heuristic for now: check if netloc contains any trusted domain key
                                reputation = 0.5
                                for (
                                    trusted_domain,
                                    score,
                                ) in TRUSTED_DOMAINS.items():
                                    if trusted_domain in domain:
                                        reputation = score
                                        break

                                source = session.query(Source).filter(
                                    Source.domain == domain,
                                ).one_or_none() or Source(domain=domain)
                                session.add(source)

                                # --- Phase 2: Full-Text Fetching ---
                                background_thread_logger.info(
                                    f"Fetching full text for: {source_url}",
                                )
                                full_text = article_fetcher.fetch_article_text(
                                    source_url,
                                )

                                # Fallback to RSS summary if full-text fails or is too short
                                content_to_process = (
                                    full_text or item["content"]
                                )
                                if not full_text:
                                    background_thread_logger.warning(
                                        f"Full-text fetch failed for {source_url}, falling back to RSS summary.",
                                    )

                                new_facts = crucible.extract_facts_from_text(
                                    content_to_process,
                                    source_url=source_url,
                                    published_date=item.get("published_date"),
                                )
                                background_thread_logger.info(
                                    f"Extracted {len(new_facts)} potential facts from {source_url}",
                                )
                                for fact in new_facts:
                                    fact.set_hash()
                                    # Check for existing fact with the same hash
                                    existing_fact = (
                                        session.query(Fact)
                                        .filter(Fact.hash == fact.hash)
                                        .one_or_none()
                                    )

                                    if existing_fact:
                                        background_thread_logger.info(
                                            f"Found duplicate fact {fact.hash[:8]}, corroborating instead of inserting.",
                                        )
                                        if source not in existing_fact.sources:
                                            existing_fact.sources.append(
                                                source,
                                            )
                                            existing_fact.score += 1
                                        session.commit()
                                        continue

                                    # If not a duplicate, proceed with adding
                                    fact.sources.append(source)
                                    fact.source_domain_reputation = reputation
                                    session.add(fact)
                                    session.commit()
                                    with fact_indexer_lock:
                                        adder.add(fact)
                                    facts_for_sealing.append(fact)

                                # --- Phase 3: Proactive P2P Fact Proposals ---
                                if facts_for_sealing:
                                    self._broadcast_fact_proposals(
                                        facts_for_sealing,
                                    )

                            if facts_for_sealing:
                                background_thread_logger.info(
                                    f"Preparing to seal {len(facts_for_sealing)} new facts into a block...",
                                )
                                latest_block = get_latest_block(session)
                                assert latest_block is not None
                                fact_hashes = sorted(
                                    [f.hash for f in facts_for_sealing],
                                )
                                new_block = Block(
                                    height=latest_block.height + 1,
                                    previous_hash=latest_block.hash,
                                    fact_hashes=json.dumps(fact_hashes),
                                    timestamp=time.time(),
                                )
                                new_block.seal_block(difficulty=4)
                                session.add(new_block)
                                session.commit()
                                background_thread_logger.info(
                                    f"Successfully sealed and added Block #{new_block.height}.",
                                )
                                broadcast_data = {
                                    "type": "new_block_header",
                                    "data": new_block.to_dict(),
                                }
                                self.broadcast_application_message(
                                    json.dumps(broadcast_data),
                                )
                                background_thread_logger.info(
                                    "Broadcasted new block header to network.",
                                )
                    except Exception as exc:
                        background_thread_logger.exception(
                            f"Critical error in learning loop: {exc}",
                        )
                    finally:
                        background_thread_logger.info(
                            "Axiom gathering cycle complete.",
                        )

            # --- The database lock is now RELEASED. The API is fully responsive. ---

            # --- PHASE 2: Verification ---
            # Acquire the lock again for this separate database transaction.
            with db_lock:
                with self.session_maker() as session:
                    try:
                        background_thread_logger.info(
                            "Starting verification phase...",
                        )
                        facts_to_verify = (
                            session.query(Fact)
                            .filter(Fact.status == "ingested")
                            .all()
                        )
                        if not facts_to_verify:
                            background_thread_logger.debug(
                                "No new facts to verify.",
                            )
                        else:
                            # SCALABILITY FIX: Limit the number of facts verified in a single cycle
                            # to avoid O(N^2) bottlenecks when the ledger is large.
                            verification_batch_size = 20
                            facts_to_verify = facts_to_verify[
                                :verification_batch_size
                            ]

                            background_thread_logger.info(
                                f"Processing {len(facts_to_verify)} facts for verification in this cycle.",
                            )
                            for fact in facts_to_verify:
                                # Check time limit DURING the loop to ensure we don't exceed it.
                                if (
                                    self.limit_time
                                    and (time.time() - self.start_time)
                                    >= self.limit_time
                                ):
                                    background_thread_logger.info(
                                        "Time limit reached during verification. Terminating cycle.",
                                    )
                                    os._exit(0)

                                claims = verification_engine.find_corroborating_claims(
                                    fact,
                                    session,
                                )
                                if len(claims) >= CORROBORATION_THRESHOLD:
                                    fact.status = FactStatus.CORROBORATED
                                    background_thread_logger.debug(
                                        f"Fact '{fact.hash[:8]}' has been corroborated with {len(claims)} pieces of evidence.",
                                    )
                                    fact.score += 10
                            session.commit()
                    except Exception as exc:
                        background_thread_logger.exception(
                            f"Error during verification phase: {exc}",
                        )

            # --- The database lock is RELEASED again. ---

            background_thread_logger.info(
                "Axiom cycle finished. Sleeping for 7.5 minutes.",
            )
            # Jitter prevents all nodes from hammering the same RSS feeds simultaneously.
            jitter = rng.uniform(0, 60)
            time.sleep(450 + jitter)

    def start(self) -> None:  # type: ignore[override]
        """Start all background tasks and the main P2P loop."""
        work_thread = threading.Thread(
            target=self._background_work_loop,
            daemon=True,
        )
        work_thread.start()

        logger.info("Starting P2P network update loop...")
        while True:
            time.sleep(0.1)
            self.update()

    @classmethod
    def start_node(
        cls,
        host: str,
        port: int,
        bootstrap_peer: str | None,
        db_name: str = "axiom_ledger.db",
        limit_cycles: int | None = None,
        **kwargs: Any,
    ) -> AxiomNode:
        """Create and initialize a complete AxiomNode.

        This is the preferred way to instantiate the node.
        """
        # 1. Use the parent's factory to create the low-level P2P components.
        p2p_instance = P2PBaseNode.start(ip_address=host, port=port)

        # 2. Create an instance of our AxiomNode, passing the bootstrap flag.
        axiom_instance = cls(
            host=p2p_instance.ip_address,
            port=p2p_instance.port,
            bootstrap_peer=bootstrap_peer,
            db_name=db_name,
            limit_cycles=limit_cycles,
            limit_time=kwargs.get("limit_time"),
            heartbeat_file=kwargs.get("heartbeat_file"),
        )

        # 3. Transfer the initialized P2P components to our instance.
        axiom_instance.serialized_port = p2p_instance.serialized_port
        axiom_instance.private_key = p2p_instance.private_key
        axiom_instance.public_key = p2p_instance.public_key
        axiom_instance.serialized_public_key = (
            p2p_instance.serialized_public_key
        )
        axiom_instance.peer_links = p2p_instance.peer_links
        axiom_instance.server_socket = p2p_instance.server_socket

        return axiom_instance


# --- All Flask API endpoints are UNCHANGED ---
app = Flask(__name__)
CORS(app)
node_instance: AxiomNode
fact_indexer: FactIndexer


@app.route("/chat", methods=["POST"])
def handle_chat_query() -> Response | tuple[Response, int]:
    """Handle natural language queries from the client.

    Finding the most semantically similar facts in the ledger.
    """
    data = request.get_json()
    if not data or "query" not in data:
        return jsonify({"error": "Missing 'query' in request body"}), 400

    query = data["query"]

    with fact_indexer_lock:
        closest_facts = fact_indexer.find_closest_facts(query)

    # Enrich results with source information
    enriched_results = []
    with db_lock:
        with node_instance.session_maker() as session:
            for result in closest_facts:
                fact_id = result["fact_id"]
                fact = session.get(Fact, fact_id)
                if fact:
                    result["sources"] = [s.domain for s in fact.sources]
                    result["status"] = fact.status.value
                    result["score"] = fact.score
                enriched_results.append(result)

    # Return the results to the client.
    return jsonify({"results": enriched_results})


@app.route("/get_timeline/<topic>", methods=["GET"])
def handle_get_timeline(topic: str) -> Response:
    """Assembles a verifiable timeline of facts related to a topic."""
    with db_lock:
        with node_instance.session_maker() as session:
            initial_facts = semantic_search_ledger(
                session,
                topic,
                min_status="ingested",
                top_n=50,
            )
            if not initial_facts:
                return jsonify(
                    {
                        "timeline": [],
                        "message": "No facts found for this topic.",
                    },
                )

            def get_date_from_fact(fact: Fact) -> datetime:
                dates = _extract_dates(fact.content)
                return min(dates) if dates else datetime.min

            sorted_facts = sorted(initial_facts, key=get_date_from_fact)
            timeline_data = [
                SerializedFact.from_fact(f).model_dump() for f in sorted_facts
            ]
            return jsonify({"timeline": timeline_data})


@app.route("/get_chain_height", methods=["GET"])
def handle_get_chain_height() -> Response:
    """Handle get chain height request."""
    with db_lock:
        with node_instance.session_maker() as session:
            latest_block = get_latest_block(session)
            return jsonify(
                {"height": latest_block.height if latest_block else -1},
            )


@app.route("/get_blocks", methods=["GET"])
def handle_get_blocks() -> Response:
    """Handle get blocks request."""
    since_height = int(request.args.get("since", -1))
    with node_instance.session_maker() as session:
        blocks = (
            session.query(Block)
            .filter(Block.height > since_height)
            .order_by(Block.height.asc())
            .all()
        )
        blocks_data = [
            {
                "height": b.height,
                "hash": b.hash,
                "previous_hash": b.previous_hash,
                "timestamp": b.timestamp,
                "nonce": b.nonce,
                "fact_hashes": json.loads(b.fact_hashes),
                "merkle_root": b.merkle_root,
            }
            for b in blocks
        ]
        return jsonify({"blocks": blocks_data})


@app.route("/status", methods=["GET"])
def handle_get_status() -> Response:
    """Handle status request."""
    with node_instance.session_maker() as session:
        latest_block = get_latest_block(session)
        height = latest_block.height if latest_block else 0
        return jsonify(
            {
                "status": "ok",
                "latest_block_height": height,
                "version": __version__,
            },
        )


@app.route("/local_query", methods=["GET"])
def handle_local_query() -> Response:
    """Handle local query request using semantic vector search."""
    search_term = request.args.get("term") or ""
    with node_instance.session_maker() as session:
        results = semantic_search_ledger(session, search_term)
        fact_models = [
            SerializedFact.from_fact(fact).model_dump() for fact in results
        ]
        return jsonify({"results": fact_models})


@app.route("/get_peers", methods=["GET"])
def handle_get_peers() -> Response:
    """Handle get peers request."""
    known_peers = []
    if node_instance is not None:
        known_peers = [link.fmt_addr() for link in node_instance.iter_links()]
    return jsonify({"peers": known_peers})


@app.route("/get_fact_ids", methods=["GET"])
def handle_get_fact_ids() -> Response:
    """Handle get fact ids request."""
    with node_instance.session_maker() as session:
        fact_ids: list[int] = [
            fact.id for fact in session.query(Fact).with_entities(Fact.id)
        ]
        return jsonify({"fact_ids": fact_ids})


@app.route("/get_fact_hashes", methods=["GET"])
def handle_get_fact_hashes() -> Response:
    """Handle get fact hashes request."""
    with node_instance.session_maker() as session:
        fact_hashes: list[str] = [
            fact.hash for fact in session.query(Fact).with_entities(Fact.hash)
        ]
        return jsonify({"fact_hashes": fact_hashes})


@app.route("/get_facts_by_id", methods=["POST"])
def handle_get_facts_by_id() -> Response:
    """Handle get facts by id request."""
    requested_ids: set[int] = set((request.json or {}).get("fact_ids", []))
    with node_instance.session_maker() as session:
        facts = list(session.query(Fact).filter(Fact.id.in_(requested_ids)))
        fact_models = [
            SerializedFact.from_fact(fact).model_dump() for fact in facts
        ]
        return jsonify({"facts": fact_models})


@app.route("/get_facts_by_hash", methods=["POST"])
def handle_get_facts_by_hash() -> Response:
    """Handle get facts by hash request."""
    requested_hashes: set[str] = set(
        (request.json or {}).get("fact_hashes", []),
    )
    with node_instance.session_maker() as session:
        facts = list(
            session.query(Fact).filter(Fact.hash.in_(requested_hashes)),
        )
        fact_models = [
            SerializedFact.from_fact(fact).model_dump() for fact in facts
        ]
        return jsonify({"facts": fact_models})


@app.route("/get_merkle_proof", methods=["GET"])
def handle_get_merkle_proof() -> Response | tuple[Response, int]:
    """Handle merkle proof request."""
    fact_hash = request.args.get("fact_hash")
    block_height_str = request.args.get("block_height")
    if not fact_hash or not block_height_str:
        return jsonify(
            {"error": "fact_hash and block_height are required parameters"},
        ), 400
    try:
        block_height = int(block_height_str)
    except ValueError:
        return jsonify({"error": "block_height must be an integer"}), 400
    with node_instance.session_maker() as session:
        block = (
            session.query(Block)
            .filter(Block.height == block_height)
            .one_or_none()
        )
        if not block:
            return jsonify(
                {"error": f"Block at height {block_height} not found"},
            ), 404
        fact_hashes_in_block = json.loads(block.fact_hashes)
        if fact_hash not in fact_hashes_in_block:
            return jsonify(
                {"error": "Fact hash not found in the specified block"},
            ), 404
        merkle_tree = merkle.MerkleTree(fact_hashes_in_block)
        try:
            fact_index = fact_hashes_in_block.index(fact_hash)
            proof = merkle_tree.get_proof(fact_index)
        except (ValueError, IndexError) as exc:
            logger.error(f"Error generating Merkle proof: {exc}")
            return jsonify({"error": "Failed to generate Merkle proof"}), 500
        return jsonify(
            {
                "fact_hash": fact_hash,
                "block_height": block_height,
                "merkle_root": block.merkle_root,
                "proof": proof,
            },
        )


@app.route("/anonymous_query", methods=["POST"])
def handle_anonymous_query() -> Response | tuple[Response, int]:
    """Handle anonymous query request."""
    return jsonify({"error": "Anonymous query not implemented in V4"}), 501


@app.route("/dao/proposals", methods=["GET"])
def handle_get_proposals() -> tuple[Response, int]:
    """Handle dao proposals request."""
    return jsonify({"error": "DAO not implemented in V4"}), 501


@app.route("/dao/submit_proposal", methods=["POST"])
def handle_submit_proposal() -> Response | tuple[Response, int]:
    """Handle submit proposal request."""
    return jsonify({"error": "DAO not implemented in V4"}), 501


@app.route("/dao/submit_vote", methods=["POST"])
def handle_submit_vote() -> Response | tuple[Response, int]:
    """Handle submit vote request."""
    return jsonify({"error": "DAO not implemented in V4"}), 501


@app.route("/verify_fact", methods=["POST"])
def handle_verify_fact() -> Response | tuple[Response, int]:
    """Handle verify fact request."""
    fact_id = (request.json or {}).get("fact_id")
    if not fact_id:
        return jsonify({"error": "fact_id is required"}), 400
    with node_instance.session_maker() as session:
        fact_to_verify = session.get(Fact, fact_id)
        if not fact_to_verify:
            return jsonify({"error": "Fact not found"}), 404
        corroborating_claims = verification_engine.find_corroborating_claims(
            fact_to_verify,
            session,
        )
        citations_report = verification_engine.verify_citations(fact_to_verify)
        verification_report = {
            "target_fact_id": fact_to_verify.id,
            "target_content": fact_to_verify.content,
            "corroboration_analysis": {
                "status": f"Found {len(corroborating_claims)} corroborating claims from other sources.",
                "corroborations": corroborating_claims,
            },
            "citation_analysis": {
                "status": f"Found {len(citations_report)} citations within the fact content.",
                "citations": citations_report,
            },
        }
        return jsonify(verification_report)


@app.route("/get_fact_context/<fact_hash>", methods=["GET"])
def handle_get_fact_context(
    fact_hash: str,
) -> Response | tuple[Response, int]:
    """Handle get fact content request."""
    with node_instance.session_maker() as session:
        target_fact = (
            session.query(Fact).filter(Fact.hash == fact_hash).one_or_none()
        )
        if not target_fact:
            return jsonify({"error": "Fact find not found"}), 404
        links = (
            session.query(FactLink)
            .filter(
                (FactLink.fact1_id == target_fact.id)
                | (FactLink.fact2_id == target_fact.id),
            )
            .all()
        )
        related_facts_data = []
        for link in links:
            other_fact = (
                link.fact2 if link.fact1_id == target_fact.id else link.fact1
            )
            related_facts_data.append(
                {
                    "relationship": link.relationship_type.value,
                    "fact": SerializedFact.from_fact(other_fact).model_dump(),
                },
            )
        return jsonify(
            {
                "target_fact": SerializedFact.from_fact(
                    target_fact,
                ).model_dump(),
                "related_facts": related_facts_data,
            },
        )


def cli_run() -> None:
    """Handle running an Axiom Node from the command line."""
    global node_instance, fact_indexer

    # 0. Configure professional logging before anything else.
    configure_logging()

    # 0.5 Detect and log environment
    env_type = detect_environment()
    logger.info(f"--- Detected Runtime Environment: {env_type} ---")

    # 1. Setup the argument parser
    parser = argparse.ArgumentParser(description="Run an Axiom P2P Node.")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host IP to bind to.",
    )
    parser.add_argument(
        "--p2p-port",
        type=int,
        default=5000,
        help="Port for P2P communication.",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="Port for the Flask API server.",
    )
    parser.add_argument(
        "--bootstrap-peer",
        type=str,
        default=None,
        help="Full URL of a peer to connect to for bootstrapping (e.g., http://host:port).",
    )
    parser.add_argument(
        "--db-name",
        type=str,
        default="axiom_ledger.db",
        help="Name of the SQLite database file.",
    )
    parser.add_argument(
        "--limit-cycles",
        type=int,
        default=None,
        help="Limit the number of work cycles before exiting (useful for CI).",
    )
    parser.add_argument(
        "--limit-time",
        type=int,
        default=None,
        help="Limit the duration of execution in seconds before exiting.",
    )
    parser.add_argument(
        "--heartbeat-file",
        type=str,
        default=None,
        help="Path to a JSON file where the node will write its current status.",
    )
    args = parser.parse_args()

    try:
        # 1.5 Log Database info
        db_path = os.path.abspath(args.db_name)
        logger.info(f"Using database: {db_path}")

        # 2. Create the AxiomNode instance, passing the arguments directly.
        node_instance = AxiomNode(
            host=args.host,
            port=args.p2p_port,
            bootstrap_peer=args.bootstrap_peer,
            db_name=args.db_name,
            limit_cycles=args.limit_cycles,
            limit_time=args.limit_time,
            heartbeat_file=args.heartbeat_file,
        )

        logger.info("--- Initializing Fact Indexer for Hybrid Search ---")
        with node_instance.session_maker() as db_session:
            # Create the indexer instance, passing it the session it needs.
            fact_indexer = FactIndexer(db_session)
            # Build the initial index.
            fact_indexer.index_facts_from_db()

        # 3. Start the Flask API server in its own thread.
        api_thread = threading.Thread(
            target=lambda: app.run(
                host=args.host,
                port=args.api_port,
                debug=False,
                use_reloader=False,
            ),
            daemon=True,
        )
        api_thread.start()
        logger.info(
            f"Flask API server started on http://{args.host}:{args.api_port}",
        )

        # 4. Start the main P2P and Axiom work loops.
        node_instance.start()

    except KeyboardInterrupt:
        logger.info("Shutdown signal received. Exiting.")
    except Exception as exc:
        logger.critical(
            f"A critical error occurred during node startup: {exc}",
            exc_info=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    cli_run()
