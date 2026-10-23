%{
/* EcoLang parser - step 3
   Checks the grammar AND generates three-address code (TAC). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "tac.h"
#include "interp.h"
#include "optimize.h"
#include "cgen.h"
#include "budget.h"

int yylex(void);
void yyerror(const char *msg);
extern int yylineno;
extern FILE *yyin;

int arg_count = 0;   /* counts arguments in a function call */
%}

%union {
    long long num; /* used for numbers and label numbers (long long: numbers can be big) */
    char *name;    /* used for variable names and temps  */
}

%token FN LET WHILE IF ELSE RETURN INT ARROW
%token EQ NE LE GE LT GT
%token <num> NUMBER
%token <name> ID

%type <name> expr
%type <num> while_start if_head

/* operator priority: lower line = higher priority */
%left EQ NE
%left LT GT LE GE
%left '+' '-'
%left '*' '/' '%'
%right UMINUS          /* -x has the highest priority */

/* fixes the "if-else" ambiguity: else belongs to the closest if */
%nonassoc LOWER_THAN_ELSE
%nonassoc ELSE

%%

program
    : function
    | program function
    ;

/* fn name(params) -> int { ... } */
function
    : FN ID { emit("func", $2, "", ""); }
      '(' params ')' ARROW INT block
      { emit("end", "", "", ""); }
    ;

params
    : /* empty */
    | param_list
    ;

param_list
    : param
    | param_list ',' param
    ;

param
    : ID ':' INT                    { emit("param_in", $1, "", ""); }
    | ID ':' INT '[' ']'            { emit("param_in", $1, "", ""); }   /* array: a: int[] */
    ;

block
    : '{' statements '}'
    ;

statements
    : /* empty */
    | statements statement
    ;

statement
    /* let a: int = expr;   ->   a = expr */
    : LET ID ':' INT '=' expr ';'
        { emit("=", $6, "", $2); }

    /* let a: int[100];     ->   a = alloc 100 */
    | LET ID ':' INT '[' expr ']' ';'
        { emit("alloc", $6, "", $2); }

    /* a[i] = expr;         ->   a[i] = value */
    | ID '[' expr ']' '=' expr ';'
        { emit("store", $1, $3, $6); }

    /* a function call on its own line:  fill(a, 10); */
    | expr ';'

    /* a = expr; */
    | ID '=' expr ';'
        { emit("=", $3, "", $1); }

    /* while loop:
         L1:
           ifFalse cond goto L2
           ...body...
           goto L1
         L2:                                   */
    | while_start '(' expr ')'
        {
            int end_label = new_label();
            emit("iffalse", $3, "", label_name(end_label));
            $<num>$ = end_label;
        }
      block
        {
            emit("goto", "", "", label_name($1));
            emit("label", "", "", label_name($<num>5));
        }

    /* if without else:
           ifFalse cond goto L1
           ...body...
         L1:                                   */
    | if_head block %prec LOWER_THAN_ELSE
        {
            emit("label", "", "", label_name($1));
        }

    /* if with else:
           ifFalse cond goto L1
           ...if body...
           goto L2
         L1:
           ...else body...
         L2:                                   */
    | if_head block ELSE
        {
            int end_label = new_label();
            emit("goto", "", "", label_name(end_label));
            emit("label", "", "", label_name($1));
            $<num>$ = end_label;
        }
      block
        {
            emit("label", "", "", label_name($<num>4));
        }

    /* return expr; */
    | RETURN expr ';'
        { emit("return", $2, "", ""); }
    ;

/* puts the label at the start of a while loop */
while_start
    : WHILE
        {
            int start_label = new_label();
            emit("label", "", "", label_name(start_label));
            $$ = start_label;
        }
    ;

/* if (cond)  ->  ifFalse cond goto Lx */
if_head
    : IF '(' expr ')'
        {
            int false_label = new_label();
            emit("iffalse", $3, "", label_name(false_label));
            $$ = false_label;
        }
    ;

/* every maths step gets a new temp:  t1 = a + b  */
expr
    : expr '+' expr   { $$ = new_temp(); emit("+",  $1, $3, $$); }
    | expr '-' expr   { $$ = new_temp(); emit("-",  $1, $3, $$); }
    | expr '*' expr   { $$ = new_temp(); emit("*",  $1, $3, $$); }
    | expr '/' expr   { $$ = new_temp(); emit("/",  $1, $3, $$); }
    | expr '%' expr   { $$ = new_temp(); emit("%",  $1, $3, $$); }
    | expr LT expr    { $$ = new_temp(); emit("<",  $1, $3, $$); }
    | expr GT expr    { $$ = new_temp(); emit(">",  $1, $3, $$); }
    | expr LE expr    { $$ = new_temp(); emit("<=", $1, $3, $$); }
    | expr GE expr    { $$ = new_temp(); emit(">=", $1, $3, $$); }
    | expr EQ expr    { $$ = new_temp(); emit("==", $1, $3, $$); }
    | expr NE expr    { $$ = new_temp(); emit("!=", $1, $3, $$); }
    | '(' expr ')'    { $$ = $2; }

    /* negative:  -5 is just the number -5,  -x becomes  t = 0 - x */
    | '-' expr %prec UMINUS
        {
            if ($2[0] >= '0' && $2[0] <= '9') {
                char *s = malloc(MAX_LEN);
                sprintf(s, "-%s", $2);
                $$ = s;
            } else {
                $$ = new_temp();
                emit("-", "0", $2, $$);
            }
        }
    | NUMBER
        {
            char *s = malloc(MAX_LEN);
            sprintf(s, "%lld", $1);
            $$ = s;
        }
    | ID              { $$ = $1; }

    /* read from an array:  t = a[i] */
    | ID '[' expr ']'
        {
            $$ = new_temp();
            emit("load", $1, $3, $$);
        }

    /* function call:  param x ... then  t = call fib, count */
    | ID '('
        {
            /* save the outer count, so calls inside calls work: f(g(x), y) */
            $<num>$ = arg_count;
            arg_count = 0;
        }
      args ')'
        {
            char count_str[MAX_LEN];
            sprintf(count_str, "%d", arg_count);
            $$ = new_temp();
            emit("call", $1, count_str, $$);
            arg_count = $<num>3;   /* put the outer count back */
        }
    ;

args
    : /* empty */
    | arg_list
    ;

arg_list
    : expr                  { emit("param", $1, "", ""); arg_count = arg_count + 1; }
    | arg_list ',' expr     { emit("param", $3, "", ""); arg_count = arg_count + 1; }
    ;

%%

void yyerror(const char *msg)
{
    printf("Syntax error on line %d\n", yylineno);
    exit(1);
}

/* how to run:
     ./ecoc file.eco                       -> runs main(), no optimisation
     ./ecoc file.eco fib 20                -> runs fib(20)
     ./ecoc file.eco fib 20 --goal=speed   -> optimise for speed   (alpha = 1)
     ./ecoc file.eco fib 20 --goal=balanced-> optimise for both    (alpha = 0.5)
     ./ecoc file.eco fib 20 --goal=carbon  -> optimise for carbon  (alpha = 0)
   extra options:
     --opt                                 -> same as --goal=speed
     --alpha=0.3                           -> any alpha between 0 and 1
     --no-cf  --no-cp  --no-sr  --no-dce   -> switch one pass off
     --no-cse --no-licm
     --profile=myfile.txt                  -> use a different energy profile
     --quiet                               -> only print the report
     --csv                                 -> print one line of numbers (for scripts)
     --emit-c=out.c                        -> also write the code as a C program
     --max-slowdown=5                      -> speed budget: least energy while at most
                                              5% slower than the speed build          */
int main(int argc, char *argv[])
{
    int do_opt = 0;
    int cf = 1;
    int cp = 1;
    int sr = 1;
    int dce = 1;
    int cse = 1;
    int licm = 1;
    int quiet = 0;
    double alpha = 1.0;
    char goal_text[64] = "none (no optimisation)";
    const char *profile_file = "energy_profile.txt";
    const char *c_file = NULL;
    int use_budget = 0;
    double max_slowdown = 0.0;

    const char *file_name = NULL;
    const char *func_name = "main";
    long long args[20];
    int nargs = 0;
    int positional = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--opt") == 0 || strcmp(argv[i], "--goal=speed") == 0) {
            do_opt = 1;
            alpha = 1.0;
            strcpy(goal_text, "speed (alpha = 1.00)");
        } else if (strcmp(argv[i], "--goal=balanced") == 0) {
            do_opt = 1;
            alpha = 0.5;
            strcpy(goal_text, "balanced (alpha = 0.50)");
        } else if (strcmp(argv[i], "--goal=carbon") == 0) {
            do_opt = 1;
            alpha = 0.0;
            strcpy(goal_text, "carbon (alpha = 0.00)");
        } else if (strncmp(argv[i], "--alpha=", 8) == 0) {
            do_opt = 1;
            alpha = atof(argv[i] + 8);
            sprintf(goal_text, "custom (alpha = %.2f)", alpha);
        } else if (strncmp(argv[i], "--max-slowdown=", 15) == 0) {
            do_opt = 1;
            use_budget = 1;
            max_slowdown = atof(argv[i] + 15);
            if (max_slowdown < 0.0) {
                max_slowdown = 0.0;
            }
        } else if (strncmp(argv[i], "--profile=", 10) == 0) {
            profile_file = argv[i] + 10;
        } else if (strncmp(argv[i], "--emit-c=", 9) == 0) {
            c_file = argv[i] + 9;
        } else if (strcmp(argv[i], "--no-cf") == 0) {
            cf = 0;
        } else if (strcmp(argv[i], "--no-cp") == 0) {
            cp = 0;
        } else if (strcmp(argv[i], "--no-sr") == 0) {
            sr = 0;
        } else if (strcmp(argv[i], "--no-dce") == 0) {
            dce = 0;
        } else if (strcmp(argv[i], "--no-cse") == 0) {
            cse = 0;
        } else if (strcmp(argv[i], "--no-licm") == 0) {
            licm = 0;
        } else if (strcmp(argv[i], "--quiet") == 0) {
            quiet = 1;
        } else if (strcmp(argv[i], "--csv") == 0) {
            quiet = 1;
            csv_mode = 1;
        } else {
            /* not an option: file name, then function name, then numbers */
            if (positional == 0) {
                file_name = argv[i];
            } else if (positional == 1) {
                func_name = argv[i];
            } else if (nargs < 20) {
                args[nargs] = atoll(argv[i]);
                nargs = nargs + 1;
            }
            positional = positional + 1;
        }
    }

    if (file_name == NULL) {
        printf("usage: ./ecoc file.eco [function] [numbers...] [--goal=speed|balanced|carbon]\n");
        return 1;
    }

    FILE *f = fopen(file_name, "r");
    if (f == NULL) {
        printf("cannot open %s\n", file_name);
        return 1;
    }

    yyin = f;
    yyparse();
    fclose(f);

    /* the optimiser needs the cost table, so load it first */
    load_profile(profile_file);

    if (!quiet) {
        printf("Parsing successful\n");
        printf("\n===== Three-address code =====\n");
        print_code();
    }

    source_name = file_name;

    /* which passes ran, for the report */
    char passes[64] = "none";
    if (do_opt) {
        clock_t t0 = clock();
        /* speed budget: first find the alpha, then optimise with it below */
        if (use_budget) {
            alpha = choose_alpha_for_budget(max_slowdown, cf, cp, dce, sr, cse, licm,
                                            func_name, args, nargs, !csv_mode);
            sprintf(goal_text, "budget %.1f%% (alpha = %.2f)", max_slowdown, alpha);
        }
        optimize_program(cf, cp, dce, sr, cse, licm, alpha);
        clock_t t1 = clock();
        opt_time_ms = 1000.0 * (double) (t1 - t0) / CLOCKS_PER_SEC;
        strcpy(passes, "");
        if (cf)  strcat(passes, "CF ");
        if (cp)  strcat(passes, "CP ");
        if (sr)  strcat(passes, "SR ");
        if (dce) strcat(passes, "DCE ");
        if (cse) strcat(passes, "CSE ");
        if (licm) strcat(passes, "LICM ");
        if (strlen(passes) == 0) {
            strcpy(passes, "none");
        } else {
            passes[strlen(passes) - 1] = '\0';   /* remove last space */
        }

        if (!quiet) {
            printf("\n===== Optimised three-address code =====\n");
            print_code();
            print_opt_summary();
        }
    }

    /* write a real C program of the (optimised) code */
    if (c_file != NULL) {
        generate_c(c_file, func_name);
    }

    run_program(func_name, args, nargs, passes, goal_text);
    return 0;
}
