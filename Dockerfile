# Base: the Playwright noble image (Ubuntu 24.04, Python 3.12 from apt) from mcr.microsoft.com,
# since docker.io is not reachable on every host; it also has the browsers a later e2e needs.
FROM mcr.microsoft.com/playwright:v1.49.1-noble

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git unzip python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/root PATH=/root/.local/bin:/root/.bun/bin:$PATH
RUN curl -LsSf https://astral.sh/uv/0.11.29/install.sh | sh
RUN curl -fsSL https://bun.sh/install | bash -s "bun-v1.4.2"

WORKDIR /app
ENV UV_PYTHON_PREFERENCE=only-system UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy

# Dependencies first, so editing the code does not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

# cognee embeds with this local model when no API key is set. Fetch it now so nothing needs the
# network at run time.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/fastembed')"

# cognee also counts tokens with tiktoken's cl100k_base, which it downloads on first use.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken
RUN .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# With no LLM key, cognee extracts the graph with a local GLiNER model. Its runtime (CPU-only
# torch + gliner2) is not in uv.lock: cognee installs it on first use, and so does the model
# download. Do both now, with cognee's own installer, so a run with no network works.
RUN .venv/bin/python -c "from cognee.tasks.graph.gliner_demo.install import install_gliner_runtime as i; i('https://download.pytorch.org/whl/cpu')"
RUN .venv/bin/python -c "from gliner2 import AutoExtractor; AutoExtractor.from_pretrained('fastino/gliner2.5-base-v1')"

COPY . .
# --inexact keeps the GLiNER runtime installed above, which is not in uv.lock.
RUN uv sync --frozen --inexact

# Everything is installed: no downloads and no re-locking from here on.
ENV UV_FROZEN=1 UV_OFFLINE=1 HF_HUB_OFFLINE=1
CMD ["bun", "run", "test"]
