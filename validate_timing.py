# validate_timing.py - checks the compiler's TIME estimates against real hardware
#
# For every benchmark and every build it:
#   1. asks EcoLang for the optimised code as a C program (--emit-c)
#   2. compiles it with  gcc -O0 -fwrapv  (gcc itself optimises nothing)
#   3. runs it, checks the answer is the same as EcoLang's interpreter,
#      and measures the real time (fastest of 5 rounds x 7 timings)
#   4. compares: estimated cycles  vs  measured nanoseconds
#
# How to run (inside the ecolang folder, after ./build.sh):
#     python3 validate_timing.py
#
# Results go to results/validation/:
#     timing.csv           every measurement (with instruction counts)
#     validation.txt       correlations and the speed-vs-carbon check
#     fig5_validation.png  predicted vs measured speed-up (Skylake cycle costs)
#     fig6_calibrated.png  the same with the calibrated costs (after calibrate.py)
#
# Full procedure:
#     python3 validate_timing.py     time all builds
#     python3 calibrate.py           measure time costs with micro.eco -> profile_calibrated.txt
#     python3 validate_timing.py     again: now also tests builds made with the calibrated model
#
# Tip: plug in your laptop and close other programs while it runs.

import math
import os
import subprocess
import sys

BENCHMARKS = [
    ("Fibonacci (loop)",      "fib.eco",       "fib",       90),
    ("Fibonacci (recursive)", "fibrec.eco",    "fib",       18),
    ("Bubble sort",           "bubble.eco",    "bubble",    200),
    ("Merge sort",            "mergesort.eco", "mergesort", 500),
    ("Quick sort",            "quicksort.eco", "quicksort", 500),
    ("Matrix multiply",       "matmul.eco",    "matmul",    20),
    ("BFS",                   "bfs.eco",       "bfs",       60),
    ("DFS",                   "dfs.eco",       "dfs",       60),
    ("Dijkstra",              "dijkstra.eco",  "dijkstra",  60),
]

# (build name, options) - all decisions made with the cpu profile,
# plus the carbon build with the datapath profile (it makes different choices)
BUILDS = [
    ("none",            ["--profile=profile_cpu.txt"]),
    ("speed",           ["--profile=profile_cpu.txt", "--goal=speed"]),
    ("balanced",        ["--profile=profile_cpu.txt", "--goal=balanced"]),
    ("carbon",          ["--profile=profile_cpu.txt", "--goal=carbon"]),
    ("carbon-datapath", ["--profile=profile_datapath.txt", "--goal=carbon"]),
]

# after calibrate.py has run, also test the decisions of the calibrated model
CAL_FILE = "profile_calibrated.txt"
if os.path.exists(CAL_FILE):
    BUILDS.append(("speed-cal",  ["--profile=" + CAL_FILE, "--goal=speed"]))
    BUILDS.append(("carbon-cal", ["--profile=" + CAL_FILE, "--goal=carbon"]))

CATS = ["alu", "mul", "div", "memory", "branch"]

FOLDER = os.path.join("results", "validation")
BUILD_DIR = os.path.join(FOLDER, "c_programs")


def cpu_name():
    """CPU model, written into the results (needed to report the experiment)."""
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


def run(command):
    out = subprocess.run(command, capture_output=True, text=True)
    return out.stdout, out.stderr, out.returncode


def estimate(file_name, function, size, options, c_file):
    """EcoLang's own numbers for this build, and write the C program."""
    command = ["./ecoc", file_name, function, str(size), "--csv", "--emit-c=" + c_file] + options
    stdout, stderr, code = run(command)
    parts = stdout.strip().split("\n")[-1].split(",")
    # columns: see run_experiments.py (result 5, counts 7-11, cycles 12, energy 13)
    counts = []
    for i in range(7, 12):
        counts.append(int(parts[i]))
    return {"result": parts[5], "cycles": int(parts[12]), "energy": int(parts[13]), "counts": counts}


def calibrated_costs():
    """ns per instruction of each kind, read from profile_calibrated.txt (or None)."""
    if not os.path.exists(CAL_FILE):
        return None
    names = ["ALU", "MUL", "DIV", "MEMORY", "BRANCH"]
    cost = [0.0] * 5
    unit = 0.01
    f = open(CAL_FILE)
    for line in f:
        words = line.split()
        if len(words) >= 3 and words[0] in names:
            cost[names.index(words[0])] = int(words[2]) * unit
    f.close()
    return cost


def predicted_ns(row, cost):
    total = 0.0
    for i in range(5):
        total = total + row["counts"][i] * cost[i]
    return total


def compile_c(c_file, exe_file):
    stdout, stderr, code = run(["gcc", "-O0", "-fwrapv", "-w", c_file, "-o", exe_file])
    if code != 0:
        print("gcc failed on", c_file)
        print(stderr)
        sys.exit(1)


def time_once(exe_file, size):
    """Run the compiled program once. It times itself 7 times; we keep its fastest time."""
    stdout, stderr, code = run(["./" + exe_file, str(size)])
    values = {}
    for line in stdout.strip().split("\n"):
        words = line.split()
        values[words[0]] = words[1]
    return values["result"], float(values["min_ns"])


ROUNDS = 5   # every program is timed in 5 rounds, builds take turns; the fastest time is kept


# ---------- simple statistics (written out by hand) ----------

def mean(xs):
    total = 0.0
    for i in range(len(xs)):
        total = total + xs[i]
    return total / len(xs)


def pearson(xs, ys):
    mx = mean(xs)
    my = mean(ys)
    sxy = 0.0
    sxx = 0.0
    syy = 0.0
    for i in range(len(xs)):
        sxy = sxy + (xs[i] - mx) * (ys[i] - my)
        sxx = sxx + (xs[i] - mx) * (xs[i] - mx)
        syy = syy + (ys[i] - my) * (ys[i] - my)
    if sxx == 0 or syy == 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def ranks(xs):
    order = []
    for i in range(len(xs)):
        order.append((xs[i], i))
    order.sort()
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][0] == order[i][0]:
            j = j + 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k][1]] = avg
        i = j + 1
    return r


def spearman(xs, ys):
    return pearson(ranks(xs), ranks(ys))


# ---------- main ----------

def main():
    if not os.path.exists("./ecoc"):
        print("./ecoc not found. Run ./build.sh first.")
        sys.exit(1)
    if not os.path.exists(BUILD_DIR):
        os.makedirs(BUILD_DIR)

    print("CPU:", cpu_name())
    print("Compiling all programs ...")
    rows = []
    for b in range(len(BENCHMARKS)):
        name, file_name, function, size = BENCHMARKS[b]
        for k in range(len(BUILDS)):
            build, options = BUILDS[k]
            base = file_name.replace(".eco", "") + "_" + build
            c_file = os.path.join(BUILD_DIR, base + ".c")
            exe_file = os.path.join(BUILD_DIR, base)
            est = estimate(file_name, function, size, options, c_file)
            compile_c(c_file, exe_file)
            row = {"benchmark": name, "build": build, "result": est["result"],
                   "est_cycles": est["cycles"], "est_energy": est["energy"],
                   "counts": est["counts"], "exe": exe_file, "size": size, "times": []}
            for i in range(5):
                row[CATS[i]] = est["counts"][i]
            rows.append(row)

    # time in rounds: in each round every program runs once, so slow moments
    # of the laptop affect all builds equally
    for r in range(ROUNDS):
        print("Timing round", r + 1, "of", ROUNDS, "...")
        for row in rows:
            result, t = time_once(row["exe"], row["size"])
            if result != row["result"]:
                print("WRONG RESULT in compiled C:", row["benchmark"], row["build"], result, "vs", row["result"])
                sys.exit(1)
            row["times"].append(t)

    for row in rows:
        best = row["times"][0]
        worst = row["times"][0]
        for i in range(len(row["times"])):
            best = min(best, row["times"][i])
            worst = max(worst, row["times"][i])
        row["time_ns"] = best
        row["min_ns"] = best
        row["max_ns"] = worst
        print("  %-22s %-16s cycles=%-9d measured=%10.0f ns  (spread %.0f%%)" %
              (row["benchmark"], row["build"], row["est_cycles"], best, 100.0 * (worst - best) / best))
    print("All compiled programs gave the same answers as the interpreter.")

    # save every measurement
    f = open(os.path.join(FOLDER, "timing.csv"), "w")
    header = ["benchmark", "build", "result", "alu", "mul", "div", "memory", "branch",
              "est_cycles", "est_energy", "time_ns", "min_ns", "max_ns"]
    f.write(",".join(header) + "\n")
    for row in rows:
        values = []
        for h in header:
            values.append(str(row[h]))
        f.write(",".join(values) + "\n")
    f.close()

    # ----- analysis -----
    lines = []
    lines.append("Machine: " + cpu_name())
    lines.append("Compiler: gcc -O0 -fwrapv, fastest of %d rounds x 7 timings" % ROUNDS)
    lines.append("")

    # (1) across all runs: do more estimated cycles mean more real time?
    log_c = []
    log_t = []
    for row in rows:
        log_c.append(math.log(row["est_cycles"]))
        log_t.append(math.log(row["time_ns"]))
    lines.append("1) All benchmarks and builds together (%d runs)" % len(rows))
    lines.append("   Pearson correlation of log(estimated cycles) vs log(measured time): %.3f" % pearson(log_c, log_t))
    lines.append("   Spearman rank correlation:                                         %.3f" % spearman(log_c, log_t))
    lines.append("")

    # (2) speed-ups vs the no-optimisation build
    pred = []
    meas = []
    lines.append("2) Speed-up over no optimisation: predicted vs measured")
    lines.append("   %-22s %-16s %10s %10s" % ("Benchmark", "Build", "predicted", "measured"))
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        base = None
        for row in rows:
            if row["benchmark"] == name and row["build"] == "none":
                base = row
        for row in rows:
            if row["benchmark"] == name and row["build"] != "none" and not row["build"].endswith("-cal"):
                p = base["est_cycles"] / row["est_cycles"]
                m = base["time_ns"] / row["time_ns"]
                row["pred_speedup"] = p
                row["meas_speedup"] = m
                pred.append(p)
                meas.append(m)
                lines.append("   %-22s %-16s %10.3f %10.3f" % (name, row["build"], p, m))
    lines.append("   Pearson correlation of predicted vs measured speed-up: %.3f" % pearson(pred, meas))
    lines.append("   Spearman rank correlation:                            %.3f" % spearman(pred, meas))
    lines.append("")

    # (3) the key claim: when EcoLang says speed build is faster than carbon build, is it really?
    lines.append("3) Speed build vs carbon build (cpu profile, Skylake cycle costs): which one is really faster?")
    compare_goals(rows, "speed", "carbon", None, lines)
    lines.append("")

    cost = calibrated_costs()
    if cost is not None and has_build(rows, "speed-cal"):
        lines.append("4) Speed build vs carbon build made with the CALIBRATED model")
        compare_goals(rows, "speed-cal", "carbon-cal", cost, lines)
        lines.append("")

        lines.append("5) Calibrated model: predicted vs measured time for every build")
        errors = []
        pred = []
        meas = []
        for b in range(len(BENCHMARKS)):
            name = BENCHMARKS[b][0]
            base = None
            for row in rows:
                if row["benchmark"] == name and row["build"] == "none":
                    base = row
            for row in rows:
                if row["benchmark"] == name:
                    e = 100.0 * abs(predicted_ns(row, cost) - row["time_ns"]) / row["time_ns"]
                    errors.append(e)
                    if row["build"] != "none":
                        row["pred_speedup_cal"] = predicted_ns(base, cost) / predicted_ns(row, cost)
                        row["meas_speedup"] = base["time_ns"] / row["time_ns"]
                        pred.append(row["pred_speedup_cal"])
                        meas.append(row["meas_speedup"])
        errors.sort()
        lines.append("   median error %.1f%%, mean error %.1f%% over %d builds" %
                     (errors[len(errors) // 2], mean(errors), len(errors)))
        lines.append("   (the calibrated costs come from micro.eco only, so these benchmarks")
        lines.append("    are unseen programs for the model)")
        lines.append("   Pearson correlation of predicted vs measured speed-up: %.3f" % pearson(pred, meas))
        lines.append("   Spearman rank correlation:                            %.3f" % spearman(pred, meas))
        lines.append("")

    f = open(os.path.join(FOLDER, "validation.txt"), "w")
    f.write("\n".join(lines) + "\n")
    f.close()
    print("")
    print("\n".join(lines))

    make_graph(rows)
    if cost is not None and has_build(rows, "speed-cal"):
        make_graph_calibrated(rows)


def has_build(rows, build):
    for row in rows:
        if row["build"] == build:
            return True
    return False


def compare_goals(rows, speed_build, carbon_build, cost, lines):
    """Is the build EcoLang thinks is faster really faster? cost=None uses est_cycles."""
    agree = 0
    total = 0
    lines.append("   %-22s %16s %16s %8s" % ("Benchmark", "predicted ratio", "measured ratio", "agree?"))
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        s = None
        c = None
        for row in rows:
            if row["benchmark"] == name and row["build"] == speed_build:
                s = row
            if row["benchmark"] == name and row["build"] == carbon_build:
                c = row
        if cost is None:
            p = c["est_cycles"] / s["est_cycles"]
        else:
            p = predicted_ns(c, cost) / predicted_ns(s, cost)
        m = c["time_ns"] / s["time_ns"]
        if s["counts"] == c["counts"]:
            verdict = "same code"
        else:
            total = total + 1
            if (p > 1.0 and m > 1.0) or (p < 1.0 and m < 1.0):
                agree = agree + 1
                verdict = "yes"
            else:
                verdict = "NO"
        lines.append("   %-22s %16.3f %16.3f %8s" % (name, p, m, verdict))
    lines.append("   (ratio > 1 means the carbon build is slower)")
    lines.append("   Direction predicted correctly in %d of %d benchmarks where the code differs" % (agree, total))


def make_graph_calibrated(rows):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
    MARKERS = ["o", "s", "^", "D", "v", "P"]
    TEXT = "#2b2b29"
    MUTED = "#6b6b66"
    GRID = "#e4e3df"
    builds = ["speed", "balanced", "carbon", "carbon-datapath", "speed-cal", "carbon-cal"]

    fig, ax = plt.subplots(figsize=(6.5, 5.2))
    low = 10.0
    high = 0.0
    for k in range(len(builds)):
        xs = []
        ys = []
        for row in rows:
            if row["build"] == builds[k] and "pred_speedup_cal" in row:
                xs.append(row["pred_speedup_cal"])
                ys.append(row["meas_speedup"])
                low = min(low, row["pred_speedup_cal"], row["meas_speedup"])
                high = max(high, row["pred_speedup_cal"], row["meas_speedup"])
        ax.scatter(xs, ys, s=46, color=COLORS[k], marker=MARKERS[k], label=builds[k],
                   edgecolors="white", linewidths=1.2, zorder=3)
    pad = 0.05
    ax.plot([low - pad, high + pad], [low - pad, high + pad], color=MUTED, linewidth=1,
            linestyle="--", zorder=1, label="perfect prediction")
    ax.set_xlabel("Predicted speed-up (calibrated model)", color=TEXT)
    ax.set_ylabel("Measured speed-up (real time, gcc -O0)", color=TEXT)
    ax.set_title("Calibrated time model vs. real hardware", color=TEXT, loc="left", fontsize=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT)
    fig.tight_layout()
    fig.savefig(os.path.join(FOLDER, "fig6_calibrated.png"), dpi=200)
    plt.close(fig)
    print("Graph saved: results/validation/fig6_calibrated.png")


def make_graph(rows):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: no graph drawn (sudo apt install python3-matplotlib)")
        return

    COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
    MARKERS = ["o", "s", "^", "D"]
    TEXT = "#2b2b29"
    MUTED = "#6b6b66"
    GRID = "#e4e3df"
    builds = ["speed", "balanced", "carbon", "carbon-datapath"]

    fig, ax = plt.subplots(figsize=(6.5, 5.2))
    low = 10.0
    high = 0.0
    for k in range(len(builds)):
        xs = []
        ys = []
        for row in rows:
            if row["build"] == builds[k] and "pred_speedup" in row:
                xs.append(row["pred_speedup"])
                ys.append(row["meas_speedup"])
                low = min(low, row["pred_speedup"], row["meas_speedup"])
                high = max(high, row["pred_speedup"], row["meas_speedup"])
        ax.scatter(xs, ys, s=46, color=COLORS[k], marker=MARKERS[k], label=builds[k],
                   edgecolors="white", linewidths=1.2, zorder=3)
    pad = 0.05
    ax.plot([low - pad, high + pad], [low - pad, high + pad], color=MUTED, linewidth=1,
            linestyle="--", zorder=1, label="perfect prediction")
    ax.set_xlabel("Predicted speed-up (estimated cycles)", color=TEXT)
    ax.set_ylabel("Measured speed-up (real time, gcc -O0)", color=TEXT)
    ax.set_title("Time model vs. real hardware", color=TEXT, loc="left", fontsize=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT)
    fig.tight_layout()
    fig.savefig(os.path.join(FOLDER, "fig5_validation.png"), dpi=200)
    plt.close(fig)
    print("Graph saved: results/validation/fig5_validation.png")


main()
