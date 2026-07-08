"""
SemanticBridge - Main Pipeline Runner
======================================
Single entry point for the full extraction + enrichment pipeline.

Usage:
    python run.py                          # Full pipeline
    python run.py --step extract           # Extraction only
    python run.py --step enrich            # Enrichment only (requires prior extract)
    python run.py --domain claims          # Single domain only
    python run.py --config custom.yaml     # Custom config file
"""

import argparse
import yaml
import json
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from extractor.snowflake_extractor import SnowflakeExtractor
from ontology.enrichment_engine import OntologyEnrichmentEngine


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def run_extraction(config: dict, domain_filter: str = None):
    """Step 1: Extract Snowflake semantic views → Canonical IR → Blob/local"""
    print("\n" + "="*60)
    print("  STEP 1: SNOWFLAKE EXTRACTION")
    print("="*60)

    extractor = SnowflakeExtractor(config)
    extractor.run()


def run_enrichment(config: dict, domain_filter: str = None):
    """Step 2: Enrich Canonical IR with ontology → Enriched IR + Gap Report"""
    print("\n" + "="*60)
    print("  STEP 2: ONTOLOGY ENRICHMENT")
    print("="*60)

    ontology_paths = config.get('ontology', {}).get('paths', [
        './ontology/hedis_ontology.json'
    ])

    print(f"\n📚 Loading ontologies: {ontology_paths}")
    engine = OntologyEnrichmentEngine(ontology_paths)

    output_base = Path(config['output'].get('local_path', './output'))

    # Find all domains that have been extracted
    domains = [
        d for d in output_base.iterdir()
        if d.is_dir() and (d / 'canonical-ir.json').exists()
    ]

    if domain_filter:
        domains = [d for d in domains if d.name == domain_filter.lower()]

    if not domains:
        print("⚠️  No extracted domains found. Run extraction first.")
        return

    for domain_path in domains:
        canonical_ir_path = domain_path / 'canonical-ir.json'
        engine.enrich(
            canonical_ir_path=str(canonical_ir_path),
            output_dir=str(domain_path)
        )

    print("\n✅ Enrichment complete.")


def print_summary(config: dict):
    """Print a summary of what was extracted and enriched."""
    print("\n" + "="*60)
    print("  PIPELINE SUMMARY")
    print("="*60)

    output_base = Path(config['output'].get('local_path', './output'))
    run_summary_path = output_base / 'run-summary.json'

    if run_summary_path.exists():
        with open(run_summary_path) as f:
            summary = json.load(f)
        print(f"\n  Client:     {summary.get('client_id')}")
        print(f"  Run at:     {summary.get('run_at')}")
        print(f"  Views:      {summary.get('total_views')}")
        print(f"  Successful: {summary.get('successful')}")
        print(f"  Total gaps: {summary.get('total_gaps')}")
        print(f"\n  Domains extracted:")
        for v in summary.get('views', []):
            status_icon = "✅" if v['status'] == 'success' else "❌"
            gap_icon = "⚠️" if v['gaps'] > 0 else "  "
            print(f"    {status_icon} {v['domain']:<20} {gap_icon} {v['gaps']} gap(s)")

    print(f"\n  Output directory: {output_base}/")
    print(f"""
  Files per domain:
    raw-semantic-view.yaml   ← Original Snowflake DDL
    canonical-ir.json        ← Parsed platform-neutral IR
    enriched-ir.json         ← Ontology-enriched IR (after enrichment)
    gap-report.json          ← Governance gaps
    manifest.json            ← Drift detection state
""")


def main():
    parser = argparse.ArgumentParser(
        description='SemanticBridge — Snowflake → Fabric Semantic Layer Accelerator'
    )
    parser.add_argument(
        '--step',
        choices=['extract', 'enrich', 'all'],
        default='all',
        help='Pipeline step to run'
    )
    parser.add_argument(
        '--domain',
        type=str,
        default=None,
        help='Single domain/schema to process (e.g. claims)'
    )
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to config YAML file'
    )

    args = parser.parse_args()

    print("""
╔══════════════════════════════════════════════════════╗
║           SemanticBridge v1.0.0                      ║
║  Snowflake Semantic Layer → Microsoft Fabric         ║
║  with Healthcare Ontology Enrichment                 ║
╚══════════════════════════════════════════════════════╝
    """)

    # Load config
    try:
        config = load_config(args.config)
        print(f"✅ Config loaded: {args.config}")
    except FileNotFoundError:
        print(f"❌ Config file not found: {args.config}")
        print(f"   Copy config.yaml.example → config.yaml and fill in your values.")
        sys.exit(1)

    # Run pipeline steps
    if args.step in ('extract', 'all'):
        run_extraction(config, args.domain)

    if args.step in ('enrich', 'all'):
        run_enrichment(config, args.domain)

    print_summary(config)

    print("""
  ─────────────────────────────────────────────────────
  Next Step: Deploy to Microsoft Fabric
  Run the Power BI MCP deploy agent against enriched-ir.json
  ─────────────────────────────────────────────────────
    """)


if __name__ == '__main__':
    main()
