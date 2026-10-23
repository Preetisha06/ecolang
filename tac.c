/* tac.c - functions to create and print three-address code */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tac.h"

struct Instr code[MAX_CODE];
int code_count = 0;

int temp_count = 0;
int label_count = 0;

/* add one instruction to the list */
void emit(const char *op, const char *arg1, const char *arg2, const char *result)
{
    if (code_count >= MAX_CODE) {
        printf("too many instructions\n");
        exit(1);
    }
    strcpy(code[code_count].op, op);
    strcpy(code[code_count].arg1, arg1);
    strcpy(code[code_count].arg2, arg2);
    strcpy(code[code_count].result, result);
    code_count = code_count + 1;
}

/* make a new temporary variable name: t1, t2, ... */
char *new_temp(void)
{
    char *name = malloc(MAX_LEN);
    temp_count = temp_count + 1;
    sprintf(name, "t%d", temp_count);
    return name;
}

/* make a new label number */
int new_label(void)
{
    label_count = label_count + 1;
    return label_count;
}

/* turn label number into a name: 3 -> "L3" */
char *label_name(int n)
{
    char *name = malloc(MAX_LEN);
    sprintf(name, "L%d", n);
    return name;
}

/* print all instructions in readable form */
void print_code(void)
{
    for (int i = 0; i < code_count; i++) {
        char *op = code[i].op;
        char *a1 = code[i].arg1;
        char *a2 = code[i].arg2;
        char *r  = code[i].result;

        if (strcmp(op, "func") == 0) {
            if (strlen(a2) > 0) {
                printf("\nfunc %s:   (@%s)\n", a1, a2);
            } else {
                printf("\nfunc %s:\n", a1);
            }
        } else if (strcmp(op, "param_in") == 0) {
            printf("%3d:    receive %s\n", i, a1);
        } else if (strcmp(op, "label") == 0) {
            printf("%3d:  %s:\n", i, r);
        } else if (strcmp(op, "=") == 0) {
            printf("%3d:    %s = %s\n", i, r, a1);
        } else if (strcmp(op, "goto") == 0) {
            printf("%3d:    goto %s\n", i, r);
        } else if (strcmp(op, "iffalse") == 0) {
            printf("%3d:    ifFalse %s goto %s\n", i, a1, r);
        } else if (strcmp(op, "return") == 0) {
            printf("%3d:    return %s\n", i, a1);
        } else if (strcmp(op, "param") == 0) {
            printf("%3d:    param %s\n", i, a1);
        } else if (strcmp(op, "call") == 0) {
            printf("%3d:    %s = call %s, %s\n", i, r, a1, a2);
        } else if (strcmp(op, "mulhs") == 0) {
            printf("%3d:    %s = mulhs(%s, %s)\n", i, r, a1, a2);
        } else if (strcmp(op, "alloc") == 0) {
            printf("%3d:    %s = alloc %s\n", i, r, a1);
        } else if (strcmp(op, "load") == 0) {
            printf("%3d:    %s = %s[%s]\n", i, r, a1, a2);
        } else if (strcmp(op, "store") == 0) {
            printf("%3d:    %s[%s] = %s\n", i, a1, a2, r);
        } else if (strcmp(op, "end") == 0) {
            printf("%3d:    end\n", i);
        } else {
            /* maths and comparisons: t1 = a + b */
            printf("%3d:    %s = %s %s %s\n", i, r, a1, op, a2);
        }
    }
}
