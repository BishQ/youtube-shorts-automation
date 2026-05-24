"""Offline smoke checks for the installed package."""

from __future__ import annotations

import argparse

from shorts_pipeline.config.settings import get_settings
from shorts_pipeline.jobs.preflight import run_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="also check live local services")
    args = parser.parse_args()
    settings = get_settings()
    assert settings.data_dir.exists()
    if args.preflight:
        report = run_preflight(settings)
        return 0 if report.ok else 1
    print("shorts-pipeline import/config smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
