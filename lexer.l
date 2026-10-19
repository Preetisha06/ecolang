%{
/* EcoLang lexer - version 2
   Same patterns as step 1, but now each pattern RETURNS a token
   to the parser instead of printing it. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "parser.tab.h"

/* 1 if s is not empty and has only digits */
int is_all_digits(const char *s)
{
    if (s[0] == '\0') {
        return 0;
    }
    for (int i = 0; s[i] != '\0'; i++) {
        if (s[i] < '0' || s[i] > '9') {
            return 0;
        }
    }
    return 1;
}
%}

%option noyywrap
%option yylineno

%%

"//".*              { /* comment: ignore */ }
[ \t\r\n]+          { /* spaces and new lines: ignore */ }

"fn"                { return FN; }
"let"               { return LET; }
"while"             { return WHILE; }
"if"                { return IF; }
"else"              { return ELSE; }
"return"            { return RETURN; }
"int"               { return INT; }

[0-9]+              { yylval.num = atoll(yytext); return NUMBER; }
[a-zA-Z_][a-zA-Z0-9_]*  {
                        /* names like t1, t25 are used by the compiler for temporaries */
                        if (yytext[0] == 't' && is_all_digits(yytext + 1)) {
                            printf("line %d: the name '%s' is reserved, please use another name\n", yylineno, yytext);
                            exit(1);
                        }
                        yylval.name = strdup(yytext);
                        return ID;
                    }

"->"                { return ARROW; }
"=="                { return EQ; }
"!="                { return NE; }
"<="                { return LE; }
">="                { return GE; }
"<"                 { return LT; }
">"                 { return GT; }

"+"                 { return '+'; }
"-"                 { return '-'; }
"*"                 { return '*'; }
"/"                 { return '/'; }
"%"                 { return '%'; }
"="                 { return '='; }
";"                 { return ';'; }
":"                 { return ':'; }
","                 { return ','; }
"("                 { return '('; }
")"                 { return ')'; }
"{"                 { return '{'; }
"["                 { return '['; }
"]"                 { return ']'; }
"}"                 { return '}'; }

.                   { printf("line %d: unknown character '%s'\n", yylineno, yytext); exit(1); }

%%
