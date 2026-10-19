/* interp.h - runs the three-address code and prints the energy report */
#ifndef INTERP_H
#define INTERP_H

/* ---------- instruction categories ---------- */
#define CAT_NONE   0     /* labels, func, end: not real instructions */
#define CAT_ALU    1     /* + - < > == = <<                           */
#define CAT_MUL    2     /* *                                         */
#define CAT_DIV    3     /* / %                                       */
#define CAT_MEM    4     /* param, receive, call, return (stack)      */
#define CAT_BRANCH 5     /* goto, ifFalse                             */
#define NUM_CATS   6

/* costs from energy_profile.txt (the optimiser uses them too) */
extern long long energy_cost[NUM_CATS];
extern long long cycle_cost[NUM_CATS];

/* set by main() before run_program */
extern int csv_mode;            /* 1 = print one CSV line instead of the report */
extern double opt_time_ms;      /* how long the optimiser took */
extern const char *source_name; /* .eco file name, for the CSV line */

int  get_category(const char *op);
void load_profile(const char *filename);
void run_program(const char *func_name, long long args[], int nargs,
                 const char *passes, const char *goal);

#endif
