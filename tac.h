/* tac.h - three-address code (TAC) storage for EcoLang
   Every instruction is stored as 4 strings:
       op    : what to do        (like "+", "=", "goto")
       arg1  : first input
       arg2  : second input
       result: where the answer goes
   Example:  t1 = a + b   ->   op="+", arg1="a", arg2="b", result="t1"  */

#ifndef TAC_H
#define TAC_H

#define MAX_CODE 50000
#define MAX_LEN  32

struct Instr {
    char op[MAX_LEN];
    char arg1[MAX_LEN];
    char arg2[MAX_LEN];
    char result[MAX_LEN];
};

extern struct Instr code[MAX_CODE];
extern int code_count;

void  emit(const char *op, const char *arg1, const char *arg2, const char *result);
char *new_temp(void);     /* makes t1, t2, t3 ...  */
int   new_label(void);    /* makes 1, 2, 3 ... (printed as L1, L2 ...) */
char *label_name(int n);  /* 1 -> "L1" */
void  print_code(void);

#endif
