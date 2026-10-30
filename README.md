# Cognee GitGraph

A pull request that touches a shared helper can break services several call hops away; this shows which ones before you merge, deterministically and with no LLM.

Demo (2 min 29 s): [cognee-gitgraph-demo.mp4](assets/cognee-gitgraph-demo.mp4).

A Cognee community connector that loads a git repository into Cognee's graph: commits, file changes, Python symbols and references, and CI test failures from JUnit XML. From that data it computes the blast radius of a pull request: which functions call the code you changed, how many hops away they are, which top-level services they live in, and which CI failures belong to those services. The blast radius is plain graph traversal over the extracted rows, so the default path makes zero LLM calls and needs no API key. Loading into Cognee is optional and also runs without a key, on Cognee's local embedded stores, its local fastembed embedding model and, for its own graph extraction step, its local GLiNER model. On the very first load Cognee downloads those models and installs CPU-only PyTorch (about 200 MB), so that one run needs the network; the Docker image does this at build time.

## Quick start

```bash
bun run setup   # uv sync --python 3.12
bun run demo    # builds a small sample monorepo, blasts a branch, loads it into Cognee
```

You need [uv](https://docs.astral.sh/uv/) and [bun](https://bun.sh) (bun is only used to run the scripts in `package.json`). `bun run demo` prints this:

```text
Blast radius of main...feature/rotate-tokens (up to 4 hops)
Changed files (1): shared/auth.py
Changed symbols (1): shared/auth.py::verify_token

At-risk services:
  billing  1 hop  shared/auth.py::verify_token -> billing/invoice.py::charge
  notifications  4 hops  shared/auth.py::verify_token -> shared/session.py::load_session -> orders/cart.py::get_cart -> orders/checkout.py::checkout -> notifications/receipts.py::send_receipt
  orders  2 hops  shared/auth.py::verify_token -> shared/session.py::load_session -> orders/cart.py::get_cart
At-risk symbols: 5 in 5 files
Callers found by walking Cognee's graph

Co-changed files:
  billing/invoice.py  (1 shared commit)
  shared/__init__.py  (1 shared commit)

CI failures in at-risk services:
  billing-tests  billing  billing.test_invoice::test_charge_with_max_age  TypeError: charge() got an unexpected keyword argument 'max_age'
  orders-tests  orders  orders.test_checkout::test_checkout_rejects_bad_token  assert False is True

Cognee graph (dataset gitgraph-b73e5edafa07):
  ci_failures: 2
  commits: 8
  file_changes: 15
  references: 10
  symbols: 8
Links between rows:
  ambiguous: 0
  from_symbol_references_symbols: 5
  missing: 0
  sha_references_commits: 15
  to_symbol_references_symbols: 10
```

The demo uses a throwaway Cognee home in a temp directory and leaves nothing behind. Its commits use a fixed author and date, so the SHAs (and the dataset name) are the same on every run.

Which path does what: without `--remember`, `blast` walks the extracted rows in memory and never imports Cognee. With `--remember` (the demo always passes it), the rows go into Cognee, `link_rows` adds the edges between them, and the walk is then read back from Cognee's graph: the symbols and references are rebuilt from the row nodes and the `from_symbol` and `to_symbol` edges, and only history and CI failures still come from the rows. The `Callers found by walking ...` line says which path ran, and `--json` has it as `computed_from` (`rows` or `cognee graph`). If the graph walk and the row walk disagree, a warning goes to stderr.

## On a real repo

```bash
bun run real-repo
```

This clones [karpathy/nanochat](https://github.com/karpathy/nanochat) (MIT) into `out/` and blasts one real commit, [2f2d7ab](https://github.com/karpathy/nanochat/commit/2f2d7ab80cd07a8b5fab9feebcb185fd3ca37339) "fix: safe DDP cleanup (check initialized PG, not just env) (#256)", against its parent [91d76cc](https://github.com/karpathy/nanochat/commit/91d76cc690ac35a253651e886a4f0b34d745a232). That commit fixed `get_dist_info`, `compute_init` and `compute_cleanup` in `nanochat/common.py` and added two helpers they call, `is_ddp_requested` and `is_ddp_initialized`. The script runs `cognee-gitgraph blast --json` without `--remember` (so no Cognee), writes the result to `tests/fixtures/real_repo/blast.json` and prints the report. A test checks that this block is the report of that file:

```text
Blast radius of 91d76cc690ac35a253651e886a4f0b34d745a232...2f2d7ab80cd07a8b5fab9feebcb185fd3ca37339 (up to 4 hops)
Changed files (1): nanochat/common.py
Changed symbols (5): nanochat/common.py::compute_cleanup, nanochat/common.py::compute_init, nanochat/common.py::get_dist_info, nanochat/common.py::is_ddp_initialized, nanochat/common.py::is_ddp_requested

At-risk services:
  scripts  1 hop  nanochat/common.py::compute_cleanup -> scripts/base_eval.py::main
At-risk symbols: 8 in 15 files
Callers found by walking the extracted rows

Co-changed files:
  scripts/base_train.py  (5 shared commits)
  scripts/base_eval.py  (4 shared commits)
  nanochat/checkpoint_manager.py  (2 shared commits)
  nanochat/dataloader.py  (2 shared commits)
  nanochat/report.py  (2 shared commits)
  scripts/base_loss.py  (2 shared commits)
  tasks/spellingbee.py  (2 shared commits)
  dev/runcpu.sh  (1 shared commit)
  nanochat/engine.py  (1 shared commit)
  nanochat/gpt.py  (1 shared commit)
  ... and 11 more
```

How to read it:

- Changed symbols come from the diff's changed line ranges, matched against each symbol's first and last line: the three fixed functions plus the two helpers the commit added. The other functions and classes in `common.py` were not touched, so they are not listed.
- `scripts` is the only service at risk. Callers in `nanochat` itself sit in the same service as the change, so they are not a service of their own. Each service shows one shortest chain (fewest hops, then sorted ids).
- The JSON has the longer chains: `get_dist_info` -> `tokenizing_distributed_data_loader_with_state` -> `tokenizing_distributed_data_loader` in `nanochat/dataloader.py` is 2 hops, and so is `get_dist_info` -> `run_categorical_eval` -> `run_chat_eval` in `scripts/chat_eval.py`.
- A caller in module-level code shows up as its file, like `scripts/base_train.py`, not as a function. That is why there are more files (15) than symbols (8).
- Co-changed leaves out commits that touch more than 20 files, such as an initial import, since they say little about what changes together. The text shows the top 10; `--json` lists all 21.
- Calls the parser cannot resolve (see Limitations) are not followed, so a caller that reaches `common.py` only through one of them is not listed.

## Install as a package

```bash
uv pip install "git+https://github.com/chrisbuildsonline/cognee-gitgraph"
# or
pip install "git+https://github.com/chrisbuildsonline/cognee-gitgraph"
```

This installs the `cognee_community_connector_gitgraph` package and the `cognee-gitgraph` command. It needs Python 3.11 to 3.13 and pins `cognee==1.6.5`.

## SDK usage

```python
import asyncio
import cognee
from cognee_community_connector_gitgraph import blast_radius, gitgraph_source, link_rows

source = gitgraph_source("path/to/repo", ci_logs="path/to/junit-xml-dir")


async def main() -> None:
    result = await cognee.remember(source, dataset_name="gitgraph", self_improvement=False)
    print(result.status)
    print(await link_rows(source))  # row-to-row edges, e.g. {"sha_references_commits": 15, ...}


asyncio.run(main())
```

- `gitgraph_source(repo_path, refs=None, ci_logs=None, symbols_ref=None)` returns a dlt source named `gitgraph`. `refs` are the branches to read history from (default: all local branches). `symbols_ref` is the ref whose tree gives the symbols (default: the first of `refs`, else `HEAD`). `ci_logs` is a JUnit XML file, a directory of them, or a list of either; without it there is no `ci_failures` table. The repo, refs and CI paths are read when the source is built, so a bad one raises `ValueError` there.
- `await link_rows(source)` writes the row-to-row edges after `cognee.remember`. It returns the number of edges written per relationship, plus `"missing"` and `"ambiguous"` counts for edges it had to skip.
- `blast_radius(tables, changed, max_hops=4)` is a pure function. `tables` maps a table name to its rows (the same rows the source holds, for example from `extract_commits` and `extract_symbols` in the package modules `git_history` and `symbols`), and `changed` is a list of repo paths (every symbol in the file changes), `path:line` items or `path:start-end` line ranges (the innermost symbols the range overlaps). It returns JSON-serialisable data with `changed_files`, `changed_symbols`, `symbols`, `files`, `services`, `co_changed` and `ci_failures`, every list sorted.

A runnable version is in `examples/example.py` (`bun run example`). It remembers the sample repo in a temp Cognee home, prints the row count per table and the links written. Pass a repo path to use your own: `uv run python examples/example.py /path/to/repo --ci-logs /path/to/junit`.

## CLI usage

```bash
cognee-gitgraph blast --repo PATH --base REF --head REF [--ci FILE_OR_DIR]... [--hops N] [--json] [--remember] [--verbose] [--html PATH]
cognee-gitgraph demo [--json] [--verbose] [--html PATH]
```

- `--ci` may be repeated; each is a JUnit XML file or a directory of them.
- `--hops` is how many caller hops to follow (default 4).
- `--json` prints the result as JSON instead of the text report.
- `--remember` also loads the repo into Cognee and links the rows (see below). Cognee's own progress output is hidden unless `--verbose` is given, which sends it to stderr.
- `--html PATH` also writes the 3D page described below. The report on stdout does not change; `wrote PATH` goes to stderr.
- The diff is `base...head` (changes on `head` since it left `base`). Symbols are read from `head`; history is read from both refs. A symbol counts as changed when one of the diff's changed line ranges on `head` overlaps its lines.
- Co-changed files leave out commits that touch more than 20 files. The text report shows the top 10; `--json` lists them all.
- A bad repo, ref or CI path prints `error: ...` to stderr and exits with status 2.

From a checkout, `bun run cli -- blast --repo . --base main --head my-branch` runs the same command.

## 3D view

```bash
cognee-gitgraph demo --html blast.html
```

![The blast radius of the sample repo as a 3D graph](assets/blast-3d.png)

`--html` writes one self-contained page: a WebGL graph (three.js) with a node per symbol and an arrow from each caller to what it calls. The layout is flat and seen at an angle so the labels stay readable. Red nodes are the changed symbols, amber nodes are at risk and grey nodes are safe. Each node is labelled with its name, service and hop count; hover for the full id. A side panel lists the at-risk services with their hops and a colour legend. The 3d-force-graph library (MIT, with three.js bundled) is inlined from `cognee_community_connector_gitgraph/vendor/`, so the page makes no network request. Drag to rotate, scroll to zoom. `bun run screenshot` regenerates the picture above.

## What lands in Cognee

`gitgraph_source` has five tables. Each has a single `id` column as its primary key, and `id` is the first column of every row.

| Table | `id` | Other columns |
| --- | --- | --- |
| `commits` | commit sha | `sha`, `parents`, `author`, `author_email`, `date`, `message`, `refs` (lists are space-separated strings) |
| `file_changes` | `<sha>:<path>` | `sha`, `path`, `change_type`, `lines_added`, `lines_deleted`, `old_path` |
| `symbols` | `<file>::<qualified name>` | `kind` (function, class, method), `name`, `file`, `line`, `end_line` |
| `references` | `<file>:<owner>-><target>:<kind>` | `from_file`, `from_symbol`, `to_symbol`, `kind` (call, import), `line` |
| `ci_failures` | `<job>:<test id>` | `job`, `test_id`, `file`, `commit_sha`, `kind`, `message` |

`ci_failures` exists only when `ci_logs` is given.

Cognee links each row to its table node but, for this dlt source, to no other row. `link_rows` adds those edges, named `{column}_references_{table}` like Cognee's own foreign-key edges:

- `sha_references_commits`: file change to its commit
- `from_symbol_references_symbols`: reference to the symbol that makes it (none for an import at module level)
- `to_symbol_references_symbols`: reference to the symbol it points at
- `commit_sha_references_commits`: CI failure to its commit, when `commit_sha` is a commit id or the unique prefix of one

With `--remember`, the CLI uses one Cognee dataset per head commit, named `gitgraph-<first 12 characters of the head sha>`, so two heads never share a dataset.

## Re-sync and forget

Every table uses dlt's `replace` write disposition with `id` as the primary key. Remembering the same source twice adds no new row nodes (there is a test for it), and running `link_rows` again adds no new edges.

`cognee.remember` runs Cognee's improvement step by default (`self_improvement=True`). The example and the CLI pass `self_improvement=False` so the sync only loads the rows.

To drop a dataset, Cognee 1.6.5 has `cognee.forget`:

```python
# the dataset, its graph nodes and vectors
await cognee.forget(dataset="gitgraph")
# only the graph and vectors, keep raw data
await cognee.forget(dataset="gitgraph", memory_only=True)
```

## Testing

```bash
bun run test        # the full pytest suite
bun run lint        # ruff check and ruff format --check
bun run typecheck   # mypy
bun run e2e:install # once, for the Playwright test of the 3D page (needs chromium)
bun run demo:video  # out/cognee-gitgraph-demo.mp4: a narrated recording of the demo (needs the network for edge-tts, and ffmpeg)
```

The tests need no API key. They need the network only on the first run, for the local models described above (fastembed `BAAI/bge-small-en-v1.5`, GLiNER `fastino/gliner2.5-base-v1`, CPU-only PyTorch). The Docker image fetches all of them while it is built, which is why the suite and the demo run there with the network disabled.

## Run with Docker

```bash
docker build -t cognee-gitgraph .
docker run --rm --network none cognee-gitgraph
docker run --rm --network none cognee-gitgraph bun run demo
```

The image bakes in the models at build time, so both runs work with the network off (`--network none`). The default command runs the test suite; the second `docker run` runs the demo. Each has a script: `bun run docker:build`, `bun run docker:test` and `bun run docker:demo`.

## Mergetober contribution

This is the Path B proposal for Mergetober: a new connector, proposed in <https://github.com/topoteretes/cognee/issues/6329>. The package is proposed for `topoteretes/cognee-community` at `packages/connector/gitgraph`.

- Connector: <https://github.com/topoteretes/cognee-community/pull/560>
- Blast radius, CLI and 3D page: <https://github.com/topoteretes/cognee-community/pull/561> (merge 560 first)

A short write-up of how it works on the sample repo: [What breaks if I change verify_token?](blog/post.md), published at <https://dev.to/chrisbuildsonline/what-breaks-if-i-change-verifytoken-1nod>.

## Stack

Python 3.12, cognee 1.6.5, dlt, GitPython, NetworkX and the standard library `ast` module. Python files only: there is no Tree-sitter and there is no web server.

## Limitations

- Symbols come from Python files only. Other languages are not parsed.
- Only top-level functions, top-level classes and their direct methods become symbols; nested functions and classes are skipped. Calls are resolved by name through imports, plus `self.m()`/`cls.m()` inside a class, `Cls.m()`, and `obj.m()` when every assignment to `obj` in the same function is `Cls(...)` of one class. Attribute chains like `self.client.m()`, dynamic calls and type inference across functions are not seen.
- A symbol spans its `def` or `class` line to its last line, so an edit that only touches a decorator marks no symbol as changed. A plain `path:line` item given to `blast_radius` still maps to the nearest symbol that starts at or before the line.
- `link_rows` reads the whole graph to find the row nodes, so it gets slower as the graph grows. A row key claimed by more than one node is not linked and is counted as `ambiguous`.
- Merge commits produce no `file_changes` rows.

## Links and license

Source: <https://github.com/chrisbuildsonline/cognee-gitgraph>

Apache-2.0, same as Cognee. See `LICENSE`.
