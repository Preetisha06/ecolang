/* optimize.c - EcoLang optimisation passes

   CF  = constant folding        t1 = 4 * 8         ->  t1 = 32
   CP  = constant/copy propagation   x = 32; y = i * x  ->  y = i * 32
   SR  = strength reduction      y = i * 32         ->  y = i << 5
   DCE = dead code elimination   removes values nobody uses,
                                 and code that can never run

   A pass never inserts instructions. It only rewrites them, or marks
   them "nop" (no operation). After each pass, all nops are removed.
   The passes run again and again until nothing changes any more.      */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "tac.h"
#include "interp.h"
#include "optimize.h"

#define MAX_FACTS 200

/* how many changes each pass made (for the summary) */
int changes_cf = 0;
int changes_cp = 0;
int changes_sr = 0;
int changes_dce = 0;
int changes_cse = 0;
int changes_licm = 0;

/* goal: alpha = 1 speed, 0.5 balanced, 0 carbon */
double goal_alpha = 1.0;        /* alpha of the function being optimised now */
double program_alpha = 1.0;     /* alpha from the command line (--goal / --alpha) */
char current_func[MAX_LEN] = "";

/* ================= decision log (--explain) =================
   Every time the goal is asked "should I make this swap?", the question
   and the answer are written down here, so the compiler can explain itself. */
#define MAX_LOG 500
struct Decision {
    char func[MAX_LEN];
    char before[140];
    char after[140];
    double t_hat;
    double e_hat;
    double alpha;
    double cost;
    int accepted;
};
struct Decision decision_log[MAX_LOG];
int log_count = 0;

/* the same question can be asked again in a later round: log it only once */
static void log_decision(const char *before, const char *after, double t_hat,
                         double e_hat, double cost, int accepted)
{
    for (int k = 0; k < log_count; k++) {
        if (strcmp(decision_log[k].func, current_func) == 0 &&
            strcmp(decision_log[k].before, before) == 0) {
            return;
        }
    }
    if (log_count >= MAX_LOG) {
        return;
    }
    struct Decision *d = &decision_log[log_count];
    strcpy(d->func, current_func);
    snprintf(d->before, sizeof(d->before), "%s", before);
    snprintf(d->after, sizeof(d->after), "%s", after);
    d->t_hat = t_hat;
    d->e_hat = e_hat;
    d->alpha = goal_alpha;
    d->cost = cost;
    d->accepted = accepted;
    log_count = log_count + 1;
}
int decomposed = 0;          /* multiplies turned into shifts + adds */
int div_rewritten = 0;       /* divisions turned into multiply + shifts */

/* ================= the goal decision =================
   Compare the NEW instructions with the OLD ones:
       T^ = new cycles / old cycles
       E^ = new energy / old energy
       cost = alpha * T^ + (1 - alpha) * E^
   The change is kept only if cost < 1 (cheaper than before for this goal). */
static int goal_accepts(long long old_e, long long old_t, long long new_e, long long new_t,
                        const char *before, const char *after)
{
    if (old_e <= 0 || old_t <= 0) {
        return 0;
    }
    double t_hat = (double) new_t / (double) old_t;
    double e_hat = (double) new_e / (double) old_e;
    double cost = goal_alpha * t_hat + (1.0 - goal_alpha) * e_hat;
    int accepted = 0;
    if (cost < 1.0) {
        accepted = 1;
    }
    log_decision(before, after, t_hat, e_hat, cost, accepted);
    return accepted;
}

/* instruction i as text, e.g. "t3 = i * 10" (for the decision log) */
static void describe(int i, char *buf)
{
    snprintf(buf, 140, "%s = %s %s %s", code[i].result, code[i].arg1, code[i].op, code[i].arg2);
}

/* ================= small helpers ================= */

static int is_num(const char *s)
{
    if (isdigit(s[0])) {
        return 1;
    }
    if (s[0] == '-' && isdigit(s[1])) {
        return 1;
    }
    return 0;
}

/* ops of the form:  result = arg1 op arg2 */
static int is_binary(const char *op)
{
    char *list[14] = { "+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "<<", ">>", "mulhs" };
    for (int i = 0; i < 14; i++) {
        if (strcmp(op, list[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

/* does this instruction READ arg1? */
static int reads_arg1(const char *op)
{
    if (is_binary(op)) {
        return 1;
    }
    if (strcmp(op, "=") == 0 || strcmp(op, "iffalse") == 0 ||
        strcmp(op, "param") == 0 || strcmp(op, "return") == 0 ||
        strcmp(op, "load") == 0 || strcmp(op, "store") == 0 ||
        strcmp(op, "alloc") == 0) {
        return 1;
    }
    return 0;
}

/* does this instruction READ arg2? */
static int reads_arg2(const char *op)
{
    if (is_binary(op) || strcmp(op, "load") == 0 || strcmp(op, "store") == 0) {
        return 1;
    }
    return 0;
}

/* only "store" READS its result field:  a[i] = value  (value is in result) */
static int reads_result(const char *op)
{
    if (strcmp(op, "store") == 0) {
        return 1;
    }
    return 0;
}

/* name of the variable this instruction WRITES, or NULL */
static char *written_var(struct Instr *in)
{
    if (is_binary(in->op) || strcmp(in->op, "=") == 0 || strcmp(in->op, "call") == 0 ||
        strcmp(in->op, "load") == 0 || strcmp(in->op, "alloc") == 0) {
        return in->result;
    }
    if (strcmp(in->op, "param_in") == 0) {
        return in->arg1;
    }
    return NULL;
}

static void make_nop(int i)
{
    strcpy(code[i].op, "nop");
    strcpy(code[i].arg1, "");
    strcpy(code[i].arg2, "");
    strcpy(code[i].result, "");
}

/* turn instruction i into:  dest = value */
static void make_copy(int i, const char *value, const char *dest)
{
    char v[MAX_LEN];
    char d[MAX_LEN];
    strcpy(v, value);    /* copy first, because value/dest may point inside code[i] */
    strcpy(d, dest);
    strcpy(code[i].op, "=");
    strcpy(code[i].arg1, v);
    strcpy(code[i].arg2, "");
    strcpy(code[i].result, d);
}

/* index of the "end" of the function that starts at 'start' */
static int func_end(int start)
{
    for (int i = start + 1; i < code_count; i++) {
        if (strcmp(code[i].op, "end") == 0) {
            return i;
        }
    }
    return code_count - 1;
}

/* how many times is 'name' read inside one function */
static int count_reads(int start, int end, const char *name)
{
    int count = 0;
    for (int i = start; i <= end; i++) {
        if (reads_arg1(code[i].op) && strcmp(code[i].arg1, name) == 0) {
            count = count + 1;
        }
        if (reads_arg2(code[i].op) && strcmp(code[i].arg2, name) == 0) {
            count = count + 1;
        }
        if (reads_result(code[i].op) && strcmp(code[i].result, name) == 0) {
            count = count + 1;
        }
    }
    return count;
}

/* how many times is 'name' written inside one function */
static int count_writes(int start, int end, const char *name)
{
    int count = 0;
    for (int i = start; i <= end; i++) {
        char *w = written_var(&code[i]);
        if (w != NULL && strcmp(w, name) == 0) {
            count = count + 1;
        }
    }
    return count;
}

/* remove all nops from the code list */
static void compact(void)
{
    int j = 0;
    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, "nop") != 0) {
            code[j] = code[i];
            j = j + 1;
        }
    }
    code_count = j;
}

/* ================= pass 1: constant folding ================= */

static int constant_folding(int start, int end)
{
    int changed = 0;
    for (int i = start; i <= end; i++) {
        char *op = code[i].op;

        /* t = 4 * 8  ->  t = 32 */
        if (is_binary(op) && is_num(code[i].arg1) && is_num(code[i].arg2)) {
            long long x = atoll(code[i].arg1);
            long long y = atoll(code[i].arg2);
            long long v = 0;

            if ((strcmp(op, "/") == 0 || strcmp(op, "%") == 0) && y == 0) {
                continue;   /* never fold a division by zero */
            }

            if (strcmp(op, "+") == 0)       v = x + y;
            else if (strcmp(op, "-") == 0)  v = x - y;
            else if (strcmp(op, "*") == 0)  v = x * y;
            else if (strcmp(op, "/") == 0)  v = x / y;
            else if (strcmp(op, "%") == 0)  v = x % y;
            else if (strcmp(op, "<") == 0)  v = x < y;
            else if (strcmp(op, ">") == 0)  v = x > y;
            else if (strcmp(op, "<=") == 0) v = x <= y;
            else if (strcmp(op, ">=") == 0) v = x >= y;
            else if (strcmp(op, "==") == 0) v = x == y;
            else if (strcmp(op, "!=") == 0) v = x != y;
            else if (strcmp(op, "<<") == 0) v = x << y;
            else if (strcmp(op, ">>") == 0) v = x >> y;
            else if (strcmp(op, "mulhs") == 0) v = (long long) (((__int128) x * y) >> 64);

            char buf[MAX_LEN];
            sprintf(buf, "%lld", v);
            make_copy(i, buf, code[i].result);
            changed = changed + 1;
        }
        /* ifFalse 0 goto L  ->  goto L        ifFalse 1 goto L  ->  (removed) */
        else if (strcmp(op, "iffalse") == 0 && is_num(code[i].arg1)) {
            if (atoll(code[i].arg1) == 0) {
                strcpy(code[i].op, "goto");
                strcpy(code[i].arg1, "");
            } else {
                make_nop(i);
            }
            changed = changed + 1;
        }
    }
    return changed;
}

/* ================= pass 2: constant / copy propagation ================= */

/* Inside one basic block (code between two labels), remember facts like
   "x is 32" or "x is the same as y", and replace later reads of x.      */
static int propagation(int start, int end)
{
    char fact_var[MAX_FACTS][MAX_LEN];
    char fact_val[MAX_FACTS][MAX_LEN];
    int nf = 0;
    int changed = 0;

    for (int i = start; i <= end; i++) {
        char *op = code[i].op;

        /* a label means another path can jump here: forget everything */
        if (strcmp(op, "label") == 0 || strcmp(op, "func") == 0) {
            nf = 0;
            continue;
        }

        /* replace reads using the facts we know */
        for (int k = 0; k < nf; k++) {
            if (reads_arg1(op) && strcmp(code[i].arg1, fact_var[k]) == 0) {
                strcpy(code[i].arg1, fact_val[k]);
                changed = changed + 1;
            }
            if (reads_arg2(op) && strcmp(code[i].arg2, fact_var[k]) == 0) {
                strcpy(code[i].arg2, fact_val[k]);
                changed = changed + 1;
            }
            if (reads_result(op) && strcmp(code[i].result, fact_var[k]) == 0) {
                strcpy(code[i].result, fact_val[k]);
                changed = changed + 1;
            }
        }

        /* this instruction writes a variable: old facts about it are wrong now */
        char *w = written_var(&code[i]);
        if (w != NULL) {
            int keep = 0;
            for (int k = 0; k < nf; k++) {
                if (strcmp(fact_var[k], w) != 0 && strcmp(fact_val[k], w) != 0) {
                    strcpy(fact_var[keep], fact_var[k]);
                    strcpy(fact_val[keep], fact_val[k]);
                    keep = keep + 1;
                }
            }
            nf = keep;

            /* new fact:  w = value */
            if (strcmp(op, "=") == 0 && strcmp(code[i].arg1, w) != 0 && nf < MAX_FACTS) {
                strcpy(fact_var[nf], w);
                strcpy(fact_val[nf], code[i].arg1);
                nf = nf + 1;
            }
        }
    }
    return changed;
}

/* t3 = i + 1      ->   i = i + 1
   i = t3                                     (only if t3 is used nowhere else) */
static int merge_temps(int start, int end)
{
    int changed = 0;
    for (int i = start; i < end; i++) {
        int j = i + 1;
        int defines_value = is_binary(code[i].op) || strcmp(code[i].op, "load") == 0;
        if (defines_value && strcmp(code[j].op, "=") == 0 &&
            strcmp(code[j].arg1, code[i].result) == 0) {

            char *x = code[i].result;
            if (count_reads(start, end, x) == 1 && count_writes(start, end, x) == 1) {
                strcpy(code[i].result, code[j].result);
                make_nop(j);
                changed = changed + 1;
            }
        }
    }
    return changed;
}

/* ================= pass 3: strength reduction ================= */

/* returns k if c == 2^k, otherwise -1 */
static int power_of_two(long long c)
{
    long long p = 1;
    int k = 0;
    while (p < c) {
        p = p * 2;
        k = k + 1;
    }
    if (p == c) {
        return k;
    }
    return -1;
}

/* put a new instruction at position pos (everything after moves down by 1) */
static void insert_at(int pos, const char *op, const char *a1, const char *a2, const char *res)
{
    if (code_count >= MAX_CODE) {
        printf("too many instructions\n");
        exit(1);
    }
    for (int j = code_count; j > pos; j--) {
        code[j] = code[j - 1];
    }
    strcpy(code[pos].op, op);
    strcpy(code[pos].arg1, a1);
    strcpy(code[pos].arg2, a2);
    strcpy(code[pos].result, res);
    code_count = code_count + 1;
}

/* x * c  ->  shifts and adds, e.g.  x * 10 = (x << 3) + (x << 1)
   Returns how many instructions were ADDED (0 if nothing was done). */
static int decompose_multiply(int i, const char *var, long long c)
{
    /* count how many cheap instructions this needs */
    int bits = 0;
    int shifts = 0;
    for (int b = 0; b < 31; b++) {
        if ((c >> b) & 1) {
            bits = bits + 1;
            if (b > 0) {
                shifts = shifts + 1;
            }
        }
    }
    int k = shifts + (bits - 1);

    /* ask the goal: is k ALU instructions better than 1 MUL? */
    long long old_e = energy_cost[CAT_MUL];
    long long old_t = cycle_cost[CAT_MUL];
    long long new_e = k * energy_cost[CAT_ALU];
    long long new_t = k * cycle_cost[CAT_ALU];
    char before[140];
    char after[140];
    describe(i, before);
    snprintf(after, sizeof(after), "%d shifts/adds", k);
    if (!goal_accepts(old_e, old_t, new_e, new_t, before, after)) {
        return -1;   /* goal says no: keep the multiply */
    }

    /* build the new instructions in a small list first */
    char ops[64][MAX_LEN];
    char as[64][MAX_LEN];
    char bs[64][MAX_LEN];
    char rs[64][MAX_LEN];
    int n = 0;

    char acc[MAX_LEN] = "";
    for (int b = 0; b < 31; b++) {
        if ((c >> b) & 1) {
            char term[MAX_LEN];
            if (b == 0) {
                strcpy(term, var);                  /* x * 1 = x itself */
            } else {
                char *t = new_temp();
                strcpy(ops[n], "<<");
                strcpy(as[n], var);
                sprintf(bs[n], "%d", b);
                strcpy(rs[n], t);
                n = n + 1;
                strcpy(term, t);
            }

            if (strlen(acc) == 0) {
                strcpy(acc, term);
            } else {
                char *t = new_temp();
                strcpy(ops[n], "+");
                strcpy(as[n], acc);
                strcpy(bs[n], term);
                strcpy(rs[n], t);
                n = n + 1;
                strcpy(acc, t);
            }
        }
    }

    /* the last add must write the original result variable */
    strcpy(rs[n - 1], code[i].result);

    /* first new instruction replaces the multiply, the rest are inserted after it */
    strcpy(code[i].op, ops[0]);
    strcpy(code[i].arg1, as[0]);
    strcpy(code[i].arg2, bs[0]);
    strcpy(code[i].result, rs[0]);
    for (int j = 1; j < n; j++) {
        insert_at(i + j, ops[j], as[j], bs[j], rs[j]);
    }
    decomposed = decomposed + 1;
    return n - 1;
}

/* ---------- division by a constant ----------
   x / d  is slow (DIV: ~40 cycles). It can be replaced by a "magic number"
   multiply and shifts (Granlund & Montgomery, PLDI 1994; the code below
   follows Warren, "Hacker's Delight", Fig. 10-1, widened to 64 bits):

       t1 = mulhs(x, M)      high 64 bits of x * M
       t1 = t1 + x           (only when M is negative)
       t2 = t1 >> s          (only when s > 0)
       t3 = t2 >> 63         -1 if t2 is negative, else 0
       q  = t2 - t3          round towards zero, like C does

   and for x % d:            r = x - q * d                           */
static void magic_numbers(long long d, long long *M, int *s)
{
    const unsigned long long two63 = 0x8000000000000000ULL;
    unsigned long long ad = (unsigned long long) d;          /* d >= 2 */
    unsigned long long t = two63 + ((unsigned long long) d >> 63);
    unsigned long long anc = t - 1 - t % ad;
    int p = 63;
    unsigned long long q1 = two63 / anc;
    unsigned long long r1 = two63 - q1 * anc;
    unsigned long long q2 = two63 / ad;
    unsigned long long r2 = two63 - q2 * ad;
    unsigned long long delta;
    do {
        p = p + 1;
        q1 = 2 * q1;
        r1 = 2 * r1;
        if (r1 >= anc) {
            q1 = q1 + 1;
            r1 = r1 - anc;
        }
        q2 = 2 * q2;
        r2 = 2 * r2;
        if (r2 >= ad) {
            q2 = q2 + 1;
            r2 = r2 - ad;
        }
        delta = ad - r2;
    } while (q1 < delta || (q1 == delta && r1 == 0));
    *M = (long long) (q2 + 1);
    *s = p - 64;
}

/* returns how many instructions were ADDED, or -1 if the goal said no */
static int rewrite_division(int i, const char *x, long long d, int is_mod)
{
    long long M;
    int s;
    magic_numbers(d, &M, &s);

    /* count the new instructions */
    int alu = 2;                 /* t3 = t2 >> 63,  q = t2 - t3 */
    if (M < 0) {
        alu = alu + 1;           /* t1 = t1 + x */
    }
    if (s > 0) {
        alu = alu + 1;           /* t2 = t1 >> s */
    }
    int mul = 1;                 /* mulhs */
    if (is_mod) {
        mul = mul + 1;           /* q * d */
        alu = alu + 1;           /* x - q * d */
    }

    /* ask the goal: is this better than 1 DIV? */
    long long new_e = mul * energy_cost[CAT_MUL] + alu * energy_cost[CAT_ALU];
    long long new_t = mul * cycle_cost[CAT_MUL] + alu * cycle_cost[CAT_ALU];
    char before[140];
    char after[140];
    describe(i, before);
    snprintf(after, sizeof(after), "magic multiply (%d MUL + %d ALU)", mul, alu);
    if (!goal_accepts(energy_cost[CAT_DIV], cycle_cost[CAT_DIV], new_e, new_t, before, after)) {
        return -1;
    }

    char ops[8][MAX_LEN];
    char as[8][MAX_LEN];
    char bs[8][MAX_LEN];
    char rs[8][MAX_LEN];
    int n = 0;
    char cur[MAX_LEN];
    char num[MAX_LEN];

    /* t1 = mulhs(x, M) */
    char *t = new_temp();
    strcpy(ops[n], "mulhs");
    strcpy(as[n], x);
    sprintf(bs[n], "%lld", M);
    strcpy(rs[n], t);
    n = n + 1;
    strcpy(cur, t);

    /* t1 = t1 + x   (only if M < 0) */
    if (M < 0) {
        t = new_temp();
        strcpy(ops[n], "+");
        strcpy(as[n], cur);
        strcpy(bs[n], x);
        strcpy(rs[n], t);
        n = n + 1;
        strcpy(cur, t);
    }

    /* t2 = t1 >> s */
    if (s > 0) {
        t = new_temp();
        strcpy(ops[n], ">>");
        strcpy(as[n], cur);
        sprintf(bs[n], "%d", s);
        strcpy(rs[n], t);
        n = n + 1;
        strcpy(cur, t);
    }

    /* t3 = t2 >> 63 */
    char *sign = new_temp();
    strcpy(ops[n], ">>");
    strcpy(as[n], cur);
    strcpy(bs[n], "63");
    strcpy(rs[n], sign);
    n = n + 1;

    /* q = t2 - t3 */
    char *q = new_temp();
    strcpy(ops[n], "-");
    strcpy(as[n], cur);
    strcpy(bs[n], sign);
    strcpy(rs[n], q);
    n = n + 1;

    if (is_mod) {
        /* t4 = q * d ;  r = x - t4 */
        t = new_temp();
        sprintf(num, "%lld", d);
        strcpy(ops[n], "*");
        strcpy(as[n], q);
        strcpy(bs[n], num);
        strcpy(rs[n], t);
        n = n + 1;

        strcpy(ops[n], "-");
        strcpy(as[n], x);
        strcpy(bs[n], t);
        strcpy(rs[n], "");     /* filled below */
        n = n + 1;
    }

    /* the last instruction writes the original result variable */
    strcpy(rs[n - 1], code[i].result);

    strcpy(code[i].op, ops[0]);
    strcpy(code[i].arg1, as[0]);
    strcpy(code[i].arg2, bs[0]);
    strcpy(code[i].result, rs[0]);
    for (int j = 1; j < n; j++) {
        insert_at(i + j, ops[j], as[j], bs[j], rs[j]);
    }
    div_rewritten = div_rewritten + 1;
    return n - 1;
}

static int strength_reduction(int start, int *end_ptr)
{
    int changed = 0;
    for (int i = start; i <= *end_ptr; i++) {
        char *op = code[i].op;
        char *a1 = code[i].arg1;
        char *a2 = code[i].arg2;

        if (strcmp(op, "*") == 0) {
            char var[MAX_LEN];
            long long c;
            if (is_num(a2) && !is_num(a1)) {
                strcpy(var, a1);
                c = atoll(a2);
            } else if (is_num(a1) && !is_num(a2)) {
                strcpy(var, a2);
                c = atoll(a1);
            } else {
                continue;
            }

            /* every option below replaces 1 MUL with ALU work, so ask the goal */
            long long mul_e = energy_cost[CAT_MUL];
            long long mul_t = cycle_cost[CAT_MUL];
            long long alu_e = energy_cost[CAT_ALU];
            long long alu_t = cycle_cost[CAT_ALU];

            char before[140];
            describe(i, before);
            if (c == 0 && goal_accepts(mul_e, mul_t, alu_e, alu_t, before, "0")) {   /* x * 0 -> 0 */
                make_copy(i, "0", code[i].result);
                changed = changed + 1;
            } else if (c == 1 && goal_accepts(mul_e, mul_t, alu_e, alu_t, before, var)) {   /* x * 1 -> x */
                make_copy(i, var, code[i].result);
                changed = changed + 1;
            } else if (power_of_two(c) > 0) {                                  /* x * 8 -> x << 3 */
                char after[140];
                snprintf(after, sizeof(after), "%s << %d", var, power_of_two(c));
                if (goal_accepts(mul_e, mul_t, alu_e, alu_t, before, after)) {
                    char shift[MAX_LEN];
                    sprintf(shift, "%d", power_of_two(c));
                    strcpy(code[i].op, "<<");
                    strcpy(code[i].arg1, var);
                    strcpy(code[i].arg2, shift);
                    changed = changed + 1;
                }
            } else if (c > 2 && c < 2147483647) {                                /* x * 10 -> (x<<3) + (x<<1) */
                int added = decompose_multiply(i, var, c);
                if (added >= 0) {
                    *end_ptr = *end_ptr + added;
                    i = i + added;
                    changed = changed + 1;
                }
            }
        }
        else if (strcmp(op, "+") == 0 && strcmp(a2, "0") == 0) {   /* x + 0 -> x */
            make_copy(i, a1, code[i].result);
            changed = changed + 1;
        }
        else if (strcmp(op, "+") == 0 && strcmp(a1, "0") == 0) {   /* 0 + x -> x */
            make_copy(i, a2, code[i].result);
            changed = changed + 1;
        }
        else if (strcmp(op, "-") == 0 && strcmp(a2, "0") == 0) {   /* x - 0 -> x */
            make_copy(i, a1, code[i].result);
            changed = changed + 1;
        }
        else if (strcmp(op, "/") == 0 && strcmp(a2, "1") == 0) {   /* x / 1 -> x */
            make_copy(i, a1, code[i].result);
            changed = changed + 1;
        }
        else if ((strcmp(op, "/") == 0 || strcmp(op, "%") == 0) &&
                 is_num(a2) && !is_num(a1) && atoll(a2) >= 2) {      /* x / 11 -> magic multiply */
            char x[MAX_LEN];
            strcpy(x, a1);
            int is_mod = 0;
            if (strcmp(op, "%") == 0) {
                is_mod = 1;
            }
            int added = rewrite_division(i, x, atoll(a2), is_mod);
            if (added >= 0) {
                *end_ptr = *end_ptr + added;
                i = i + added;
                changed = changed + 1;
            }
        }
    }
    return changed;
}

/* ================= pass 4: dead code elimination ================= */

static int dead_code_elimination(int start, int end)
{
    int changed = 0;

    /* (a) code after goto/return can never run, until the next label */
    int unreachable = 0;
    for (int i = start + 1; i < end; i++) {
        if (strcmp(code[i].op, "label") == 0) {
            unreachable = 0;
            continue;
        }
        if (unreachable && strcmp(code[i].op, "nop") != 0) {
            make_nop(i);
            changed = changed + 1;
            continue;
        }
        if (strcmp(code[i].op, "goto") == 0 || strcmp(code[i].op, "return") == 0) {
            unreachable = 1;
        }
    }

    /* (b) values that are written but never read */
    for (int i = start; i <= end; i++) {
        char *op = code[i].op;
        if (is_binary(op) || strcmp(op, "=") == 0 || strcmp(op, "load") == 0) {
            if (strcmp(op, "=") == 0 && strcmp(code[i].arg1, code[i].result) == 0) {
                make_nop(i);                     /* x = x does nothing */
                changed = changed + 1;
            } else if (count_reads(start, end, code[i].result) == 0) {
                make_nop(i);
                changed = changed + 1;
            }
        }
    }
    return changed;
}

/* ================= pass 5: common subexpression elimination (CSE) =================
   Inside one basic block, if the same calculation (or the same array read)
   was already done and its inputs have not changed, reuse the old answer:
       t1 = a[j]  ...  t7 = a[j]      ->      t7 = t1
   An array WRITE (store) or a function call forgets all remembered array
   reads, because the array may have changed.                                */

#define MAX_EXPRS 300

static int is_commutative(const char *op)
{
    if (strcmp(op, "+") == 0 || strcmp(op, "*") == 0 || strcmp(op, "==") == 0 ||
        strcmp(op, "!=") == 0 || strcmp(op, "mulhs") == 0) {
        return 1;
    }
    return 0;
}

static int common_subexpressions(int start, int end)
{
    char e_op[MAX_EXPRS][MAX_LEN];
    char e_a1[MAX_EXPRS][MAX_LEN];
    char e_a2[MAX_EXPRS][MAX_LEN];
    char e_res[MAX_EXPRS][MAX_LEN];
    int ne = 0;
    int changed = 0;

    for (int i = start; i <= end; i++) {
        char *op = code[i].op;

        /* new basic block: forget everything */
        if (strcmp(op, "label") == 0 || strcmp(op, "func") == 0) {
            ne = 0;
            continue;
        }

        int pure = is_binary(op) || strcmp(op, "load") == 0;

        /* (1) already computed?  then copy the old result instead */
        if (pure) {
            for (int k = 0; k < ne; k++) {
                if (strcmp(e_op[k], op) != 0) {
                    continue;
                }
                int same = 0;
                if (strcmp(e_a1[k], code[i].arg1) == 0 && strcmp(e_a2[k], code[i].arg2) == 0) {
                    same = 1;
                }
                if (is_commutative(op) &&
                    strcmp(e_a1[k], code[i].arg2) == 0 && strcmp(e_a2[k], code[i].arg1) == 0) {
                    same = 1;
                }
                if (same) {
                    make_copy(i, e_res[k], code[i].result);
                    changed = changed + 1;
                    break;
                }
            }
        }

        /* (2) a store or a call may change arrays: forget remembered loads */
        if (strcmp(code[i].op, "store") == 0 || strcmp(code[i].op, "call") == 0) {
            int keep = 0;
            for (int k = 0; k < ne; k++) {
                if (strcmp(e_op[k], "load") != 0) {
                    strcpy(e_op[keep], e_op[k]);
                    strcpy(e_a1[keep], e_a1[k]);
                    strcpy(e_a2[keep], e_a2[k]);
                    strcpy(e_res[keep], e_res[k]);
                    keep = keep + 1;
                }
            }
            ne = keep;
        }

        /* (3) the written variable changed: forget expressions that use it */
        char *w = written_var(&code[i]);
        if (w != NULL) {
            int keep = 0;
            for (int k = 0; k < ne; k++) {
                if (strcmp(e_a1[k], w) != 0 && strcmp(e_a2[k], w) != 0 && strcmp(e_res[k], w) != 0) {
                    strcpy(e_op[keep], e_op[k]);
                    strcpy(e_a1[keep], e_a1[k]);
                    strcpy(e_a2[keep], e_a2[k]);
                    strcpy(e_res[keep], e_res[k]);
                    keep = keep + 1;
                }
            }
            ne = keep;
        }

        /* (4) remember this calculation (if it is still a calculation) */
        op = code[i].op;
        pure = is_binary(op) || strcmp(op, "load") == 0;
        if (pure && w != NULL && ne < MAX_EXPRS &&
            strcmp(code[i].arg1, w) != 0 && strcmp(code[i].arg2, w) != 0) {
            strcpy(e_op[ne], op);
            strcpy(e_a1[ne], code[i].arg1);
            strcpy(e_a2[ne], code[i].arg2);
            strcpy(e_res[ne], w);
            ne = ne + 1;
        }
    }
    return changed;
}

/* ================= pass 6: loop-invariant code motion (LICM) =================
   A loop looks like:     Lx:  ...body...  goto Lx
   A calculation inside the body whose inputs never change in the loop gives
   the same answer every time round. Move it to just BEFORE the loop:
       L3:  t5 = i * n  ...  goto L3      ->      t5 = i * n   L3: ... goto L3
   Only safe calculations are moved: no division (it could divide by zero
   when the loop would not have run), no array reads, and only compiler
   temporaries (t1, t2 ...) that are written exactly once.                    */

static int is_temp_name(const char *s)
{
    if (s[0] != 't' || s[1] == '\0') {
        return 0;
    }
    for (int i = 1; s[i] != '\0'; i++) {
        if (s[i] < '0' || s[i] > '9') {
            return 0;
        }
    }
    return 1;
}

/* is 'name' written anywhere between positions from..to ? */
static int written_in_range(int from, int to, const char *name)
{
    for (int i = from; i <= to; i++) {
        char *w = written_var(&code[i]);
        if (w != NULL && strcmp(w, name) == 0) {
            return 1;
        }
    }
    return 0;
}

static int loop_invariant_motion(int start, int end)
{
    int changed = 0;
    for (int e = start; e <= end; e++) {
        if (strcmp(code[e].op, "goto") != 0) {
            continue;
        }
        /* find the label this goto jumps BACK to */
        int s = -1;
        for (int j = start; j < e; j++) {
            if (strcmp(code[j].op, "label") == 0 && strcmp(code[j].result, code[e].result) == 0) {
                s = j;
            }
        }
        if (s == -1) {
            continue;   /* a forward jump, not a loop */
        }
        /* the spot before the loop must be reachable by falling through */
        if (s - 1 >= start) {
            char *prev = code[s - 1].op;
            if (strcmp(prev, "goto") == 0 || strcmp(prev, "return") == 0) {
                continue;
            }
        }

        for (int i = s + 1; i < e; i++) {
            char *op = code[i].op;
            if (!is_binary(op) || strcmp(op, "/") == 0 || strcmp(op, "%") == 0) {
                continue;
            }
            if (!is_temp_name(code[i].result) || count_writes(start, end, code[i].result) != 1) {
                continue;
            }
            int a1_ok = is_num(code[i].arg1) || !written_in_range(s, e, code[i].arg1);
            int a2_ok = is_num(code[i].arg2) || !written_in_range(s, e, code[i].arg2);
            if (!a1_ok || !a2_ok) {
                continue;
            }

            /* move instruction i to position s (just before the label) */
            struct Instr moving = code[i];
            for (int j = i; j > s; j--) {
                code[j] = code[j - 1];
            }
            code[s] = moving;
            s = s + 1;            /* the label moved down by one */
            changed = changed + 1;
        }
    }
    return changed;
}

/* ================= running the passes ================= */

/* alpha for the function that starts at i: its own @goal, or the program's */
static double function_alpha(int i)
{
    if (strcmp(code[i].arg2, "speed") == 0) {
        return 1.0;
    }
    if (strcmp(code[i].arg2, "balanced") == 0) {
        return 0.5;
    }
    if (strcmp(code[i].arg2, "carbon") == 0) {
        return 0.0;
    }
    return program_alpha;
}

/* run one pass on every function, then remove the nops */
static int apply_pass(int which)
{
    int changed = 0;
    int i = 0;
    while (i < code_count) {
        if (strcmp(code[i].op, "func") == 0) {
            int e = func_end(i);
            /* a function marked @speed / @balanced / @carbon uses its own goal */
            goal_alpha = function_alpha(i);
            strcpy(current_func, code[i].arg1);
            if (which == 1) changed = changed + constant_folding(i, e);
            if (which == 2) changed = changed + propagation(i, e);
            if (which == 3) changed = changed + merge_temps(i, e);
            if (which == 4) changed = changed + strength_reduction(i, &e);
            if (which == 5) changed = changed + dead_code_elimination(i, e);
            if (which == 6) changed = changed + common_subexpressions(i, e);
            if (which == 7) changed = changed + loop_invariant_motion(i, e);
            e = func_end(i);    /* the function may have grown or shrunk */
            i = e + 1;
        } else {
            i = i + 1;
        }
    }
    compact();
    return changed;
}

/* sets all the summary counters back to 0 (the speed-budget search
   optimises many times, so it clears them between tries) */
void reset_opt_stats(void)
{
    changes_cf = 0;
    changes_cp = 0;
    changes_sr = 0;
    changes_dce = 0;
    changes_cse = 0;
    changes_licm = 0;
    decomposed = 0;
    div_rewritten = 0;
    log_count = 0;
}

void optimize_program(int do_cf, int do_cp, int do_dce, int do_sr,
                      int do_cse, int do_licm, double alpha)
{
    program_alpha = alpha;
    goal_alpha = alpha;
    for (int round = 0; round < 20; round++) {
        int total = 0;
        int c;

        if (do_cf) {
            c = apply_pass(1);
            changes_cf = changes_cf + c;
            total = total + c;
        }
        if (do_cp) {
            c = apply_pass(2) + apply_pass(3);
            changes_cp = changes_cp + c;
            total = total + c;
        }
        if (do_cse) {
            c = apply_pass(6);
            changes_cse = changes_cse + c;
            total = total + c;
        }
        if (do_licm) {
            c = apply_pass(7);
            changes_licm = changes_licm + c;
            total = total + c;
        }
        if (do_sr) {
            c = apply_pass(4);
            changes_sr = changes_sr + c;
            total = total + c;
        }
        if (do_dce) {
            c = apply_pass(5);
            changes_dce = changes_dce + c;
            total = total + c;
        }

        if (total == 0) {
            break;   /* nothing changed: we are done */
        }
    }
}

void print_opt_summary(void)
{
    printf("\n===== Optimisation summary =====\n");
    printf("constant folding      : %d changes\n", changes_cf);
    printf("propagation           : %d changes\n", changes_cp);
    printf("strength reduction    : %d changes\n", changes_sr);
    printf("dead code elimination : %d changes\n", changes_dce);
    printf("common subexpressions : %d changes\n", changes_cse);
    printf("loop-invariant motion : %d changes\n", changes_licm);

    /* multiplies by a number that the goal decided to KEEP */
    int kept = 0;
    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, "*") == 0 && (is_num(code[i].arg1) || is_num(code[i].arg2))) {
            kept = kept + 1;
        }
    }
    printf("goal alpha            : %.2f\n", program_alpha);
    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, "func") == 0 && strlen(code[i].arg2) > 0) {
            printf("   function %-10s: @%s (alpha = %.2f)\n", code[i].arg1, code[i].arg2, function_alpha(i));
        }
    }
    printf("multiplies -> shifts+adds : %d done, %d kept as multiply\n", decomposed, kept);

    int kept_div = 0;
    for (int i = 0; i < code_count; i++) {
        if ((strcmp(code[i].op, "/") == 0 || strcmp(code[i].op, "%") == 0) && is_num(code[i].arg2)) {
            kept_div = kept_div + 1;
        }
    }
    printf("divisions  -> magic multiply  : %d done, %d kept as division\n", div_rewritten, kept_div);
}

/* --explain: every swap the goal was asked about, and why it said yes or no */
void print_decision_log(void)
{
    printf("\n===== Why: goal decisions (cost = alpha*T + (1-alpha)*E, keep if cost < 1) =====\n");
    if (log_count == 0) {
        printf("no speed-vs-energy swaps were possible in this program\n");
        return;
    }
    printf("%-12s %-22s %-30s %5s %6s %6s %6s  %s\n",
           "function", "instruction", "swap to", "alpha", "T", "E", "cost", "decision");
    for (int k = 0; k < log_count; k++) {
        struct Decision *d = &decision_log[k];
        const char *verdict = "REJECTED";
        if (d->accepted) {
            verdict = "ACCEPTED";
        }
        char reason[64];
        if (d->t_hat < 1.0 && d->e_hat < 1.0) {
            strcpy(reason, "cheaper in time and energy");
        } else if (d->t_hat < 1.0) {
            strcpy(reason, "faster, but more energy");
        } else if (d->e_hat < 1.0) {
            strcpy(reason, "less energy, but slower");
        } else {
            strcpy(reason, "worse in both");
        }
        printf("%-12s %-22s %-30s %5.2f %6.2f %6.2f %6.2f  %s (%s)\n",
               d->func, d->before, d->after, d->alpha, d->t_hat, d->e_hat, d->cost,
               verdict, reason);
    }
}
