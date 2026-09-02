"""CLI for managing Chronicle target input data."""

import argparse
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, select

from .etl_soi import load_soi_targets
from .schema import (
    DEFAULT_DB_PATH,
    SourceArtifact,
    SourceRow,
    SourceTable,
    Stratum,
    Target,
    init_db,
    get_engine,
)


def cmd_init(args):
    """Initialize the database."""
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH
    init_db(db_path)
    print(f"Initialized database at {db_path}")


def cmd_load(args):
    """Load targets from a source."""
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH
    engine = init_db(db_path)

    with Session(engine) as session:
        if args.source == "soi" or args.source == "all":
            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_soi_targets(session, years=years)
            print(f"Loaded SOI targets for years: {years or 'all available'}")

        if args.source == "soi-state" or args.source == "all":
            from .etl_soi_state import load_soi_state_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_soi_state_targets(session, years=years)
            print(
                f"Loaded state-level SOI targets for years: {years or 'all available'}"
            )

        if args.source == "soi-credits" or args.source == "all":
            from .etl_soi_credits import load_soi_credits_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_soi_credits_targets(session, years=years)
            print(f"Loaded SOI credits targets for years: {years or 'all available'}")

        if args.source == "soi-income-sources" or args.source == "all":
            from .etl_soi_income_sources import load_soi_income_sources_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_soi_income_sources_targets(session, years=years)
            print(
                f"Loaded SOI income sources targets for years: {years or 'all available'}"
            )

        if args.source == "soi-deductions" or args.source == "all":
            from .etl_soi_deductions import load_soi_deductions_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_soi_deductions_targets(session, years=years)
            print(
                f"Loaded SOI deductions targets for years: {years or 'all available'}"
            )

        if args.source == "snap" or args.source == "all":
            from .etl_snap import load_snap_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_snap_targets(session, years=years)
            print(f"Loaded SNAP targets for years: {years or 'all available'}")

        if args.source == "medicaid" or args.source == "all":
            from .etl_medicaid import load_medicaid_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_medicaid_targets(session, years=years)
            print(f"Loaded Medicaid targets for years: {years or 'all available'}")

        if args.source == "aca" or args.source == "all":
            from .etl_aca_enrollment import load_aca_enrollment_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_aca_enrollment_targets(session, years=years)
            print(
                f"Loaded ACA Marketplace targets for years: {years or 'all available'}"
            )

        if args.source == "census" or args.source == "all":
            from .etl_census import load_census_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_census_targets(session, years=years)
            print(f"Loaded Census targets for years: {years or 'all available'}")

        if args.source == "ssa" or args.source == "all":
            from .etl_ssa import load_ssa_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_ssa_targets(session, years=years)
            print(f"Loaded SSA targets for years: {years or 'all available'}")

        if args.source == "ssi" or args.source == "all":
            from .etl_ssi import load_ssi_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_ssi_targets(session, years=years)
            print(f"Loaded SSI targets for years: {years or 'all available'}")

        if args.source == "bls" or args.source == "all":
            from .etl_bls import load_bls_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_bls_targets(session, years=years)
            print(f"Loaded BLS targets for years: {years or 'all available'}")

        if args.source == "cps" or args.source == "all":
            from .etl_cps import load_cps_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_cps_targets(session, years=years)
            print(f"Loaded CPS monthly targets for years: {years or 'all available'}")

        if args.source == "cbo" or args.source == "all":
            from .etl_cbo import load_cbo_targets

            years = [int(y) for y in args.years.split(",")] if args.years else None
            load_cbo_targets(session, years=years)
            print(f"Loaded CBO projections for years: {years or 'all available'}")


def cmd_stats(args):
    """Show database statistics."""
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return

    engine = get_engine(db_path)
    with Session(engine) as session:
        strata_count = len(session.exec(select(Stratum)).all())
        targets_count = len(session.exec(select(Target)).all())

        # Get unique sources
        sources = session.exec(select(Target.source).distinct()).all()

        # Get unique years
        years = session.exec(select(Target.period).distinct()).all()

        print(f"Database: {db_path}")
        print(f"Strata: {strata_count}")
        print(f"Targets: {targets_count}")
        print(f"Sources: {', '.join(str(s) for s in sources)}")
        print(f"Years: {', '.join(str(y) for y in sorted(years))}")


def cmd_load_source_files(args):
    """Load full source files into the source artifact tables."""
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH
    engine = init_db(db_path)

    if args.inventory != "pe":
        raise ValueError(f"Unsupported source-file inventory: {args.inventory}")

    from .pe_source_inventory import pe_source_specs
    from .source_files import ingest_source_artifacts, prune_source_artifacts

    include_us = args.jurisdiction in {"all", "us"}
    include_uk = args.jurisdiction in {"all", "uk"}
    specs = pe_source_specs(
        pe_us_root=Path(args.pe_us_root) if args.pe_us_root else None,
        pe_uk_root=Path(args.pe_uk_root) if args.pe_uk_root else None,
        include_us=include_us,
        include_uk=include_uk,
    )
    if args.limit:
        specs = specs[: args.limit]
    if not specs and not args.limit:
        raise ValueError(
            "Refusing to prune source artifacts with an empty source-file inventory."
        )

    with Session(engine) as session:
        results = ingest_source_artifacts(session, specs)
        removed = 0 if args.limit else prune_source_artifacts(session, specs)

    print(f"Loaded {len(results)} source artifacts into {db_path}")
    if removed:
        print(f"Pruned {removed} stale source artifacts")
    print(f"Parsed tables: {sum(result.table_count for result in results):,}")
    print(f"Parsed rows: {sum(result.row_count for result in results):,}")


def cmd_source_stats(args):
    """Show source artifact statistics."""
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return

    engine = get_engine(db_path)
    with Session(engine) as session:
        artifact_count = session.exec(select(func.count(SourceArtifact.id))).one()
        table_count = session.exec(select(func.count(SourceTable.id))).one()
        row_count = session.exec(select(func.count(SourceRow.id))).one()
        by_pipeline = session.exec(
            select(
                SourceArtifact.origin_project,
                SourceArtifact.pipeline,
                func.count(SourceArtifact.id),
            )
            .group_by(SourceArtifact.origin_project, SourceArtifact.pipeline)
            .order_by(SourceArtifact.origin_project, SourceArtifact.pipeline)
        ).all()
        by_source = session.exec(
            select(SourceArtifact.source_id, func.count(SourceArtifact.id))
            .group_by(SourceArtifact.source_id)
            .order_by(SourceArtifact.source_id)
        ).all()

    print(f"Database: {db_path}")
    print(f"Source artifacts: {artifact_count:,}")
    print(f"Source tables: {table_count:,}")
    print(f"Source rows: {row_count:,}")
    print("Pipelines:")
    for origin_project, pipeline, count in by_pipeline:
        print(f"  {origin_project}/{pipeline}: {count:,}")
    print("Sources:")
    for source_id, count in by_source:
        print(f"  {source_id}: {count:,}")


def cmd_query(args):
    """Query targets."""
    db_path = Path(args.db) if args.db else DEFAULT_DB_PATH

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return

    engine = get_engine(db_path)
    with Session(engine) as session:
        query = select(Target, Stratum).join(Stratum)

        if args.variable:
            query = query.where(Target.variable == args.variable)
        if args.year:
            query = query.where(Target.period == int(args.year))
        if args.source:
            query = query.where(Target.source == args.source)

        results = session.exec(query).all()

        if not results:
            print("No targets found matching criteria")
            return

        print(
            f"{'Stratum':<30} {'Variable':<25} {'Year':<6} {'Value':>15} {'Source':<10}"
        )
        print("-" * 90)
        for target, stratum in results[: args.limit]:
            print(
                f"{stratum.name:<30} {target.variable:<25} {target.period:<6} {target.value:>15,.0f} {target.source.value:<10}"
            )


def _pe_source_root_env_default(jurisdiction: str) -> str | None:
    from chronicle.env import env_value

    from .pe_source_inventory import (
        PE_UK_DATA_ROOT_ENV,
        PE_US_DATA_ROOT_ENV,
    )

    env_var = PE_US_DATA_ROOT_ENV if jurisdiction == "us" else PE_UK_DATA_ROOT_ENV
    return env_value(env_var)


def main():
    parser = argparse.ArgumentParser(description="Manage Chronicle target input data")
    parser.add_argument("--db", help=f"Database path (default: {DEFAULT_DB_PATH})")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    init_parser = subparsers.add_parser("init", help="Initialize database")
    init_parser.set_defaults(func=cmd_init)

    # load
    load_parser = subparsers.add_parser("load", help="Load targets from source")
    load_parser.add_argument(
        "source",
        choices=[
            "soi",
            "soi-state",
            "soi-credits",
            "soi-income-sources",
            "soi-deductions",
            "snap",
            "medicaid",
            "aca",
            "census",
            "ssa",
            "ssi",
            "bls",
            "cps",
            "cbo",
            "all",
        ],
        help="Data source to load",
    )
    load_parser.add_argument(
        "--years", help="Comma-separated years to load (default: all)"
    )
    load_parser.set_defaults(func=cmd_load)

    # stats
    stats_parser = subparsers.add_parser("stats", help="Show database statistics")
    stats_parser.set_defaults(func=cmd_stats)

    # load-source-files
    source_parser = subparsers.add_parser(
        "load-source-files",
        help="Load full parsed source artifacts into the database",
    )
    source_parser.add_argument(
        "inventory",
        choices=["pe"],
        help="Source-file inventory to load",
    )
    source_parser.add_argument(
        "--jurisdiction",
        choices=["all", "us", "uk"],
        default="all",
        help="Limit the source-file inventory",
    )
    source_parser.add_argument(
        "--pe-us-root",
        default=_pe_source_root_env_default("us"),
        help="Path to the policyengine-us-data checkout",
    )
    source_parser.add_argument(
        "--pe-uk-root",
        default=_pe_source_root_env_default("uk"),
        help="Path to the policyengine-uk-data checkout",
    )
    source_parser.add_argument(
        "--limit",
        type=int,
        help="Load only the first N artifacts, for smoke tests",
    )
    source_parser.set_defaults(func=cmd_load_source_files)

    # source-stats
    source_stats_parser = subparsers.add_parser(
        "source-stats", help="Show source artifact statistics"
    )
    source_stats_parser.set_defaults(func=cmd_source_stats)

    # query
    query_parser = subparsers.add_parser("query", help="Query targets")
    query_parser.add_argument("--variable", help="Filter by variable name")
    query_parser.add_argument("--year", help="Filter by year")
    query_parser.add_argument("--source", help="Filter by source")
    query_parser.add_argument("--limit", type=int, default=20, help="Max results")
    query_parser.set_defaults(func=cmd_query)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
