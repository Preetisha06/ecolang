/* optimize.h - classical optimisation passes on three-address code */
#ifndef OPTIMIZE_H
#define OPTIMIZE_H

/* each flag is 1 (on) or 0 (off)
   alpha: 1 = speed, 0.5 = balanced, 0 = carbon */
void optimize_program(int do_cf, int do_cp, int do_dce, int do_sr,
                      int do_cse, int do_licm, double alpha);
void print_opt_summary(void);
void reset_opt_stats(void);
void print_decision_log(void);

#endif
