/* cgen.c - C back end for EcoLang

   Every three-address instruction becomes one line of C, so the C program
   does exactly the same work as the three-address code:

       t1 = a + b          ->   v_t1 = v_a + v_b;
       ifFalse t1 goto L2  ->   if (!v_t1) goto L2;
       t5 = a[i]           ->   v_t5 = HEAP[ARR_START[v_a] + v_i];
       t9 = call fib, 1    ->   psp -= 1;  v_t9 = f_fib(PSTACK[psp + 0]);

   Names get a prefix (v_ for variables, f_ for functions) so they can never
   clash with C words like "int" or with C's own main().

   The generated program is meant to be compiled with  gcc -O0 -fwrapv
   so gcc does NOT optimise it: the only differences between builds are the
   ones EcoLang's own passes made.                                          */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "tac.h"
#include "cgen.h"

#define MAX_NAMES 400

static int is_number(const char *s)
{
    if (isdigit(s[0])) {
        return 1;
    }
    if (s[0] == '-' && isdigit(s[1])) {
        return 1;
    }
    return 0;
}

/* operand as C text: numbers get LL, variables get v_ */
static void operand(char *out, const char *s)
{
    if (is_number(s)) {
        sprintf(out, "%sLL", s);
    } else {
        sprintf(out, "v_%s", s);
    }
}

static int is_binary_op(const char *op)
{
    char *list[14] = { "+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "<<", ">>", "mulhs" };
    for (int i = 0; i < 14; i++) {
        if (strcmp(op, list[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

/* ---------- list of local variable names in one function ---------- */

static char names[MAX_NAMES][MAX_LEN];
static int name_count = 0;

static void add_name(const char *s)
{
    if (s[0] == '\0' || is_number(s)) {
        return;
    }
    for (int i = 0; i < name_count; i++) {
        if (strcmp(names[i], s) == 0) {
            return;
        }
    }
    if (name_count < MAX_NAMES) {
        strcpy(names[name_count], s);
        name_count = name_count + 1;
    }
}

static int is_param(int start, int end, const char *s)
{
    for (int i = start; i <= end; i++) {
        if (strcmp(code[i].op, "param_in") == 0 && strcmp(code[i].arg1, s) == 0) {
            return 1;
        }
    }
    return 0;
}

static int func_end_at(int start)
{
    for (int i = start + 1; i < code_count; i++) {
        if (strcmp(code[i].op, "end") == 0) {
            return i;
        }
    }
    return code_count - 1;
}

/* function header:  long long f_name(long long v_a, long long v_b) */
static void write_header(FILE *f, int start, int end)
{
    fprintf(f, "long long f_%s(", code[start].arg1);
    int count = 0;
    for (int i = start; i <= end; i++) {
        if (strcmp(code[i].op, "param_in") == 0) {
            if (count > 0) {
                fprintf(f, ", ");
            }
            fprintf(f, "long long v_%s", code[i].arg1);
            count = count + 1;
        }
    }
    if (count == 0) {
        fprintf(f, "void");
    }
    fprintf(f, ")");
}

static int count_params(int start, int end)
{
    int count = 0;
    for (int i = start; i <= end; i++) {
        if (strcmp(code[i].op, "param_in") == 0) {
            count = count + 1;
        }
    }
    return count;
}

/* number of parameters of function 'name' (needed for calls) */
static int params_of(const char *name)
{
    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, "func") == 0 && strcmp(code[i].arg1, name) == 0) {
            return count_params(i, func_end_at(i));
        }
    }
    return 0;
}

static void write_function(FILE *f, int start, int end)
{
    write_header(f, start, end);
    fprintf(f, "\n{\n");

    /* declare every local variable (not the parameters) */
    name_count = 0;
    for (int i = start + 1; i < end; i++) {
        char *op = code[i].op;
        if (is_binary_op(op) || strcmp(op, "load") == 0 || strcmp(op, "store") == 0) {
            add_name(code[i].arg1);
            add_name(code[i].arg2);
            add_name(code[i].result);
        } else if (strcmp(op, "=") == 0 || strcmp(op, "alloc") == 0) {
            add_name(code[i].arg1);
            add_name(code[i].result);
        } else if (strcmp(op, "iffalse") == 0 || strcmp(op, "param") == 0 || strcmp(op, "return") == 0) {
            add_name(code[i].arg1);
        } else if (strcmp(op, "call") == 0) {
            add_name(code[i].result);
        }
    }
    for (int k = 0; k < name_count; k++) {
        if (!is_param(start, end, names[k])) {
            fprintf(f, "    long long v_%s = 0;\n", names[k]);
        }
    }

    char a[64];
    char b[64];
    char r[64];
    for (int i = start + 1; i < end; i++) {
        char *op = code[i].op;
        if (strcmp(op, "param_in") == 0 || strcmp(op, "nop") == 0) {
            continue;
        }
        if (strcmp(op, "label") == 0) {
            fprintf(f, "%s: ;\n", code[i].result);
            continue;
        }
        if (strcmp(op, "goto") == 0) {
            fprintf(f, "    goto %s;\n", code[i].result);
            continue;
        }
        if (strcmp(op, "iffalse") == 0) {
            operand(a, code[i].arg1);
            fprintf(f, "    if (!%s) goto %s;\n", a, code[i].result);
            continue;
        }
        if (strcmp(op, "return") == 0) {
            operand(a, code[i].arg1);
            fprintf(f, "    return %s;\n", a);
            continue;
        }
        if (strcmp(op, "=") == 0) {
            operand(a, code[i].arg1);
            fprintf(f, "    v_%s = %s;\n", code[i].result, a);
            continue;
        }
        if (strcmp(op, "param") == 0) {
            operand(a, code[i].arg1);
            fprintf(f, "    PSTACK[psp] = %s; psp = psp + 1;\n", a);
            continue;
        }
        if (strcmp(op, "call") == 0) {
            int n = atoi(code[i].arg2);
            fprintf(f, "    psp = psp - %d;\n", n);
            fprintf(f, "    v_%s = f_%s(", code[i].result, code[i].arg1);
            for (int k = 0; k < n; k++) {
                if (k > 0) {
                    fprintf(f, ", ");
                }
                fprintf(f, "PSTACK[psp + %d]", k);
            }
            fprintf(f, ");\n");
            continue;
        }
        if (strcmp(op, "alloc") == 0) {
            operand(a, code[i].arg1);
            fprintf(f, "    v_%s = eco_alloc(%s);\n", code[i].result, a);
            continue;
        }
        if (strcmp(op, "load") == 0) {
            operand(a, code[i].arg1);
            operand(b, code[i].arg2);
            fprintf(f, "    v_%s = HEAP[ARR_START[%s] + %s];\n", code[i].result, a, b);
            continue;
        }
        if (strcmp(op, "store") == 0) {
            operand(a, code[i].arg1);
            operand(b, code[i].arg2);
            operand(r, code[i].result);
            fprintf(f, "    HEAP[ARR_START[%s] + %s] = %s;\n", a, b, r);
            continue;
        }
        if (strcmp(op, "mulhs") == 0) {
            operand(a, code[i].arg1);
            operand(b, code[i].arg2);
            fprintf(f, "    v_%s = MULHS(%s, %s);\n", code[i].result, a, b);
            continue;
        }
        if (strcmp(op, "*") == 0 || strcmp(op, "/") == 0 || strcmp(op, "%") == 0) {
            /* Even at -O0, gcc rewrites  x / 7  or  x * 10  with a written-out number
               into its own shifts and multiplies. Passing the operands through
               OPAQUE_A() / OPAQUE_B() hides them, so the CPU really runs the multiply or divide
               that EcoLang chose, and nothing else.                              */
            operand(a, code[i].arg1);
            operand(b, code[i].arg2);
            fprintf(f, "    v_%s = OPAQUE_A(%s) %s OPAQUE_B(%s);\n", code[i].result, a, op, b);
            continue;
        }
        if (is_binary_op(op)) {
            operand(a, code[i].arg1);
            operand(b, code[i].arg2);
            fprintf(f, "    v_%s = %s %s %s;\n", code[i].result, a, op, b);
            continue;
        }
        fprintf(f, "    /* unknown instruction: %s */\n", op);
    }
    fprintf(f, "    return 0;\n}\n\n");
}

void generate_c(const char *out_file, const char *func_name)
{
    FILE *f = fopen(out_file, "w");
    if (f == NULL) {
        printf("cannot write %s\n", out_file);
        return;
    }

    fprintf(f, "/* generated by EcoLang - compile with:  gcc -O0 -fwrapv -w file.c -o prog */\n");
    fprintf(f, "#include <stdio.h>\n#include <stdlib.h>\n#include <time.h>\n\n");

    /* mulhs = high 64 bits of a 64 x 64 multiply. Real compilers do this in ONE
       instruction (x86-64: one-operand imulq). gcc -O0 would turn the plain C
       version into ~15 instructions, which is unfair, so on x86-64 we write
       the single instruction directly.                                      */
    fprintf(f, "/* a value gcc cannot see through, so it cannot optimise the operation */\n");
    fprintf(f, "static volatile long long opaque_a_, opaque_b_;\n");
    fprintf(f, "#define OPAQUE_A(v) (opaque_a_ = (v), opaque_a_)\n");
    fprintf(f, "#define OPAQUE_B(v) (opaque_b_ = (v), opaque_b_)\n\n");
    fprintf(f, "#if defined(__x86_64__)\n");
    fprintf(f, "#define MULHS(a, b) ({ long long lo_ = (a), hi_; \\\n");
    fprintf(f, "    __asm__(\"imulq %%2\" : \"=d\"(hi_), \"+a\"(lo_) : \"r\"((long long) (b)) : \"cc\"); hi_; })\n");
    fprintf(f, "#else\n");
    fprintf(f, "#define MULHS(a, b) ((long long) (((__int128) (a) * (b)) >> 64))\n");
    fprintf(f, "#endif\n\n");
    fprintf(f, "#define HEAP_SIZE 4000000\n");
    fprintf(f, "static long long HEAP[HEAP_SIZE];\n");
    fprintf(f, "static long long heap_used = 0;\n");
    fprintf(f, "static long long ARR_START[200000];\n");
    fprintf(f, "static long long arr_count = 1;\n");
    fprintf(f, "static long long PSTACK[200000];\n");
    fprintf(f, "static int psp = 0;\n\n");
    fprintf(f, "static long long eco_alloc(long long n)\n{\n");
    fprintf(f, "    if (n <= 0 || heap_used + n > HEAP_SIZE || arr_count >= 200000) {\n");
    fprintf(f, "        printf(\"out of array memory\\n\");\n        exit(1);\n    }\n");
    fprintf(f, "    long long id = arr_count;\n    arr_count = arr_count + 1;\n");
    fprintf(f, "    ARR_START[id] = heap_used;\n");
    fprintf(f, "    for (long long i = 0; i < n; i++) {\n        HEAP[heap_used + i] = 0;\n    }\n");
    fprintf(f, "    heap_used = heap_used + n;\n    return id;\n}\n\n");

    /* prototypes, so functions can call each other in any order */
    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, "func") == 0) {
            write_header(f, i, func_end_at(i));
            fprintf(f, ";\n");
        }
    }
    fprintf(f, "\n");

    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, "func") == 0) {
            int e = func_end_at(i);
            write_function(f, i, e);
            i = e;
        }
    }

    /* main: run the function, then time it */
    int n = params_of(func_name);
    fprintf(f, "static long long run_once(long long *a)\n{\n");
    fprintf(f, "    heap_used = 0;\n    arr_count = 1;\n    psp = 0;\n");
    fprintf(f, "    return f_%s(", func_name);
    for (int k = 0; k < n; k++) {
        if (k > 0) {
            fprintf(f, ", ");
        }
        fprintf(f, "a[%d]", k);
    }
    fprintf(f, ");\n}\n\n");

    fprintf(f, "static double now_ns(void)\n{\n    struct timespec ts;\n");
    fprintf(f, "    clock_gettime(CLOCK_MONOTONIC, &ts);\n");
    fprintf(f, "    return ts.tv_sec * 1e9 + ts.tv_nsec;\n}\n\n");

    fprintf(f, "int main(int argc, char *argv[])\n{\n");
    fprintf(f, "    long long a[20];\n");
    fprintf(f, "    for (int i = 0; i < 20; i++) {\n        a[i] = 0;\n    }\n");
    fprintf(f, "    for (int i = 1; i < argc && i <= 20; i++) {\n        a[i - 1] = atoll(argv[i]);\n    }\n");
    fprintf(f, "    long long result = run_once(a);\n\n");
    fprintf(f, "    /* find how many runs take about 0.1 seconds */\n");
    fprintf(f, "    long long reps = 1;\n");
    fprintf(f, "    while (1) {\n");
    fprintf(f, "        double t0 = now_ns();\n");
    fprintf(f, "        for (long long r = 0; r < reps; r++) {\n            run_once(a);\n        }\n");
    fprintf(f, "        double t1 = now_ns();\n");
    fprintf(f, "        if (t1 - t0 > 1e8 || reps > 100000000) {\n            break;\n        }\n");
    fprintf(f, "        reps = reps * 2;\n    }\n\n");
    fprintf(f, "    /* measure 7 times, keep the median (middle) value */\n");
    fprintf(f, "    double times[7];\n");
    fprintf(f, "    for (int m = 0; m < 7; m++) {\n");
    fprintf(f, "        double t0 = now_ns();\n");
    fprintf(f, "        for (long long r = 0; r < reps; r++) {\n            run_once(a);\n        }\n");
    fprintf(f, "        double t1 = now_ns();\n");
    fprintf(f, "        times[m] = (t1 - t0) / reps;\n    }\n");
    fprintf(f, "    for (int i = 0; i < 7; i++) {\n");
    fprintf(f, "        for (int j = 0; j < 6 - i; j++) {\n");
    fprintf(f, "            if (times[j] > times[j + 1]) {\n");
    fprintf(f, "                double tmp = times[j];\n                times[j] = times[j + 1];\n                times[j + 1] = tmp;\n");
    fprintf(f, "            }\n        }\n    }\n");
    fprintf(f, "    printf(\"result %%lld\\n\", result);\n");
    fprintf(f, "    printf(\"time_ns %%.1f\\n\", times[3]);\n");
    fprintf(f, "    printf(\"min_ns %%.1f\\n\", times[0]);\n");
    fprintf(f, "    printf(\"max_ns %%.1f\\n\", times[6]);\n");
    fprintf(f, "    return 0;\n}\n");

    fclose(f);
}
