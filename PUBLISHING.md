# Publishing `inferenceci` to PyPI

The release workflow (`.github/workflows/release.yml`) builds and publishes
on every `v*` tag pushed to GitHub. It uses **PyPI Trusted Publishers**
(OIDC) — no API tokens stored in GitHub secrets.

## One-time setup

1. Create the project on PyPI:
   - Go to https://pypi.org/manage/account/publishing/
   - "Add a new pending publisher"
   - Fields:
     - PyPI Project Name: `inferenceci`
     - Owner: `raghavg27`
     - Repository name: `InferenceCI`
     - Workflow name: `release.yml`
     - Environment name: `pypi`
2. In the GitHub repo:
   - Settings → Environments → New environment → name `pypi`
   - (Optional) Require manual approval before deploying to `pypi`.

That's it. The first tag-triggered run claims the project name.

## Cutting a release

```bash
# 1. bump the version in pyproject.toml + src/inferenceci/__init__.py
$EDITOR pyproject.toml src/inferenceci/__init__.py

# 2. commit, tag, push
git add pyproject.toml src/inferenceci/__init__.py
git commit -m "chore: release v0.1.1"
git tag v0.1.1
git push origin main --tags
```

The workflow will:

1. Build a wheel + sdist from the tagged commit.
2. Verify `pyproject.toml` version matches the tag.
3. Publish to PyPI via OIDC.

## Manual fallback

If you ever need to publish from a laptop (you shouldn't):

```bash
pip install build twine
python -m build
twine check dist/*
twine upload dist/*           # requires a PyPI API token
```

## Versioning

[Semver](https://semver.org). Pre-1.0 we treat minor bumps as breaking.
