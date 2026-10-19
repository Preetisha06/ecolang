/* cgen.h - turns the (optimised) three-address code into a real C program */
#ifndef CGEN_H
#define CGEN_H

/* writes a complete C program to 'out_file'.
   The program runs 'func_name' with the numbers given on its command line,
   prints the result and how long one run takes in nanoseconds.            */
void generate_c(const char *out_file, const char *func_name);

#endif
