"""Deterministic file-sync tool for HiClaw workers.

Provides pull / push / stat / list operations on shared MinIO paths,
exposed as a CLI that any worker runtime can call.

Unlike the built-in ``file-sync`` SKILL.md (which is prompt-based
documentation telling the LLM how to run ``mc mirror``), this is a
deterministic tool with structured input/output.
"""

from hiclaw_filesync.cli import main

__all__ = ["main"]
