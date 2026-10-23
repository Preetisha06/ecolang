/* budget.h - speed-budget optimisation */
#ifndef BUDGET_H
#define BUDGET_H

/* Finds the greenest build that is at most max_slowdown percent slower
   (estimated time) than the speed build. Tries alpha = 1.00, 0.99, ... 0.00,
   prints a table of what it tried, and returns the alpha it picked.
   The code is put back exactly as it was before; main() then optimises
   once more with the returned alpha. */
double choose_alpha_for_budget(double max_slowdown,
                               int cf, int cp, int dce, int sr, int cse, int licm,
                               const char *func_name, long long args[], int nargs,
                               int print_table);

#endif
