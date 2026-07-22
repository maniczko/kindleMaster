from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production_acceptance_target import UnsafeAcceptanceTarget, validate_staging_target
from run_production_p0_acceptance import main as run_acceptance


def main() -> int:
    base_url = os.environ.get("KINDLEMASTER_STAGING_BASE_URL", "")
    try:
        target = validate_staging_target(base_url)
    except UnsafeAcceptanceTarget as error:
        print(f"Staging acceptance blocked [{error.code}]: {error}", file=sys.stderr)
        return 2
    print(f"Validated staging acceptance target: {target.host} ({target.source})")
    return run_acceptance()


if __name__ == "__main__":
    raise SystemExit(main())
