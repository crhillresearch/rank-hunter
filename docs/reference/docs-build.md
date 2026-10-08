# Documentation Build

Rank Hunter documentation is built with MkDocs Material.

Configuration lives in `mkdocs.yml`; documentation source lives under `docs/`.

## Install documentation dependencies

Use a Python environment with MkDocs Material installed.

A minimal setup is:

```bash
python -m pip install -r requirements-docs.txt
```

The documentation dependencies are declared in `requirements-docs.txt`.

## Local preview

From the repository root:

```bash
mkdocs serve
```

Open the local URL printed by MkDocs.

The development server reloads when Markdown/config changes.

## Strict build

Before merging documentation changes:

```bash
mkdocs build --strict
```

Strict mode is important because it catches broken internal links and navigation problems that a browser preview may not make obvious.

## Navigation

The left navigation is explicitly maintained in `mkdocs.yml`.

When adding a page:

1. create the Markdown file under `docs/`;
2. add it to `nav:` unless it is intentionally unlisted;
3. add links from related pages;
4. run a strict build.

## Markdown extensions

The current site enables:

- admonitions;
- tables;
- attributes;
- Markdown in HTML;
- syntax highlighting;
- inline highlighting;
- superfences;
- tabbed blocks;
- linked table-of-contents anchors.

Use standard Markdown unless an extension materially improves readability.

## Math

The docs use Markdown/LaTeX-style delimiters for formulas.

When changing the site renderer, verify that mathematical notation still renders in the deployed environment.

Do not replace exact formulas with screenshots.

## Code examples

Commands should be copy/pasteable and should specify:

- working directory assumptions;
- `--db` when relevant;
- Sage-capable Python when scientific code is required;
- placeholders in obvious uppercase or descriptive form.

Prefer:

```bash
sage -python -m rank42.cli rank-bounds \
  --db rank42.db \
  --curve-id 123
```

over an unexplained fragment.

## Version accuracy

Documentation must match the release branch.

Before documenting a CLI flag or Pipeline stage:

- check the parser/catalog in the same branch;
- do not copy a command from a development chat or older release;
- use `--help` when validating examples.

## Scientific wording

Documentation is part of the proof boundary.

Use:

- “rank ≥ 17” for lower-bound-only results;
- “timeout/inconclusive” for unfinished computation;
- “heuristic” for ranking signals;
- “exact rank” only when lower and upper meet.

Do not simplify these distinctions for marketing prose.

## Link/style checks

Before merge:

```bash
mkdocs build --strict
```

Also read the generated page at mobile/narrow width for large tables and code blocks.

## Deployment to docs.rankhunter.net

Documentation is deployed from the **public** `crhillresearch/rank-hunter` repository, branch `main`. The `.github/workflows/docs.yml` workflow builds the site strictly, then publishes it to GitHub Pages. It does **not** deploy from the development repository.

For the initial public release:

1. Open `crhillresearch/rank-hunter` → **Settings → Pages**.
2. Select **GitHub Actions** as the Pages deployment source.
3. Set **Custom domain** to `docs.rankhunter.net`.
4. In Namecheap Advanced DNS add a `CNAME` record: host `docs`, value `crhillresearch.github.io`.
5. Let GitHub complete domain verification and HTTPS certificate issuance.
6. The first public `main` push containing `docs/`, `mkdocs.yml`, and the workflow will build/deploy automatically. Later documentation changes trigger redeployment. You can also run the **Documentation** workflow manually.

`docs/CNAME` and `mkdocs.yml` both declare `docs.rankhunter.net`. Keep them aligned.

The marketing site at `rankhunter.net` is a separate GitHub Pages deployment from `crhillresearch/rh-website`.
