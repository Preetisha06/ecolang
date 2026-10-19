%{
/* EcoLang lexer - step 1
   For now it just prints every token it finds.
   Later we will connect it to the Bison parser. */
#include <stdio.h>
%}

%option noyywrap
%option yylineno

%%

"//".*              { /* comment: ignore */ }
[ \t\r\n]+          { /* spaces and new lines: ignore */ }

"fn"                { printf("line %d: KEYWORD_FN      %s\n", yylineno, yytext); }
"let"               { printf("line %d: KEYWORD_LET     %s\n", yylineno, yytext); }
"while"             { printf("line %d: KEYWORD_WHILE   %s\n", yylineno, yytext); }
"if"                { printf("line %d: KEYWORD_IF      %s\n", yylineno, yytext); }
"else"              { printf("line %d: KEYWORD_ELSE    %s\n", yylineno, yytext); }
"return"            { printf("line %d: KEYWORD_RETURN  %s\n", yylineno, yytext); }
"int"               { printf("line %d: TYPE_INT        %s\n", yylineno, yytext); }

[0-9]+              { printf("line %d: NUMBER          %s\n", yylineno, yytext); }
[a-zA-Z_][a-zA-Z0-9_]*  { printf("line %d: IDENTIFIER      %s\n", yylineno, yytext); }

"->"                { printf("line %d: ARROW           %s\n", yylineno, yytext); }
"=="                { printf("line %d: EQ              %s\n", yylineno, yytext); }
"!="                { printf("line %d: NE              %s\n", yylineno, yytext); }
"<="                { printf("line %d: LE              %s\n", yylineno, yytext); }
">="                { printf("line %d: GE              %s\n", yylineno, yytext); }
"<"                 { printf("line %d: LT              %s\n", yylineno, yytext); }
">"                 { printf("line %d: GT              %s\n", yylineno, yytext); }

"+"                 { printf("line %d: PLUS            %s\n", yylineno, yytext); }
"-"                 { printf("line %d: MINUS           %s\n", yylineno, yytext); }
"*"                 { printf("line %d: MULTIPLY        %s\n", yylineno, yytext); }
"/"                 { printf("line %d: DIVIDE          %s\n", yylineno, yytext); }
"%"                 { printf("line %d: MOD             %s\n", yylineno, yytext); }
"="                 { printf("line %d: ASSIGN          %s\n", yylineno, yytext); }

";"                 { printf("line %d: SEMICOLON       %s\n", yylineno, yytext); }
":"                 { printf("line %d: COLON           %s\n", yylineno, yytext); }
","                 { printf("line %d: COMMA           %s\n", yylineno, yytext); }
"("                 { printf("line %d: LPAREN          %s\n", yylineno, yytext); }
")"                 { printf("line %d: RPAREN          %s\n", yylineno, yytext); }
"{"                 { printf("line %d: LBRACE          %s\n", yylineno, yytext); }
"}"                 { printf("line %d: RBRACE          %s\n", yylineno, yytext); }

.                   { printf("line %d: ERROR unknown character '%s'\n", yylineno, yytext); }

%%

int main(int argc, char *argv[])
{
    if (argc < 2) {
        printf("usage: ./lexer file.eco\n");
        return 1;
    }

    FILE *f = fopen(argv[1], "r");
    if (f == NULL) {
        printf("cannot open %s\n", argv[1]);
        return 1;
    }

    yyin = f;
    yylex();
    fclose(f);
    return 0;
}
