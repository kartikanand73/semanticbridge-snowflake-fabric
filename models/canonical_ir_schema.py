"""
SemanticBridge - Canonical IR Schema
=====================================
Platform-neutral intermediate representation of a Snowflake semantic view.
Everything downstream (enrichment, Power BI MCP deploy) reads this contract.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime
import json
import hashlib


# ─────────────────────────────────────────────
# Core Building Blocks
# ─────────────────────────────────────────────

@dataclass
class PhysicalColumn:
    """Represents a column in the underlying Snowflake table."""
    column_name: str
    data_type: str
    is_nullable: bool
    comment: Optional[str] = None


@dataclass
class IRDimension:
    """A categorical attribute — maps to a Power BI column."""
    name: str
    physical_column: str
    data_type: str
    description: Optional[str] = None
    synonyms: list[str] = field(default_factory=list)
    is_primary_key: bool = False
    display_folder: Optional[str] = None


@dataclass
class IRTimeDimension:
    """A date/time attribute — maps to Power BI date table or time intelligence."""
    name: str
    physical_column: str
    data_type: str
    description: Optional[str] = None
    granularity: str = "DAY"  # DAY, MONTH, YEAR, HOUR


@dataclass
class IRMeasure:
    """A numeric aggregation — maps to a Power BI DAX measure."""
    name: str
    expression: str              # Snowflake SQL expression e.g. SUM(NET_PAID_AMT)
    aggregation: str             # SUM, COUNT, AVG, MIN, MAX, COUNT_DISTINCT
    physical_column: str         # Underlying column being aggregated
    data_type: str
    description: Optional[str] = None
    synonyms: list[str] = field(default_factory=list)
    format_string: Optional[str] = None   # e.g. "$#,##0.00"
    display_folder: Optional[str] = None
    is_hidden: bool = False


@dataclass
class IRRelationship:
    """A join path between two entities — maps to a Power BI relationship."""
    name: str
    from_entity: str
    from_column: str
    to_entity: str
    to_column: str
    join_type: str = "MANY_TO_ONE"    # MANY_TO_ONE, ONE_TO_ONE, ONE_TO_MANY
    is_active: bool = True


@dataclass
class IREntity:
    """
    A logical table — maps to a Power BI table in the semantic model.
    Wraps a physical Snowflake table with semantic enrichment.
    """
    name: str
    physical_database: str
    physical_schema: str
    physical_table: str
    description: Optional[str] = None
    synonyms: list[str] = field(default_factory=list)

    dimensions: list[IRDimension] = field(default_factory=list)
    time_dimensions: list[IRTimeDimension] = field(default_factory=list)
    measures: list[IRMeasure] = field(default_factory=list)
    physical_columns: list[PhysicalColumn] = field(default_factory=list)


@dataclass
class IRMetadata:
    """Provenance and drift-detection metadata."""
    extracted_at: str
    source_type: str                # "snowflake_semantic_view" | "cortex_yaml_stage"
    source_database: str
    source_schema: str
    source_view_name: str
    semantic_view_hash: str         # SHA-256 of raw YAML DDL — drift detection key
    schema_fingerprint: str         # SHA-256 of physical schema — drift detection
    extractor_version: str = "1.0.0"
    client_id: Optional[str] = None
    domain: Optional[str] = None


# ─────────────────────────────────────────────
# Root Canonical IR Object
# ─────────────────────────────────────────────

@dataclass
class CanonicalIR:
    """
    The root canonical intermediate representation.
    One CanonicalIR per Snowflake semantic view.
    Serializes to JSON and lands in Azure Blob as canonical-ir.json.
    """
    metadata: IRMetadata
    entities: list[IREntity] = field(default_factory=list)
    relationships: list[IRRelationship] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: str):
        with open(path, 'w') as f:
            f.write(self.to_json())
        print(f"✅ Canonical IR saved → {path}")


# ─────────────────────────────────────────────
# Hash Utilities
# ─────────────────────────────────────────────

def compute_hash(content: str) -> str:
    """SHA-256 hash for drift detection."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def compute_schema_fingerprint(columns: list[dict]) -> str:
    """
    Fingerprint of physical schema for drift detection.
    Sensitive to column renames, type changes, drops.
    """
    schema_str = json.dumps(columns, sort_keys=True)
    return compute_hash(schema_str)
