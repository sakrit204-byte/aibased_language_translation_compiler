"""Orchestrates: source -> lexer -> parser -> semantic -> codegen -> C file.

Usage:
    python -m src.driver tests/programs/factorial.py -o out.c
    python -m src.driver tests/programs/factorial.py -o out.c --no-ai
"""

import argparse
import sys

from .lexer.lexer import Lexer, LexError
from .parser.parser import Parser, ParseError
from .semantic.type_checker import TypeChecker
from .semantic.symbol_table import SemanticError
from .codegen.emitter import Emitter
from .ai_assist import gate


def compile_source(source: str, use_ai: bool, log_path=None):
    tokens = Lexer(source).tokenize()
    ast = Parser(tokens).parse_program()
    TypeChecker().check(ast)

    ai_resolver = None
    if use_ai:
        ai_resolver = lambda call_node, args_str: gate.resolve(call_node, args_str, log_path)

    emitter = Emitter(ai_resolver=ai_resolver)
    c_code = emitter.emit_program(ast)
    stats = {"ai_calls_used": emitter.ai_calls_used, "total_ai_eligible_calls": emitter.total_call_sites}
    return c_code, stats


def _ast_to_dict(node):
    """Recursively convert AST nodes to plain dicts for GUI display."""
    import dataclasses
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        d = {"_type": type(node).__name__}
        for f in dataclasses.fields(node):
            d[f.name] = _ast_to_dict(getattr(node, f.name))
        return d
    if isinstance(node, list):
        return [_ast_to_dict(item) for item in node]
    if isinstance(node, tuple):
        return tuple(_ast_to_dict(item) for item in node)
    return node


def compile_source_detailed(source: str, use_ai: bool, model: str = None, log_path=None):
    """Like compile_source but returns intermediate artifacts for GUI visualization."""
    from .ai_assist import client as ai_client
    results = {"stages": {}}

    # Stage 1: Lexer
    try:
        tokens = Lexer(source).tokenize()
        results["stages"]["lexer"] = {
            "status": "ok",
            "token_count": len(tokens),
            "tokens": [(t.kind, t.value, t.line) for t in tokens],
        }
    except LexError as e:
        results["stages"]["lexer"] = {"status": "error", "error": str(e)}
        raise

    # Stage 2: Parser
    try:
        ast = Parser(tokens).parse_program()
        results["stages"]["parser"] = {
            "status": "ok",
            "ast": _ast_to_dict(ast),
            "function_count": len(ast.functions),
        }
    except ParseError as e:
        results["stages"]["parser"] = {"status": "error", "error": str(e)}
        raise

    # Stage 3: Semantic analysis
    try:
        symtab = TypeChecker().check(ast)
        results["stages"]["semantic"] = {
            "status": "ok",
            "functions": {
                name: {"params": info["params"], "return_type": info["return_type"]}
                for name, info in symtab.functions.items()
            },
        }
    except SemanticError as e:
        results["stages"]["semantic"] = {"status": "error", "error": str(e)}
        raise

    # Stage 4–5: Codegen + AI gate
    ai_resolver = None
    if use_ai:
        if model and model != ai_client.DEFAULT_MODEL:
            original_model = ai_client.DEFAULT_MODEL
            ai_client.DEFAULT_MODEL = model
        ai_resolver = lambda call_node, args_str: gate.resolve(call_node, args_str, log_path)

    try:
        emitter = Emitter(ai_resolver=ai_resolver)
        c_code = emitter.emit_program(ast)
        results["stages"]["codegen"] = {
            "status": "ok",
            "ai_calls_used": emitter.ai_calls_used,
            "total_call_sites": emitter.total_call_sites,
        }
        results["stages"]["ai_gate"] = {
            "status": "used" if emitter.ai_calls_used > 0 else "skipped",
            "calls": emitter.ai_calls_used,
        }
    except Exception as e:
        results["stages"]["codegen"] = {"status": "error", "error": str(e)}
        raise
    finally:
        if use_ai and model and model != ai_client.DEFAULT_MODEL:
            try:
                ai_client.DEFAULT_MODEL = original_model
            except NameError:
                pass

    results["c_code"] = c_code
    stats = {"ai_calls_used": emitter.ai_calls_used, "total_ai_eligible_calls": emitter.total_call_sites}
    return c_code, stats, results


def main():
    ap = argparse.ArgumentParser(description="Python-subset -> C hybrid compiler")
    ap.add_argument("source", help="input .py file (subset grammar)")
    ap.add_argument("-o", "--output", default="out.c", help="output .c file")
    ap.add_argument("--no-ai", action="store_true", help="disable AI-assist; fail hard on any gap")
    ap.add_argument("--ai-log", default="reports/ai_invocation_log.md")
    args = ap.parse_args()

    with open(args.source) as f:
        source = f.read()

    try:
        c_code, stats = compile_source(source, use_ai=not args.no_ai, log_path=args.ai_log)
    except (LexError, ParseError, SemanticError) as e:
        print(f"Compile error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "w") as f:
        f.write(c_code)

    print(f"Wrote {args.output}")
    print(f"AI-assist used on {stats['ai_calls_used']}/{stats['total_ai_eligible_calls']} eligible call site(s)")


if __name__ == "__main__":
    main()
