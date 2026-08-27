"""Recursive-descent parser: token stream -> AST."""

from . import ast_nodes as A


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    # ---- token helpers ----
    def cur(self):
        return self.tokens[self.pos]

    def at(self, kind, value=None):
        t = self.cur()
        if t.kind != kind:
            return False
        return value is None or t.value == value

    def eat(self, kind, value=None):
        if not self.at(kind, value):
            t = self.cur()
            expected = f"{kind}" + (f" {value!r}" if value else "")
            raise ParseError(f"line {t.line}: expected {expected}, got {t.kind} {t.value!r}")
        t = self.cur()
        self.pos += 1
        return t

    def at_kw(self, word):
        return self.at("KEYWORD", word)

    # ---- top level ----
    def parse_program(self):
        funcs = []
        while not self.at("EOF"):
            funcs.append(self.parse_function())
        return A.Program(funcs)

    def parse_function(self):
        self.eat("KEYWORD", "def")
        name = self.eat("NAME").value
        self.eat("LPAREN")
        params = []
        if not self.at("RPAREN"):
            params.append(self.parse_param())
            while self.at("COMMA"):
                self.eat("COMMA")
                params.append(self.parse_param())
        self.eat("RPAREN")
        self.eat("ARROW")
        ret_type = self.parse_type()
        self.eat("COLON")
        line = self.cur().line
        body = self.parse_block()
        return A.FunctionDef(name, params, ret_type, body, line)

    def parse_param(self):
        name = self.eat("NAME").value
        self.eat("COLON")
        t = self.parse_type()
        return A.Param(name, t)

    def parse_type(self):
        t = self.eat("KEYWORD")
        if t.value not in ("int", "float", "str", "bool", "None"):
            raise ParseError(f"line {t.line}: {t.value!r} is not a valid type in this subset")
        return t.value

    def parse_block(self):
        self.eat("NEWLINE")
        self.eat("INDENT")
        stmts = []
        while not self.at("DEDENT"):
            stmts.append(self.parse_statement())
        self.eat("DEDENT")
        return stmts

    # ---- statements ----
    def parse_statement(self):
        if self.at_kw("if"):
            return self.parse_if()
        if self.at_kw("while"):
            return self.parse_while()
        if self.at_kw("for"):
            return self.parse_for()
        if self.at_kw("return"):
            return self.parse_return()
        if self.at_kw("print"):
            return self.parse_print()
        if self.at("NAME") and self._peek_is_assign():
            return self.parse_assign()
        return self.parse_expr_stmt()

    def _peek_is_assign(self):
        return (self.pos + 1 < len(self.tokens)
                and self.tokens[self.pos + 1].kind == "ASSIGN")

    def parse_assign(self):
        name_tok = self.eat("NAME")
        self.eat("ASSIGN")
        value = self.parse_expression()
        self.eat("NEWLINE")
        return A.Assign(name_tok.value, value, name_tok.line)

    def parse_if(self):
        line = self.cur().line
        branches = []
        self.eat("KEYWORD", "if")
        cond = self.parse_expression()
        self.eat("COLON")
        body = self.parse_block()
        branches.append((cond, body))
        while self.at_kw("elif"):
            self.eat("KEYWORD", "elif")
            cond = self.parse_expression()
            self.eat("COLON")
            body = self.parse_block()
            branches.append((cond, body))
        if self.at_kw("else"):
            self.eat("KEYWORD", "else")
            self.eat("COLON")
            body = self.parse_block()
            branches.append((None, body))
        return A.If(branches, line)

    def parse_while(self):
        line = self.cur().line
        self.eat("KEYWORD", "while")
        cond = self.parse_expression()
        self.eat("COLON")
        body = self.parse_block()
        return A.While(cond, body, line)

    def parse_for(self):
        line = self.cur().line
        self.eat("KEYWORD", "for")
        var = self.eat("NAME").value
        self.eat("KEYWORD", "in")
        self.eat("KEYWORD", "range")
        self.eat("LPAREN")
        start = A.Num("0", False, line)
        stop = self.parse_expression()
        if self.at("COMMA"):
            self.eat("COMMA")
            start = stop
            stop = self.parse_expression()
        self.eat("RPAREN")
        self.eat("COLON")
        body = self.parse_block()
        return A.For(var, start, stop, body, line)

    def parse_return(self):
        line = self.cur().line
        self.eat("KEYWORD", "return")
        value = None
        if not self.at("NEWLINE"):
            value = self.parse_expression()
        self.eat("NEWLINE")
        return A.Return(value, line)

    def parse_print(self):
        line = self.cur().line
        self.eat("KEYWORD", "print")
        self.eat("LPAREN")
        args = []
        if not self.at("RPAREN"):
            args.append(self.parse_expression())
            while self.at("COMMA"):
                self.eat("COMMA")
                args.append(self.parse_expression())
        self.eat("RPAREN")
        self.eat("NEWLINE")
        return A.Print(args, line)

    def parse_expr_stmt(self):
        line = self.cur().line
        expr = self.parse_expression()
        self.eat("NEWLINE")
        return A.ExprStmt(expr, line)

    # ---- expressions (precedence climbing) ----
    def parse_expression(self):
        return self.parse_bool_or()

    def parse_bool_or(self):
        left = self.parse_bool_and()
        while self.at_kw("or"):
            line = self.eat("KEYWORD", "or").line
            right = self.parse_bool_and()
            left = A.BoolOp("or", left, right, line)
        return left

    def parse_bool_and(self):
        left = self.parse_bool_not()
        while self.at_kw("and"):
            line = self.eat("KEYWORD", "and").line
            right = self.parse_bool_not()
            left = A.BoolOp("and", left, right, line)
        return left

    def parse_bool_not(self):
        if self.at_kw("not"):
            line = self.eat("KEYWORD", "not").line
            operand = self.parse_comparison()
            return A.UnaryOp("not", operand, line)
        return self.parse_comparison()

    CMP_OPS = {"EQ": "==", "NE": "!=", "LT": "<", "GT": ">", "LE": "<=", "GE": ">="}

    def parse_comparison(self):
        left = self.parse_arith()
        if self.cur().kind in self.CMP_OPS:
            op_tok = self.tokens[self.pos]
            self.pos += 1
            right = self.parse_arith()
            return A.Compare(self.CMP_OPS[op_tok.kind], left, right, op_tok.line)
        return left

    def parse_arith(self):
        left = self.parse_term()
        while self.at("PLUS") or self.at("MINUS"):
            op_tok = self.tokens[self.pos]
            self.pos += 1
            right = self.parse_term()
            left = A.BinOp(op_tok.value, left, right, op_tok.line)
        return left

    def parse_term(self):
        left = self.parse_factor()
        while self.at("STAR") or self.at("SLASH") or self.at("PERCENT"):
            op_tok = self.tokens[self.pos]
            self.pos += 1
            right = self.parse_factor()
            left = A.BinOp(op_tok.value, left, right, op_tok.line)
        return left

    def parse_factor(self):
        if self.at("MINUS"):
            line = self.eat("MINUS").line
            operand = self.parse_atom()
            return A.UnaryOp("-", operand, line)
        return self.parse_atom()

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
        if t.kind == "KEYWORD" and t.value == "True":
            self.pos += 1
            return A.Bool(True, t.line)
        if t.kind == "KEYWORD" and t.value == "False":
            self.pos += 1
            return A.Bool(False, t.line)
        if t.kind == "NAME":
            if self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].kind == "LPAREN":
                return self.parse_call()
            self.pos += 1
            return A.Name(t.value, t.line)
        if t.kind == "LPAREN":
            self.eat("LPAREN")
            expr = self.parse_expression()
            self.eat("RPAREN")
            return expr
        raise ParseError(f"line {t.line}: unexpected token {t.kind} {t.value!r}")

    def parse_call(self):
        name_tok = self.eat("NAME")
        self.eat("LPAREN")
        args = []
        if not self.at("RPAREN"):
            args.append(self.parse_expression())
            while self.at("COMMA"):
                self.eat("COMMA")
                args.append(self.parse_expression())
        self.eat("RPAREN")
        return A.Call(name_tok.value, args, name_tok.line)
