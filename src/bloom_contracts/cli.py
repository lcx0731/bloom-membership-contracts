"""Run a synthetic capacity and absent-query audit."""

import argparse
import json

from .audit import audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=4000)
    parser.add_argument("--queries", type=int, default=20000)
    parser.add_argument("--target", type=float, default=.01)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.items, args.queries, args.target, args.seed), indent=2, allow_nan=False))
    except (ValueError, ArithmeticError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
