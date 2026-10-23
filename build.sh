#!/bin/bash
# builds the EcoLang compiler in one go
bison -d parser.y && flex lexer.l && gcc -fwrapv parser.tab.c lex.yy.c tac.c interp.c optimize.c cgen.c budget.c -o ecoc && echo "Build OK: ./ecoc is ready"
