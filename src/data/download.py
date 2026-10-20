"""Download raw datasets and verify their checksums.

Usage: python -m src.data.download [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import urllib.request
from pathlib import Path

from src.config import get_settings
from src.data.sources import ALL_SOURCES, Source

log = logging.getLogger(__name__)
_CHUNK = 1 << 20


class ChecksumError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(source: Source, dest_dir: Path, force: bool = False) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / source.filename
    if target.exists() and not force and sha256_of(target) == source.sha256:
        log.info("%s already present", source.filename)
        return target

    partial = target.with_suffix(target.suffix + ".part")
    log.info("downloading %s", source.url)
    with urllib.request.urlopen(source.url, timeout=120) as resp, partial.open("wb") as out:
        shutil.copyfileobj(resp, out, _CHUNK)

    actual = sha256_of(partial)
    if actual != source.sha256:
        partial.unlink(missing_ok=True)
        raise ChecksumError(f"{source.filename}: expected {source.sha256}, got {actual}")
    partial.replace(target)
    return target


def fetch_all(dest_dir: Path | None = None, force: bool = False) -> dict[str, Path]:
    dest_dir = dest_dir or get_settings().raw_dir
    return {s.name: fetch(s, dest_dir, force) for s in ALL_SOURCES}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for name, path in fetch_all(force=args.force).items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
