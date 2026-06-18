# Releasing senrah

This document describes how to cut a versioned release. v1.1 publishes to
**TestPyPI** only — the production PyPI upload is a deliberate manual gate and is
intentionally out of scope for this milestone.

## Version: single source of truth

The package version lives in **exactly one place**:

```toml
# pyproject.toml
[project]
version = "0.1.0"
```

`setuptools` reads it from there at build time, and the package exposes it at
runtime via installed-distribution metadata:

```python
# src/senrah/__init__.py
from importlib.metadata import version
__version__ = version("senrah")
```

`senrah --version` prints this value. **Never** hardcode the version string
anywhere else — there is no second copy to keep in sync.

## Bumping the version

1. Edit the single `version` line in `pyproject.toml`, following
   [SemVer](https://semver.org/) (e.g. `0.1.0` → `0.1.1` for a patch).
2. Commit the bump:

   ```bash
   git commit -am "chore: bump version to 0.1.1"
   ```

## Cutting a release

The tag **must** match the `pyproject.toml` version.

```bash
git tag -a v0.1.1 -m "senrah 0.1.1"
git push origin v0.1.1
```

Pushing a `v*` tag triggers `.github/workflows/release.yml`:

1. **build** — `python -m build` produces an sdist + wheel, `twine check`
   validates them, the wheel is smoke-installed into a fresh venv and
   `senrah --version` is run to prove the entry point works, then the artifacts
   are uploaded.
2. **publish** — downloads the built artifacts and publishes them to TestPyPI
   via Trusted Publishing (OIDC). No API token is stored in the repository.

Verify the upload at: https://test.pypi.org/project/senrah/

## One-time setup: TestPyPI Trusted Publishing

Trusted Publishing must be configured once on TestPyPI before the first tag
push (there is no stored token to fall back on). On
https://test.pypi.org, add a **pending publisher** (Account → Publishing) with:

| Field             | Value                        |
|-------------------|------------------------------|
| PyPI Project Name | `senrah`                     |
| Owner             | `ivanovresearch`             |
| Repository name   | `senrah`                     |
| Workflow name     | `release.yml`                |
| Environment name  | `testpypi`                   |

The `environment: testpypi` and `permissions: id-token: write` in
`release.yml` are what authorize the exchange — they are already in place.

## Notes / future hardening

- **Production PyPI upload is manual/deferred** for v1.1. When promoting, repeat
  the Trusted Publishing setup on https://pypi.org and add a production publish
  job (or do a one-off `twine upload`).
- **Action pinning:** workflow actions are pinned to major-version tags
  (`@v4`, `@v5`, `@release/v1`). Pinning to full commit SHAs is a future
  supply-chain hardening step.
