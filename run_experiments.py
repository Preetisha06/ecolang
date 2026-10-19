# run_experiments.py - runs every EcoLang experiment and saves CSV files + graphs
#
# How to run (inside the ecolang folder, after ./build.sh):
#     python3 run_experiments.py
#
# It runs everything once for EACH energy profile:
#     slide     energy_profile.txt     (the original Review-1 table, not from a paper)
#     datapath  profile_datapath.txt   (Horowitz ISSCC 2014 operation energy + Skylake cycles)
#     cpu       profile_cpu.txt        (same + 70 pJ instruction overhead per instruction)
#     cpu-calibrated, datapath-calibrated   (after calibrate.py: time costs measured on this computer)
#
# For each profile it creates a folder results/<profile> with:
#     main.csv       every benchmark x every build (RQ1, RQ3, RQ4)
#     ablation.csv   carbon build with one pass switched off at a time (RQ2)
#     alpha.csv      alpha from 1.0 down to 0.0 (the speed vs carbon trade-off)
#     summary.txt    the main tables as text, ready to copy into the paper
#     fig1_energy_saving.png, fig2_ablation.png, fig3_breakdown.png, fig4_alpha.png
# and one file comparing the profiles: results/goal_comparison.txt (RQ3)

import os
import subprocess
import sys

# ---------------- what to run ----------------

# (name, file, function, input size)
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

# (build name, extra options)
BUILDS = [
    ("none",     []),
    ("speed",    ["--goal=speed"]),
    ("balanced", ["--goal=balanced"]),
    ("carbon",   ["--goal=carbon"]),
]

# switch off one pass at a time, starting from the carbon build
ABLATION = [
    ("all passes", []),
    ("without CF",  ["--no-cf"]),
    ("without CP",  ["--no-cp"]),
    ("without SR",  ["--no-sr"]),
    ("without DCE", ["--no-dce"]),
    ("without CSE", ["--no-cse"]),
    ("without LICM", ["--no-licm"]),
]

ALPHAS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]

# (short name, profile file)
PROFILES = [
    ("slide",    "energy_profile.txt"),
    ("datapath", "profile_datapath.txt"),
    ("cpu",      "profile_cpu.txt"),
]

# time costs measured on this computer by calibrate.py (if it has been run)
if os.path.exists("profile_calibrated.txt"):
    PROFILES.append(("cpu-calibrated", "profile_calibrated.txt"))
if os.path.exists("profile_calibrated_datapath.txt"):
    PROFILES.append(("datapath-calibrated", "profile_calibrated_datapath.txt"))

# the profile being run right now (set in main)
PROFILE_FILE = "energy_profile.txt"

# the columns that ./ecoc --csv prints, in order
COLUMNS = ["file", "function", "args", "goal", "passes", "result", "instructions",
           "alu", "mul", "div", "memory", "branch",
           "cycles", "energy_units", "energy_j", "co2_g", "opt_time_ms", "array_cells"]


# ---------------- helpers ----------------

def run_ecoc(file_name, function, size, options):
    """Run the compiler once and return its numbers as a dictionary."""
    command = ["./ecoc", file_name, function, str(size), "--csv", "--profile=" + PROFILE_FILE] + options
    output = subprocess.run(command, capture_output=True, text=True)
    lines = output.stdout.strip().split("\n")
    last = lines[len(lines) - 1]
    parts = last.split(",")
    if len(parts) != len(COLUMNS):
        print("ERROR running:", " ".join(command))
        print(output.stdout, output.stderr)
        sys.exit(1)

    row = {}
    for i in range(len(COLUMNS)):
        row[COLUMNS[i]] = parts[i]
    return row


def percent_change(new, old):
    """How much smaller or bigger new is than old, in percent (negative = saved)."""
    return 100.0 * (new - old) / old


def write_csv(path, header, rows):
    f = open(path, "w")
    f.write(",".join(header) + "\n")
    for row in rows:
        values = []
        for h in header:
            values.append(str(row[h]))
        f.write(",".join(values) + "\n")
    f.close()


# ---------------- experiment 1: every benchmark x every build ----------------

def experiment_main():
    print("Experiment 1: 9 benchmarks x 4 builds")
    rows = []
    for b in range(len(BENCHMARKS)):
        name, file_name, function, size = BENCHMARKS[b]
        baseline = None
        for k in range(len(BUILDS)):
            build_name, options = BUILDS[k]
            r = run_ecoc(file_name, function, size, options)

            # every build must give the SAME answer, otherwise the optimiser is wrong
            if baseline is None:
                baseline = r
            elif r["result"] != baseline["result"]:
                print("  WRONG RESULT:", name, build_name, r["result"], "vs", baseline["result"])
                sys.exit(1)

            row = {
                "benchmark": name,
                "size": size,
                "build": build_name,
                "result": r["result"],
                "instructions": int(r["instructions"]),
                "alu": int(r["alu"]),
                "mul": int(r["mul"]),
                "div": int(r["div"]),
                "memory": int(r["memory"]),
                "branch": int(r["branch"]),
                "cycles": int(r["cycles"]),
                "energy_units": int(r["energy_units"]),
                "energy_j": float(r["energy_j"]),
                "co2_g": float(r["co2_g"]),
                "opt_time_ms": float(r["opt_time_ms"]),
                "array_cells": int(r["array_cells"]),
                "energy_saving_pct": round(-percent_change(int(r["energy_units"]), int(baseline["energy_units"])), 2),
                "time_saving_pct": round(-percent_change(int(r["cycles"]), int(baseline["cycles"])), 2),
            }
            rows.append(row)
        print("  done:", name, "(result", baseline["result"] + ", same in all builds)")
    return rows


# ---------------- experiment 2: ablation (which pass matters most) ----------------

def experiment_ablation():
    print("Experiment 2: ablation, carbon build with one pass off")
    rows = []
    for b in range(len(BENCHMARKS)):
        name, file_name, function, size = BENCHMARKS[b]
        none_run = run_ecoc(file_name, function, size, [])
        none_energy = int(none_run["energy_units"])
        full_energy = 0
        for k in range(len(ABLATION)):
            label, options = ABLATION[k]
            r = run_ecoc(file_name, function, size, ["--goal=carbon"] + options)
            energy = int(r["energy_units"])
            if k == 0:
                full_energy = energy
            row = {
                "benchmark": name,
                "setting": label,
                "energy_units": energy,
                "cycles": int(r["cycles"]),
                "energy_saving_pct": round(-percent_change(energy, none_energy), 2),
                # how much energy we LOSE by turning this pass off
                "lost_saving_pct": round(100.0 * (energy - full_energy) / none_energy, 2),
            }
            rows.append(row)
    return rows


# ---------------- experiment 3: alpha sweep ----------------

def experiment_alpha():
    print("Experiment 3: alpha sweep")
    rows = []
    programs = [("goals demo", "goals.eco", "work", 1000)] + BENCHMARKS
    for p in range(len(programs)):
        name, file_name, function, size = programs[p]
        for a in range(len(ALPHAS)):
            alpha = ALPHAS[a]
            r = run_ecoc(file_name, function, size, ["--alpha=" + str(alpha)])
            rows.append({
                "benchmark": name,
                "alpha": alpha,
                "cycles": int(r["cycles"]),
                "energy_units": int(r["energy_units"]),
                "mul": int(r["mul"]),
            })
    return rows


# ---------------- text summary ----------------

def find(rows, benchmark, key, value):
    for row in rows:
        if row["benchmark"] == benchmark and row[key] == value:
            return row
    return None


def write_summary(main_rows, ablation_rows, path):
    lines = []
    lines.append("TABLE 1 - Energy and time saving vs. no optimisation (%)")
    lines.append("%-24s %10s %10s %10s %10s %10s %10s" %
                 ("Benchmark", "E speed", "E balanced", "E carbon", "T speed", "T balanced", "T carbon"))
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        s = find(main_rows, name, "build", "speed")
        m = find(main_rows, name, "build", "balanced")
        c = find(main_rows, name, "build", "carbon")
        lines.append("%-24s %10.2f %10.2f %10.2f %10.2f %10.2f %10.2f" %
                     (name, s["energy_saving_pct"], m["energy_saving_pct"], c["energy_saving_pct"],
                      s["time_saving_pct"], m["time_saving_pct"], c["time_saving_pct"]))

    lines.append("")
    lines.append("TABLE 2 - Energy saving LOST when one pass is switched off (carbon build, % of baseline)")
    lines.append("%-24s %8s %8s %8s %8s %8s %8s" % ("Benchmark", "CF", "CP", "SR", "DCE", "CSE", "LICM"))
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        values = []
        for label in ["without CF", "without CP", "without SR", "without DCE", "without CSE", "without LICM"]:
            values.append(find(ablation_rows, name, "setting", label)["lost_saving_pct"])
        lines.append("%-24s %8.2f %8.2f %8.2f %8.2f %8.2f %8.2f" %
                     (name, values[0], values[1], values[2], values[3], values[4], values[5]))

    lines.append("")
    lines.append("TABLE 3 - Share of energy by instruction type, no optimisation (%)")
    lines.append("%-24s %8s %8s %8s %8s %8s" % ("Benchmark", "ALU", "MUL", "DIV", "MEMORY", "BRANCH"))
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        r = find(main_rows, name, "build", "none")
        shares = energy_shares(r)
        lines.append("%-24s %8.1f %8.1f %8.1f %8.1f %8.1f" % (name, shares[0], shares[1], shares[2], shares[3], shares[4]))

    f = open(path, "w")
    f.write("\n".join(lines) + "\n")
    f.close()
    print("")
    print("\n".join(lines))


def read_costs():
    """Energy cost per category from energy_profile.txt (same file the compiler uses)."""
    costs = {"ALU": 1, "MUL": 4, "DIV": 8, "MEMORY": 6, "BRANCH": 1}
    f = open(PROFILE_FILE)
    for line in f:
        words = line.split()
        if len(words) >= 2 and words[0] in costs:
            costs[words[0]] = int(words[1])
    f.close()
    return costs


COSTS = None


def energy_shares(r):
    e = [r["alu"] * COSTS["ALU"], r["mul"] * COSTS["MUL"], r["div"] * COSTS["DIV"],
         r["memory"] * COSTS["MEMORY"], r["branch"] * COSTS["BRANCH"]]
    total = 0
    for i in range(len(e)):
        total = total + e[i]
    shares = []
    for i in range(len(e)):
        shares.append(100.0 * e[i] / total)
    return shares


# ---------------- graphs ----------------

# colour-blind-checked palette (validated), in a fixed order
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
TEXT = "#2b2b29"
MUTED = "#6b6b66"
GRID = "#e4e3df"


def style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def make_graphs(main_rows, ablation_rows, alpha_rows, folder):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("")
        print("matplotlib is not installed, so no graphs were drawn.")
        print("Install it with:  sudo apt install python3-matplotlib")
        print("then run this script again. (All CSV files are already saved.)")
        return

    names = []
    for b in range(len(BENCHMARKS)):
        names.append(BENCHMARKS[b][0])
    x = list(range(len(names)))

    # ---- figure 1: energy saving per benchmark, per goal (RQ1, RQ3) ----
    fig, ax = plt.subplots(figsize=(10, 4.2))
    goals = ["speed", "balanced", "carbon"]
    width = 0.26
    for g in range(len(goals)):
        values = []
        for b in range(len(names)):
            values.append(find(main_rows, names[b], "build", goals[g])["energy_saving_pct"])
        positions = []
        for b in range(len(names)):
            positions.append(x[b] + (g - 1) * width)
        ax.bar(positions, values, width=width - 0.03, color=COLORS[g], label="--goal=" + goals[g])
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=20, ha="right", color=TEXT)
    ax.set_ylabel("Energy saved vs. no optimisation (%)", color=TEXT)
    ax.set_title("Estimated energy saving by goal", color=TEXT, loc="left", fontsize=12)
    style_axes(ax)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "fig1_energy_saving.png"), dpi=200)
    plt.close(fig)

    # ---- figure 2: ablation, which pass matters most (RQ2) ----
    fig, ax = plt.subplots(figsize=(10, 4.2))
    passes = ["without CF", "without CP", "without SR", "without DCE", "without CSE", "without LICM"]
    labels = ["Constant folding", "Propagation", "Strength reduction", "Dead code elim.",
              "Common subexpr.", "Loop-invariant motion"]
    width = 0.13
    for p in range(len(passes)):
        values = []
        for b in range(len(names)):
            values.append(find(ablation_rows, names[b], "setting", passes[p])["lost_saving_pct"])
        positions = []
        for b in range(len(names)):
            positions.append(x[b] + (p - 2.5) * width)
        ax.bar(positions, values, width=width - 0.02, color=COLORS[p], label=labels[p])
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=20, ha="right", color=TEXT)
    ax.set_ylabel("Energy saving lost when pass is off (%)", color=TEXT)
    ax.set_title("Contribution of each optimisation pass (carbon build)", color=TEXT, loc="left", fontsize=12)
    style_axes(ax)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "fig2_ablation.png"), dpi=200)
    plt.close(fig)

    # ---- figure 3: where the energy goes (stacked 100% bars) ----
    fig, ax = plt.subplots(figsize=(10, 4.2))
    cats = ["ALU", "MUL", "DIV", "MEMORY", "BRANCH"]
    bottoms = [0.0] * len(names)
    for c in range(len(cats)):
        values = []
        for b in range(len(names)):
            shares = energy_shares(find(main_rows, names[b], "build", "none"))
            values.append(shares[c])
        ax.bar(x, values, bottom=bottoms, width=0.6, color=COLORS[c], label=cats[c],
               edgecolor="white", linewidth=1.5)
        for b in range(len(names)):
            bottoms[b] = bottoms[b] + values[b]
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=20, ha="right", color=TEXT)
    ax.set_ylabel("Share of estimated energy (%)", color=TEXT)
    ax.set_ylim(0, 100)
    ax.set_title("Where the energy goes (no optimisation)", color=TEXT, loc="left", fontsize=12)
    style_axes(ax)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT, ncol=5, loc="upper center",
              bbox_to_anchor=(0.5, -0.32))
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "fig3_breakdown.png"), dpi=200)
    plt.close(fig)

    # ---- figure 4: alpha sweep on the goals demo (energy and time, both indexed to alpha = 1) ----
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    demo = []
    for row in alpha_rows:
        if row["benchmark"] == "goals demo":
            demo.append(row)
    base_e = demo[0]["energy_units"]
    base_t = demo[0]["cycles"]
    alphas = []
    e_idx = []
    t_idx = []
    for i in range(len(demo)):
        alphas.append(demo[i]["alpha"])
        e_idx.append(100.0 * demo[i]["energy_units"] / base_e)
        t_idx.append(100.0 * demo[i]["cycles"] / base_t)
    ax.plot(alphas, e_idx, color=COLORS[0], linewidth=2, marker="o", markersize=5, label="Energy")
    ax.plot(alphas, t_idx, color=COLORS[1], linewidth=2, marker="s", markersize=5, label="Time (cycles)")
    ax.invert_xaxis()
    ax.set_xlabel("alpha  (1 = speed  ...  0 = carbon)", color=TEXT)
    ax.set_ylabel("Indexed to alpha = 1 (= 100)", color=TEXT)
    ax.set_title("Speed vs. carbon trade-off (goals demo)", color=TEXT, loc="left", fontsize=12)
    style_axes(ax)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "fig4_alpha.png"), dpi=200)
    plt.close(fig)

    print("")
    print("Graphs saved in the results folder.")


# ---------------- comparing goals across profiles (RQ3) ----------------

def goal_comparison(all_main, path):
    lines = []
    lines.append("SPEED vs CARBON build under each energy profile")
    lines.append("(positive = carbon build uses that much LESS energy / is that much SLOWER than the speed build)")
    lines.append("")
    for p in range(len(PROFILES)):
        pname = PROFILES[p][0]
        rows = all_main[pname]
        lines.append("Profile: " + pname)
        lines.append("%-24s %16s %16s %10s" % ("Benchmark", "energy saved %", "time cost %", "same code?"))
        for b in range(len(BENCHMARKS)):
            name = BENCHMARKS[b][0]
            s = find(rows, name, "build", "speed")
            c = find(rows, name, "build", "carbon")
            e_saved = 100.0 * (s["energy_units"] - c["energy_units"]) / s["energy_units"]
            t_cost = 100.0 * (c["cycles"] - s["cycles"]) / s["cycles"]
            same = "yes"
            if s["energy_units"] != c["energy_units"] or s["cycles"] != c["cycles"]:
                same = "NO"
            lines.append("%-24s %16.2f %16.2f %10s" % (name, e_saved, t_cost, same))
        lines.append("")
    f = open(path, "w")
    f.write("\n".join(lines) + "\n")
    f.close()
    print("\n".join(lines))


# ---------------- main ----------------

def main():
    global COSTS
    global PROFILE_FILE
    if not os.path.exists("./ecoc"):
        print("./ecoc not found. Run ./build.sh first.")
        sys.exit(1)

    if not os.path.exists("results"):
        os.makedirs("results")

    all_main = {}
    for p in range(len(PROFILES)):
        pname, pfile = PROFILES[p]
        PROFILE_FILE = pfile
        COSTS = read_costs()
        folder = os.path.join("results", pname)
        if not os.path.exists(folder):
            os.makedirs(folder)
        print("")
        print("================ Profile:", pname, "(" + pfile + ") ================")

        main_rows = experiment_main()
        write_csv(os.path.join(folder, "main.csv"),
                  ["benchmark", "size", "build", "result", "instructions", "alu", "mul", "div",
                   "memory", "branch", "cycles", "energy_units", "energy_j", "co2_g",
                   "opt_time_ms", "array_cells", "energy_saving_pct", "time_saving_pct"], main_rows)

        ablation_rows = experiment_ablation()
        write_csv(os.path.join(folder, "ablation.csv"),
                  ["benchmark", "setting", "energy_units", "cycles", "energy_saving_pct", "lost_saving_pct"],
                  ablation_rows)

        alpha_rows = experiment_alpha()
        write_csv(os.path.join(folder, "alpha.csv"),
                  ["benchmark", "alpha", "cycles", "energy_units", "mul"], alpha_rows)

        write_summary(main_rows, ablation_rows, os.path.join(folder, "summary.txt"))
        make_graphs(main_rows, ablation_rows, alpha_rows, folder)
        all_main[pname] = main_rows

    print("")
    goal_comparison(all_main, os.path.join("results", "goal_comparison.txt"))
    print("All results are in the 'results' folder.")


main()
