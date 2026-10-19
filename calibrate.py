# calibrate.py - measures how long each kind of instruction takes on THIS computer
#
# 1. Every microbenchmark in micro.eco is turned into C by EcoLang, compiled with
#    gcc -O0 -fwrapv and timed (fastest of 5 rounds x 7 timings).
#    All microbenchmarks have the same shape (8 x  "x = m OP k"  in a loop),
#    so differences in time come from the operation, not from the code shape.
# 2. EcoLang's interpreter counts the ALU, MUL, DIV, MEMORY and BRANCH
#    instructions each one runs.
# 3. Least squares (on relative error) finds the time per instruction of each kind:
#        measured time  =  ALU count x t_alu + MUL count x t_mul + ...
# 4. TEST: the fitted costs predict the 9 real benchmarks, which were NOT used
#    for fitting (needs results/validation/timing.csv from validate_timing.py).
# 5. Writes two profiles with the measured time costs from step 3:
#        profile_calibrated.txt           energy of profile_cpu.txt
#        profile_calibrated_datapath.txt  energy of profile_datapath.txt
#
# How to run (inside the ecolang folder, after ./build.sh):
#     python3 validate_timing.py     (optional first: gives the benchmark test in step 4)
#     python3 calibrate.py
#     python3 validate_timing.py     (now also tests builds made with the calibrated model)

import os
import subprocess
import sys

N = 20000                # loop iterations in every microbenchmark
ROUNDS = 5
TIME_UNIT_NS = 0.01      # profile_calibrated.txt stores time in units of 0.01 ns
CATS = ["ALU", "MUL", "DIV", "MEMORY", "BRANCH"]

# (label, function in micro.eco, extra options)
MICRO = [
    ("loop only",        "m_loop",     []),
    ("add",              "m_add",      []),
    ("multiply",         "m_mul",      []),
    ("divide",           "m_div",      []),
    ("remainder",        "m_mod",      []),
    ("divide by const",  "m_divconst", []),
    ("magic multiply",   "m_divconst", ["--goal=speed", "--profile=profile_cpu.txt"]),
    ("array read",       "m_load",     []),
    ("array write",      "m_store",    []),
    ("function call",    "m_call",     []),
    ("if statement",     "m_branch",   []),
]

FOLDER = os.path.join("results", "calibration")
BUILD_DIR = os.path.join(FOLDER, "c_programs")
TIMING = os.path.join("results", "validation", "timing.csv")
TEST_BUILDS = ["none", "speed", "balanced", "carbon", "carbon-datapath"]


def run(command):
    out = subprocess.run(command, capture_output=True, text=True)
    return out.stdout, out.stderr, out.returncode


def cpu_name():
    try:
        f = open("/proc/cpuinfo")
        for line in f:
            if line.startswith("model name"):
                f.close()
                return line.split(":", 1)[1].strip()
        f.close()
    except OSError:
        pass
    return "unknown CPU"


# ---------- least squares on relative error, no negative costs ----------

def least_squares(A, y, use):
    idx = []
    for i in range(len(use)):
        if use[i]:
            idx.append(i)
    n = len(idx)
    M = []
    for a in range(n):
        row = []
        for b in range(n):
            s = 0.0
            for r in range(len(A)):
                s = s + A[r][idx[a]] * A[r][idx[b]]
            row.append(s)
        s = 0.0
        for r in range(len(A)):
            s = s + A[r][idx[a]] * y[r]
        row.append(s)
        M.append(row)
    for col in range(n):
        best = col
        for r in range(col + 1, n):
            if abs(M[r][col]) > abs(M[best][col]):
                best = r
        M[col], M[best] = M[best], M[col]
        if M[col][col] == 0:
            continue
        for r in range(n):
            if r != col:
                factor = M[r][col] / M[col][col]
                for k in range(col, n + 1):
                    M[r][k] = M[r][k] - factor * M[col][k]
    cost = [0.0] * len(use)
    for a in range(n):
        if M[a][a] != 0:
            cost[idx[a]] = M[a][n] / M[a][a]
    return cost


def fit(rows):
    # each row is divided by its own time: the fit minimises RELATIVE error
    A = []
    y = []
    for row in rows:
        scaled = []
        for i in range(len(CATS)):
            scaled.append(row["counts"][i] / row["time_ns"])
        A.append(scaled)
        y.append(1.0)
    use = [True] * len(CATS)
    while True:
        cost = least_squares(A, y, use)
        worst = -1
        for i in range(len(CATS)):
            if use[i] and cost[i] < 0:
                if worst == -1 or cost[i] < cost[worst]:
                    worst = i
        if worst == -1:
            return cost
        use[worst] = False


def predict(counts, cost):
    total = 0.0
    for i in range(len(CATS)):
        total = total + counts[i] * cost[i]
    return total


def median(xs):
    s = list(xs)
    s.sort()
    if len(s) % 2 == 1:
        return s[len(s) // 2]
    return (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2.0


def mean(xs):
    total = 0.0
    for i in range(len(xs)):
        total = total + xs[i]
    return total / len(xs)


# ---------- step 1-2: time the microbenchmarks ----------

def time_microbenchmarks():
    if not os.path.exists(BUILD_DIR):
        os.makedirs(BUILD_DIR)
    print("CPU:", cpu_name())
    print("Compiling microbenchmarks ...")
    rows = []
    for i in range(len(MICRO)):
        label, function, options = MICRO[i]
        c_file = os.path.join(BUILD_DIR, "micro_" + str(i) + ".c")
        exe_file = os.path.join(BUILD_DIR, "micro_" + str(i))
        command = ["./ecoc", "micro.eco", function, str(N), "--csv", "--emit-c=" + c_file] + options
        stdout, stderr, code = run(command)
        parts = stdout.strip().split("\n")[-1].split(",")
        counts = []
        for k in range(7, 12):
            counts.append(float(parts[k]))
        stdout, stderr, code = run(["gcc", "-O0", "-fwrapv", "-w", c_file, "-o", exe_file])
        if code != 0:
            print("gcc failed:", stderr)
            sys.exit(1)
        rows.append({"label": label, "exe": exe_file, "result": parts[5], "counts": counts, "times": []})

    for r in range(ROUNDS):
        print("Timing round", r + 1, "of", ROUNDS, "...")
        for row in rows:
            stdout, stderr, code = run(["./" + row["exe"], str(N)])
            values = {}
            for line in stdout.strip().split("\n"):
                words = line.split()
                values[words[0]] = words[1]
            if values["result"] != row["result"]:
                print("WRONG RESULT in", row["label"], values["result"], "vs", row["result"])
                sys.exit(1)
            row["times"].append(float(values["min_ns"]))

    for row in rows:
        best = row["times"][0]
        for i in range(len(row["times"])):
            best = min(best, row["times"][i])
        row["time_ns"] = best
    return rows


# ---------- step 4: test on the real benchmarks ----------

def read_benchmarks():
    if not os.path.exists(TIMING):
        return None
    f = open(TIMING)
    header = f.readline().strip().split(",")
    if "alu" not in header:
        f.close()
        return None
    rows = []
    for line in f:
        parts = line.strip().split(",")
        if len(parts) != len(header):
            continue
        row = {}
        for i in range(len(header)):
            row[header[i]] = parts[i]
        if row["build"] not in TEST_BUILDS:
            continue
        counts = []
        for name in ["alu", "mul", "div", "memory", "branch"]:
            counts.append(float(row[name]))
        rows.append({"benchmark": row["benchmark"], "build": row["build"],
                     "counts": counts, "time_ns": float(row["time_ns"])})
    f.close()
    return rows


def main():
    if not os.path.exists("./ecoc"):
        print("./ecoc not found. Run ./build.sh first.")
        sys.exit(1)
    if not os.path.exists("micro.eco"):
        print("micro.eco not found.")
        sys.exit(1)

    rows = time_microbenchmarks()
    cost = fit(rows)

    lines = []
    lines.append("Machine: " + cpu_name())
    lines.append("Compiler: gcc -O0 -fwrapv, fastest of %d rounds x 7 timings, %d loop iterations" % (ROUNDS, N))
    lines.append("")
    lines.append("1) Time per instruction, fitted on %d microbenchmarks" % len(rows))
    lines.append("   %-7s %10s %10s %14s" % ("Kind", "ns", "x ALU", "old (Skylake)"))
    old = [1, 3, 40, 5, 1]
    for i in range(len(CATS)):
        ratio = 0.0
        if cost[0] > 0:
            ratio = cost[i] / cost[0]
        lines.append("   %-7s %10.3f %10.2f %14d" % (CATS[i], cost[i], ratio, old[i]))
    lines.append("")
    lines.append("2) How well the fit explains each microbenchmark")
    lines.append("   %-18s %12s %12s %8s" % ("Microbenchmark", "measured ns", "fitted ns", "error"))
    for row in rows:
        fitted = predict(row["counts"], cost)
        lines.append("   %-18s %12.0f %12.0f %7.1f%%" %
                     (row["label"], row["time_ns"], fitted, 100.0 * (fitted - row["time_ns"]) / row["time_ns"]))
    lines.append("")

    bench = read_benchmarks()
    if bench is None:
        lines.append("3) Benchmark test skipped: run  python3 validate_timing.py  first, then this script again.")
    else:
        errors = []
        signed = []
        lines.append("3) TEST on the real benchmarks (NOT used for fitting)")
        lines.append("   %-22s %-16s %12s %12s %8s" % ("Benchmark", "Build", "measured ns", "predicted", "error"))
        for row in bench:
            p = predict(row["counts"], cost)
            e = 100.0 * (p - row["time_ns"]) / row["time_ns"]
            signed.append(e)
            errors.append(abs(e))
            lines.append("   %-22s %-16s %12.0f %12.0f %7.1f%%" %
                         (row["benchmark"], row["build"], row["time_ns"], p, e))
        lines.append("   Prediction error on unseen programs: median %.1f%%, mean %.1f%% (%d builds)" %
                     (median(errors), mean(errors), len(errors)))
        lines.append("   Average signed error %.1f%% (negative = model predicts too little time)" % mean(signed))

    bad = False
    for i in range(len(CATS)):
        if cost[i] <= 0:
            bad = True
    if bad:
        lines.append("")
        lines.append("WARNING: a cost came out zero - the timings were too noisy.")
        lines.append("Plug in the charger, close other programs and run again.")

    if not os.path.exists(FOLDER):
        os.makedirs(FOLDER)
    f = open(os.path.join(FOLDER, "calibration.txt"), "w")
    f.write("\n".join(lines) + "\n")
    f.close()
    print("")
    print("\n".join(lines))
    if bad:
        sys.exit(1)
    write_profile(cost)


def write_profile(cost):
    """Two calibrated profiles: same TIME costs (measured here), and the ENERGY of
       profile_cpu.txt (with instruction overhead) or profile_datapath.txt (operation only)."""
    variants = [("profile_calibrated.txt", "profile_cpu.txt",
                 "same as profile_cpu.txt (Horowitz, ISSCC 2014 + 70 pJ per instruction)"),
                ("profile_calibrated_datapath.txt", "profile_datapath.txt",
                 "same as profile_datapath.txt (Horowitz, ISSCC 2014, operation only)")]
    for v in range(len(variants)):
        out_file, energy_file, energy_text = variants[v]
        energy = {}
        f = open(energy_file)
        for line in f:
            words = line.split()
            if len(words) >= 3 and words[0] in CATS:
                energy[words[0]] = words[1]
        f.close()

        out = []
        out.append("# EcoLang energy profile: CALIBRATED  (written by calibrate.py - do not edit by hand)")
        out.append("# Machine: " + cpu_name())
        out.append("#")
        out.append("# ENERGY: " + energy_text + ",")
        out.append("#         energy unit = 0.1 pJ")
        out.append("# TIME:   measured on this machine with micro.eco (gcc -O0), see")
        out.append("#         results/calibration/calibration.txt; time unit = %.2f ns" % TIME_UNIT_NS)
        out.append("#")
        out.append("# category   energy_units   time_units")
        for i in range(len(CATS)):
            units = int(round(cost[i] / TIME_UNIT_NS))
            if units < 1:
                units = 1
            out.append("%-12s %-14s %d" % (CATS[i], energy[CATS[i]], units))
        out.append("")
        out.append("K            0.0000000000001")
        out.append("")
        out.append("# grams of CO2 per kWh (India, FY 2024-25, CEA v21.0)")
        out.append("GRID         710")
        f = open(out_file, "w")
        f.write("\n".join(out) + "\n")
        f.close()
        print("Written:", out_file)
    print("")
    print("Next:  python3 validate_timing.py   then   python3 run_experiments.py")


main()
