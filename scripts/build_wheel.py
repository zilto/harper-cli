#!/usr/bin/env python3
"""Discover `Automattic/harper` release assets and package them as wheels.

This project ships a single pure-Python module that `exec`s a bundled
`harper-cli` binary (see `src/harper/__main__.py`). This script:

1. Looks up the given release `--version` via the GitHub API and lists its
   assets, instead of assuming a fixed set of targets/extensions exist.
2. For each asset named `harper-cli-<target>.<tar.gz|zip>` whose `<target>`
   is a recognized Rust target triple (see `TARGET_TO_WHEEL_TAGS`), downloads
   it and extracts the `harper-cli` (or `harper-cli.exe`) binary. Assets for
   unrecognized targets are skipped with a warning.
3. For each recognized target, stages *only that target's* binary into
   `src/harper/_bin/` in this checkout, and temporarily bumps this project's
   `pyproject.toml` version (leading `v` stripped from `--version`) to match
   the upstream release, so its PyPI version tracks upstream 1:1. Both
   changes are reverted in a `finally` block (even on error), so the real
   checkout ends up exactly as it started; this script is safe to run
   repeatedly against a local working tree. `src/harper/_bin/` is
   gitignored, so this staging never risks being committed.
4. Builds a wheel from the checkout. `[tool.hatch.build.targets.wheel]`
   normally excludes `src/harper/_bin/` (since it's gitignored), but its
   `artifacts` setting force-includes the binary anyway; this produces a
   purelib, "any platform" wheel, which this script then retags to the
   target's wheel platform tag(s) with the `wheel` package so PyPI and pip
   resolve one wheel per platform correctly.
5. Re-opens the built wheel and asserts it contains exactly one `_bin/`
   entry, matching this target's binary name. Targets are built one at a
   time and the binary is removed between builds, but this check guards
   against ever accidentally shipping more than one platform's binary in a
   single wheel.

Only proxies the existing prebuilt assets upstream publishes; does not build
`harper-cli` from source.

Requires `uv` (and network access to github.com/api.github.com) on `PATH`.
Has no third-party Python dependencies of its own, so it can be run the same
way locally (e.g. to smoke-test a release before triggering CI) as in GitHub
Actions:

    uv run scripts/build_wheel.py --version v2.8.0 --output-dir dist

To then exercise one of the built wheels' `harper` command without
installing it into any persistent environment:

    uv run --with dist/harper_cli-2.8.0-py3-none-macosx_11_0_arm64.whl \
        --no-project harper --version

Set `GITHUB_TOKEN` in the environment to use an authenticated GitHub API
request (higher rate limit); anonymous requests work fine too.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Generator
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = ROOT / "pyproject.toml"
BIN_DIR = ROOT / "src" / "harper" / "_bin"
RELEASE_API_URL_TEMPLATE = "https://api.github.com/repos/Automattic/harper/releases/tags/{version}"
ASSET_NAME_RE = re.compile(r"^harper-cli-(?P<target>.+)\.(?P<ext>tar\.gz|zip)$")

# Rust target triple -> PyPI wheel platform tag(s). This is the one place
# platform-specific knowledge (e.g. macOS minimum-version policy) has to be
# hardcoded; everything else is discovered from the actual release assets.
TARGET_TO_WHEEL_TAGS = {
    "x86_64-unknown-linux-gnu": ["manylinux_2_17_x86_64", "manylinux2014_x86_64"],
    "aarch64-unknown-linux-gnu": ["manylinux_2_17_aarch64", "manylinux2014_aarch64"],
    "x86_64-apple-darwin": ["macosx_10_12_x86_64"],
    "aarch64-apple-darwin": ["macosx_11_0_arm64"],
    "x86_64-pc-windows-msvc": ["win_amd64"],
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Automattic/harper release tag to package, e.g. v2.8.0",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="Directory to write the built wheels to (default: dist)",
    )
    return parser.parse_args(argv)


def list_release_assets(version: str) -> list[tuple[str, str]]:
    """Return `(asset_name, download_url)` pairs for the given release tag."""
    url = RELEASE_API_URL_TEMPLATE.format(version=version)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "harper-cli-release-script",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request) as response:
        payload = json.load(response)

    return [(asset["name"], asset["browser_download_url"]) for asset in payload["assets"]]


def resolve_wheel_targets(assets: list[tuple[str, str]]) -> list[tuple[str, str, str, list[str]]]:
    """Match assets against known targets.

    Returns `(asset_name, download_url, target, wheel_tags)` tuples for
    recognized assets; unrecognized ones are skipped with a warning.
    """
    resolved = []
    for name, url in assets:
        match = ASSET_NAME_RE.match(name)
        if not match:
            continue

        target = match.group("target")
        wheel_tags = TARGET_TO_WHEEL_TAGS.get(target)
        if wheel_tags is None:
            logger.info(f"Skipping unrecognized wheel target: `{target}`")
            continue

        resolved.append((name, url, target, wheel_tags))
    return resolved


def download_release_asset(url: str, asset_name: str, dest_dir: Path) -> Path:
    """Download the release asset and return its local path."""
    dest = dest_dir / asset_name
    with urllib.request.urlopen(url) as response, dest.open("wb") as f:
        shutil.copyfileobj(response, f)

    return dest


def extract_binary(archive_path: Path, extract_dir: Path) -> Path:
    """Extract the archive and return the path to the `harper-cli` binary inside it."""
    if archive_path.name.endswith(".tar.gz"):
        with tarfile.open(archive_path) as tar:
            tar.extractall(extract_dir)
    elif archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)
    else:
        raise ValueError(f"Unsupported archive format: `{archive_path}`")

    for candidate in extract_dir.rglob("*"):
        if candidate.is_file() and candidate.name in ("harper-cli", "harper-cli.exe"):
            return candidate
    raise FileNotFoundError(f"Downloaded `{archive_path.name}` but found no `harper-cli` binary inside it.")


@contextlib.contextmanager
def stamped_pyproject_version(version: str) -> Generator[None]:
    """Temporarily rewrite `pyproject.toml`'s version, restoring it afterward.

    Always restores the original file, even if the build fails, so the real
    checkout never ends up with a stale bumped version.
    """
    pkg_version = version.removeprefix("v")
    original_text = PYPROJECT_PATH.read_text()
    new_text, count = re.subn(r'(?m)^version = ".*"$', f'version = "{pkg_version}"', original_text, count=1)
    if count != 1:
        raise RuntimeError(f"could not find a `version = \"...\"` line in {PYPROJECT_PATH}")

    PYPROJECT_PATH.write_text(new_text)
    try:
        yield
    finally:
        PYPROJECT_PATH.write_text(original_text)


@contextlib.contextmanager
def staged_binary(binary_path: Path) -> Generator[Path]:
    """Temporarily copy `binary_path` into `src/harper/_bin/`, removing it afterward.

    `src/harper/_bin/` is gitignored, so this never risks being committed,
    but it's still cleaned up so the checkout is never left holding a stale
    binary from a previous run.
    """
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    is_windows_binary = binary_path.suffix.lower() == ".exe"
    dest = BIN_DIR / ("harper-cli.exe" if is_windows_binary else "harper-cli")
    shutil.copy2(binary_path, dest)
    if not is_windows_binary:
        dest.chmod(0o755)

    try:
        yield dest
    finally:
        dest.unlink(missing_ok=True)


def verify_single_binary(wheel_path: Path, expected_name: str) -> None:
    """Assert `wheel_path` contains exactly one `harper/_bin/` entry, named `expected_name`."""
    with zipfile.ZipFile(wheel_path) as zf:
        bin_entries = [n for n in zf.namelist() if n.startswith("harper/_bin/")]
    if bin_entries != [f"harper/_bin/{expected_name}"]:
        raise SystemExit(
            f"expected {wheel_path.name} to contain exactly one `harper/_bin/{expected_name}` entry, "
            f"found {bin_entries!r}"
        )


def build_wheel(binary_path: Path, wheel_tags: list[str], output_dir: Path, version: str) -> None:
    """Stage `binary_path`, build a wheel from this checkout, and retag it for `wheel_tags`."""
    output_dir.mkdir(parents=True, exist_ok=True)

    with (
        stamped_pyproject_version(version),
        staged_binary(binary_path) as staged_dest,
        tempfile.TemporaryDirectory() as raw_dir,
    ):
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", raw_dir],
            cwd=ROOT,
            check=True,
        )
        raw_wheels = list(Path(raw_dir).glob("*.whl"))
        if len(raw_wheels) != 1:
            raise SystemExit(f"expected exactly one built wheel, got {raw_wheels}")

        retag_cmd = [
            "uvx",
            "wheel",
            "tags",
            "--python-tag",
            "py3",
            "--abi-tag",
            "none",
        ]
        for tag in wheel_tags:
            retag_cmd += ["--platform-tag", tag]
        retag_cmd += ["--remove", str(raw_wheels[0])]
        subprocess.run(retag_cmd, cwd=raw_dir, check=True)

        for wheel in Path(raw_dir).glob("*.whl"):
            verify_single_binary(wheel, staged_dest.name)
            shutil.move(str(wheel), output_dir / wheel.name)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    assets = list_release_assets(args.version)
    resolved = resolve_wheel_targets(assets)
    if not resolved:
        raise SystemExit(f"Found no `harper-cli` release for `version={args.version}`")

    for asset_name, url, target, wheel_tags in resolved:
        logger.info(f"Building wheel for `{target=}`")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = download_release_asset(url, asset_name, tmp_path)
            extract_dir = tmp_path / "extracted"
            extract_dir.mkdir()
            binary_path = extract_binary(archive_path, extract_dir)
            build_wheel(binary_path, wheel_tags, args.output_dir, args.version)


if __name__ == "__main__":
    main()
