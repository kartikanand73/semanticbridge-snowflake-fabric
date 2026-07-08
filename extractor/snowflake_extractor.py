"""
SemanticBridge - Snowflake Semantic View Extractor
===================================================
Connects to Snowflake, discovers semantic views, extracts YAML DDL,
validates against physical schema, parses to Canonical IR, writes to
Azure Blob Storage (or local output folder for dev/demo).

Usage:
    extractor = SnowflakeExtractor(config)
    extractor.run()

Config is loaded from config.yaml — see config.yaml.example
"""

import os
import json
import yaml
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# Snowflake connector — install: pip install snowflake-connector-python
import snowflake.connector

# Azure Blob — install: pip install azure-storage-blob
# Only needed if use_blob=True in config
try:
    from azure.storage.blob import BlobServiceClient
    BLOB_AVAILABLE = True
except ImportError:
    BLOB_AVAILABLE = False

from extractor.yaml_parser import SnowflakeYAMLParser
from models.canonical_ir_schema import compute_hash, compute_schema_fingerprint


class SnowflakeExtractor:

    def __init__(self, config: dict):
        self.config = config
        self.snowflake_cfg = config['snowflake']
        self.output_cfg = config['output']
        self.client_id = config.get('client_id', 'demo')
        self.parser = SnowflakeYAMLParser(client_id=self.client_id)

        # Output path for local dev / demo
        self.local_output = Path(self.output_cfg.get('local_path', './output'))
        self.local_output.mkdir(parents=True, exist_ok=True)

        # Azure Blob (optional)
        self.use_blob = self.output_cfg.get('use_blob', False)
        if self.use_blob:
            if not BLOB_AVAILABLE:
                raise ImportError(
                    "azure-storage-blob not installed. "
                    "Run: pip install azure-storage-blob"
                )
            self.blob_client = BlobServiceClient.from_connection_string(
                self.output_cfg['blob_connection_string']
            )
            self.container = self.output_cfg['blob_container']

    # ─────────────────────────────────────────────
    # Main Entry Point
    # ─────────────────────────────────────────────

    def run(self):
        print("\n🚀 SemanticBridge Extractor Starting")
        print(f"   Client: {self.client_id}")
        print(f"   Database: {self.snowflake_cfg['database']}\n")

        conn = self._connect()

        try:
            views = self._discover_semantic_views(conn)

            if not views:
                print("⚠️  No semantic views found in database.")
                return

            print(f"📋 Found {len(views)} semantic view(s):\n")
            for v in views:
                print(f"   → {v['name']} ({v['schema']})")

            print()
            results = []

            for view in views:
                result = self._process_view(conn, view)
                results.append(result)

            # Write run summary
            self._write_run_summary(results)
            print("\n✅ Extraction complete.")
            print(f"   Output: {self.local_output}/\n")

        finally:
            conn.close()

    # ─────────────────────────────────────────────
    # Snowflake Connection
    # ─────────────────────────────────────────────

    def _connect(self) -> snowflake.connector.SnowflakeConnection:
        print("🔗 Connecting to Snowflake...")

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        private_key_path = self.snowflake_cfg.get('private_key_path')

        with open(private_key_path, 'rb') as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
                backend=default_backend()
            )

        private_key_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )

        conn = snowflake.connector.connect(
            account=self.snowflake_cfg['account'],
            user=self.snowflake_cfg['user'],
            private_key=private_key_bytes,
            role=self.snowflake_cfg.get('role', 'ACCOUNTADMIN'),
            warehouse=self.snowflake_cfg.get('warehouse', 'DEMO_WH'),
            database=self.snowflake_cfg['database']
        )

        print("   ✅ Connected\n")
        return conn


    # ─────────────────────────────────────────────
    # Discovery
    # ─────────────────────────────────────────────

    def _discover_semantic_views(
        self,
        conn: snowflake.connector.SnowflakeConnection
    ) -> list[dict]:
        """
        Discover all semantic views in the configured database.
        Uses SHOW SEMANTIC VIEWS — returns name, schema, owner, created_on.
        """
        print("🔍 Discovering semantic views...")
        cursor = conn.cursor()
        cursor.execute(
            f"SHOW SEMANTIC VIEWS IN DATABASE {self.snowflake_cfg['database']}"
        )

        rows = cursor.fetchall()
        cols = [desc[0].lower() for desc in cursor.description]

        views = []
        for row in rows:
            row_dict = dict(zip(cols, row))
            views.append({
                'name': row_dict.get('name', ''),
                'schema': row_dict.get('schema_name', row_dict.get('schema', '')),
                'owner': row_dict.get('owner', ''),
                'created_on': str(row_dict.get('created_on', ''))
            })

        return views

    # ─────────────────────────────────────────────
    # Per-View Processing
    # ─────────────────────────────────────────────

    def _process_view(
        self,
        conn: snowflake.connector.SnowflakeConnection,
        view: dict
    ) -> dict:

        view_name = view['name']
        schema = view['schema']
        database = self.snowflake_cfg['database']
        domain = schema.lower()

        print(f"⚙️  Processing: {view_name} ({schema})")

        # 1. Extract raw YAML DDL
        yaml_ddl = self._extract_ddl(conn, database, schema, view_name)
        print(f"   ✅ DDL extracted ({len(yaml_ddl)} chars)")

        # 2. Extract physical schema for base tables
        physical_schema = self._extract_physical_schema(
            conn, database, schema, yaml_ddl
        )
        print(f"   ✅ Physical schema: {len(physical_schema)} columns")

        # 3. Validate references
        gaps = self._validate_references(yaml_ddl, physical_schema)
        if gaps:
            print(f"   ⚠️  {len(gaps)} reference gap(s) detected")

        # 4. Parse YAML → Canonical IR
        canonical_ir = self.parser.parse(
            yaml_ddl=yaml_ddl,
            physical_schema=physical_schema,
            source_database=database,
            source_schema=schema,
            source_view_name=view_name
        )
        print(f"   ✅ Canonical IR built")
        print(f"      Entities: {len(canonical_ir.entities)}")
        print(f"      Relationships: {len(canonical_ir.relationships)}")

        # 5. Build manifest
        manifest = self._build_manifest(
            view, yaml_ddl, physical_schema, canonical_ir, gaps
        )

        # 6. Write outputs
        self._write_outputs(domain, yaml_ddl, canonical_ir, manifest, gaps)
        print(f"   ✅ Written to output/{domain}/\n")

        return {
            'view_name': view_name,
            'domain': domain,
            'status': 'success',
            'gaps': len(gaps)
        }

    # ─────────────────────────────────────────────
    # DDL Extraction
    # ─────────────────────────────────────────────

    def _extract_ddl(
        self,
        conn,
        database: str,
        schema: str,
        view_name: str
    ) -> str:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT GET_DDL('SEMANTIC_VIEW', "
            f"'{database}.{schema}.{view_name}')"
        )
        row = cursor.fetchone()
        return row[0] if row else ""

    # ─────────────────────────────────────────────
    # Physical Schema Extraction
    # ─────────────────────────────────────────────

    def _extract_physical_schema(
        self,
        conn,
        database: str,
        schema: str,
        yaml_ddl: str
    ) -> list[dict]:
        """
        Extract physical column metadata for all base tables
        referenced in the semantic view YAML.
        """
        table_names = self._extract_table_names(yaml_ddl)
        all_columns = []

        cursor = conn.cursor()
        for table in table_names:
            cursor.execute(f"""
                SELECT 
                    column_name,
                    data_type,
                    is_nullable,
                    comment
                FROM {database}.INFORMATION_SCHEMA.COLUMNS
                WHERE table_schema = '{schema.upper()}'
                AND table_name = '{table.upper()}'
                ORDER BY ordinal_position
            """)
            rows = cursor.fetchall()
            cols = [desc[0].lower() for desc in cursor.description]
            for row in rows:
                all_columns.append(dict(zip(cols, row)))

        return all_columns

    def _extract_table_names(self, yaml_ddl: str) -> list[str]:
        """Pull base table names from YAML DDL."""
        try:
            parsed = yaml.safe_load(yaml_ddl)
            model = parsed.get('semantic_model', parsed)
            tables = []
            for t in model.get('tables', []):
                base = t.get('base_table', {})
                if base.get('table'):
                    tables.append(base['table'])
                else:
                    tables.append(t.get('name', ''))
            return tables
        except Exception:
            return []

    # ─────────────────────────────────────────────
    # Reference Validation
    # ─────────────────────────────────────────────

    def _validate_references(
        self,
        yaml_ddl: str,
        physical_schema: list[dict]
    ) -> list[dict]:
        """
        Check every column reference in the semantic view YAML
        exists in the physical schema.
        Returns list of gap dicts for the gap report.
        """
        gaps = []
        physical_columns = {
            col['column_name'].upper()
            for col in physical_schema
        }

        try:
            parsed = yaml.safe_load(yaml_ddl)
            model = parsed.get('semantic_model', parsed)

            for table in model.get('tables', []):
                # Check dimensions
                for dim in table.get('dimensions', []):
                    col = dim.get('expr', dim.get('name', '')).upper()
                    col = col.strip()
                    if col and col not in physical_columns:
                        gaps.append({
                            'type': 'BROKEN_DIMENSION_REFERENCE',
                            'semantic_name': dim.get('name'),
                            'physical_column': col,
                            'severity': 'HIGH'
                        })

                # Check measures
                for measure in table.get('measures', []):
                    expr = measure.get('expr', '')
                    # Extract column from expression
                    import re
                    cols_in_expr = re.findall(r'\b([A-Z_][A-Z0-9_]*)\b', expr.upper())
                    agg_keywords = {
                        'SUM', 'COUNT', 'AVG', 'MIN', 'MAX',
                        'DISTINCT', 'ROUND', 'COALESCE', 'CASE',
                        'WHEN', 'THEN', 'ELSE', 'END', 'AS', 'AND',
                        'OR', 'NOT', 'NULL', 'TRUE', 'FALSE'
                    }
                    for col in cols_in_expr:
                        if col not in agg_keywords and col not in physical_columns:
                            gaps.append({
                                'type': 'BROKEN_MEASURE_REFERENCE',
                                'semantic_name': measure.get('name'),
                                'physical_column': col,
                                'severity': 'HIGH'
                            })

        except Exception as e:
            gaps.append({
                'type': 'YAML_PARSE_ERROR',
                'error': str(e),
                'severity': 'CRITICAL'
            })

        return gaps

    # ─────────────────────────────────────────────
    # Manifest Builder
    # ─────────────────────────────────────────────

    def _build_manifest(
        self,
        view: dict,
        yaml_ddl: str,
        physical_schema: list[dict],
        canonical_ir,
        gaps: list[dict]
    ) -> dict:
        return {
            "client_id": self.client_id,
            "domain": view['schema'].lower(),
            "snowflake_view_name": view['name'],
            "snowflake_schema": view['schema'],
            "snowflake_database": self.snowflake_cfg['database'],
            "last_extraction": datetime.now(timezone.utc).isoformat(),
            "semantic_view_hash": compute_hash(yaml_ddl),
            "schema_fingerprint": compute_schema_fingerprint(physical_schema),
            "entity_count": len(canonical_ir.entities),
            "relationship_count": len(canonical_ir.relationships),
            "gap_count": len(gaps),
            "drift_status": "clean" if not gaps else "gaps_detected",
            "fabric_semantic_model_id": None,   # populated after deploy
            "last_deploy": None,                 # populated after deploy
            "health_check": {
                "last_run": None,
                "status": "pending"
            },
            "ontology_versions": {
                "hedis": None,                   # populated after enrichment
                "fhir": None,
                "custom": None
            }
        }

    # ─────────────────────────────────────────────
    # Output Writers
    # ─────────────────────────────────────────────

    def _write_outputs(
        self,
        domain: str,
        yaml_ddl: str,
        canonical_ir,
        manifest: dict,
        gaps: list[dict]
    ):
        domain_path = self.local_output / domain
        domain_path.mkdir(parents=True, exist_ok=True)

        # Raw YAML DDL — untouched source
        self._write_local(
            domain_path / 'raw-semantic-view.yaml',
            yaml_ddl
        )

        # Canonical IR JSON
        self._write_local(
            domain_path / 'canonical-ir.json',
            canonical_ir.to_json()
        )

        # Manifest
        self._write_local(
            domain_path / 'manifest.json',
            json.dumps(manifest, indent=2)
        )

        # Gap Report
        gap_report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "domain": domain,
            "total_gaps": len(gaps),
            "gaps": gaps
        }
        self._write_local(
            domain_path / 'gap-report.json',
            json.dumps(gap_report, indent=2)
        )

        # Mirror to Azure Blob if configured
        if self.use_blob:
            for filename in [
                'raw-semantic-view.yaml',
                'canonical-ir.json',
                'manifest.json',
                'gap-report.json'
            ]:
                self._write_blob(domain, filename, domain_path / filename)

    def _write_local(self, path: Path, content: str):
        with open(path, 'w') as f:
            f.write(content)

    def _write_blob(self, domain: str, filename: str, local_path: Path):
        blob_path = f"semantic-bridge/{self.client_id}/{domain}/{filename}"
        blob = self.blob_client.get_blob_client(
            container=self.container,
            blob=blob_path
        )
        with open(local_path, 'rb') as f:
            blob.upload_blob(f, overwrite=True)

    def _write_run_summary(self, results: list[dict]):
        summary = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "client_id": self.client_id,
            "total_views": len(results),
            "successful": sum(1 for r in results if r['status'] == 'success'),
            "total_gaps": sum(r['gaps'] for r in results),
            "views": results
        }
        self._write_local(
            self.local_output / 'run-summary.json',
            json.dumps(summary, indent=2)
        )
