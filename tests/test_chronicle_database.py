"""Tests for the relational Chronicle database artifact."""

from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from chronicle.core import build_aggregate_constraints
from chronicle.database import build_chronicle_db
from chronicle.epoch import HASH_DOMAINS, Epoch
from chronicle.harness import build_chronicle_db_file
from chronicle.jurisdictions.us.soi import (
    AXIOM_IRC_AGI_CONCEPT,
    build_soi_table_1_1_source_cells,
    build_soi_table_1_1_facts,
)
from chronicle.sources.cells import build_source_cell_key
from chronicle.sources.rows import SourceRow, build_source_row_key


def test_build_aggregate_constraints_lifts_agi_filters():
    fact = next(
        fact
        for fact in build_soi_table_1_1_facts(2023)
        if fact.source_record_id == "irs_soi.ty2023.table_1_1.100k_to_200k.return_count"
    )

    constraints = build_aggregate_constraints(fact)

    assert [
        (item.variable, item.operator, item.value, item.unit) for item in constraints
    ] == [
        (AXIOM_IRC_AGI_CONCEPT, ">=", 100_000, "usd"),
        (AXIOM_IRC_AGI_CONCEPT, "<", 200_000, "usd"),
    ]


def test_build_chronicle_db_writes_aggregate_fact_constraints_and_lineage(tmp_path):
    db_path = tmp_path / "ledger-fixture.db"
    facts = build_soi_table_1_1_facts(2023)
    cells = build_soi_table_1_1_source_cells(2023)

    report = build_chronicle_db(facts, db_path, source_cells=cells)

    assert report.facts_count == 80
    assert report.source_records_count == 80
    assert report.source_cells_count == 1932
    assert report.source_artifacts_count == 1

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        build = connection.execute(
            "SELECT build_id, schema_version FROM ledger_builds"
        ).fetchone()
        artifact = connection.execute(
            """
            SELECT raw_r2_bucket, raw_r2_key, raw_r2_uri
            FROM source_artifacts
            WHERE artifact_sha256 = ?
            """,
            (cells[0].artifact.sha256,),
        ).fetchone()
        build_artifact_count = connection.execute(
            "SELECT COUNT(*) FROM build_artifacts"
        ).fetchone()[0]
        all_returns = connection.execute(
            """
            SELECT *
            FROM aggregate_facts
            WHERE source_record_id = ?
            """,
            ("irs_soi.ty2023.table_1_1.all.return_count",),
        ).fetchone()

        assert all_returns["measure_concept"] == "irs_soi.individual_income_tax_returns"
        assert all_returns["aggregation_method"] == "sum"
        assert all_returns["entity_name"] == "tax_unit"
        assert all_returns["value_numeric"] == 160_602_107
        assert all_returns["domain"] == "all_individual_income_tax_returns"
        assert all_returns["provenance_class"] == "administrative"
        assert all_returns["survey_instrument"] is None
        assert build["build_id"].startswith("ledger.build.v1:")
        assert build["schema_version"] == "ledger.relational.v1"
        assert artifact["raw_r2_bucket"] == "ledger-raw"
        assert artifact["raw_r2_key"].startswith("raw/irs_soi/soi-table-1-1/2023/")
        assert artifact["raw_r2_uri"].startswith("r2://ledger-raw/")
        assert build_artifact_count == 0

        bracket = connection.execute(
            """
            SELECT fact_key
            FROM aggregate_facts
            WHERE source_record_id = ?
            """,
            ("irs_soi.ty2023.table_1_1.100k_to_200k.return_count",),
        ).fetchone()
        constraints = connection.execute(
            """
            SELECT variable, operator, value_numeric, unit
            FROM aggregate_constraints
            WHERE fact_key = ?
            ORDER BY ordinal
            """,
            (bracket["fact_key"],),
        ).fetchall()

        assert [tuple(row) for row in constraints] == [
            (AXIOM_IRC_AGI_CONCEPT, ">=", 100_000, "usd"),
            (AXIOM_IRC_AGI_CONCEPT, "<", 200_000, "usd"),
        ]

        agi_fact = connection.execute(
            """
            SELECT
                measure_concept,
                measure_source_concept,
                measure_concept_relation,
                layout_record_set_id,
                layout_groupby_value_id,
                layout_measure_id
            FROM aggregate_facts
            WHERE source_record_id = ?
            """,
            ("irs_soi.ty2023.table_1_1.all.adjusted_gross_income",),
        ).fetchone()
        alignment = connection.execute(
            """
            SELECT source_concept, canonical_concept, relation, authority
            FROM concept_alignments
            WHERE source_concept = ?
            """,
            ("irs_soi.adjusted_gross_income",),
        ).fetchone()

        assert tuple(agi_fact) == (
            AXIOM_IRC_AGI_CONCEPT,
            "irs_soi.adjusted_gross_income",
            "exact",
            "irs_soi.ty2023.table_1_1",
            "all",
            "adjusted_gross_income",
        )
        assert tuple(alignment) == (
            "irs_soi.adjusted_gross_income",
            AXIOM_IRC_AGI_CONCEPT,
            "exact",
            "ledger-us",
        )

        lineage = connection.execute(
            """
            SELECT source_cells.address, source_cells.raw_value_numeric
            FROM aggregate_facts
            JOIN fact_source_cells
              ON fact_source_cells.fact_key = aggregate_facts.fact_key
            JOIN source_cells
              ON source_cells.source_cell_key = fact_source_cells.source_cell_key
            WHERE aggregate_facts.source_record_id = ?
            """,
            ("irs_soi.ty2023.table_1_1.all.return_count",),
        ).fetchone()

        assert tuple(lineage) == ("B10", 160_602_107)


def test_build_chronicle_db_build_id_changes_when_fact_payload_changes(tmp_path):
    cells = build_soi_table_1_1_source_cells(2023)
    facts = build_soi_table_1_1_facts(2023)
    changed_facts = [replace(facts[0], value=999), *facts[1:]]

    original = build_chronicle_db(
        facts,
        tmp_path / "original.db",
        source_cells=cells,
    )
    changed = build_chronicle_db(
        changed_facts,
        tmp_path / "changed.db",
        source_cells=cells,
    )

    assert original.build_id != changed.build_id


def test_build_chronicle_db_emits_chronicle_epoch_with_valid_lineage(tmp_path):
    fact = next(
        fact
        for fact in build_soi_table_1_1_facts(2023)
        if fact.source_record_id == "irs_soi.ty2023.table_1_1.all.return_count"
    )
    cell = next(
        cell for cell in build_soi_table_1_1_source_cells(2023) if cell.address == "B10"
    )
    row = SourceRow(
        artifact=cell.artifact,
        sheet_name=cell.sheet_name,
        row_number=cell.row_number,
        values={"Returns": cell.raw_value},
    )
    ledger_row_key = build_source_row_key(row)
    cell = replace(cell, source_row_key=ledger_row_key)
    fact = replace(fact, source_row_keys=(ledger_row_key,))

    ledger = build_chronicle_db(
        [fact],
        tmp_path / "ledger.db",
        source_cells=[cell],
        source_rows=[row],
    )
    chronicle = build_chronicle_db(
        [fact],
        tmp_path / "chronicle.db",
        source_cells=[cell],
        source_rows=[row],
        emit_epoch=Epoch.CHRONICLE,
    )

    assert (
        ledger.build_id.split(":", maxsplit=1)[1]
        == chronicle.build_id.split(":", maxsplit=1)[1]
    )
    assert chronicle.build_id.startswith("chronicle.build.v2:")
    with sqlite3.connect(tmp_path / "chronicle.db") as connection:
        build = connection.execute(
            "SELECT build_id, schema_version FROM ledger_builds"
        ).fetchone()
        keys = connection.execute(
            """
            SELECT
                aggregate_facts.fact_key,
                fact_source_cells.source_cell_key,
                fact_source_rows.source_row_key,
                source_cells.source_row_key
            FROM aggregate_facts
            JOIN fact_source_cells USING (fact_key)
            JOIN fact_source_rows USING (fact_key)
            JOIN source_cells USING (source_cell_key)
            """
        ).fetchone()
        row_value_keys = connection.execute(
            """
            SELECT
                source_row_values.source_row_value_key,
                source_rows.source_row_key,
                source_columns.source_column_key
            FROM source_row_values
            JOIN source_rows USING (source_row_key)
            JOIN source_columns USING (source_column_key)
            """
        ).fetchone()
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert build == (chronicle.build_id, "chronicle.relational.v2")
    assert keys[0].startswith("chronicle.fact.v2:")
    assert keys[1].startswith("chronicle.source_cell.v2:")
    assert keys[2].startswith("chronicle.source_row.v2:")
    assert keys[3] == keys[2]
    assert row_value_keys[0].startswith("chronicle.source_row_value.v2:")
    assert row_value_keys[1].startswith("chronicle.source_row.v2:")
    assert row_value_keys[2].startswith("chronicle.source_column.v2:")
    assert foreign_key_errors == []


@pytest.mark.parametrize("emit_epoch", list(Epoch), ids=lambda epoch: epoch.value)
def test_build_chronicle_db_deduplicates_mixed_epoch_lineage_aliases(
    tmp_path,
    emit_epoch,
):
    fact = next(
        fact
        for fact in build_soi_table_1_1_facts(2023)
        if fact.source_record_id == "irs_soi.ty2023.table_1_1.all.return_count"
    )
    cell = next(
        cell for cell in build_soi_table_1_1_source_cells(2023) if cell.address == "B10"
    )
    row = SourceRow(
        artifact=cell.artifact,
        sheet_name=cell.sheet_name,
        row_number=cell.row_number,
        values={"Returns": cell.raw_value},
    )
    ledger_cell_key = build_source_cell_key(cell, epoch=Epoch.LEDGER)
    chronicle_cell_key = HASH_DOMAINS["source_cell"].key_for_epoch(
        ledger_cell_key,
        Epoch.CHRONICLE,
    )
    ledger_row_key = build_source_row_key(row, epoch=Epoch.LEDGER)
    chronicle_row_key = HASH_DOMAINS["source_row"].key_for_epoch(
        ledger_row_key,
        Epoch.CHRONICLE,
    )
    cell = replace(cell, source_row_key=ledger_row_key)
    fact = replace(
        fact,
        source_cell_keys=(ledger_cell_key, chronicle_cell_key),
        source_row_keys=(ledger_row_key, chronicle_row_key),
    )

    db_path = tmp_path / f"{emit_epoch.value}.db"
    build_chronicle_db(
        [fact],
        db_path,
        source_cells=[cell],
        source_rows=[row],
        emit_epoch=emit_epoch,
    )

    expected_cell_key = HASH_DOMAINS["source_cell"].key_for_epoch(
        ledger_cell_key,
        emit_epoch,
    )
    expected_row_key = HASH_DOMAINS["source_row"].key_for_epoch(
        ledger_row_key,
        emit_epoch,
    )
    with sqlite3.connect(db_path) as connection:
        source_record_counts = connection.execute(
            "SELECT source_cell_count, source_row_count FROM source_records"
        ).fetchone()
        fact_cells = connection.execute(
            "SELECT source_cell_key, ordinal FROM fact_source_cells"
        ).fetchall()
        fact_rows = connection.execute(
            "SELECT source_row_key, ordinal FROM fact_source_rows"
        ).fetchall()
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert source_record_counts == (1, 1)
    assert fact_cells == [(expected_cell_key, 0)]
    assert fact_rows == [(expected_row_key, 0)]
    assert foreign_key_errors == []


def test_build_chronicle_db_rejects_unknown_explicit_build_epoch(tmp_path):
    with pytest.raises(
        ValueError,
        match=(
            "ledger[.]build[.]v1.*chronicle[.]build[.]v2|"
            "chronicle[.]build[.]v2.*ledger[.]build[.]v1"
        ),
    ):
        build_chronicle_db(
            [],
            tmp_path / "unknown-build.db",
            build_id="future.build.v9:unknown",
        )


def test_build_chronicle_db_rejects_unresolved_source_cell_lineage(tmp_path):
    fact = replace(
        build_soi_table_1_1_facts(2023)[0],
        source_cell_keys=("ledger.source_cell.v1:missing",),
    )

    with pytest.raises(sqlite3.IntegrityError):
        build_chronicle_db([fact], tmp_path / "bad-lineage.db")


def test_build_chronicle_db_file_uses_fixture_facts_and_cells(tmp_path):
    db_path = tmp_path / "ledger-fixture.db"

    report = build_chronicle_db_file(db_path)

    assert report.facts_count == 80
    assert report.source_cells_count == 1932
    with sqlite3.connect(db_path) as connection:
        facts_count = connection.execute(
            "SELECT COUNT(*) FROM aggregate_facts"
        ).fetchone()[0]
        cells_count = connection.execute(
            "SELECT COUNT(*) FROM source_cells"
        ).fetchone()[0]

    assert facts_count == 80
    assert cells_count == 1932
