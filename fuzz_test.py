# fuzz_test.py - random testing of the EcoLang compiler ("differential testing")
#
# Writes thousands of random EcoLang programs and runs each one:
#   - without optimisation (the reference answer)
#   - with every goal (speed / balanced / carbon) and several energy profiles
#   - with single passes switched off
#   - compiled to C (unoptimised and carbon builds) and run for real
# Every run must give exactly the same answer. Any difference is a compiler bug,
# and the program that shows it is saved in results/fuzz/ for debugging.
#
# The generated programs always finish and never divide by zero or read
# outside an array, so a crash or a different answer always means a bug.
#
# How to run (inside the ecolang folder, after ./build.sh):
#     python3 fuzz_test.py            (500 programs)
#     python3 fuzz_test.py 2000       (more programs)
#     python3 fuzz_test.py 200 5000   (200 programs, starting at program number 5000)

import os
import random
import subprocess
import sys

FOLDER = os.path.join("results", "fuzz")

SETTINGS = [
    ["--goal=speed", "--profile=profile_cpu.txt"],
    ["--goal=carbon", "--profile=profile_cpu.txt"],
    ["--goal=balanced", "--profile=profile_cpu.txt"],
    ["--goal=speed", "--profile=profile_datapath.txt"],
    ["--goal=carbon", "--profile=profile_datapath.txt"],
    ["--goal=speed", "--profile=energy_profile.txt"],
    ["--alpha=0.3", "--profile=profile_datapath.txt"],
    ["--goal=speed", "--profile=profile_cpu.txt", "--no-cse"],
    ["--goal=speed", "--profile=profile_cpu.txt", "--no-licm"],
    ["--goal=speed", "--profile=profile_cpu.txt", "--no-cp"],
    ["--goal=carbon", "--profile=profile_datapath.txt", "--no-dce"],
    ["--goal=speed", "--profile=profile_cpu.txt", "--no-sr", "--no-cf"],
]

# multiplying / dividing by these numbers exercises strength reduction and the
# "magic number" division (powers of two, small odd numbers, big numbers ...)
MUL_CONSTS = [0, 1, 2, 3, 4, 5, 7, 8, 10, 15, 16, 255, 1000, 65536, 1103515245]
DIV_CONSTS = [2, 3, 4, 5, 7, 8, 9, 10, 11, 13, 16, 100, 1000, 2147483648, 1000000007]

VARS = ["a", "b", "c", "d"]


class Gen:
    def __init__(self, rng):
        self.rng = rng
        self.loop_id = 0
        self.allow_calls = True
        self.allow_arr = True

    def number(self):
        r = self.rng.random()
        if r < 0.6:
            return str(self.rng.randint(0, 20))
        if r < 0.8:
            return str(self.rng.randint(0, 100000))
        return "-" + str(self.rng.randint(1, 1000))

    def atom(self, names):
        r = self.rng.random()
        if r < 0.55:
            return self.rng.choice(names)
        if r < 0.85 or not self.allow_arr:
            return self.number()
        return "arr[" + self.index(names, 1) + "]"

    def index(self, names, depth):
        # always 0..7:  ((e % 8) + 8) % 8
        return "((" + self.expr(names, depth) + ") % 8 + 8) % 8"

    def expr(self, names, depth):
        if depth <= 0 or self.rng.random() < 0.3:
            return self.atom(names)
        r = self.rng.random()
        left = self.expr(names, depth - 1)
        if r < 0.25:
            return "(" + left + " + " + self.expr(names, depth - 1) + ")"
        if r < 0.40:
            return "(" + left + " - " + self.expr(names, depth - 1) + ")"
        if r < 0.55:
            return "(" + left + " * " + str(self.rng.choice(MUL_CONSTS)) + ")"
        if r < 0.62:
            return "(" + left + " * " + self.expr(names, depth - 1) + ")"
        if r < 0.72:
            return "(" + left + " / " + str(self.rng.choice(DIV_CONSTS)) + ")"
        if r < 0.80:
            return "(" + left + " % " + str(self.rng.choice(DIV_CONSTS)) + ")"
        if r < 0.85:
            # divisor x*x+1 is never 0 (a square is never -1)
            v = self.rng.choice(names)
            return "(" + left + " / (" + v + " * " + v + " + 1))"
        if r < 0.92:
            op = self.rng.choice(["<", ">", "<=", ">=", "==", "!="])
            return "(" + left + " " + op + " " + self.expr(names, depth - 1) + ")"
        if r < 0.96 or not self.allow_calls:
            return "-(" + left + ")"
        return "helper(" + left + ", " + self.expr(names, depth - 1) + ")"

    def block(self, names, depth, indent):
        lines = []
        count = self.rng.randint(1, 4)
        for i in range(count):
            lines = lines + self.statement(names, depth, indent)
        return lines

    def statement(self, names, depth, indent):
        pad = "  " * indent
        r = self.rng.random()
        if r < 0.45 or depth <= 0:
            v = self.rng.choice(VARS)       # never a loop counter, so loops always end
            return [pad + v + " = " + self.expr(names, 3) + ";"]
        if r < 0.60:
            return [pad + "arr[" + self.index(names, 1) + "] = " + self.expr(names, 3) + ";"]
        if r < 0.80:
            lines = [pad + "if (" + self.expr(names, 2) + ") {"]
            lines = lines + self.block(names, depth - 1, indent + 1)
            if self.rng.random() < 0.5:
                lines.append(pad + "} else {")
                lines = lines + self.block(names, depth - 1, indent + 1)
            lines.append(pad + "}")
            return lines
        # a loop that always finishes: its counter is never written in the body
        self.loop_id = self.loop_id + 1
        k = "k" + str(self.loop_id)
        lines = [pad + "let " + k + ": int = 0;",
                 pad + "while (" + k + " < " + str(self.rng.randint(1, 12)) + ") {"]
        lines = lines + self.block(names + [k], depth - 1, indent + 1)
        lines.append(pad + "  " + k + " = " + k + " + 1;")
        lines.append(pad + "}")
        return lines

    def program(self):
        lines = []
        lines.append("fn helper(x: int, y: int) -> int {")
        self.allow_calls = False          # helper must not call itself
        self.allow_arr = False            # and has no array
        lines.append("  return " + self.expr(["x", "y"], 2) + ";")
        self.allow_calls = True
        self.allow_arr = True
        lines.append("}")
        lines.append("")
        lines.append("fn main(n: int) -> int {")
        lines.append("  let arr: int[8];")
        for i in range(len(VARS)):
            lines.append("  let " + VARS[i] + ": int = " + self.number() + ";")
        lines.append("  " + VARS[0] + " = n;")
        for i in range(self.rng.randint(4, 10)):
            lines = lines + self.statement(VARS, 3, 1)
        check = "a + b * 3 + c * 5 + d * 7"
        for i in range(8):
            check = check + " + arr[" + str(i) + "] * " + str(i + 11)
        lines.append("  return " + check + ";")
        lines.append("}")
        return "\n".join(lines) + "\n"


def run(command, env=None):
    out = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
    return out.stdout + out.stderr


def answer(text):
    for line in text.split("\n"):
        words = line.split()
        if len(words) >= 3 and words[0] == "result" and words[1] == ":":
            return words[2]
        if len(words) == 2 and words[0] == "result":
            return words[1]
    return "NO RESULT: " + text.strip()[-200:]


def main():
    count = 500
    first = 0
    if len(sys.argv) > 1:
        count = int(sys.argv[1])
    if len(sys.argv) > 2:
        first = int(sys.argv[2])       # start at a different program number
    if not os.path.exists("./ecoc"):
        print("./ecoc not found. Run ./build.sh first.")
        sys.exit(1)
    if not os.path.exists(FOLDER):
        os.makedirs(FOLDER)

    env = dict(os.environ)
    env["ECO_NO_TIMING"] = "1"
    tag = str(os.getpid())
    src = os.path.join(FOLDER, "prog_" + tag + ".eco")
    c_file = os.path.join(FOLDER, "prog_" + tag + ".c")
    exe = os.path.join(FOLDER, "prog_" + tag)

    failures = 0
    runs = 0
    for p in range(first, first + count):
        rng = random.Random(1000 + p)
        text = Gen(rng).program()
        f = open(src, "w")
        f.write(text)
        f.close()
        n = str(rng.randint(-50, 50))

        reference = answer(run(["./ecoc", src, "main", n, "--quiet"]))
        problems = []
        if reference.startswith("NO RESULT"):
            problems.append("no-opt: " + reference)
        for s in range(len(SETTINGS)):
            got = answer(run(["./ecoc", src, "main", n, "--quiet"] + SETTINGS[s]))
            runs = runs + 1
            if got != reference:
                problems.append(" ".join(SETTINGS[s]) + " -> " + got)

        # compiled C: no optimisation, and a carbon build
        for opts in [[], ["--goal=speed", "--profile=profile_cpu.txt"], ["--goal=carbon", "--profile=profile_datapath.txt"]]:
            run(["./ecoc", src, "main", n, "--quiet", "--emit-c=" + c_file] + opts)
            gcc = subprocess.run(["gcc", "-O0", "-fwrapv", "-w", c_file, "-o", exe], capture_output=True, text=True)
            runs = runs + 1
            if gcc.returncode != 0:
                problems.append("C " + " ".join(opts) + ": gcc failed: " + gcc.stderr[-300:])
                continue
            got = answer(run(["./" + exe, n], env))
            if got != reference:
                problems.append("C " + " ".join(opts) + " -> " + got)

        if len(problems) > 0:
            failures = failures + 1
            bad = os.path.join(FOLDER, "fail_" + str(p) + ".eco")
            f = open(bad, "w")
            f.write("// run with: ./ecoc " + bad + " main " + n + "\n")
            f.write("// expected (no optimisation): " + reference + "\n")
            for i in range(len(problems)):
                f.write("// " + problems[i].replace("\n", " ") + "\n")
            f.write(text)
            f.close()
            print("program", p, "FAILED - saved to", bad)
        if (p + 1 - first) % 50 == 0:
            print(p + 1 - first, "programs tested,", failures, "failures")

    for name in [src, c_file, exe]:
        if os.path.exists(name):
            os.remove(name)
    print("")
    print("Tested", count, "random programs (", runs, "optimised / compiled runs ). Failures:", failures)
    f = open(os.path.join(FOLDER, "fuzz_summary_" + str(first) + ".txt"), "w")
    f.write("random programs: %d\nruns compared with the unoptimised answer: %d\nfailures: %d\n" % (count, runs, failures))
    f.close()


main()
