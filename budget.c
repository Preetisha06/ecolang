/* budget.c - speed-budget optimisation

   The user gives a time limit instead of a goal:
       ./ecoc bfs.eco bfs 60 --max-slowdown=5
   means "use as little energy as possible, but be at most 5% slower
   than the fastest (speed) build".

   How it works:
     1. save a copy of the unoptimised code
     2. for alpha = 1.00, 0.99, ... 0.00 (101 tries):
          put the saved code back, optimise with this alpha,
          run it quietly, write down its est. time and energy
     3. limit = time of the speed build (alpha = 1.0) x (1 + slowdown/100)
     4. pick the build with the LEAST energy whose time is within the limit
        (if two builds use the same energy, keep the faster one)
     5. put the saved code back and return the chosen alpha            */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tac.h"
#include "interp.h"
#include "optimize.h"
#include "budget.h"

#define NUM_TRIES 101           /* alpha = 1.00, 0.99, ... 0.00 */

extern int temp_count;          /* from tac.c */

struct Instr saved_code[MAX_CODE];
int saved_count = 0;
int saved_temps = 0;

/* the optimised code of the previous try: if the next alpha gives exactly
   the same code, we reuse its numbers instead of running it again */
struct Instr last_code[MAX_CODE];
int last_count = -1;

/* copy the code into saved_code */
static void save_code(void)
{
    for (int i = 0; i < code_count; i++) {
        saved_code[i] = code[i];
    }
    saved_count = code_count;
    saved_temps = temp_count;
}

/* 1 if the code now is exactly the same as last_code */
static int same_as_last(void)
{
    if (code_count != last_count) {
        return 0;
    }
    for (int i = 0; i < code_count; i++) {
        if (strcmp(code[i].op, last_code[i].op) != 0 ||
            strcmp(code[i].arg1, last_code[i].arg1) != 0 ||
            strcmp(code[i].arg2, last_code[i].arg2) != 0 ||
            strcmp(code[i].result, last_code[i].result) != 0) {
            return 0;
        }
    }
    return 1;
}

/* remember the code now as last_code */
static void remember_code(void)
{
    for (int i = 0; i < code_count; i++) {
        last_code[i] = code[i];
    }
    last_count = code_count;
}

/* put saved_code back, so the next try starts from the same code */
static void restore_code(void)
{
    for (int i = 0; i < saved_count; i++) {
        code[i] = saved_code[i];
    }
    code_count = saved_count;
    temp_count = saved_temps;
    reset_opt_stats();
}

double choose_alpha_for_budget(double max_slowdown,
                               int cf, int cp, int dce, int sr, int cse, int licm,
                               const char *func_name, long long args[], int nargs,
                               int print_table)
{
    double alpha[NUM_TRIES];
    long long answer[NUM_TRIES];
    long long cycles[NUM_TRIES];
    long long energy[NUM_TRIES];

    save_code();

    /* step 2: try every alpha */
    for (int k = 0; k < NUM_TRIES; k++) {
        alpha[k] = 1.0 - 0.01 * k;
        if (alpha[k] < 0.0) {
            alpha[k] = 0.0;
        }
        restore_code();
        optimize_program(cf, cp, dce, sr, cse, licm, alpha[k]);
        if (k > 0 && same_as_last()) {
            /* same code as the previous alpha: same numbers */
            answer[k] = answer[k - 1];
            cycles[k] = cycles[k - 1];
            energy[k] = energy[k - 1];
        } else {
            measure_program(func_name, args, nargs, &answer[k], &cycles[k], &energy[k]);
            remember_code();
        }
    }

    /* every build must give the same answer */
    for (int k = 1; k < NUM_TRIES; k++) {
        if (answer[k] != answer[0]) {
            printf("warning: alpha = %.2f gave a different answer (%lld vs %lld)\n",
                   alpha[k], answer[k], answer[0]);
        }
    }

    /* step 3: the time limit, measured from the speed build */
    long long speed_cycles = cycles[0];
    long long speed_energy = energy[0];
    double limit = speed_cycles * (1.0 + max_slowdown / 100.0);

    /* step 4: least energy within the limit (alpha = 1.0 always fits) */
    int best = 0;
    for (int k = 1; k < NUM_TRIES; k++) {
        if (cycles[k] <= limit && energy[k] < energy[best]) {
            best = k;
        }
    }

    if (print_table) {
        printf("\n===== Speed budget: at most %.1f%% slower than the speed build =====\n",
               max_slowdown);
        printf("(%d alphas tried; each line is a different build, shown with the\n", NUM_TRIES);
        printf(" first alpha that produced it)\n");
        printf("alpha    est. time      energy   slower than speed   energy saved   in budget\n");
        for (int k = 0; k < NUM_TRIES; k++) {
            /* same build as the line above: do not print it again */
            if (k > 0 && cycles[k] == cycles[k - 1] && energy[k] == energy[k - 1]) {
                continue;
            }
            double slower = 100.0 * (cycles[k] - speed_cycles) / speed_cycles;
            double saved = 100.0 * (speed_energy - energy[k]) / speed_energy;
            const char *fits = "yes";
            if (cycles[k] > limit) {
                fits = "no";
            }
            const char *mark = "";
            if (k == best) {
                mark = "   <- chosen";
            }
            printf(" %.2f  %11lld %11lld %17.2f%% %13.2f%%   %-3s%s\n",
                   alpha[k], cycles[k], energy[k], slower, saved, fits, mark);
        }
        double best_slower = 100.0 * (cycles[best] - speed_cycles) / speed_cycles;
        double best_saved = 100.0 * (speed_energy - energy[best]) / speed_energy;
        printf("chosen alpha = %.2f : %.2f%% less energy than the speed build, %.2f%% slower\n",
               alpha[best], best_saved, best_slower);
    }

    /* step 5: leave the code exactly as we found it */
    restore_code();
    return alpha[best];
}
