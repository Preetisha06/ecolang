# budget_sweep.py - experiment for the speed-budget mode (--max-slowdown)
#
# How to run (inside the ecolang folder, after ./build.sh):
#     python3 budget_sweep.py
#
# For every benchmark and every budget (0, 1, 2, 5, 10, 20 % slower than
# the speed build) it asks EcoLang for the greenest build within the budget,
# using the calibrated profile. It saves in results/budget/:
#     budget.csv          every benchmark x every budget
#     summary.txt         the table as text, ready for the slides / paper
#     fig8_budget.png     energy saved vs. allowed slowdown

import os
import subprocess
import sys

PROFILE_FILE = "profile_calibrated.txt"
FOLDER = os.path.join("results", "budget")
BUDGETS = [0, 1, 2, 5, 10, 20]

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

# the columns that ./ecoc --csv prints, in order
COLUMNS = ["file", "function", "args", "goal", "passes", "result", "instructions",
           "alu", "mul", "div", "memory", "branch",
           "cycles", "energy_units", "energy_j", "co2_g", "opt_time_ms", "array_cells"]


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


def alpha_from_goal(goal_text):
    """'budget 5.0% (alpha = 0.96)'  ->  '0.96'"""
    start = goal_text.find("alpha = ")
    if start == -1:
        return ""
    end = goal_text.find(")", start)
    return goal_text[start + 8:end]


def main():
    os.makedirs(FOLDER, exist_ok=True)
    rows = []

    for b in range(len(BENCHMARKS)):
        name, file_name, function, size = BENCHMARKS[b]
        speed = run_ecoc(file_name, function, size, ["--goal=speed"])
        carbon = run_ecoc(file_name, function, size, ["--goal=carbon"])
        speed_cycles = float(speed["cycles"])
        speed_energy = float(speed["energy_units"])
        carbon_saved = 100.0 * (speed_energy - float(carbon["energy_units"])) / speed_energy
        carbon_slower = 100.0 * (float(carbon["cycles"]) - speed_cycles) / speed_cycles

        for k in range(len(BUDGETS)):
            budget = BUDGETS[k]
            r = run_ecoc(file_name, function, size, ["--max-slowdown=" + str(budget)])
            if r["result"] != speed["result"]:
                print("  WRONG RESULT:", name, "budget", budget, r["result"], "vs", speed["result"])
                sys.exit(1)
            saved = 100.0 * (speed_energy - float(r["energy_units"])) / speed_energy
            slower = 100.0 * (float(r["cycles"]) - speed_cycles) / speed_cycles
            rows.append({
                "benchmark": name,
                "budget_pct": budget,
                "alpha": alpha_from_goal(r["goal"]),
                "slower_pct": round(slower, 2),
                "energy_saved_pct": round(saved, 2),
                "carbon_goal_saved_pct": round(carbon_saved, 2),
                "carbon_goal_slower_pct": round(carbon_slower, 2),
            })
        print("  done:", name, "(result", speed["result"] + ", same for every budget)")

    # ---- CSV ----
    header = ["benchmark", "budget_pct", "alpha", "slower_pct", "energy_saved_pct",
              "carbon_goal_saved_pct", "carbon_goal_slower_pct"]
    f = open(os.path.join(FOLDER, "budget.csv"), "w")
    f.write(",".join(header) + "\n")
    for row in rows:
        values = []
        for h in header:
            values.append(str(row[h]))
        f.write(",".join(values) + "\n")
    f.close()

    # ---- text table ----
    lines = []
    lines.append("SPEED BUDGET - energy saved vs. the speed build (%), calibrated profile")
    lines.append("(in brackets: how much slower the chosen build really is, est. time)")
    title = "%-22s" % "Benchmark"
    for k in range(len(BUDGETS)):
        title = title + "%15s" % ("<= " + str(BUDGETS[k]) + "% slower")
    title = title + "%18s" % "carbon goal"
    lines.append(title)
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        line = "%-22s" % name
        carbon_text = ""
        for row in rows:
            if row["benchmark"] == name:
                cell = "%.2f (%.2f)" % (row["energy_saved_pct"], row["slower_pct"])
                line = line + "%15s" % cell
                carbon_text = "%.2f (%.2f)" % (row["carbon_goal_saved_pct"], row["carbon_goal_slower_pct"])
        line = line + "%18s" % carbon_text
        lines.append(line)
    text = "\n".join(lines)
    print("")
    print(text)
    f = open(os.path.join(FOLDER, "summary.txt"), "w")
    f.write(text + "\n")
    f.close()

    make_graph(rows)
    print("")
    print("Results are in", FOLDER)


def make_graph(rows):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed, so no graph was drawn.")
        return

    COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6b6b66"]
    TEXT = "#2b2b29"
    MUTED = "#6b6b66"
    GRID = "#e4e3df"

    fig, ax = plt.subplots(figsize=(8, 4.2))
    c = 0
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        xs = []
        ys = []
        for row in rows:
            if row["benchmark"] == name:
                xs.append(row["budget_pct"])
                ys.append(row["energy_saved_pct"])
        # skip benchmarks where the goal never changes the code
        changes = 0
        for i in range(len(ys)):
            if ys[i] != 0:
                changes = 1
        if changes == 0:
            continue
        ax.plot(xs, ys, marker="o", color=COLORS[c % len(COLORS)], label=name, linewidth=2)
        c = c + 1

    ax.set_xlabel("Allowed slowdown vs. speed build (%)", color=TEXT)
    ax.set_ylabel("Energy saved vs. speed build (%)", color=TEXT)
    ax.set_title("Speed budget: greenest build within a time limit", color=TEXT, loc="left", fontsize=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xticks(BUDGETS)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT)
    fig.tight_layout()
    fig.savefig(os.path.join(FOLDER, "fig8_budget.png"), dpi=200)
    plt.close(fig)


main()
