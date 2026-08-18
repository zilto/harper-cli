# harper-cli

A PyPI package that bundles the pre-built `harper-cli` binary from
[`Automattic/harper`](https://github.com/Automattic/harper/) so it can be
installed with `pip`/`uv`. Automattic authors and builds the binary; this
repository only handles fetching/packaging it for distribution on PyPI.

This project is not affiliated with, endorsed by, or an official mirror of
`Automattic/harper`.

## Installation

Install from PyPI with `pip`:

```sh
pip install harper-cli
```

or with `uv`:

```sh
uv add harper-cli
```

## Usage

Run `harper lint` on one or more files, or pipe text through standard input.
See `harper --help` and `harper lint --help` for the full set of options
(output format, dialect, ignore/only rules, etc.) exposed by the underlying
`harper-cli` binary.

### As a one-off tool, without installing

```sh
uvx --from harper-cli harper lint README.md
```

### Inside a project virtual environment

If `harper-cli` is a project dependency (installed via `pip install`/`uv add`
above), run it directly once the venv is active:

```sh
harper lint README.md
```

Or, without activating the venv, via `uv run`:

```sh
uv run harper lint README.md
```

### As a `pre-commit` hook

Since this package only wraps a binary, there's no dedicated `pre-commit`
hook repo to point to. Declare it as a local hook instead:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: harper-lint
        name: harper lint
        entry: uvx --from harper-cli harper lint
        language: system
        types: [markdown,python]
```

## License

See [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE) for full attribution
details. In short: the bundled `harper-cli` binary is Copyright 2024 Elijah
Potter and licensed under Apache-2.0 by the Automattic/harper project. The
packaging code in this repository is separately Copyright 2026 Thierry Jean,
also under Apache-2.0.
