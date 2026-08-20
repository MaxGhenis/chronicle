"""Selector and source-record specs for source-cell and source-row lineage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from chronicle.core import (
    DEFAULT_ASSERTION,
    AggregateConstraint,
    PeriodCoverage,
    SourceRecordLayout,
)
from chronicle.sources.cells import SourceCell, build_source_cell_key

Scalar = str | int | float | bool | None
CellsBySheetAddress = dict[tuple[str, str], SourceCell]


@dataclass(frozen=True)
class CellGuardSpec:
    """A guard cell that must match before a selector resolves."""

    address: str
    expected_value: Scalar
    label: str = "guard cell"


@dataclass(frozen=True)
class RangeLabelGuardSpec:
    """A guard that validates labels across every row in a selected range."""

    column: str
    expected_values: tuple[Scalar, ...]
    label: str = "range label sequence"


@dataclass(frozen=True)
class CellSelectorSpec:
    """A checked selector for one parsed source cell."""

    selector_id: str
    sheet_name: str
    address: str
    end_address: str | None = None
    expected_cell_type: str | None = None
    expected_row_header_address: str | None = None
    expected_row_header: Scalar = None
    expected_column_header_address: str | None = None
    expected_column_header: Scalar = None
    guard_cells: tuple[CellGuardSpec, ...] = ()
    range_label_guards: tuple[RangeLabelGuardSpec, ...] = ()


@dataclass(frozen=True)
class SourceRegionSpec:
    """One logical source region selected from a parsed artifact."""

    region_id: str
    sheet_name: str
    top_row: int
    left_column: int
    bottom_row: int
    right_column: int
    region_kind: str = "table"
    label: str | None = None
    record_set_id: str | None = None


@dataclass(frozen=True)
class SourceRecordSpec:
    """Simulator-neutral source record interpretation for a selected cell."""

    source_record_id: str
    selector: CellSelectorSpec
    concept: str
    unit: str
    period_type: str
    period: int | str
    geography_id: str
    geography_level: str
    geography_name: str | None
    geography_vintage: str | None
    entity: str
    entity_role: str | None
    aggregation: str
    domain: str
    provenance_class: str
    survey_instrument: str | None = None
    filters: dict[str, Scalar] = field(default_factory=dict)
    constraints: tuple[AggregateConstraint, ...] = ()
    value_scale: int | float = 1
    divisor_selector: CellSelectorSpec | None = None
    round_to: int | float | None = None
    source_concept: str | None = None
    concept_relation: str | None = None
    concept_authority: str | None = None
    concept_evidence_url: str | None = None
    concept_evidence_notes: str | None = None
    legal_vintage: str | None = None
    layout: SourceRecordLayout | None = None
    assertion: str = DEFAULT_ASSERTION
    period_coverage: PeriodCoverage | None = None


@dataclass(frozen=True)
class SourceRecordSetRowGuard:
    """A row-relative guard cell for compact source-record specs."""

    column: str
    expected_value: Scalar
    row: int | str = "start"
    label: str = "row guard"


@dataclass(frozen=True)
class SourceRecordSetRangeLabelGuard:
    """A row-range guard for compact source-record specs."""

    column: str
    expected_values: tuple[Scalar, ...]
    label: str = "range label sequence"


@dataclass(frozen=True)
class SourceRecordSetRow:
    """One logical row in a compact source-record set spec."""

    value_id: str
    label: str
    ordinal: int
    row_number: int
    row_end_number: int | None = None
    column: str | None = None
    source_column_id: str | None = None
    expected_column_header_row: int | None = None
    expected_column_header: Scalar = None
    geography_id: str | None = None
    geography_level: str | None = None
    geography_name: str | None = None
    geography_vintage: str | None = None
    filters: dict[str, Scalar] = field(default_factory=dict)
    constraints: tuple[AggregateConstraint, ...] = ()
    value_scale: int | float = 1
    source_row_id: str | None = None
    table_record_kind: str = "detail"
    expected_row_header: Scalar = None
    expected_row_header_column: str | None = None
    guard_cells: tuple[SourceRecordSetRowGuard, ...] = ()
    range_label_guards: tuple[SourceRecordSetRangeLabelGuard, ...] = ()


@dataclass(frozen=True)
class SourceRecordSetMeasure:
    """One logical measure/column in a compact source-record set spec."""

    measure_id: str
    label: str
    ordinal: int
    column: str
    concept: str
    unit: str
    aggregation: str
    value_scale: int | float = 1
    divisor_column: str | None = None
    round_to: int | float | None = None
    source_column_id: str | None = None
    expected_cell_type: str = "number"
    expected_column_header_row: int | None = None
    expected_column_header: Scalar = None
    source_concept: str | None = None
    concept_relation: str | None = None
    concept_authority: str | None = None
    concept_evidence_url: str | None = None
    concept_evidence_notes: str | None = None
    legal_vintage: str | None = None
    filters: dict[str, Scalar] = field(default_factory=dict)
    constraints: tuple[AggregateConstraint, ...] = ()


@dataclass(frozen=True)
class SourceRecordSetSpec:
    """Compact authoring spec that expands into atomic source-record specs."""

    record_set_id: str
    record_set_spec_id: str
    source_record_id_prefix: str
    sheet_name: str
    period_type: str
    period: int | str
    geography_id: str
    geography_level: str
    geography_name: str | None
    geography_vintage: str | None
    entity: str
    entity_role: str | None
    domain: str
    groupby_dimension: str
    rows: tuple[SourceRecordSetRow, ...]
    measures: tuple[SourceRecordSetMeasure, ...]
    provenance_class: str
    survey_instrument: str | None = None
    shared_filters: dict[str, Scalar] = field(default_factory=dict)
    shared_constraints: tuple[AggregateConstraint, ...] = ()
    assertion: str = DEFAULT_ASSERTION
    period_coverage: PeriodCoverage | None = None


@dataclass(frozen=True)
class SourceRecord:
    """Resolved source record with cell-level lineage."""

    source_record_id: str
    value: int | float | str
    spec: SourceRecordSpec
    source_cell_keys: tuple[str, ...]
    source_cell_addresses: tuple[str, ...]
    source_row_keys: tuple[str, ...] = ()


def compile_source_record_set_specs(
    spec: SourceRecordSetSpec,
) -> list[SourceRecordSpec]:
    """Expand a compact record-set spec into atomic source-record specs."""
    spec_hash = _record_set_spec_hash(spec)
    source_specs: list[SourceRecordSpec] = []
    for row in sorted(spec.rows, key=lambda item: item.ordinal):
        for measure in sorted(spec.measures, key=lambda item: item.ordinal):
            source_column = row.column or measure.column
            column_header_row = (
                row.expected_column_header_row
                if row.expected_column_header_row is not None
                else measure.expected_column_header_row
            )
            column_header = (
                row.expected_column_header
                if row.expected_column_header is not None
                else measure.expected_column_header
            )
            source_record_id = (
                f"{spec.source_record_id_prefix}.{row.value_id}.{measure.measure_id}"
            )
            filters = {**spec.shared_filters, **row.filters, **measure.filters}
            source_specs.append(
                SourceRecordSpec(
                    source_record_id=source_record_id,
                    selector=CellSelectorSpec(
                        selector_id=f"{source_record_id}.selector",
                        sheet_name=spec.sheet_name,
                        address=f"{source_column}{row.row_number}",
                        end_address=(
                            f"{source_column}{row.row_end_number}"
                            if row.row_end_number is not None
                            else None
                        ),
                        expected_cell_type=measure.expected_cell_type,
                        expected_row_header_address=(
                            f"{row.expected_row_header_column or 'A'}{row.row_number}"
                        ),
                        expected_row_header=(
                            row.expected_row_header
                            if row.expected_row_header is not None
                            else row.label
                        ),
                        expected_column_header_address=(
                            f"{source_column}{column_header_row}"
                            if column_header_row is not None
                            else None
                        ),
                        expected_column_header=column_header,
                        guard_cells=_row_guard_cells(row),
                        range_label_guards=_row_range_label_guards(row),
                    ),
                    concept=measure.concept,
                    unit=measure.unit,
                    period_type=spec.period_type,
                    period=spec.period,
                    geography_id=row.geography_id or spec.geography_id,
                    geography_level=row.geography_level or spec.geography_level,
                    geography_name=(
                        row.geography_name
                        if row.geography_name is not None
                        else spec.geography_name
                    ),
                    geography_vintage=(
                        row.geography_vintage
                        if row.geography_vintage is not None
                        else spec.geography_vintage
                    ),
                    entity=spec.entity,
                    entity_role=spec.entity_role,
                    aggregation=measure.aggregation,
                    filters=filters,
                    constraints=(
                        *spec.shared_constraints,
                        *row.constraints,
                        *measure.constraints,
                    ),
                    domain=spec.domain,
                    provenance_class=spec.provenance_class,
                    survey_instrument=spec.survey_instrument,
                    value_scale=measure.value_scale * row.value_scale,
                    divisor_selector=(
                        CellSelectorSpec(
                            selector_id=f"{source_record_id}.divisor_selector",
                            sheet_name=spec.sheet_name,
                            address=f"{measure.divisor_column}{row.row_number}",
                            expected_cell_type="number",
                        )
                        if measure.divisor_column is not None
                        else None
                    ),
                    round_to=measure.round_to,
                    assertion=spec.assertion,
                    period_coverage=spec.period_coverage,
                    source_concept=measure.source_concept,
                    concept_relation=measure.concept_relation,
                    concept_authority=measure.concept_authority,
                    concept_evidence_url=measure.concept_evidence_url,
                    concept_evidence_notes=measure.concept_evidence_notes,
                    legal_vintage=measure.legal_vintage,
                    layout=SourceRecordLayout(
                        record_set_id=spec.record_set_id,
                        record_set_spec_id=spec.record_set_spec_id,
                        record_set_spec_hash=spec_hash,
                        groupby_dimension=spec.groupby_dimension,
                        groupby_value_id=row.value_id,
                        groupby_value_label=row.label,
                        groupby_ordinal=row.ordinal,
                        measure_id=measure.measure_id,
                        measure_label=measure.label,
                        measure_ordinal=measure.ordinal,
                        source_row_id=row.source_row_id or row.value_id,
                        source_column_id=(
                            row.source_column_id
                            or measure.source_column_id
                            or measure.measure_id
                        ),
                        table_record_kind=row.table_record_kind,
                    ),
                )
            )
    return source_specs


def source_regions_from_record_set_spec(
    spec: SourceRecordSetSpec,
) -> tuple[SourceRegionSpec, ...]:
    """Build source-region specs implied by a compact record-set spec."""
    row_numbers = [
        number
        for row in spec.rows
        for number in (row.row_number, row.row_end_number)
        if number is not None
    ]
    columns = [
        1,
        *(_excel_column_number(measure.column) for measure in spec.measures),
        *(
            _excel_column_number(row.column)
            for row in spec.rows
            if row.column is not None
        ),
    ]
    return (
        SourceRegionSpec(
            region_id=f"{spec.record_set_id}.selected_region",
            sheet_name=spec.sheet_name,
            top_row=min(row_numbers),
            left_column=min(columns),
            bottom_row=max(row_numbers),
            right_column=max(columns),
            region_kind="record_set",
            label=spec.record_set_id,
            record_set_id=spec.record_set_id,
        ),
    )


def _row_guard_cells(row: SourceRecordSetRow) -> tuple[CellGuardSpec, ...]:
    guards = []
    for guard in row.guard_cells:
        row_number = _row_guard_row_number(row, guard)
        guards.append(
            CellGuardSpec(
                address=f"{guard.column}{row_number}",
                expected_value=guard.expected_value,
                label=guard.label,
            )
        )
    return tuple(guards)


def _row_range_label_guards(
    row: SourceRecordSetRow,
) -> tuple[RangeLabelGuardSpec, ...]:
    guards = []
    for guard in row.range_label_guards:
        if row.row_end_number is None:
            raise ValueError("Range label guard requires row_end_number")
        expected_count = row.row_end_number - row.row_number + 1
        if len(guard.expected_values) != expected_count:
            raise ValueError(
                f"Range label guard {guard.label!r} expected {expected_count} "
                f"values, got {len(guard.expected_values)}"
            )
        guards.append(
            RangeLabelGuardSpec(
                column=guard.column,
                expected_values=_strict_range_label_values(guard.expected_values),
                label=guard.label,
            )
        )
    return tuple(guards)


def _row_guard_row_number(
    row: SourceRecordSetRow,
    guard: SourceRecordSetRowGuard,
) -> int:
    if isinstance(guard.row, bool):
        raise ValueError(f"Row guard row must not be boolean: {guard.row!r}")
    if isinstance(guard.row, int):
        if guard.row < 1:
            raise ValueError(f"Row guard row must be at least 1: {guard.row!r}")
        return guard.row
    if guard.row == "start":
        return row.row_number
    if guard.row == "end":
        if row.row_end_number is None:
            raise ValueError("End row guard requires row_end_number")
        return row.row_end_number
    raise ValueError(f"Unsupported row guard anchor: {guard.row!r}")


def resolve_source_record(
    cells: list[SourceCell],
    spec: SourceRecordSpec,
    *,
    cells_by_sheet_address: CellsBySheetAddress | None = None,
) -> SourceRecord:
    """Resolve a source-record spec against parsed source cells."""
    if cells_by_sheet_address is None:
        cells_by_sheet_address = build_cells_by_sheet_address(cells)
    value_cells = _resolve_value_cells(
        cells,
        spec.selector,
        cells_by_sheet_address=cells_by_sheet_address,
    )
    value = _scale_value(_sum_cell_values(value_cells), spec.value_scale)
    divisor_cells: list[SourceCell] = []
    if spec.divisor_selector is not None:
        divisor_cells = _resolve_value_cells(
            cells,
            spec.divisor_selector,
            cells_by_sheet_address=cells_by_sheet_address,
        )
        divisor = _sum_cell_values(divisor_cells)
        if not isinstance(divisor, (int, float)) or divisor == 0:
            raise ValueError(
                f"Source record {spec.source_record_id!r} has invalid divisor "
                f"{divisor!r}."
            )
        value = value / divisor
        if spec.round_to is not None:
            value = round(value / spec.round_to) * spec.round_to
    lineage_cells = [
        *value_cells,
        *divisor_cells,
        *_selector_lineage_guard_cells(
            cells,
            spec.selector,
            cells_by_sheet_address=cells_by_sheet_address,
        ),
    ]
    seen_cell_keys: set[str] = set()
    source_cell_keys = []
    source_cell_addresses = []
    for lineage_cell in lineage_cells:
        cell_key = build_source_cell_key(lineage_cell)
        if cell_key in seen_cell_keys:
            continue
        seen_cell_keys.add(cell_key)
        source_cell_keys.append(cell_key)
        source_cell_addresses.append(lineage_cell.address)
    return SourceRecord(
        source_record_id=spec.source_record_id,
        value=value,
        spec=spec,
        source_cell_keys=tuple(source_cell_keys),
        source_cell_addresses=tuple(source_cell_addresses),
        source_row_keys=(
            tuple(
                dict.fromkeys(
                    cell.source_row_key
                    for cell in value_cells
                    if cell.source_row_key is not None
                )
            )
        ),
    )


def resolve_cell_selector(
    cells: list[SourceCell],
    spec: CellSelectorSpec,
    *,
    cells_by_sheet_address: CellsBySheetAddress | None = None,
) -> SourceCell:
    """Resolve and validate a cell selector."""
    if spec.end_address is not None:
        raise ValueError("resolve_cell_selector only supports single-cell selectors")
    if cells_by_sheet_address is None:
        cells_by_sheet_address = build_cells_by_sheet_address(cells)
    try:
        cell = cells_by_sheet_address[(spec.sheet_name, spec.address)]
    except KeyError as exc:
        raise ValueError(
            f"Selector {spec.selector_id!r} did not match "
            f"{spec.sheet_name}!{spec.address}"
        ) from exc

    if (
        spec.expected_cell_type is not None
        and cell.cell_type != spec.expected_cell_type
    ):
        raise ValueError(
            f"Selector {spec.selector_id!r} expected cell type "
            f"{spec.expected_cell_type!r}, got {cell.cell_type!r}"
        )

    _resolve_guard_cell(
        cells_by_sheet_address,
        spec,
        address=spec.expected_row_header_address,
        expected_value=spec.expected_row_header,
        label="row header",
    )
    _resolve_guard_cell(
        cells_by_sheet_address,
        spec,
        address=spec.expected_column_header_address,
        expected_value=spec.expected_column_header,
        label="column header",
    )
    _resolve_explicit_guard_cells(cells_by_sheet_address, spec)
    _resolve_range_label_guards(cells_by_sheet_address, spec)

    return cell


def _resolve_value_cells(
    cells: list[SourceCell],
    spec: CellSelectorSpec,
    *,
    cells_by_sheet_address: CellsBySheetAddress | None = None,
) -> list[SourceCell]:
    if cells_by_sheet_address is None:
        cells_by_sheet_address = build_cells_by_sheet_address(cells)
    if spec.end_address is None:
        return [
            resolve_cell_selector(
                cells,
                spec,
                cells_by_sheet_address=cells_by_sheet_address,
            )
        ]

    start_column, start_row = _split_cell_address(spec.address)
    end_column, end_row = _split_cell_address(spec.end_address)
    if start_column != end_column:
        raise ValueError(
            f"Selector {spec.selector_id!r} ranges must stay within one column"
        )
    if end_row < start_row:
        raise ValueError(f"Selector {spec.selector_id!r} has an inverted range")

    selected_cells = []
    for row_number in range(start_row, end_row + 1):
        address = f"{start_column}{row_number}"
        try:
            cell = cells_by_sheet_address[(spec.sheet_name, address)]
        except KeyError as exc:
            raise ValueError(
                f"Selector {spec.selector_id!r} did not match "
                f"{spec.sheet_name}!{address}"
            ) from exc
        if (
            spec.expected_cell_type is not None
            and cell.cell_type != spec.expected_cell_type
        ):
            raise ValueError(
                f"Selector {spec.selector_id!r} expected cell type "
                f"{spec.expected_cell_type!r}, got {cell.cell_type!r}"
            )
        selected_cells.append(cell)

    _resolve_guard_cell(
        cells_by_sheet_address,
        spec,
        address=spec.expected_row_header_address,
        expected_value=spec.expected_row_header,
        label="row header",
    )
    _resolve_guard_cell(
        cells_by_sheet_address,
        spec,
        address=spec.expected_column_header_address,
        expected_value=spec.expected_column_header,
        label="column header",
    )
    _resolve_explicit_guard_cells(cells_by_sheet_address, spec)
    _resolve_range_label_guards(cells_by_sheet_address, spec)
    return selected_cells


def _selector_lineage_guard_cells(
    cells: list[SourceCell],
    spec: CellSelectorSpec,
    *,
    cells_by_sheet_address: CellsBySheetAddress | None = None,
) -> tuple[SourceCell, ...]:
    if cells_by_sheet_address is None:
        cells_by_sheet_address = build_cells_by_sheet_address(cells)
    guard_cells = []
    column_header = _resolve_guard_cell(
        cells_by_sheet_address,
        spec,
        address=spec.expected_column_header_address,
        expected_value=spec.expected_column_header,
        label="column header",
    )
    if column_header is not None:
        guard_cells.append(column_header)
    guard_cells.extend(_resolve_explicit_guard_cells(cells_by_sheet_address, spec))
    for range_label_cells in _resolve_range_label_guards(
        cells_by_sheet_address,
        spec,
    ):
        guard_cells.extend(range_label_cells)
    return tuple(guard_cells)


def build_cells_by_sheet_address(
    cells: list[SourceCell],
) -> CellsBySheetAddress:
    """Index source cells for repeated selector resolution."""
    return {(cell.sheet_name, cell.address): cell for cell in cells}


def _resolve_explicit_guard_cells(
    cells_by_sheet_address: dict[tuple[str, str], SourceCell],
    spec: CellSelectorSpec,
) -> list[SourceCell]:
    guard_cells = []
    for guard in spec.guard_cells:
        guard_cell = _resolve_guard_cell(
            cells_by_sheet_address,
            spec,
            address=guard.address,
            expected_value=guard.expected_value,
            label=guard.label,
        )
        if guard_cell is not None:
            guard_cells.append(guard_cell)
    return guard_cells


def _resolve_range_label_guards(
    cells_by_sheet_address: dict[tuple[str, str], SourceCell],
    spec: CellSelectorSpec,
) -> list[list[SourceCell]]:
    if not spec.range_label_guards:
        return []
    if spec.end_address is None:
        raise ValueError(
            f"Selector {spec.selector_id!r} has range label guards but no end address"
        )
    _start_column, start_row = _split_cell_address(spec.address)
    _end_column, end_row = _split_cell_address(spec.end_address)
    expected_count = end_row - start_row + 1
    resolved = []
    for guard in spec.range_label_guards:
        if len(guard.expected_values) != expected_count:
            raise ValueError(
                f"Selector {spec.selector_id!r} expected {guard.label} length "
                f"{expected_count}, got {len(guard.expected_values)}"
            )
        guard_cells = []
        for offset, expected_value in enumerate(guard.expected_values):
            if expected_value is None:
                raise ValueError(
                    f"Selector {spec.selector_id!r} expected {guard.label} "
                    "must not contain null"
                )
            address = f"{guard.column}{start_row + offset}"
            guard_cell = _resolve_guard_cell(
                cells_by_sheet_address,
                spec,
                address=address,
                expected_value=expected_value,
                label=f"{guard.label} {address}",
            )
            if guard_cell is not None:
                guard_cells.append(guard_cell)
        resolved.append(guard_cells)
    return resolved


def _strict_range_label_values(values: tuple[Scalar, ...]) -> tuple[Scalar, ...]:
    if any(value is None for value in values):
        raise ValueError("Range label guard expected values must not contain null")
    return values


def _resolve_guard_cell(
    cells_by_sheet_address: dict[tuple[str, str], SourceCell],
    spec: CellSelectorSpec,
    *,
    address: str | None,
    expected_value: Scalar,
    label: str,
) -> SourceCell | None:
    if not address or expected_value is None:
        return None
    try:
        guard_cell = cells_by_sheet_address[(spec.sheet_name, address)]
    except KeyError as exc:
        raise ValueError(
            f"Selector {spec.selector_id!r} missing {label} {address}"
        ) from exc
    if guard_cell.raw_value != expected_value:
        raise ValueError(
            f"Selector {spec.selector_id!r} expected {label} "
            f"{expected_value!r}, got {guard_cell.raw_value!r}"
        )
    return guard_cell


def _scale_value(value: Scalar, scale: int | float) -> int | float | str:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Cannot scale nonnumeric source value {value!r}")
    if isinstance(value, int | float):
        scaled = value * scale
        if isinstance(scaled, float) and scaled.is_integer():
            return int(scaled)
        return scaled
    if scale != 1:
        raise ValueError(f"Cannot scale nonnumeric source value {value!r}")
    return value


def _sum_cell_values(cells: list[SourceCell]) -> Scalar:
    if len(cells) == 1:
        return cells[0].raw_value
    total: int | float = 0
    for cell in cells:
        value = cell.raw_value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"Cannot sum nonnumeric source value {value!r}")
        total += value
    if isinstance(total, float) and total.is_integer():
        return int(total)
    return total


def _split_cell_address(address: str) -> tuple[str, int]:
    column = ""
    row = ""
    for char in address:
        if char.isalpha() and not row:
            column += char.upper()
        elif char.isdigit():
            row += char
        else:
            raise ValueError(f"Malformed cell address: {address!r}")
    if not column or not row:
        raise ValueError(f"Malformed cell address: {address!r}")
    return column, int(row)


def _record_set_spec_hash(spec: SourceRecordSetSpec) -> str:
    payload = asdict(spec)
    if not payload.get("shared_constraints"):
        payload.pop("shared_constraints", None)
    if payload.get("assertion") == DEFAULT_ASSERTION:
        payload.pop("assertion", None)
    if payload.get("period_coverage") is None:
        payload.pop("period_coverage", None)
    for row in payload["rows"]:
        if row.get("row_end_number") is None:
            row.pop("row_end_number", None)
        if row.get("column") is None:
            row.pop("column", None)
        if row.get("source_column_id") is None:
            row.pop("source_column_id", None)
        if row.get("expected_column_header_row") is None:
            row.pop("expected_column_header_row", None)
        if row.get("expected_column_header") is None:
            row.pop("expected_column_header", None)
        if row.get("expected_row_header") is None:
            row.pop("expected_row_header", None)
        if row.get("expected_row_header_column") is None:
            row.pop("expected_row_header_column", None)
        if not row.get("guard_cells"):
            row.pop("guard_cells", None)
        else:
            row["guard_cells"] = sorted(
                row["guard_cells"],
                key=_row_guard_hash_sort_key,
            )
        if not row.get("range_label_guards"):
            row.pop("range_label_guards", None)
        else:
            row["range_label_guards"] = sorted(
                row["range_label_guards"],
                key=_range_label_guard_hash_sort_key,
            )
        if row.get("value_scale") == 1:
            row.pop("value_scale", None)
        for key in (
            "geography_id",
            "geography_level",
            "geography_name",
            "geography_vintage",
        ):
            if row.get(key) is None:
                row.pop(key, None)
    for measure in payload["measures"]:
        if measure.get("expected_column_header") is None:
            measure.pop("expected_column_header", None)
        if measure.get("expected_column_header_row") is None:
            measure.pop("expected_column_header_row", None)
        if not measure.get("filters"):
            measure.pop("filters", None)
        if not measure.get("constraints"):
            measure.pop("constraints", None)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _row_guard_hash_sort_key(guard: dict[str, Scalar]) -> tuple[str, str, str, str]:
    row = guard.get("row")
    if isinstance(row, int):
        row_key = f"int:{row:010d}"
    else:
        row_key = f"str:{row}"
    return (
        str(guard.get("column", "")).upper(),
        row_key,
        repr(guard.get("expected_value")),
        str(guard.get("label", "")),
    )


def _range_label_guard_hash_sort_key(
    guard: dict[str, Scalar],
) -> tuple[str, str]:
    return (
        str(guard.get("column", "")).upper(),
        str(guard.get("label", "")),
    )


def _excel_column_number(column_name: str) -> int:
    value = 0
    for char in column_name.upper():
        if not ("A" <= char <= "Z"):
            raise ValueError(f"Malformed Excel column name: {column_name!r}")
        value = value * 26 + ord(char) - ord("A") + 1
    return value
