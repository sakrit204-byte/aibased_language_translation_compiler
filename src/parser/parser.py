"""Recursive-descent parser: token stream -> AST.

Operator precedence follows CPython's, lowest to highest:

    or  <  and  <  not  <  comparison  <  + -  <  * / // %  <  unary  <  **

Two details that are easy to get wrong and are handled explicitly here:
  * `**` is right-associative and binds *tighter* than a unary minus on its
    left, so `-2 ** 2` is `-(2 ** 2)` = -4, not `(-2) ** 2` = 4.
  * Its right operand may itself be unary, so `2 ** -1` parses.
"""

from . import ast_nodes as A
from ..lexer.tokens import TYPE_NAMES


class ParseError(Exception):
    pass


# Statement-level augmented assignment operators.
AUG_OPS = {
    "PLUS_ASSIGN": "+", "MINUS_ASSIGN": "-", "STAR_ASSIGN": "*",
    "SLASH_ASSIGN": "/", "FLOORDIV_ASSIGN": "//", "PERCENT_ASSIGN": "%",
    "POW_ASSIGN": "**",
}

CMP_OPS = {"EQ": "==", "NE": "!=", "LT": "<", "GT": ">", "LE": "<=", "GE": ">="}

ADD_OPS = {"PLUS": "+", "MINUS": "-"}
MUL_OPS = {"STAR": "*", "SLASH": "/", "FLOORDIV": "//", "PERCENT": "%"}


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    # ---- token helpers ----

    def cur(self):
        return self.tokens[self.pos]

    def nxt(self, offset=1):
        i = self.pos + offset
        return self.tokens[i] if i < len(self.tokens) else self.tokens[-1]

    def at(self, kind, value=None):
        t = self.cur()
        if t.kind != kind:
            return False
        return value is None or t.value == value

    def at_kw(self, word):
        return self.at("KEYWORD", word)

    def at_name(self, word):
        return self.at("NAME", word)

    def eat(self, kind, value=None):
        if not self.at(kind, value):
            t = self.cur()
            expected = kind + (f" {value!r}" if value else "")
            raise ParseError(
                f"line {t.line}: expected {expected}, got {t.kind} {t.value!r}"
            )
        t = self.cur()
        self.pos += 1
        return t

    def take(self):
        t = self.cur()
        self.pos += 1
        return t

    # ---- top level ----

    def parse_program(self):
        funcs = []
        while not self.at("EOF"):
            if self.at("NEWLINE"):        # stray blank logical line
                self.take()
                continue
            if not self.at_kw("def"):
                t = self.cur()
                raise ParseError(
                    f"line {t.line}: only function definitions are allowed at module level "
                    f"in this subset, found {t.kind} {t.value!r}. Move top-level code into "
                    f"a function (for example `def main() -> int:`)."
                )
            funcs.append(self.parse_function())
        if not funcs:
            raise ParseError("empty program: define at least one function")
        return A.Program(funcs)

    def parse_function(self):
        line = self.eat("KEYWORD", "def").line
        name = self.eat("NAME").value
        self.eat("LPAREN")
        params = []
        seen = set()
        if not self.at("RPAREN"):
            params.append(self.parse_param(seen))
            while self.at("COMMA"):
                self.eat("COMMA")
                params.append(self.parse_param(seen))
        self.eat("RPAREN")
        if not self.at("ARROW"):
            t = self.cur()
            raise ParseError(
                f"line {t.line}: function '{name}' needs an explicit return-type annotation, "
                f"e.g. `def {name}(...) -> int:`. This subset has no type inference across "
                f"function boundaries."
            )
        self.eat("ARROW")
        ret_type = self.parse_type()
        self.eat("COLON")
        body, docstring = self.parse_block(allow_docstring=True)
        return A.FunctionDef(name, params, ret_type, body, line, docstring)

    def parse_param(self, seen):
        tok = self.eat("NAME")
        if tok.value in seen:
            raise ParseError(f"line {tok.line}: duplicate parameter '{tok.value}'")
        seen.add(tok.value)
        if not self.at("COLON"):
            raise ParseError(
                f"line {tok.line}: parameter '{tok.value}' needs a type annotation, "
                f"e.g. `{tok.value}: int`"
            )
        self.eat("COLON")
        return A.Param(tok.value, self.parse_type())

    def parse_type(self):
        t = self.cur()
        if t.kind == "KEYWORD" and t.value == "None":
            self.pos += 1
            return "None"
        if t.kind == "NAME" and t.value in TYPE_NAMES:
            self.pos += 1
            return t.value
        raise ParseError(
            f"line {t.line}: {t.value!r} is not a type in this subset "
            f"(expected one of int, float, str, bool, None)"
        )

    def parse_block(self, allow_docstring=False):
        self.eat("NEWLINE")
        self.eat("INDENT")
        docstring = None
        stmts = []
        while not self.at("DEDENT"):
            if self.at("EOF"):
                raise ParseError(f"line {self.cur().line}: unexpected end of file inside a block")
            stmt = self.parse_statement()
            # A bare string literal as the first statement is a docstring:
            # keep it as metadata rather than emitting a no-op C expression.
            if (allow_docstring and not stmts and docstring is None
                    and isinstance(stmt, A.ExprStmt) and isinstance(stmt.expr, A.Str)):
                docstring = stmt.expr.value
                continue
            stmts.append(stmt)
        self.eat("DEDENT")
        if not stmts:
            stmts = [A.Pass(self.cur().line)]
        return (stmts, docstring) if allow_docstring else stmts

    # ---- statements ----

    def parse_statement(self):
        if self.at("INDENT"):
            raise ParseError(
                f"line {self.cur().line}: unexpected indent - this line is indented "
                f"further than the one before it without an `if`, `while`, `for` or "
                f"`def` header to open a new block"
            )
        if self.at_kw("if"):
            return self.parse_if()
        if self.at_kw("while"):
            return self.parse_while()
        if self.at_kw("for"):
            return self.parse_for()
        if self.at_kw("return"):
            return self.parse_return()
        if self.at_kw("break"):
            line = self.take().line
            self.eat("NEWLINE")
            return A.Break(line)
        if self.at_kw("continue"):
            line = self.take().line
            self.eat("NEWLINE")
            return A.Continue(line)
        if self.at_kw("pass"):
            line = self.take().line
            self.eat("NEWLINE")
            return A.Pass(line)
        if self.at_kw("def"):
            raise ParseError(
                f"line {self.cur().line}: nested function definitions are not part of this subset"
            )
        if self.at_name("print") and self.nxt().kind == "LPAREN":
            return self.parse_print()
        if self.at("NAME"):
            nxt = self.nxt().kind
            if nxt == "ASSIGN" or nxt == "COLON":
                return self.parse_assign()
            if nxt in AUG_OPS:
                return self.parse_aug_assign()
        return self.parse_expr_stmt()

    def parse_assign(self):
        name_tok = self.eat("NAME")
        annotation = None
        if self.at("COLON"):
            self.eat("COLON")
            annotation = self.parse_type()
            if not self.at("ASSIGN"):
                raise ParseError(
                    f"line {name_tok.line}: '{name_tok.value}: {annotation}' must be given a "
                    f"value — bare declarations are not part of this subset"
                )
        self.eat("ASSIGN")
        value = self.parse_expression()
        if self.at("ASSIGN"):
            raise ParseError(
                f"line {name_tok.line}: chained assignment (a = b = c) is not part of this subset"
            )
        self.eat("NEWLINE")
        return A.Assign(name_tok.value, value, name_tok.line, annotation)

    def parse_aug_assign(self):
        name_tok = self.eat("NAME")
        op_tok = self.take()
        value = self.parse_expression()
        self.eat("NEWLINE")
        return A.AugAssign(name_tok.value, AUG_OPS[op_tok.kind], value, name_tok.line)

    def parse_if(self):
        line = self.cur().line
        branches = []
        self.eat("KEYWORD", "if")
        cond = self.parse_expression()
        self.eat("COLON")
        branches.append((cond, self.parse_block()))
        while self.at_kw("elif"):
            self.eat("KEYWORD", "elif")
            cond = self.parse_expression()
            self.eat("COLON")
            branches.append((cond, self.parse_block()))
        if self.at_kw("else"):
            self.eat("KEYWORD", "else")
            self.eat("COLON")
            branches.append((None, self.parse_block()))
        return A.If(branches, line)

    def parse_while(self):
        line = self.eat("KEYWORD", "while").line
        cond = self.parse_expression()
        self.eat("COLON")
        body = self.parse_block()
        if self.at_kw("else"):
            raise ParseError(f"line {self.cur().line}: while/else is not part of this subset")
        return A.While(cond, body, line)

    def parse_for(self):
        line = self.eat("KEYWORD", "for").line
        var = self.eat("NAME").value
        self.eat("KEYWORD", "in")
        if not self.at_name("range"):
            t = self.cur()
            raise ParseError(
                f"line {t.line}: `for` may only iterate over range(...) in this subset "
                f"(there are no sequence types), found {t.value!r}"
            )
        self.eat("NAME", "range")
        self.eat("LPAREN")
        args = [self.parse_expression()]
        while self.at("COMMA"):
            self.eat("COMMA")
            args.append(self.parse_expression())
        self.eat("RPAREN")
        self.eat("COLON")

        if len(args) == 1:
            start, stop, step = A.Num("0", False, line), args[0], None
        elif len(args) == 2:
            start, stop, step = args[0], args[1], None
        elif len(args) == 3:
            start, stop, step = args
        else:
            raise ParseError(f"line {line}: range() takes 1 to 3 arguments, got {len(args)}")

        body = self.parse_block()
        if self.at_kw("else"):
            raise ParseError(f"line {self.cur().line}: for/else is not part of this subset")
        return A.For(var, start, stop, step, body, line)

    def parse_return(self):
        line = self.eat("KEYWORD", "return").line
        value = None
        if not self.at("NEWLINE"):
            value = self.parse_expression()
        self.eat("NEWLINE")
        return A.Return(value, line)

    def parse_print(self):
        line = self.eat("NAME", "print").line
        self.eat("LPAREN")
        args = []
        if not self.at("RPAREN"):
            args.append(self.parse_expression())
            while self.at("COMMA"):
                self.eat("COMMA")
                if self.at("RPAREN"):        # trailing comma
                    break
                args.append(self.parse_expression())
        self.eat("RPAREN")
        self.eat("NEWLINE")
        return A.Print(args, line)

    def parse_expr_stmt(self):
        line = self.cur().line
        expr = self.parse_expression()
        self.eat("NEWLINE")
        if not isinstance(expr, (A.Call, A.Str)):
            raise ParseError(
                f"line {line}: this expression has no effect — only function calls are "
                f"valid as statements"
            )
        return A.ExprStmt(expr, line)

    # ---- expressions ----

    def parse_expression(self):
        return self.parse_bool_or()

    def parse_bool_or(self):
        left = self.parse_bool_and()
        while self.at_kw("or"):
            line = self.take().line
            left = A.BoolOp("or", left, self.parse_bool_and(), line)
        return left

    def parse_bool_and(self):
        left = self.parse_bool_not()
        while self.at_kw("and"):
            line = self.take().line
            left = A.BoolOp("and", left, self.parse_bool_not(), line)
        return left

    def parse_bool_not(self):
        if self.at_kw("not"):
            line = self.take().line
            return A.UnaryOp("not", self.parse_bool_not(), line)
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_arith()
        if self.cur().kind in CMP_OPS:
            op_tok = self.take()
            right = self.parse_arith()
            if self.cur().kind in CMP_OPS:
                raise ParseError(
                    f"line {self.cur().line}: chained comparisons (a < b < c) are not part of "
                    f"this subset — write `a < b and b < c` instead. (Translating them "
                    f"faithfully needs a temporary for the middle operand, so they are "
                    f"rejected rather than mistranslated.)"
                )
            return A.Compare(CMP_OPS[op_tok.kind], left, right, op_tok.line)
        return left

    def parse_arith(self):
        left = self.parse_term()
        while self.cur().kind in ADD_OPS:
            op_tok = self.take()
            left = A.BinOp(ADD_OPS[op_tok.kind], left, self.parse_term(), op_tok.line)
        return left

    def parse_term(self):
        left = self.parse_unary()
        while self.cur().kind in MUL_OPS:
            op_tok = self.take()
            left = A.BinOp(MUL_OPS[op_tok.kind], left, self.parse_unary(), op_tok.line)
        return left

    def parse_unary(self):
        if self.at("MINUS") or self.at("PLUS"):
            op_tok = self.take()
            operand = self.parse_unary()
            if op_tok.kind == "PLUS":
                return A.UnaryOp("+", operand, op_tok.line)
            return A.UnaryOp("-", operand, op_tok.line)
        return self.parse_power()

    def parse_power(self):
        base = self.parse_atom()
        if self.at("POW"):
            line = self.take().line
            # Right-associative, and the exponent may be unary: 2 ** -1, 2 ** 3 ** 2.
            return A.BinOp("**", base, self.parse_unary(), line)
        return base

    def parse_atom(self):
        t = self.cur()
        if t.kind == "NUMBER":
            self.pos += 1
            return A.Num(t.value, False, t.line)
        if t.kind == "FLOAT":
            self.pos += 1
            return A.Num(t.value, True, t.line)
        if t.kind == "STRING":
            self.pos += 1
            return A.Str(t.value, t.line)
        if t.kind == "KEYWORD":
            if t.value == "True":
                self.pos += 1
                return A.Bool(True, t.line)
            if t.value == "False":
                self.pos += 1
                return A.Bool(False, t.line)
            if t.value == "None":
                self.pos += 1
                return A.NoneLit(t.line)
        if t.kind == "NAME":
            if self.nxt().kind == "LPAREN":
                return self.parse_call()
            self.pos += 1
            return A.Name(t.value, t.line)
        if t.kind == "LPAREN":
            self.eat("LPAREN")
            expr = self.parse_expression()
            if self.at("COMMA"):
                raise ParseError(f"line {t.line}: tuples are not part of this subset")
            self.eat("RPAREN")
            return expr
        raise ParseError(f"line {t.line}: unexpected token {t.kind} {t.value!r}")

    def parse_call(self):
        name_tok = self.eat("NAME")
        if name_tok.value == "print":
            raise ParseError(
                f"line {name_tok.line}: print() returns None and may only be used as a "
                f"statement in this subset, not inside an expression"
            )
        if name_tok.value == "range":
            raise ParseError(
                f"line {name_tok.line}: range() may only appear in a `for` header in this subset"
            )
        self.eat("LPAREN")
        args = []
        if not self.at("RPAREN"):
            args.append(self.parse_expression())
            while self.at("COMMA"):
                self.eat("COMMA")
                if self.at("RPAREN"):        # trailing comma
                    break
                args.append(self.parse_expression())
        self.eat("RPAREN")
        return A.Call(name_tok.value, args, name_tok.line)
