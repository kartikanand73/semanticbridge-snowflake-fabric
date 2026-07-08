"""
SemanticBridge - Ontology Enrichment Engine
============================================
Reads Canonical IR + ontology files from Blob/local.
Injects synonyms, descriptions, display folders, FHIR mappings.
Produces Enriched IR + Gap Report.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from difflib import SequenceMatcher





class OntologyEnrichmentEngine:

    def __init__(self, ontology_paths: list[str]):
        self.term_graph = self._load_ontologies(ontology_paths)

    # ─────────────────────────────────────────────
    # Load Ontologies
    # ─────────────────────────────────────────────

    def _load_ontologies(self, paths: list[str]) -> dict:
        """
        Load and merge all ontology files into a unified term graph.
        """
        merged = {
            "measures": {},
            "entities": {},
            "dimensions": {}
        }

        for path in paths:
            with open(path, 'r') as f:
                ontology = json.load(f)

            graph = ontology.get('term_graph', {})

            for measure in graph.get('measures', []):
                key = measure['canonical_name'].lower()
                merged['measures'][key] = measure

            for entity in graph.get('entities', []):
                key = entity['canonical_name'].lower()
                merged['entities'][key] = entity

            for dim in graph.get('dimensions', []):
                key = dim['canonical_name'].lower()
                merged['dimensions'][key] = dim

        print(f"   📚 Term graph loaded:")
        print(f"      Measures: {len(merged['measures'])}")
        print(f"      Entities: {len(merged['entities'])}")
        print(f"      Dimensions: {len(merged['dimensions'])}")

        return merged

    # ─────────────────────────────────────────────
    # Main Enrichment Entry Point
    # ─────────────────────────────────────────────

    def enrich(
        self,
        canonical_ir_path: str,
        output_dir: str
    ) -> dict:
        """
        Load Canonical IR, enrich with ontology, write Enriched IR + Gap Report.
        Returns enrichment summary.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Load Canonical IR
        with open(canonical_ir_path, 'r') as f:
            canonical_ir = json.load(f)

        domain = canonical_ir['metadata'].get('domain', 'unknown')
        print(f"\n🔬 Enriching: {domain}")

        enriched_ir = json.loads(json.dumps(canonical_ir))  # deep copy
        gaps = []
        enrichment_stats = {
            "measures_enriched": 0,
            "entities_enriched": 0,
            "dimensions_enriched": 0,
            "synonyms_added": 0,
            "descriptions_added": 0,
            "fhir_mappings_added": 0
        }

        # Enrich entities
        for entity in enriched_ir.get('entities', []):
            self._enrich_entity(entity, gaps, enrichment_stats)

        # Build gap report
        gap_report = self._build_gap_report(domain, canonical_ir, gaps)

        # Write outputs
        enriched_path = output_path / 'enriched-ir.json'
        gap_path = output_path / 'gap-report.json'

        with open(enriched_path, 'w') as f:
            json.dump(enriched_ir, f, indent=2)

        with open(gap_path, 'w') as f:
            json.dump(gap_report, f, indent=2)

        print(f"   ✅ Enriched IR written → enriched-ir.json")
        print(f"   ✅ Gap Report written  → gap-report.json")
        print(f"   📊 Stats: {enrichment_stats}")

        return enrichment_stats

    # ─────────────────────────────────────────────
    # Entity Enrichment
    # ─────────────────────────────────────────────

    def _enrich_entity(
        self,
        entity: dict,
        gaps: list,
        stats: dict
    ):
        entity_name = entity.get('name', '').lower()
        ontology_entity = self._match_term(entity_name, 'entities')
        
        IDENTIFIER_FIELDS = {'CLAIM_ID', 'MEMBER_ID', 'PROVIDER_ID', 'DATE_KEY', 'DRG_CODE'}

        if ontology_entity:
            self._inject_enrichment(entity, ontology_entity, stats)
            stats['entities_enriched'] += 1
        else:
            gaps.append({
                'type': 'UNMAPPED_ENTITY',
                'name': entity.get('name'),
                'severity': 'MEDIUM',
                'recommendation': f"Add '{entity.get('name')}' to ontology entity definitions"
            })

        # Enrich measures
        for measure in entity.get('measures', []):
            measure_name = measure.get('name', '').lower()

            # Skip identifier fields — not measures
            if measure_name.upper() in IDENTIFIER_FIELDS:
                continue

            ontology_measure = self._match_term(measure_name, 'measures')

            if ontology_measure:
                self._inject_enrichment(measure, ontology_measure, stats)
                if ontology_measure.get('fhir_resource'):
                    measure['fhir_resource'] = ontology_measure['fhir_resource']
                    stats['fhir_mappings_added'] += 1
                stats['measures_enriched'] += 1
            else:
                gaps.append({
                    'type': 'UNMAPPED_MEASURE',
                    'name': measure.get('name'),
                    'entity': entity.get('name'),
                    'severity': 'HIGH',
                    'recommendation': f"Add '{measure.get('name')}' to HEDIS measure ontology or custom ontology"
                })

        # Enrich dimensions
        for dim in entity.get('dimensions', []):
            dim_name = dim.get('name', '').lower()
            ontology_dim = self._match_term(dim_name, 'dimensions')

            if ontology_dim:
                self._inject_enrichment(dim, ontology_dim, stats)
                if ontology_dim.get('fhir_resource'):
                    dim['fhir_resource'] = ontology_dim['fhir_resource']
                    stats['fhir_mappings_added'] += 1
                stats['dimensions_enriched'] += 1

    def _inject_enrichment(
        self,
        ir_node: dict,
        ontology_term: dict,
        stats: dict
    ):
        """Inject ontology enrichment into an IR node."""

        # Add description if missing
        if not ir_node.get('description') and ontology_term.get('definition'):
            ir_node['description'] = ontology_term['definition']
            stats['descriptions_added'] += 1

        # Merge synonyms (deduplicated)
        existing_synonyms = set(ir_node.get('synonyms', []))
        new_synonyms = set(ontology_term.get('synonyms', []))
        merged = list(existing_synonyms | new_synonyms)
        ir_node['synonyms'] = merged
        stats['synonyms_added'] += len(new_synonyms - existing_synonyms)

        # Add display folder if missing
        if not ir_node.get('display_folder') and ontology_term.get('display_folder'):
            ir_node['display_folder'] = ontology_term['display_folder']

        # Add format string if missing
        if not ir_node.get('format_string') and ontology_term.get('format_string'):
            ir_node['format_string'] = ontology_term['format_string']

        # Tag with ontology source
        ir_node['ontology_matched'] = ontology_term.get('canonical_name')
        ir_node['hedis_domain'] = ontology_term.get('hedis_domain')

    # ─────────────────────────────────────────────
    # Term Matching
    # ─────────────────────────────────────────────

    def _match_term(
        self,
        name: str,
        category: str,
        threshold: float = 0.75
    ) -> dict | None:
        """
        Fuzzy match a semantic view term against the ontology term graph.
        Tries: exact match → canonical name match → synonym match → fuzzy match.
        """
        terms = self.term_graph.get(category, {})
        name_lower = name.lower().replace('_', ' ')

        # 1. Exact canonical name match
        if name.lower() in terms:
            return terms[name.lower()]

        # 2. Search canonical names with underscore normalization
        for key, term in terms.items():
            if key.replace('_', ' ') == name_lower:
                return term

        # 3. Synonym match
        for key, term in terms.items():
            synonyms_lower = [s.lower() for s in term.get('synonyms', [])]
            if name_lower in synonyms_lower:
                return term

        # 4. Fuzzy match on canonical name
        best_match = None
        best_score = 0.0
        for key, term in terms.items():
            score = SequenceMatcher(
                None, name_lower, key.replace('_', ' ')
            ).ratio()
            if score > best_score and score >= threshold:
                best_score = score
                best_match = term

        return best_match

    # ─────────────────────────────────────────────
    # Gap Report
    # ─────────────────────────────────────────────

    def _build_gap_report(
        self,
        domain: str,
        canonical_ir: dict,
        gaps: list[dict]
    ) -> dict:
        high = [g for g in gaps if g.get('severity') == 'HIGH']
        medium = [g for g in gaps if g.get('severity') == 'MEDIUM']
        low = [g for g in gaps if g.get('severity') == 'LOW']

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "domain": domain,
            "source_view": canonical_ir['metadata'].get('source_view_name'),
            "summary": {
                "total_gaps": len(gaps),
                "high_severity": len(high),
                "medium_severity": len(medium),
                "low_severity": len(low)
            },
            "gaps": {
                "high": high,
                "medium": medium,
                "low": low
            },
            "recommendations": self._generate_recommendations(gaps)
        }

    def _generate_recommendations(self, gaps: list[dict]) -> list[str]:
        recs = []
        unmapped_measures = [g for g in gaps if g['type'] == 'UNMAPPED_MEASURE']
        unmapped_entities = [g for g in gaps if g['type'] == 'UNMAPPED_ENTITY']
        broken_refs = [g for g in gaps if 'BROKEN' in g['type']]

        if broken_refs:
            recs.append(
                f"⚠️  {len(broken_refs)} broken column reference(s) detected. "
                "Resolve before deploying semantic model to Fabric."
            )
        if unmapped_measures:
            names = ', '.join(g['name'] for g in unmapped_measures[:5])
            recs.append(
                f"📋 {len(unmapped_measures)} measure(s) have no ontology mapping: {names}. "
                "Add to HEDIS ontology or custom ontology file."
            )
        if unmapped_entities:
            names = ', '.join(g['name'] for g in unmapped_entities[:3])
            recs.append(
                f"🏷️  {len(unmapped_entities)} entity/entities unmapped: {names}. "
                "Consider adding to custom enterprise ontology."
            )
        if not gaps:
            recs.append(
                "✅ All semantic view terms mapped to ontology. "
                "Semantic model is ready for Fabric deployment."
            )
        return recs
