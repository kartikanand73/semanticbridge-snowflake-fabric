# extractor/yaml_parser.py
# ─────────────────────────────────────────────────────────────────────────────
# Snowflake Semantic View DDL Parser
# Handles SQL DDL format from GET_DDL('SEMANTIC_VIEW', ...)
# Fixed: section extractor handles nested parentheses correctly
# ─────────────────────────────────────────────────────────────────────────────

import re
from datetime import datetime, timezone
from models.canonical_ir_schema import (
    CanonicalIR, IRMetadata, IREntity, IRDimension,
    IRTimeDimension, IRMeasure, IRRelationship,
    PhysicalColumn, compute_hash, compute_schema_fingerprint
)


class SnowflakeYAMLParser:

    def __init__(self, client_id: str = "demo"):
        self.client_id = client_id

    # ─────────────────────────────────────────────────────────────────────────
    # Entry Point
    # ─────────────────────────────────────────────────────────────────────────

    def parse(
        self,
        yaml_ddl: str,
        physical_schema: list,
        source_database: str,
        source_schema: str,
        source_view_name: str
    ) -> CanonicalIR:

        ddl = yaml_ddl.strip()

        metadata = IRMetadata(
            extracted_at=datetime.now(timezone.utc).isoformat(),
            source_type="snowflake_semantic_view_ddl",
            source_database=source_database,
            source_schema=source_schema,
            source_view_name=source_view_name,
            semantic_view_hash=compute_hash(ddl),
            schema_fingerprint=compute_schema_fingerprint(physical_schema),
            client_id=self.client_id,
            domain=source_schema.lower()
        )

        # Extract sections using paren-depth-aware extractor
        tables_raw        = self._extract_section(ddl, 'tables')
        relationships_raw = self._extract_section(ddl, 'relationships')
        facts_raw         = self._extract_section(ddl, 'facts')
        dimensions_raw    = self._extract_section(ddl, 'dimensions')
        metrics_raw       = self._extract_section(ddl, 'metrics')
        view_comment      = self._extract_view_comment(ddl)

        # Debug — print what was extracted
        print(f"      tables_raw length:     {len(tables_raw)}")
        print(f"      facts_raw length:      {len(facts_raw)}")
        print(f"      dimensions_raw length: {len(dimensions_raw)}")
        print(f"      metrics_raw length:    {len(metrics_raw)}")
        print(f"      relationships_raw len: {len(relationships_raw)}")

        table_map = self._parse_tables_section(tables_raw)
        print(f"      table_map keys: {list(table_map.keys())}")

        entities = self._build_entities(
            table_map=table_map,
            facts_raw=facts_raw,
            dimensions_raw=dimensions_raw,
            metrics_raw=metrics_raw,
            physical_schema=physical_schema,
            source_database=source_database,
            source_schema=source_schema,
            view_comment=view_comment
        )

        relationships = self._parse_relationships_section(relationships_raw)

        return CanonicalIR(
            metadata=metadata,
            entities=entities,
            relationships=relationships
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Section Extractor — paren-depth-aware
    # Finds "section_name (" then reads until the matching closing ")"
    # Handles nested parens like NULLIF(SUM(claims.mm),0) correctly
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_section(self, ddl: str, section: str) -> str:
        # Find where this section starts
        pattern = re.compile(
            rf'\b{section}\s*\(', re.IGNORECASE
        )
        match = pattern.search(ddl)
        if not match:
            return ""

        start = match.end()   # position just after the opening (
        depth = 1
        pos   = start

        while pos < len(ddl) and depth > 0:
            if ddl[pos] == '(':
                depth += 1
            elif ddl[pos] == ')':
                depth -= 1
            pos += 1

        # Content between the outermost ( and )
        return ddl[start:pos - 1].strip()

    def _extract_view_comment(self, ddl: str) -> str:
        # Last comment='' in the DDL is the view-level comment
        matches = re.findall(r"comment='([^']*)'", ddl, re.IGNORECASE)
        return matches[-1] if matches else ""

    # ─────────────────────────────────────────────────────────────────────────
    # Tables Section Parser
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_tables_section(self, tables_raw: str) -> dict:
        table_map = {}
        if not tables_raw:
            return table_map

        # Split on comma followed by newline (top-level commas only)
        entries = self._split_top_level(tables_raw)

        for entry in entries:
            entry = entry.strip().rstrip(',').strip()
            if not entry:
                continue

            match = re.match(
                r'(\w+)\s+as\s+([\w.]+)\s+primary\s+key\s*\((\w+)\)'
                r'(?:\s+comment=\'([^\']*)\')?',
                entry, re.IGNORECASE
            )
            if match:
                alias    = match.group(1).upper()
                full_ref = match.group(2)
                pk       = match.group(3)
                comment  = match.group(4) or ""
                parts    = full_ref.split('.')

                table_map[alias] = {
                    'database':    parts[0] if len(parts) > 2 else "",
                    'schema':      parts[1] if len(parts) > 2 else "",
                    'table':       parts[2] if len(parts) > 2 else parts[-1],
                    'full_ref':    full_ref,
                    'primary_key': pk,
                    'comment':     comment
                }

        return table_map

    # ─────────────────────────────────────────────────────────────────────────
    # Top-Level Comma Splitter
    # Splits on commas that are NOT inside nested parentheses
    # Critical for metrics like: SUM(claims.paid) / NULLIF(SUM(claims.mm),0)
    # ─────────────────────────────────────────────────────────────────────────

    def _split_top_level(self, text: str) -> list:
        parts = []
        depth = 0
        current = []

        for char in text:
            if char == '(':
                depth += 1
                current.append(char)
            elif char == ')':
                depth -= 1
                current.append(char)
            elif char == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(char)

        if current:
            parts.append(''.join(current).strip())

        return [p for p in parts if p]

    # ─────────────────────────────────────────────────────────────────────────
    # Entity Builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_entities(
        self,
        table_map: dict,
        facts_raw: str,
        dimensions_raw: str,
        metrics_raw: str,
        physical_schema: list,
        source_database: str,
        source_schema: str,
        view_comment: str
    ) -> list:

        all_facts      = self._parse_facts(facts_raw)
        all_dimensions = self._parse_dimensions(dimensions_raw)
        all_metrics    = self._parse_metrics(metrics_raw)

        print(f"      facts parsed:      {len(all_facts)}")
        print(f"      dimensions parsed: {len(all_dimensions)}")
        print(f"      metrics parsed:    {len(all_metrics)}")

        entities = []

        for alias, table_info in table_map.items():

            # Facts → IRMeasure
            measures = []
            for fact in all_facts:
                if fact['table_alias'].upper() == alias:
                    measures.append(IRMeasure(
                        name=fact['semantic_name'],
                        expression=fact['source_column'],
                        aggregation="NONE",
                        physical_column=fact['source_column'],
                        data_type="VARCHAR",
                        description=None,
                        synonyms=[],
                        format_string=None,
                        display_folder="Facts",
                        is_hidden=False
                    ))

            # Metrics → IRMeasure
            for metric in all_metrics:
                if metric['table_alias'].upper() == alias:
                    measures.append(IRMeasure(
                        name=metric['semantic_name'],
                        expression=metric['expression'],
                        aggregation=self._infer_aggregation(metric['expression']),
                        physical_column=self._extract_column_from_expr(
                            metric['expression']
                        ),
                        data_type="NUMBER",
                        description=metric.get('comment', ''),
                        synonyms=[],
                        format_string=self._infer_format(metric['semantic_name']),
                        display_folder="Metrics",
                        is_hidden=False
                    ))

            # Dimensions → IRDimension or IRTimeDimension
            dimensions = []
            time_dimensions = []
            for dim in all_dimensions:
                if dim['table_alias'].upper() == alias:
                    if any(t in dim['semantic_name'].upper()
                           for t in ['YEAR', 'QUARTER', 'MONTH', 'DATE', 'DAY']):
                        time_dimensions.append(IRTimeDimension(
                            name=dim['semantic_name'],
                            physical_column=dim['source_column'],
                            data_type="DATE",
                            description=None,
                            granularity=self._infer_granularity(dim['semantic_name'])
                        ))
                    else:
                        dimensions.append(IRDimension(
                            name=dim['semantic_name'],
                            physical_column=dim['source_column'],
                            data_type="VARCHAR",
                            description=None,
                            synonyms=[],
                            is_primary_key=(
                                dim['source_column'] == table_info['primary_key']
                            ),
                            display_folder=f"{alias.title()} Attributes"
                        ))

            # Add primary key if not already present
            pk = table_info['primary_key']
            pk_cols = [d.physical_column for d in dimensions]
            if pk and pk not in pk_cols:
                dimensions.insert(0, IRDimension(
                    name=pk,
                    physical_column=pk,
                    data_type="VARCHAR",
                    description=None,
                    synonyms=[],
                    is_primary_key=True,
                    display_folder=f"{alias.title()} Keys"
                ))

            entities.append(IREntity(
                name=alias,
                physical_database=table_info['database'] or source_database,
                physical_schema=table_info['schema'] or source_schema,
                physical_table=table_info['table'],
                description=table_info['comment'] or view_comment,
                synonyms=[],
                dimensions=dimensions,
                time_dimensions=time_dimensions,
                measures=measures,
                physical_columns=self._map_physical_columns(physical_schema)
            ))

        return entities

    # ─────────────────────────────────────────────────────────────────────────
    # Section Parsers
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_facts(self, facts_raw: str) -> list:
        facts = []
        if not facts_raw:
            return facts
        for entry in self._split_top_level(facts_raw):
            entry = entry.strip()
            if not entry:
                continue
            match = re.match(
                r'(\w+)\.(\w+)\s+as\s+(\w+)', entry, re.IGNORECASE
            )
            if match:
                facts.append({
                    'table_alias':   match.group(1).upper(),
                    'source_column': match.group(2),
                    'semantic_name': match.group(3)
                })
        return facts

    def _parse_dimensions(self, dimensions_raw: str) -> list:
        dimensions = []
        if not dimensions_raw:
            return dimensions
        for entry in self._split_top_level(dimensions_raw):
            entry = entry.strip()
            if not entry:
                continue
            match = re.match(
                r'(\w+)\.(\w+)\s+as\s+(\w+)', entry, re.IGNORECASE
            )
            if match:
                dimensions.append({
                    'table_alias':   match.group(1).upper(),
                    'source_column': match.group(2),
                    'semantic_name': match.group(3)
                })
        return dimensions

    def _parse_metrics(self, metrics_raw: str) -> list:
        metrics = []
        if not metrics_raw:
            return metrics

        for entry in self._split_top_level(metrics_raw):
            entry = entry.strip()
            if not entry:
                continue

            # TABLE.METRIC_NAME as EXPRESSION [comment='...']
            match = re.match(
                r'(\w+)\.(\w+)\s+as\s+(.*?)(?:\s+comment=\'([^\']*)\')?$',
                entry, re.IGNORECASE | re.DOTALL
            )
            if match:
                metrics.append({
                    'table_alias':   match.group(1).upper(),
                    'semantic_name': match.group(2),
                    'expression':    match.group(3).strip(),
                    'comment':       match.group(4) or ''
                })
        return metrics

    def _parse_relationships_section(self, relationships_raw: str) -> list:
        relationships = []
        if not relationships_raw:
            return relationships

        for entry in self._split_top_level(relationships_raw):
            entry = entry.strip()
            if not entry:
                continue
            match = re.match(
                r'(\w+)\s+as\s+(\w+)\((\w+)\)\s+references\s+(\w+)\((\w+)\)',
                entry, re.IGNORECASE
            )
            if match:
                relationships.append(IRRelationship(
                    name=match.group(1),
                    from_entity=match.group(2).upper(),
                    from_column=match.group(3),
                    to_entity=match.group(4).upper(),
                    to_column=match.group(5),
                    join_type="MANY_TO_ONE",
                    is_active=True
                ))
        return relationships

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _infer_aggregation(self, expression: str) -> str:
        expr = expression.upper()
        if expr.startswith('SUM'):    return "SUM"
        if expr.startswith('COUNT'):  return "COUNT"
        if expr.startswith('AVG'):    return "AVG"
        if expr.startswith('MIN'):    return "MIN"
        if expr.startswith('MAX'):    return "MAX"
        if '/' in expr:               return "RATIO"
        return "NONE"

    def _extract_column_from_expr(self, expression: str) -> str:
        match = re.search(r'\((\w+)\.(\w+)\)', expression, re.IGNORECASE)
        if match:
            return match.group(2)
        match = re.search(r'\((\w+)\)', expression, re.IGNORECASE)
        if match:
            return match.group(1)
        return expression

    def _infer_format(self, metric_name: str) -> str:
        name = metric_name.upper()
        if any(x in name for x in ['AMOUNT', 'PAID', 'BILLED', 'ALLOWED', 'PMPM']):
            return "$#,##0.00"
        if 'RATE' in name:  return "0.00%"
        if 'COUNT' in name: return "#,##0"
        return "#,##0.00"

    def _infer_granularity(self, name: str) -> str:
        name = name.upper()
        if 'YEAR' in name:    return "YEAR"
        if 'QUARTER' in name: return "QUARTER"
        if 'MONTH' in name:   return "MONTH"
        if 'DAY' in name:     return "DAY"
        return "DAY"

    def _map_physical_columns(self, physical_schema: list) -> list:
        from models.canonical_ir_schema import PhysicalColumn
        return [
            PhysicalColumn(
                column_name=col.get('column_name', ''),
                data_type=col.get('data_type', ''),
                is_nullable=col.get('is_nullable', 'YES') == 'YES',
                comment=col.get('comment', None)
            )
            for col in physical_schema
        ]