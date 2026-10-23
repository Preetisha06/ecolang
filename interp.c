/* interp.c - EcoLang interpreter + energy/carbon counter

   It walks through the three-address code one instruction at a time,
   does what each instruction says, and counts how many instructions
   of each type (category) actually ran.

   energy (units) = sum of  count x energy cost   for each category
   time  (cycles) = sum of  count x cycle cost    for each category
   energy (joules)= energy units x K
   carbon (grams) = joules / 3,600,000 x GRID      (GRID = g CO2 per kWh)  */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "tac.h"
#include "interp.h"

#define MAX_VARS    3000
#define MAX_LABELS  50000
#define MAX_PENDING 100
#define MAX_DEPTH   5000

char *cat_names[NUM_CATS] = { "NONE", "ALU", "MUL", "DIV", "MEMORY", "BRANCH" };

/* default costs - can be changed in energy_profile.txt */
long long energy_cost[NUM_CATS] = { 0, 1, 4, 8, 6, 1 };
long long cycle_cost[NUM_CATS]  = { 0, 1, 3, 20, 4, 1 };
double joules_per_unit = 0.000000001;   /* K: assumed 1 nJ per unit until RAPL calibration */
double grid_g_per_kwh  = 710.0;         /* India FY 2024-25, CEA */

long long cat_count[NUM_CATS];

int csv_mode = 0;
double opt_time_ms = 0.0;
const char *source_name = "";

/* ---------- array memory ----------
   All arrays live in one big list called heap.
   A variable like "a" only holds the array's ID number.
   Passing "a" to a function passes the ID, so the function
   changes the SAME array (like passing an array in C).        */
#define HEAP_SIZE  2000000
#define MAX_ARRAYS 100000
long long heap[HEAP_SIZE];
int heap_used = 0;
int arr_start[MAX_ARRAYS];
int arr_size[MAX_ARRAYS];
int arr_count = 1;          /* IDs start at 1, so 0 is never a valid array */
int label_pos[MAX_LABELS];
int call_depth = 0;

/* one function call = one frame with its own variables */
struct Frame {
    char names[MAX_VARS][MAX_LEN];
    long long values[MAX_VARS];
    int var_count;
    long long pending[MAX_PENDING];   /* values pushed by "param" before a call */
    int pending_count;
};

/* ---------- helpers ---------- */

void runtime_error(const char *msg, const char *detail)
{
    printf("Runtime error: %s %s\n", msg, detail);
    exit(1);
}

int get_category(const char *op)
{
    if (strcmp(op, "+") == 0 || strcmp(op, "-") == 0 || strcmp(op, "=") == 0 ||
        strcmp(op, "<") == 0 || strcmp(op, ">") == 0 || strcmp(op, "<=") == 0 ||
        strcmp(op, ">=") == 0 || strcmp(op, "==") == 0 || strcmp(op, "!=") == 0 ||
        strcmp(op, "<<") == 0 || strcmp(op, ">>") == 0) {
        return CAT_ALU;
    }
    if (strcmp(op, "*") == 0 || strcmp(op, "mulhs") == 0) {
        return CAT_MUL;
    }
    if (strcmp(op, "/") == 0 || strcmp(op, "%") == 0) {
        return CAT_DIV;
    }
    if (strcmp(op, "param") == 0 || strcmp(op, "param_in") == 0 ||
        strcmp(op, "call") == 0 || strcmp(op, "return") == 0 ||
        strcmp(op, "load") == 0 || strcmp(op, "store") == 0 ||
        strcmp(op, "alloc") == 0) {
        return CAT_MEM;
    }
    if (strcmp(op, "goto") == 0 || strcmp(op, "iffalse") == 0) {
        return CAT_BRANCH;
    }
    return CAT_NONE;
}

/* read costs from the profile file (if it exists) */
void load_profile(const char *filename)
{
    FILE *f = fopen(filename, "r");
    if (f == NULL) {
        printf("(no %s found, using default costs)\n", filename);
        return;
    }

    char word[64];
    while (fscanf(f, "%63s", word) == 1) {
        if (word[0] == '#') {
            /* comment line: skip the rest of it */
            char rest[256];
            fgets(rest, sizeof(rest), f);
        } else if (strcmp(word, "K") == 0) {
            fscanf(f, "%lf", &joules_per_unit);
        } else if (strcmp(word, "GRID") == 0) {
            fscanf(f, "%lf", &grid_g_per_kwh);
        } else {
            long long e = 0;
            long long c = 0;
            fscanf(f, "%lld %lld", &e, &c);
            for (int i = 1; i < NUM_CATS; i++) {
                if (strcmp(word, cat_names[i]) == 0) {
                    energy_cost[i] = e;
                    cycle_cost[i] = c;
                }
            }
        }
    }
    fclose(f);
}

int is_number(const char *s)
{
    if (isdigit(s[0])) {
        return 1;
    }
    if (s[0] == '-' && isdigit(s[1])) {
        return 1;
    }
    return 0;
}

void set_var(struct Frame *fr, const char *name, long long value)
{
    for (int i = 0; i < fr->var_count; i++) {
        if (strcmp(fr->names[i], name) == 0) {
            fr->values[i] = value;
            return;
        }
    }
    if (fr->var_count >= MAX_VARS) {
        runtime_error("too many variables, last one:", name);
    }
    strcpy(fr->names[fr->var_count], name);
    fr->values[fr->var_count] = value;
    fr->var_count = fr->var_count + 1;
}

long long get_var(struct Frame *fr, const char *name)
{
    for (int i = 0; i < fr->var_count; i++) {
        if (strcmp(fr->names[i], name) == 0) {
            return fr->values[i];
        }
    }
    runtime_error("variable used before it has a value:", name);
    return 0;
}

/* an operand is either a number like "5" or a variable like "a" */
long long value_of(struct Frame *fr, const char *s)
{
    if (is_number(s)) {
        return atoll(s);
    }
    return get_var(fr, s);
}

int find_function(const char *name)
{
    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, "func") == 0 && strcmp(code[i].arg1, name) == 0) {
            return i;
        }
    }
    return -1;
}

/* "L7" -> position of label L7 in the code */
int jump_target(const char *label)
{
    int n = atoi(label + 1);
    return label_pos[n];
}

/* make a new array of 'size' zeros, return its ID */
long long new_array(long long size)
{
    if (size <= 0) {
        runtime_error("array size must be more than 0", "");
    }
    if (arr_count >= MAX_ARRAYS || heap_used + size > HEAP_SIZE) {
        runtime_error("out of array memory", "");
    }
    int id = arr_count;
    arr_start[id] = heap_used;
    arr_size[id] = (int) size;
    for (int i = 0; i < size; i++) {
        heap[heap_used + i] = 0;
    }
    heap_used = heap_used + (int) size;
    arr_count = arr_count + 1;
    return id;
}

/* position of a[index] inside heap, with checks */
int array_cell(long long id, long long index, const char *name)
{
    if (id <= 0 || id >= arr_count) {
        runtime_error("not an array:", name);
    }
    if (index < 0 || index >= arr_size[id]) {
        char msg[100];
        sprintf(msg, "%s[%lld] (size is %d)", name, index, arr_size[id]);
        runtime_error("array index out of range:", msg);
    }
    return arr_start[id] + (int) index;
}

/* ---------- the interpreter ---------- */

long long run_function(const char *name, long long args[], int nargs)
{
    int start = find_function(name);
    if (start == -1) {
        runtime_error("no function named", name);
    }

    call_depth = call_depth + 1;
    if (call_depth > MAX_DEPTH) {
        runtime_error("too much recursion in", name);
    }

    struct Frame *fr = calloc(1, sizeof(struct Frame));
    int arg_index = 0;
    long long result = 0;
    int pc = start + 1;

    while (pc < code_count) {
        char *op = code[pc].op;
        char *a1 = code[pc].arg1;
        char *a2 = code[pc].arg2;
        char *r  = code[pc].result;

        int cat = get_category(op);
        if (cat != CAT_NONE) {
            cat_count[cat] = cat_count[cat] + 1;
        }

        if (strcmp(op, "end") == 0 || strcmp(op, "func") == 0) {
            break;   /* function finished without a return */
        } else if (strcmp(op, "label") == 0) {
            /* nothing to do */
        } else if (strcmp(op, "param_in") == 0) {
            if (arg_index >= nargs) {
                runtime_error("missing argument for", a1);
            }
            set_var(fr, a1, args[arg_index]);
            arg_index = arg_index + 1;
        } else if (strcmp(op, "=") == 0) {
            set_var(fr, r, value_of(fr, a1));
        } else if (strcmp(op, "+") == 0) {
            set_var(fr, r, value_of(fr, a1) + value_of(fr, a2));
        } else if (strcmp(op, "-") == 0) {
            set_var(fr, r, value_of(fr, a1) - value_of(fr, a2));
        } else if (strcmp(op, "*") == 0) {
            set_var(fr, r, value_of(fr, a1) * value_of(fr, a2));
        } else if (strcmp(op, "/") == 0 || strcmp(op, "%") == 0) {
            long long b = value_of(fr, a2);
            if (b == 0) {
                runtime_error("division by zero in", name);
            }
            if (strcmp(op, "/") == 0) {
                set_var(fr, r, value_of(fr, a1) / b);
            } else {
                set_var(fr, r, value_of(fr, a1) % b);
            }
        } else if (strcmp(op, "<<") == 0) {
            set_var(fr, r, value_of(fr, a1) << value_of(fr, a2));
        } else if (strcmp(op, ">>") == 0) {
            set_var(fr, r, value_of(fr, a1) >> value_of(fr, a2));        /* arithmetic shift */
        } else if (strcmp(op, "mulhs") == 0) {
            /* high 64 bits of the 128-bit product */
            __int128 big = (__int128) value_of(fr, a1) * value_of(fr, a2);
            set_var(fr, r, (long long) (big >> 64));
        } else if (strcmp(op, "<") == 0) {
            set_var(fr, r, value_of(fr, a1) < value_of(fr, a2));
        } else if (strcmp(op, ">") == 0) {
            set_var(fr, r, value_of(fr, a1) > value_of(fr, a2));
        } else if (strcmp(op, "<=") == 0) {
            set_var(fr, r, value_of(fr, a1) <= value_of(fr, a2));
        } else if (strcmp(op, ">=") == 0) {
            set_var(fr, r, value_of(fr, a1) >= value_of(fr, a2));
        } else if (strcmp(op, "==") == 0) {
            set_var(fr, r, value_of(fr, a1) == value_of(fr, a2));
        } else if (strcmp(op, "!=") == 0) {
            set_var(fr, r, value_of(fr, a1) != value_of(fr, a2));
        } else if (strcmp(op, "alloc") == 0) {
            set_var(fr, r, new_array(value_of(fr, a1)));
        } else if (strcmp(op, "load") == 0) {
            int pos = array_cell(value_of(fr, a1), value_of(fr, a2), a1);
            set_var(fr, r, heap[pos]);
        } else if (strcmp(op, "store") == 0) {
            int pos = array_cell(value_of(fr, a1), value_of(fr, a2), a1);
            heap[pos] = value_of(fr, r);
        } else if (strcmp(op, "goto") == 0) {
            pc = jump_target(r);
            continue;
        } else if (strcmp(op, "iffalse") == 0) {
            if (value_of(fr, a1) == 0) {
                pc = jump_target(r);
                continue;
            }
        } else if (strcmp(op, "param") == 0) {
            fr->pending[fr->pending_count] = value_of(fr, a1);
            fr->pending_count = fr->pending_count + 1;
        } else if (strcmp(op, "call") == 0) {
            int n = atoi(a2);
            long long call_args[MAX_PENDING];
            int first = fr->pending_count - n;
            for (int i = 0; i < n; i++) {
                call_args[i] = fr->pending[first + i];
            }
            fr->pending_count = first;
            long long v = run_function(a1, call_args, n);
            set_var(fr, r, v);
        } else if (strcmp(op, "return") == 0) {
            result = value_of(fr, a1);
            break;
        }

        pc = pc + 1;
    }

    free(fr);
    call_depth = call_depth - 1;
    return result;
}

/* ---------- report ---------- */

/* runs the program once from a clean start and fills cat_count[] */
long long execute_program(const char *func_name, long long args[], int nargs)
{
    /* remember where every label is, so jumps are fast */
    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, "label") == 0) {
            int n = atoi(code[i].result + 1);
            if (n < 0 || n >= MAX_LABELS) {
                runtime_error("too many labels in the program", code[i].result);
            }
            label_pos[n] = i;
        }
    }

    for (int i = 0; i < NUM_CATS; i++) {
        cat_count[i] = 0;
    }

    /* empty array memory, so every run starts the same */
    heap_used = 0;
    arr_count = 1;
    call_depth = 0;

    return run_function(func_name, args, nargs);
}

/* runs the program quietly and gives back its answer, est. time and energy
   (used by the speed-budget search, which tries many builds) */
void measure_program(const char *func_name, long long args[], int nargs,
                     long long *answer, long long *cycles, long long *energy)
{
    *answer = execute_program(func_name, args, nargs);
    long long total_cycles = 0;
    long long total_energy = 0;
    for (int i = 1; i < NUM_CATS; i++) {
        total_cycles = total_cycles + cat_count[i] * cycle_cost[i];
        total_energy = total_energy + cat_count[i] * energy_cost[i];
    }
    *cycles = total_cycles;
    *energy = total_energy;
}

void run_program(const char *func_name, long long args[], int nargs,
                 const char *passes, const char *goal)
{
    long long answer = execute_program(func_name, args, nargs);

    long long total_instr = 0;
    long long total_energy = 0;
    long long total_cycles = 0;
    for (int i = 1; i < NUM_CATS; i++) {
        total_instr = total_instr + cat_count[i];
        total_energy = total_energy + cat_count[i] * energy_cost[i];
        total_cycles = total_cycles + cat_count[i] * cycle_cost[i];
    }

    double joules = total_energy * joules_per_unit;
    double grams = joules / 3600000.0 * grid_g_per_kwh;

    /* CSV: one line with every number, for the experiment script */
    if (csv_mode) {
        printf("%s,%s,", source_name, func_name);
        for (int i = 0; i < nargs; i++) {
            if (i > 0) {
                printf(" ");
            }
            printf("%lld", args[i]);
        }
        printf(",%s,%s,%lld,%lld", goal, passes, answer, total_instr);
        for (int i = 1; i < NUM_CATS; i++) {
            printf(",%lld", cat_count[i]);
        }
        printf(",%lld,%lld,%.6e,%.6e,%.4f,%d\n",
               total_cycles, total_energy, joules, grams, opt_time_ms, heap_used);
        return;
    }

    printf("\n===== EcoLang Sustainability Report =====\n");
    printf("program       : %s(", func_name);
    for (int i = 0; i < nargs; i++) {
        if (i > 0) {
            printf(", ");
        }
        printf("%lld", args[i]);
    }
    printf(")\n");
    printf("result        : %lld\n", answer);
    printf("instructions  : %lld\n", total_instr);
    for (int i = 1; i < NUM_CATS; i++) {
        printf("   %-7s    : %10lld  x %2lld units\n", cat_names[i], cat_count[i], energy_cost[i]);
    }
    printf("est. time     : %lld cycles\n", total_cycles);
    printf("energy        : %lld units / %.3e J\n", total_energy, joules);
    printf("carbon        : %.3e g CO2\n", grams);
    printf("array memory  : %d cells\n", heap_used);
    printf("optimise time : %.3f ms\n", opt_time_ms);
    printf("goal          : %s\n", goal);
    printf("passes        : %s\n", passes);
    printf("==========================================\n");
}
