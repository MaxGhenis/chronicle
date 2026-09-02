"""Chronicle CLI entry point."""

from __future__ import annotations

import sys

from db.cli import main as db_main


def main() -> None:
    """Dispatch Chronicle-owned harness commands or the database CLI."""
    if sys.argv[1:2] in (
        ["bootstrap-r2"],
        ["build-bundle"],
        ["build-consumer-artifact"],
        ["build-db"],
        ["build-fixture-facts"],
        ["build-source-cells"],
        ["build-source-rows"],
        ["build-suite"],
        ["export-consumer-facts"],
        ["export-db-tables"],
        ["fetch-artifact"],
        ["inventory-artifacts"],
        ["load-supabase-mirror"],
        ["plan-pe-sources"],
        ["publish-derived"],
        ["publish-raw"],
        ["register-artifact"],
        ["scaffold-package"],
        ["validate-concept-alignments"],
        ["validate-package"],
        ["validate-source-cells"],
        ["validate-source-rows"],
        ["validate-facts"],
    ):
        from chronicle.harness import main as harness_main

        raise SystemExit(harness_main(sys.argv[1:]))

    db_main()


__all__ = ["main"]


if __name__ == "__main__":
    main()
