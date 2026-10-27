"""Git history, Python symbols and CI failures as a cognee data-source connector.

Cognee remembers documents, but a codebase's most useful memory is the history
around the code: who changed what, which symbols a commit touched, and which CI
failures followed. This package turns that history into a dlt source cognee can
ingest, and computes the blast radius of a pull request from the resulting graph.
"""

__version__ = "0.1.0"
