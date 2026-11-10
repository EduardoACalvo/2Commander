"""Checks that every text marked with _("...") or _c("context", "...") has a translation in each locale file.

Usage:
    python tools/check_translations.py                 check all locales against all source files
    python tools/check_translations.py --dump FILE...  print the marked texts of FILEs as a JSON
                                                       skeleton (Spanish -> "") for translating

Reports, per locale: missing translations, translations whose {placeholders} differ from the
source text, and entries that are no longer used. It also flags _() calls whose argument is not
a plain string literal (f-strings or variables cannot be looked up). Exit code 1 on problems.
Keys of _c("context", "text") calls are written "context|text" in the locale files.
"""
import ast
import json
import os
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALE_DIR = os.path.join(ROOT, "fm", "locales")
SOURCES = [os.path.join(ROOT, "main.py")] + [
    os.path.join(ROOT, "fm", f) for f in sorted(os.listdir(os.path.join(ROOT, "fm")))
    if f.endswith(".py")]


def marked_texts(path):
    """[(text, line)] for each _("...") call, and [(line, problem)] for bad calls."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    found, bad = [], []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        lits = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if node.func.id == "_":
            if len(node.args) == 1 and len(lits) == 1:
                found.append((lits[0], node.lineno))
            else:
                bad.append((node.lineno, "argument of _() is not a plain string literal"))
        elif node.func.id == "_c":   # _c(context, text) -> key "context|text"
            if len(node.args) == 2 and len(lits) == 2:
                found.append((lits[0] + "|" + lits[1], node.lineno))
            else:
                bad.append((node.lineno, "arguments of _c() are not plain string literals"))
    return found, bad


def placeholders(s):
    try:
        return sorted({f for _t, f, _s, _c in string.Formatter().parse(s) if f is not None})
    except ValueError:
        return ["<invalid format string>"]


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if argv[:1] == ["--dump"]:
        out = {}
        for p in argv[1:]:
            for text, _line in marked_texts(p)[0]:
                out.setdefault(text, "")
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    keys, problems = {}, 0
    for p in SOURCES:
        found, bad = marked_texts(p)
        rel = os.path.relpath(p, ROOT)
        for text, line in found:
            keys.setdefault(text, f"{rel}:{line}")
        for line, msg in bad:
            print(f"{rel}:{line}: {msg}")
            problems += 1
    print(f"{len(keys)} distinct marked texts in {len(SOURCES)} files")

    for fn in sorted(os.listdir(LOCALE_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(LOCALE_DIR, fn), encoding="utf-8") as f:
            table = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        missing = [k for k in keys if not table.get(k)]
        unused = [k for k in table if k not in keys]
        mismatch = [k for k in keys if table.get(k) and placeholders(k) != placeholders(table[k])]
        print(f"\n{fn}: {len(table)} entries, {len(missing)} missing, "
              f"{len(mismatch)} placeholder mismatches, {len(unused)} unused")
        for k in missing:
            print(f"  missing   {keys[k]}: {k!r}")
        for k in mismatch:
            print(f"  mismatch  {keys[k]}: {k!r} -> {table[k]!r}")
        for k in unused:
            print(f"  unused    {k!r}")
        problems += len(missing) + len(mismatch)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
