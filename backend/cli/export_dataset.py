"""Command-line dataset export (same as the button on the labeling page).

    .venv/bin/python -m backend.cli.export_dataset [--reviewed-only] [--val-ratio 0.2] [--no-negatives] [--max-side 1280]
"""

import argparse
import json

from ..core.config import EXPORT_MAX_SIDE
from ..storage.export import export_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--reviewed-only", action="store_true", help="only bboxes a human confirmed or drew")
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--no-negatives", action="store_true", help="skip 'reject' samples as negative images")
    parser.add_argument("--max-side", type=int, default=EXPORT_MAX_SIDE, help="0 = keep original size")
    args = parser.parse_args()

    try:
        result = export_dataset(args.reviewed_only, args.val_ratio, not args.no_negatives, args.max_side)
    except ValueError as e:
        raise SystemExit(f"Nothing exported: {e}")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
