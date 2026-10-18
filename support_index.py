# -*- coding: utf-8 -*-
"""Индексация FAQ/документации поддержки в RAG (задача 3).

Запуск:
    python support_index.py

Индексирует docs/support (или SUPPORT_DOCS_DIR) в общий session/rag_index.db.
Поиск /support ограничен этим каталогом, поэтому FAQ не смешивается с кодом
других проектов.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from rtk_app import support as support_mod  # noqa: E402
from rtk_app.rag import RagStore  # noqa: E402


def build_index(docs_dir=None, verbose=True):
    """Строит RAG-индекс по FAQ поддержки; возвращает отчёт индексатора."""
    target = os.path.abspath(str(docs_dir or support_mod.docs_dir()))
    if not os.path.isdir(target):
        raise SystemExit("Папка FAQ не найдена: %s" % target)
    report = RagStore().index_docs(docs_dir=target, verbose=verbose)
    print("[support] индекс: files=%s chunks=%s errors=%s"
          % (report.get("files"), report.get("chunks"),
             report.get("errors")))
    print("[support] каталог: %s" % target)
    return report


if __name__ == "__main__":
    result = build_index()
    raise SystemExit(0 if result.get("ok") else 1)
