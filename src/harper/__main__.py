from __future__ import annotations

import contextlib
import importlib.resources
import os
import pathlib
import sys
from collections.abc import Generator


@contextlib.contextmanager
def find_binary_path() -> Generator[pathlib.Path, None, None]:
    """Yield the absolute filesystem path of the bundled `harper-cli` binary.

    The binary is packaged as resource data, which may not always live directly
    on disk (e.g. if `harper` is ever loaded from a zipped wheel). `as_file`
    transparently extracts it to a temporary file in that case, and cleans it
    up afterwards.
    """
    name = "harper-cli.exe" if os.name == "nt" else "harper-cli"
    resource = importlib.resources.files("harper") / "_bin" / name
    if not resource.is_file():
        raise FileNotFoundError(f"Bundled `harper-cli` binary not found at `{resource}`.")
    with importlib.resources.as_file(resource) as candidate:
        yield candidate


def main() -> None:
    """Exec the bundled binary with this process's argv."""
    with find_binary_path() as binary_path:
        argv = (str(binary_path), *sys.argv[1:])
        if os.name == "nt":
            import subprocess

            result = subprocess.run(argv, check=False)
            raise SystemExit(result.returncode)
        os.execv(str(binary_path), argv)


if __name__ == "__main__":
    main()
