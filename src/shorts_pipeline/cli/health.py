"""Run local production readiness checks."""

from __future__ import annotations

import argparse
import json

from shorts_pipeline.config.settings import get_settings
from shorts_pipeline.jobs.preflight import run_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--optional", action="store_true", help="include optional services")
    args = parser.parse_args()
    report = run_preflight(get_settings(), include_optional=args.optional)
    print(json.dumps(report.model_dump(), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
