#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2023/5/25 10:20
@Author  : alexanderwu
@File    : __init__.py
"""

try:
    from alpha.document_store.faiss_store import FaissStore  # noqa: F401
except Exception:
    # Optional dependency (langchain, faiss, etc.). Ignore if not needed.
    FaissStore = None  # type: ignore

__all__ = ["FaissStore"]
