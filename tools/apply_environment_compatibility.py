#!/usr/bin/env python3
"""Restore the deployed Semantic Kernel/Pydantic compatibility adjustment."""
import argparse
import hashlib
from pathlib import Path
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SHA256 = "3ed06f59be2088a8ed38a5e955475b9733df0b365b0839ecbed8010858991158"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    if prefix != Path(sys.prefix).resolve() or not prefix.is_relative_to(Path("/data")):
        parser.error("Run with the newly installed environment Python below /data")
    target = Path(sysconfig.get_path("purelib")) / "semantic_kernel/sk_pydantic.py"
    source = ROOT / "env/compat/semantic_kernel_sk_pydantic.py"
    expected = source.read_bytes()
    actual = target.read_bytes()
    if actual == expected:
        print("Semantic Kernel compatibility already restored")
        return
    if hashlib.sha256(actual).hexdigest() != ORIGINAL_SHA256:
        parser.error("Unexpected Semantic Kernel source; inspect before applying compatibility")
    target.write_bytes(expected)
    print("Restored the deployed Semantic Kernel/Pydantic compatibility adjustment")


if __name__ == "__main__":
    main()
