# -*- coding: utf-8 -*-
"""CLI работы с файлами проекта (docs/Final_task.md, задача 4).

Примеры:
    python files_cli.py find "RAG_MIN_SCORE" --root G:\\9potok\\test1310
    python files_cli.py docs --root G:\\9potok\\test1310\\versions\\1210 `
        --base HEAD~1 --head HEAD --model DeepSeek-flash
    python files_cli.py docs --root ... --apply --model Local
    python files_cli.py new adr "Выбор SQLite для RAG" --root ... --apply
    python files_cli.py new changelog "последние изменения" --root ...

По умолчанию изменения только показываются diff'ом. Запись — с --apply.
"""
import argparse
import io
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from rtk_app import file_tasks  # noqa: E402


def _caller(args):
    return file_tasks.default_caller(
        args.model, max_tokens=args.max_tokens,
        local_url=args.local_url, local_model=args.local_model,
        local_label=args.local_label)


def _print_result(result, out=""):
    text = str(result.get("text") or "")
    if not text:
        text = "Ошибка: %s" % (result.get("error") or "неизвестная ошибка")
    if out:
        with io.open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("Записано в %s" % out)
    else:
        print(text)
    return 0 if result.get("ok") else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Работа с файлами проекта: поиск, docs, новый файл")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find", help="поиск использований по файлам")
    p_find.add_argument("query", help="строка или regex")
    p_find.add_argument("--root", default=".", help="корень проекта")
    p_find.add_argument("--subdir", default="", help="подпапка")
    p_find.add_argument("--ext", default="", help="расширения через запятую")
    p_find.add_argument("--regex", action="store_true", help="regex-поиск")
    p_find.add_argument("--max-results", type=int, default=200)
    p_find.add_argument("--out", default="", help="записать текст в файл")

    p_docs = sub.add_parser("docs", help="обновить документацию по diff")
    p_docs.add_argument("--root", default=".", help="корень проекта")
    p_docs.add_argument("--base", default="HEAD~1")
    p_docs.add_argument("--head", default="HEAD")
    p_docs.add_argument("--model", default="")
    p_docs.add_argument("--local-url", default="")
    p_docs.add_argument("--local-model", default="")
    p_docs.add_argument("--local-label", default="")
    p_docs.add_argument("--max-tokens", type=int, default=4000)
    p_docs.add_argument("--top-k", type=int, default=8)
    p_docs.add_argument("--no-rag", action="store_true")
    p_docs.add_argument("--apply", action="store_true",
                        help="записать изменения (по умолчанию только diff)")
    p_docs.add_argument("--out", default="", help="записать отчёт в файл")

    p_new = sub.add_parser("new", help="создать ADR/CHANGELOG/README")
    p_new.add_argument("kind", nargs="?", default="adr")
    p_new.add_argument("topic", nargs="?", default="")
    p_new.add_argument("--root", default=".", help="корень проекта")
    p_new.add_argument("--path", default="", help="явный путь файла")
    p_new.add_argument("--model", default="")
    p_new.add_argument("--local-url", default="")
    p_new.add_argument("--local-model", default="")
    p_new.add_argument("--local-label", default="")
    p_new.add_argument("--max-tokens", type=int, default=4000)
    p_new.add_argument("--top-k", type=int, default=8)
    p_new.add_argument("--no-rag", action="store_true")
    p_new.add_argument("--apply", action="store_true",
                       help="записать файл (по умолчанию только diff)")
    p_new.add_argument("--out", default="", help="записать отчёт в файл")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "find":
            result = file_tasks.run_find(
                args.root, args.query, subdir=args.subdir,
                exts=tuple(x.strip() for x in args.ext.split(",")
                           if x.strip()),
                regex=args.regex, max_results=args.max_results)
        elif args.cmd == "docs":
            result = file_tasks.run_update_docs(
                args.root, base=args.base, head=args.head, model=args.model,
                caller=_caller(args), apply=args.apply,
                use_rag=not args.no_rag, top_k=args.top_k)
        else:
            result = file_tasks.run_generate_file(
                args.root, kind=args.kind, topic=args.topic,
                model=args.model, caller=_caller(args), apply=args.apply,
                use_rag=not args.no_rag, path=args.path, top_k=args.top_k)
    except Exception as exc:                     # noqa: BLE001
        print("Ошибка: %s" % exc)
        return 1
    return _print_result(result, out=getattr(args, "out", ""))


if __name__ == "__main__":
    sys.exit(main())
