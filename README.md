# SemanticBridge 🌉
### Snowflake Cortex Semantic Views → Microsoft Fabric Semantic Models
#### with Healthcare Ontology Enrichment

> *"Your Snowflake team spent months building a semantic layer. Your Fabric team is about to spend months rebuilding it. SemanticBridge fixes that."*

---

## The Problem

Enterprise organizations running **Snowflake + Microsoft Fabric in parallel** are building their semantic layer **twice**:

- Once in **Snowflake Cortex Analyst** (semantic view DDL)
- Once in **Microsoft Fabric** (Power BI semantic model)

This creates metric drift, duplicate governance effort, and months of redundant work per domain. In healthcare, where HEDIS measures and FHIR terminology must be consistent across every query surface, this is a critical gap.

---

## What It Does

```
SNOWFLAKE                          MICROSOFT FABRIC
─────────────────────              ──────────────────────────────
Cortex Semantic View    ────────►  Power BI Semantic Model
  (SQL DDL via GET_DDL)             (Direct Lake / DirectQuery)

HEDIS Ontology          ────────►  Clinical vocabulary injected
  (MY2026 specifications)           into every measure + dimension

Gap Report              ────────►  Governance artifact for CDO
  (unmapped terms flagged)          review before production deploy
```

---

## Architecture

```
Step 1 — EXTRACT
  Snowflake connector → GET_DDL('SEMANTIC_VIEW', ...)
  SQL DDL parser → entities, relationships, measures, dimensions
  Output: canonical-ir.json (platform-neutral IR)

Step 2 — ENRICH
  HEDIS MY2026 ontology loaded
  Fuzzy term matching → synonym injection
  FHIR R4 resource mappings added
  Gap report generated
  Output: enriched-ir.json + gap-report.json

Step 3 — DEPLOY (in progress)
  Fabric mirroring → Delta tables in OneLake
  Power BI semantic model → DirectQuery on Snowflake shortcut
  Output: Live Fabric semantic model
```

---

## Results Against Real Healthcare Data

Ran against `HEALTHCARE_DEMO.CLAIMS.CLAIMS_SEMANTIC`:

**Extraction:**
```
Entities:      5   (CLAIMS, MEMBERS, PROVIDERS, DRG, DATES)
Relationships: 4   (all join paths preserved)
Measures:      11  (5 facts + 6 metrics including PMPM, DENIAL_RATE)
Dimensions:    11
Time dims:     3   (CAL_YEAR, CAL_QUARTER, CAL_MONTH)
```

**Enrichment:**
```json
{
  "total_gaps": 0,
  "recommendations": [
    "✅ All semantic view terms mapped to ontology. Ready for Fabric deployment."
  ]
}
```

Zero governance gaps on first run.

---

## Healthcare Ontology Pack

Pre-built HEDIS MY2026 ontology — measures enriched with clinical definitions, synonyms, and FHIR R4 mappings:

- `TOTAL_PAID` → Net Paid Amount → `["reimbursement", "net payment", "plan paid"]`
- `PMPM` → Per Member Per Month → `["cost per member", "monthly pmpm"]`
- `DENIAL_RATE` → Denial Rate → `["denied rate", "disallowance rate"]`
- `TOTAL_BILLED` → Billed Amount → `["charges", "submitted charges"]`
- `TOTAL_ALLOWED` → Allowed Amount → `["negotiated rate", "contractual allowance"]`

Entities mapped to FHIR R4 resources: Patient, Claim, Practitioner, Period.

---

## Quick Start

```bash
pip install snowflake-connector-python PyYAML python-dotenv cryptography

cp config.yaml.example config.yaml
# Edit with your Snowflake credentials

python run.py --step extract
python run.py --step enrich
python run.py  # full pipeline
```

---

## Output Structure

```
output/
└── claims/
    ├── raw-semantic-view.yaml   ← Original Snowflake DDL
    ├── canonical-ir.json        ← Platform-neutral IR
    ├── enriched-ir.json         ← HEDIS-enriched with synonyms + FHIR
    ├── gap-report.json          ← Governance gaps + recommendations
    └── manifest.json            ← Drift detection hashes
```

---

## Drift Detection

Every run computes SHA-256 hashes of the DDL and physical schema. If hashes change → drift detected → pipeline reruns. Gap report surfaces breaking vs additive changes before they reach production.

---

## Roadmap

- [x] Snowflake semantic view extraction
- [x] Canonical IR schema
- [x] HEDIS MY2026 ontology enrichment
- [x] Gap report generation
- [x] Drift detection
- [ ] Fabric mirroring + semantic model deploy
- [ ] Power BI MCP integration
- [ ] Ontology workload in Fabric
- [ ] Azure Function event-driven trigger
- [ ] FHIR R4 full ontology pack

---

## Related Work

**HealthIQ** — Production A2A multi-agent platform on Azure AI Foundry with full OpenTelemetry observability.
→ [fabriciq-multi-agent-reference-architecture](https://github.com/kartikanand73/fabriciq-multi-agent-reference-architecture)

---

## Built By

**Kartik Anand** — Cloud & AI Architect | Microsoft

[LinkedIn](https://linkedin.com/in/kartikanand) | [Medium](https://medium.com/@kartikanand_11915) | [GitHub](https://github.com/kartikanand73)
