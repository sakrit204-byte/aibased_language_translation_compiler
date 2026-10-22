"""
Orchestrates: source -> lexer -> parser -> semantic analysis -> codegen -> C.

    python -m src.driver tests/programs/factorial.py -o out.c
    python -m src.driver tests/programs/factorial.py --run
    python -m src.driver program.py -o out.c --ai        # opt in to AI-assist

AI-assist is **off by default**. The deterministic core translates every
construct in the grammar and every builtin on its own; AI is only consulted for
a call to a name the compiler does not know, and only when you ask for it.
"""

import argparse
import os
import subprocess
import sys
import tempfile

from .ai_assist import client as ai_client
from .ai_assist.gate import AIAssistError, Gate
from .codegen.emitter import Emitter
from .codegen.rules import CodegenError
from .lexer.lexer import LexError, Lexer
from .parser.parser import ParseError, Parser
from .semantic.symbol_table import SemanticError
from .semantic.type_checker import TypeChecker
from .toolchain import (
    ToolchainError, compile_command, exe_suffix, find_c_compiler, install_hint, run_env,
)

DEFAULT_AI_LOG = os.path.join("reports", "ai_invocation_log.md")
DEFAULT_AI_CACHE = os.path.join("reports", "ai_cache.json")

# Errors that mean "the input program is wrong", as opposed to a compiler bug.
CompileError = (LexError, ParseError, SemanticError, CodegenError, AIAssistError)


def compile_source(source, use_ai=False, model=None, log_path=None, cache_path=None):
    """Translate `source` to C. Returns (c_code, stats)."""
    c_code, stats, _ = compile_source_detailed(
        source, use_ai=use_ai, model=model, log_path=log_path, cache_path=cache_path
    )
    return c_code, stats


def compile_source_detailed(source, use_ai=False, model=None, log_path=None,
                            cache_path=None):
    """Like compile_source, but also returns per-stage artifacts for the GUI.

    On failure the exception propagates, and `results["stages"]` holds whatever
    completed — enough for the pipeline view to show where it stopped.
    """
    results = {"stages": {}}

    # -- 1. lexical analysis ------------------------------------------------
    try:
        tokens = Lexer(source).tokenize()
    except LexError as e:
        results["stages"]["lexer"] = {"status": "error", "error": str(e)}
        raise
    results["stages"]["lexer"] = {
        "status": "ok",
        "token_count": len(tokens),
        "tokens": [(t.kind, t.value, t.line) for t in tokens],
    }

    # -- 2. parsing ---------------------------------------------------------
    try:
        ast = Parser(tokens).parse_program()
    except ParseError as e:
        results["stages"]["parser"] = {"status": "error", "error": str(e)}
        raise
    results["stages"]["parser"] = {
        "status": "ok",
        "ast": ast_to_dict(ast),
        "function_count": len(ast.functions),
    }

    # -- 3. semantic analysis ----------------------------------------------
    checker = TypeChecker(ai_enabled=use_ai)
    try:
        symtab = checker.check(ast)
    except SemanticError as e:
        results["stages"]["semantic"] = {"status": "error", "error": str(e)}
        raise
    results["stages"]["semantic"] = {
        "status": "ok",
        "functions": {
            name: {"params": info["params"], "return_type": info["return_type"]}
            for name, info in symtab.functions.items()
        },
        "locals": {name: dict(v) for name, v in symtab.function_locals.items()},
        "call_sites": checker.call_sites,
        "ai_eligible_calls": len(checker.ai_calls),
    }

    # -- 4/5. codegen, with the AI gate wired in only if it was requested ---
    gate = None
    resolver = None
    if use_ai:
        gate = Gate(model=model, log_path=log_path, cache_path=cache_path)
        resolver = gate.resolve

    emitter = Emitter(symtab, ai_resolver=resolver, profiles=checker.profiles,
                      class_info=checker.classes,
                      global_dicts=checker.global_dicts)
    try:
        c_code = emitter.emit_program(ast)
    except (CodegenError, AIAssistError) as e:
        results["stages"]["codegen"] = {"status": "error", "error": str(e)}
        if gate is not None:
            results["stages"]["ai_gate"] = {"status": "error", "error": str(e),
                                            "records": gate.records}
        raise

    results["stages"]["codegen"] = {
        "status": "ok",
        "runtime_helpers": sorted(emitter.needs),
        "ai_call_sites": emitter.ai_call_sites,
        "total_call_sites": checker.call_sites,
    }
    results["stages"]["ai_gate"] = _gate_stage(use_ai, gate, emitter)
    results["c_code"] = c_code
    results["profiles"] = [p.name for p in checker.profiles]
    results["link"] = link_requirements(checker.profiles)

    stats = {
        "imports": [p.name for p in checker.profiles],
        "ai_calls_used": emitter.ai_call_sites,
        "total_call_sites": checker.call_sites,
        "ai_enabled": use_ai,
        "ai_queries": gate.queries_made if gate else 0,
        "ai_cache_hits": gate.cache_hits if gate else 0,
        "runtime_helpers": len(emitter.needs),
    }
    return c_code, stats, results


def link_requirements(profiles):
    """The extra .c files and linker flags the activated profiles need.

    Paths are resolved against the project root so this works regardless of
    the caller's working directory.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sources, flags = [], []
    for profile in profiles:
        for rel in profile.link_sources:
            path = os.path.join(root, rel.replace("/", os.sep))
            if path not in sources:
                sources.append(path)
        for flag in profile.link_flags:
            if flag not in flags:
                flags.append(flag)
    return {"sources": sources, "flags": flags}


def _gate_stage(use_ai, gate, emitter):
    if not use_ai:
        return {"status": "disabled",
                "detail": "AI-assist was not enabled; every construct had a static rule."}
    if emitter.ai_call_sites == 0:
        return {"status": "skipped",
                "detail": "AI-assist was enabled but never needed."}
    return {"status": "used", "calls": emitter.ai_call_sites,
            "queries": gate.queries_made, "cache_hits": gate.cache_hits,
            "records": gate.records}


def ast_to_dict(node):
    """Recursively convert AST nodes to plain dicts for display."""
    import dataclasses

    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        d = {"_type": type(node).__name__}
        for f in dataclasses.fields(node):
            value = getattr(node, f.name)
            if value is None and f.name in ("inferred_type", "declared_type",
                                            "expanded", "ai_expr", "docstring", "kind"):
                continue
            d[f.name] = ast_to_dict(value)
        return d
    if isinstance(node, list):
        return [ast_to_dict(item) for item in node]
    if isinstance(node, tuple):
        return [ast_to_dict(item) for item in node]
    return node


# --------------------------------------------------------------------- build

def build_and_run(c_code, argv=(), timeout=30, link=None):
    """Compile `c_code` with the detected C compiler and run it.

    Returns (returncode, stdout, stderr). Raises ToolchainError if there is no
    C compiler installed.
    """
    cc = find_c_compiler()
    if cc is None:
        raise ToolchainError(install_hint())

    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, "prog.c")
        bin_path = os.path.join(tmp, "prog" + exe_suffix())
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(c_code)
        env = run_env(cc)
        build = subprocess.run(
            compile_command(cc, c_path, bin_path,
                            extra_sources=(link or {}).get("sources", ()),
                            extra_flags=(link or {}).get("flags", ())),
            capture_output=True, text=True, cwd=tmp, timeout=timeout, env=env,
        )
        if build.returncode != 0:
            detail = build.stderr or build.stdout or "(no diagnostics)"
            return build.returncode, build.stdout, f"{cc} failed:\n{detail}"
        run = subprocess.run(
            [bin_path, *argv], capture_output=True, text=True, timeout=timeout, env=env,
        )
        return run.returncode, run.stdout, run.stderr


# ----------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Hybrid Python-subset -> C compiler",
        epilog="AI-assist is off by default; the deterministic core handles the "
               "whole language on its own.",
    )
    ap.add_argument("source", help="input .py file (see grammar/subset.ebnf)")
    ap.add_argument("-o", "--output", help="write C to this file (default: <source>.c)")
    ap.add_argument("--ai", action="store_true",
                    help="enable AI-assist for calls with no static rule")
    ap.add_argument("--no-ai", action="store_true",
                    help="explicitly disable AI-assist (the default)")
    ap.add_argument("--model", default=ai_client.DEFAULT_MODEL,
                    help=f"Ollama model for AI-assist (default: {ai_client.DEFAULT_MODEL})")
    ap.add_argument("--ai-log", default=DEFAULT_AI_LOG, help="AI invocation log path")
    ap.add_argument("--ai-cache", default=DEFAULT_AI_CACHE,
                    help="verified-translation cache path")
    ap.add_argument("--simplify", action="store_true",
                    help="after translating, ask the model to simplify the C and "
                         "keep its answer only if both versions produce identical "
                         "output")
    ap.add_argument("--run", action="store_true",
                    help="build the C with the detected compiler and run it")
    ap.add_argument("--emit-stdout", action="store_true", help="print the C instead of writing it")
    ap.add_argument("--check", action="store_true",
                    help="survey everything in the file this subset cannot "
                         "translate, instead of stopping at the first one")
    ap.add_argument("--repair", action="store_true",
                    help="if translation fails, ask the model to rewrite the "
                         "program into the subset, and keep its answer only if "
                         "the compiled result prints exactly what the original "
                         "prints under CPython")
    ap.add_argument("--repair-out",
                    help="where to write an accepted repair "
                         "(default: <source>.repaired.py)")
    args = ap.parse_args(argv)

    if args.ai and args.no_ai:
        ap.error("--ai and --no-ai are mutually exclusive")
    use_ai = args.ai and not args.no_ai

    try:
        with open(args.source, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        print(f"error: cannot read {args.source}: {e}", file=sys.stderr)
        return 2

    if args.check:
        from .diagnose import report
        print(report(source, args.source))
        return 0

    try:
        c_code, stats, results = compile_source_detailed(
            source, use_ai=use_ai, model=args.model,
            log_path=args.ai_log, cache_path=args.ai_cache,
        )
    except CompileError as e:
        print(f"{args.source}: {e}", file=sys.stderr)
        if not args.repair:
            return 1
        source, c_code, stats, results = _repair(args, source, str(e))
        if source is None:
            return 1

    if args.simplify:
        from .ai_assist.simplify import simplify
        before = c_code.count("\n")
        c_code, report = simplify(c_code, model=args.model, log_path=args.ai_log)
        if report["accepted"]:
            print(f"simplified {before} -> {report['simplified_lines']} lines; "
                  f"{report['reason']}")
        else:
            print(f"simplification rejected, original kept: {report['reason']}",
                  file=sys.stderr)

    if args.emit_stdout:
        print(c_code)
    else:
        out = args.output or os.path.splitext(args.source)[0] + ".c"
        with open(out, "w", encoding="utf-8") as f:
            f.write(c_code)
        print(f"wrote {out}")

    print(
        f"call sites: {stats['total_call_sites']}   "
        f"AI-resolved: {stats['ai_calls_used']}   "
        f"model queries: {stats['ai_queries']}"
        + ("" if use_ai else "   (AI-assist disabled)")
    )
    if stats.get("imports"):
        print(f"linking shims: {', '.join(stats['imports'])}")

    if args.run:
        try:
            code, out_text, err_text = build_and_run(
                c_code, link=results.get("link"))
        except ToolchainError as e:
            print(f"\ncannot run: {e}", file=sys.stderr)
            return 3
        sys.stdout.write(out_text)
        sys.stderr.write(err_text)
        return code
    return 0


#: The same messages are shown in the IDE, where the real punctuation reads
#: better, and on a Windows console, where cp1252 cannot encode it and Python
#: falls back to printing `→` in the middle of a sentence.
_PUNCTUATION = {"…": "...", "—": "-", "–": "-", "→": "->",
                "‘": "'", "’": "'", "“": '"', "”": '"'}


def _plain(text):
    """`text` with typographic punctuation replaced, for a console that may
    not be able to encode it."""
    for fancy, plain in _PUNCTUATION.items():
        text = text.replace(fancy, plain)
    try:
        encoding = sys.stderr.encoding or "utf-8"
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        text = text.encode("ascii", "replace").decode("ascii")
    return text


def _repair(args, source, error):
    """Run the source-level repair after a failed translation.

    Returns (repaired_source, c_code, stats, results), or four Nones when
    nothing was accepted. The repaired program is written out so the diff is
    reviewable rather than something the compiler quietly used.
    """
    from .ai_assist.repair import repair as repair_source

    def progress(stage, text, ok):
        mark = {True: "  ok ", False: "  no ", None: "  .. "}[ok]
        print(f"{mark}{stage}: {_plain(text)}", file=sys.stderr)

    print("\nattempting an AI repair of the source\n", file=sys.stderr)
    repaired, report = repair_source(
        source, error=error, model=args.model, log_path=args.ai_log,
        progress=progress,
    )
    if not repaired:
        print(_plain(f"\nno repair accepted: {report['reason']}"), file=sys.stderr)
        return None, None, None, None

    out = args.repair_out or os.path.splitext(args.source)[0] + ".repaired.py"
    with open(out, "w", encoding="utf-8") as f:
        f.write(repaired)

    import difflib
    print("\n--- what changed ---", file=sys.stderr)
    for line in difflib.unified_diff(
            source.splitlines(), repaired.splitlines(),
            fromfile=args.source, tofile=out, lineterm="", n=2):
        print(line, file=sys.stderr)
    print("\n--- what was compared ---", file=sys.stderr)
    reference = report.get("reference") or {}
    print(f"target: {reference.get('bytes')} bytes, exit {reference.get('exit')}, "
          f"sha256 {str(reference.get('sha256', ''))[:16]}...", file=sys.stderr)
    for label, detail in report.get("checks", ()):
        print(f"  {label:<38} {detail}", file=sys.stderr)
    print(_plain(f"\nrepair accepted: {report['reason']}"), file=sys.stderr)
    print(f"wrote {out}", file=sys.stderr)

    c_code, stats, results = compile_source_detailed(repaired)
    return repaired, c_code, stats, results


if __name__ == "__main__":
    sys.exit(main())
