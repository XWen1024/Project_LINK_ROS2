#!/usr/bin/env python3
"""Generate a cryptographically strong Fall Guard shared token."""

from __future__ import annotations

import argparse
import secrets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bytes",
        type=int,
        default=48,
        help="random byte count before URL-safe base64 encoding (default: 48)",
    )
    args = parser.parse_args()
    if args.bytes < 32:
        parser.error("--bytes must be at least 32")
    print(secrets.token_urlsafe(args.bytes))


if __name__ == "__main__":
    main()
