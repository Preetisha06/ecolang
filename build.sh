#!/bin/bash
# builds the EcoLang compiler in one go
bison -d parser.y && flex lexer.l && gcc parser.tab.c lex.yy.c tac.c interp.c optimize.c cgen.c -o ecoc && echo "Build OK: ./ecoc is ready"
