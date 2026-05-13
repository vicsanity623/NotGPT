"""Ledger - Fact Database Logic."""

from __future__ import annotations

# Copyright (C) 2025 The Axiom Contributors
# This program is licensed under the Peer Production License (PPL).
# See the LICENSE file for full details.
import datetime
import enum
import hashlib
import json
import logging
from typing import Any

from pydantic import BaseModel
from spacy.tokens.doc import Doc
from sqlalchemy import (
    Boolean,
    Engine,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from typing_extensions import Self, TypedDict

from axiom_server import merkle
from axiom_server.common import NLP_MODEL

logger = logging.getLogger("ledger")

DB_NAME = "axiom_ledger.db"
_engine: Engine | None = None
_SessionMaker: sessionmaker[Session] | None = None


def get_engine(db_name: str = DB_NAME) -> Engine:
    """Create a new SQLAlchemy engine."""
    return create_engine(
        f"sqlite:///{db_name}",
        connect_args={"timeout": 30},
    )


def get_session_maker(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create a new session maker."""
    if engine is None:
        engine = get_engine()
    return sessionmaker(bind=engine)


ENGINE = get_engine()
SessionMaker = get_session_maker(ENGINE)


class SyncRequiredError(Exception):
    """Raised when a peer is ahead of us and we need to catch up."""

    def __init__(self, target_height: int) -> None:
        """Initialize SyncRequiredError with the target height."""
        self.target_height = target_height
        super().__init__(f"Sync required to height {target_height}")


class LedgerError(Exception):
    """Ledger Error."""

    __slots__ = ()


class Base(DeclarativeBase):
    """DeclarativeBase subclass."""

    __slots__ = ()


class FactStatus(str, enum.Enum):
    """Defines the sophisticated verification lifecycle for a Fact."""

    INGESTED = "ingested"
    LOGICALLY_CONSISTENT = "logically_consistent"
    CORROBORATED = "corroborated"
    EMPIRICALLY_VERIFIED = "empirically_verified"


class RelationshipType(str, enum.Enum):
    """Defines the nature of the link between two facts."""

    CORRELATION = "correlation"
    CONTRADICTION = "contradiction"
    CAUSATION = "causation"
    CHRONOLOGY = "chronology"
    ELABORATION = "elaboration"


class Block(Base):
    """Block table."""

    __tablename__ = "blockchain"
    height: Mapped[int] = mapped_column(Integer, primary_key=True)
    hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    nonce: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fact_hashes: Mapped[str] = mapped_column(Text, nullable=False)

    merkle_root: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="",
    )

    def __init__(self, **kwargs: Any) -> None:
        """Initialize Block."""
        super().__init__(**kwargs)
        self.nonce = self.nonce or 0

    def calculate_hash(self) -> str:
        """Return hash from this block."""
        block_string = json.dumps(
            {
                "height": self.height,
                "previous_hash": self.previous_hash,
                "fact_hashes": sorted(json.loads(self.fact_hashes)),
                "timestamp": self.timestamp,
                "nonce": self.nonce,
                "merkle_root": self.merkle_root,
            },
            sort_keys=True,
        ).encode()
        return hashlib.sha256(block_string).hexdigest()

    def seal_block(self, difficulty: int) -> None:
        """Calculate the Merkle Root and then seal the block via Proof of Work."""
        fact_hashes_list = json.loads(self.fact_hashes)
        if fact_hashes_list:
            merkle_tree = merkle.MerkleTree(fact_hashes_list)
            self.merkle_root = merkle_tree.root.hex()
        else:
            self.merkle_root = hashlib.sha256(b"").hexdigest()

        self.hash = self.calculate_hash()
        target = "0" * difficulty
        while not self.hash.startswith(target):
            self.nonce += 1
            self.hash = self.calculate_hash()
        logger.info(f"Block sealed! Hash: {self.hash}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary for P2P broadcasting."""
        return {
            "height": self.height,
            "hash": self.hash,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
        }


class SerializedSemantics(BaseModel):
    """Serialized semantics."""

    doc: str
    subject: str
    object: str


class Semantics(TypedDict):
    """Semantics dictionary."""

    doc: Doc
    subject: str
    object: str


def semantics_from_serialized(serialized: SerializedSemantics) -> Semantics:
    """Return Semantics dictionary from serialized semantics."""
    return Semantics(
        {
            "doc": Doc(NLP_MODEL.vocab).from_json(json.loads(serialized.doc)),
            "subject": serialized.subject,
            "object": serialized.object,
        },
    )


class Fact(Base):
    """A single, objective statement extracted from a source."""

    __tablename__ = "facts"

    vector_data: Mapped[FactVector] = relationship(
        back_populates="fact",
        cascade="all, delete-orphan",
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(String, default="", nullable=False)
    status: Mapped[FactStatus] = mapped_column(
        Enum(FactStatus),
        default=FactStatus.INGESTED,
        nullable=False,
    )

    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disputed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    hash: Mapped[str] = mapped_column(String, default="", nullable=False)
    last_checked: Mapped[str] = mapped_column(
        String,
        default=lambda: datetime.datetime.now(
            datetime.timezone.utc,
        ).isoformat(),
        nullable=False,
    )
    semantics: Mapped[str] = mapped_column(
        String,
        default="{}",
        nullable=False,
    )

    # --- Phase 2: Metadata for Atomic Facts ---
    published_date: Mapped[str | None] = mapped_column(String, nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    primary_source_url: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    source_domain_reputation: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    entities_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
        nullable=False,
    )

    sources: Mapped[list[Source]] = relationship(
        "Source",
        secondary="fact_source_link",
        back_populates="facts",
    )
    links: Mapped[list[FactLink]] = relationship(
        "FactLink",
        primaryjoin="or_(Fact.id == FactLink.fact1_id, Fact.id == FactLink.fact2_id)",
        viewonly=True,
    )

    @classmethod
    def from_model(cls, model: SerializedFact) -> Self:
        """Return new Fact from serialized fact."""
        return cls(
            content=model.content,
            score=model.score,
            disputed=model.disputed,
            hash=model.hash,
            last_checked=model.last_checked,
            semantics=model.semantics.model_dump_json(),
            published_date=model.published_date,
            extraction_confidence=model.extraction_confidence,
            primary_source_url=model.primary_source_url,
            source_domain_reputation=model.source_domain_reputation,
            entities_json=model.entities_json,
        )

    @property
    def corroborated(self) -> bool:
        """Return if score is positive."""
        return self.score > 0

    def has_source(self, domain: str) -> bool:
        """Return if any source uses given domain."""
        return any(source.domain == domain for source in self.sources)

    def set_hash(self) -> str:
        """Set self.hash."""
        self.hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        return self.hash

    def get_serialized_semantics(self) -> SerializedSemantics:
        """Return serialized semantics."""
        return SerializedSemantics.model_validate_json(self.semantics)

    def get_semantics(self) -> Semantics:
        """Return Semantics dictionary."""
        serializable = self.get_serialized_semantics()
        return semantics_from_serialized(serializable)

    def set_semantics(self, semantics: Semantics) -> None:
        """Serialize semantics for database storage."""
        self.semantics = json.dumps(
            {
                "doc": json.dumps(semantics["doc"].to_json()),
                "subject": semantics["subject"],
                "object": semantics["object"],
            },
        )


class FactVector(Base):
    """Stores the pre-computed NLP vector for a fact for fast semantic search."""

    __tablename__ = "fact_vectors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("facts.id"), unique=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    fact: Mapped[Fact] = relationship(back_populates="vector_data")


class SerializedFact(BaseModel):
    """Serialized Fact table entry."""

    content: str
    score: int
    disputed: bool
    hash: str
    last_checked: str
    semantics: SerializedSemantics
    sources: list[str]
    published_date: str | None
    extraction_confidence: float
    primary_source_url: str | None
    source_domain_reputation: float
    entities_json: str

    @classmethod
    def from_fact(cls, fact: Fact) -> Self:
        """Return SerializedFact from Fact."""
        return cls(
            content=fact.content,
            score=fact.score,
            disputed=fact.disputed,
            hash=fact.hash,
            last_checked=fact.last_checked,
            semantics=fact.get_serialized_semantics(),
            sources=[source.domain for source in fact.sources],
            published_date=fact.published_date,
            extraction_confidence=fact.extraction_confidence,
            primary_source_url=fact.primary_source_url,
            source_domain_reputation=fact.source_domain_reputation,
            entities_json=fact.entities_json,
        )


class Source(Base):
    """Source table entry."""

    __tablename__ = "source"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    facts: Mapped[list[Fact]] = relationship(
        "Fact",
        secondary="fact_source_link",
        back_populates="sources",
    )


class FactSourceLink(Base):
    """Fact Source Link table entry."""

    __tablename__ = "fact_source_link"
    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id"),
        primary_key=True,
    )
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("source.id"),
        primary_key=True,
    )


class FactLink(Base):
    """Fact Link table entry."""

    __tablename__ = "fact_link"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    relationship_type: Mapped[RelationshipType] = mapped_column(
        Enum(RelationshipType),
        default=RelationshipType.CORRELATION,
        nullable=False,
    )

    score: Mapped[int] = mapped_column(Integer, nullable=False)
    fact1_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id"),
        nullable=False,
    )
    fact1: Mapped[Fact] = relationship("Fact", foreign_keys=[fact1_id])
    fact2_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id"),
        nullable=False,
    )
    fact2: Mapped[Fact] = relationship("Fact", foreign_keys=[fact2_id])


def initialize_database(engine: Engine) -> None:
    """Ensure the database file and ALL required tables exist."""
    Base.metadata.create_all(engine)
    logger.info("initialized database")


def get_latest_block(session: Session) -> Block | None:
    """Return latest block from session if it exists."""
    return session.query(Block).order_by(Block.height.desc()).first()


def create_genesis_block(session: Session) -> None:
    """Create initial block.

    The Genesis block is the unique starting point for the Axiom blockchain.
    We use a hardcoded timestamp to ensure that all nodes in the network
    calculate the exact same hash for block height 0, which is required
    for them to synchronize.
    """
    if get_latest_block(session):
        return
    genesis = Block(
        height=0,
        previous_hash="0",
        fact_hashes=json.dumps([]),
        timestamp=1715414400.0,
    )
    genesis.seal_block(difficulty=2)
    session.add(genesis)
    session.commit()
    logger.info("Genesis Block created and sealed.")


def add_block_from_peer_data(
    session: Session,
    block_data: dict[str, Any],
) -> Block:
    """Validate and add a new block received from a peer.

    This is the core of blockchain synchronization. It ensures that a node
    only accepts blocks that correctly extend its own version of the chain.

    Args:
        session: The active SQLAlchemy database session.
        block_data: A dictionary containing the block header data from a peer.

    Returns:
        The newly added Block object.

    Raises:
        ValueError: If the block is invalid (e.g., wrong height, hash mismatch).
        KeyError: If the peer data is missing required fields.

    """
    latest_local_block = get_latest_block(session)
    if not latest_local_block:
        raise LedgerError("Cannot add peer block: Local ledger has no blocks.")

    peer_height = block_data["height"]
    current_height = latest_local_block.height

    if peer_height <= current_height:
        logger.info(
            f"Ignoring old block #{peer_height} from peer (current height is {current_height}).",
        )
        return latest_local_block

    expected_height = current_height + 1
    if peer_height > expected_height:
        logger.warning(
            f"Received future block #{peer_height} (expected #{expected_height}). Sync required.",
        )
        raise SyncRequiredError(target_height=peer_height)

    if block_data["previous_hash"] != latest_local_block.hash:
        raise ValueError(
            f"Block integrity error: Peer block's previous_hash "
            f"({block_data['previous_hash']}) does not match local head "
            f"({latest_local_block.hash}). A fork may have occurred.",
        )

    new_block = Block(
        height=block_data["height"],
        hash=block_data["hash"],
        previous_hash=block_data["previous_hash"],
        merkle_root=block_data["merkle_root"],
        timestamp=block_data["timestamp"],
        nonce=block_data.get("nonce", 0),
        fact_hashes="[]",
    )

    try:
        session.add(new_block)
        session.commit()
        logger.info(
            f"Added new block #{new_block.height} from peer to local ledger.",
        )
        return new_block
    except IntegrityError:
        session.rollback()
        logger.warning(
            f"Block #{block_data['height']} already exists in ledger. Skipping duplicate.",
        )
        return get_latest_block(session) or latest_local_block


def get_all_facts_for_analysis(session: Session) -> list[Fact]:
    """Return list of all facts."""
    return session.query(Fact).all()


def add_fact_corroboration(
    session: Session,
    fact_id: int,
    source_id: int,
) -> None:
    """Increment a fact's trust score and add the source to it. Both must already exist."""
    fact = session.get(Fact, fact_id)
    source = session.get(Source, source_id)
    if fact is None:
        raise LedgerError(f"fact not found: {fact_id=}")
    if source is None:
        raise LedgerError(f"source not found: {source_id=}")
    add_fact_object_corroboration(fact, source)


def add_fact_object_corroboration(fact: Fact, source: Source) -> None:
    """Increment a fact's trust score and add the source to it. Does nothing if the source already exists."""
    if source not in fact.sources:
        fact.sources.append(source)
        fact.score += 1
        logger.info(
            f"corroborated existing fact {fact.id} {fact.score=} with source {source.id}",
        )


def insert_uncorroborated_fact(
    session: Session,
    content: str,
    source_id: int,
) -> None:
    """Insert a fact for the first time. The source must exist."""
    source = session.get(Source, source_id)
    if source is None:
        raise LedgerError(f"source not found: {source_id=}")
    fact = Fact(content=content, score=0, sources=[source])
    fact.set_hash()
    session.add(fact)
    logger.info(f"inserted uncorroborated fact {fact.id=}")


def insert_relationship(
    session: Session,
    fact_id_1: int,
    fact_id_2: int,
    score: int,
    relationship_type: RelationshipType = RelationshipType.CORRELATION,
) -> None:
    """Insert a relationship between two facts into the knowledge graph. Both facts must exist."""
    fact1 = session.get(Fact, fact_id_1)
    fact2 = session.get(Fact, fact_id_2)
    if fact1 is None:
        raise LedgerError(f"fact(s) not found: {fact_id_1=}")
    if fact2 is None:
        raise LedgerError(f"fact(s) not found: {fact_id_2=}")
    insert_relationship_object(session, fact1, fact2, score, relationship_type)


def insert_relationship_object(
    session: Session,
    fact1: Fact,
    fact2: Fact,
    score: int,
    relationship_type: RelationshipType,
) -> None:
    """Insert fact relationship given Fact objects."""
    link = FactLink(
        score=score,
        fact1=fact1,
        fact2=fact2,
        relationship_type=relationship_type,
    )
    session.add(link)
    logger.info(
        f"inserted {relationship_type.value} relationship between {fact1.id=} and {fact2.id=} with {score=}",
    )


def mark_facts_as_disputed(
    session: Session,
    original_facts_id: int,
    new_facts_id: int,
) -> None:
    """Mark two facts as disputed and links them together."""
    original_facts = session.get(Fact, original_facts_id)
    new_facts = session.get(Fact, new_facts_id)
    if original_facts is None:
        raise LedgerError(f"fact not found: {original_facts_id=}")
    if new_facts is None:
        raise LedgerError(f"fact not found: {new_facts_id=}")
    mark_fact_objects_as_disputed(session, original_facts, new_facts)


def mark_fact_objects_as_disputed(
    session: Session,
    original_fact: Fact,
    new_fact: Fact,
) -> None:
    """Mark two Fact objects as disputed and link them."""
    original_fact.disputed = True
    new_fact.disputed = True
    link = FactLink(
        score=-1,
        fact1=original_fact,
        fact2=new_fact,
        relationship_type=RelationshipType.CONTRADICTION,
    )
    session.add(link)
    logger.info(
        f"marked facts as disputed: {original_fact.id=}, {new_fact.id=}",
    )


class Votes(TypedDict):
    """Votes dictionary."""

    choice: str
    weight: float


class Proposal(TypedDict):
    """Proposal dictionary."""

    text: str
    proposer: str
    votes: dict[str, Votes]


def export_ledger_to_jsonl(session: Session, output_path: str) -> int:
    """Export all non-disputed facts and their metadata to a JSONL file.

    Args:
        session: The SQLAlchemy session to use.
        output_path: Path to the output file.

    Returns:
        The number of facts exported.

    """
    facts = session.query(Fact).filter(Fact.disputed == False).all()  # noqa: E712
    count = 0
    with open(output_path, "w") as f:
        for fact in facts:
            data = SerializedFact.from_fact(fact).model_dump()
            f.write(json.dumps(data) + "\n")
            count += 1
    logger.info(f"Exported {count} facts to {output_path}")
    return count
