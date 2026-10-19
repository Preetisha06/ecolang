# sweep_sizes.py - do the savings hold when the input gets bigger?
#
# Runs every benchmark at 5 input sizes with three builds (no optimisation,
# speed goal, carbon goal) and records the estimated energy and time.
# Every build at every size must give the same answer.
#
# Uses profile_calibrated.txt if calibrate.py has been run, otherwise profile_cpu.txt.
# Another profile can be given:   python3 sweep_sizes.py profile_calibrated_datapath.txt
#
# How to run (inside the ecolang folder, after ./build.sh):
#     python3 sweep_sizes.py
#
# Results go to results/sizes/:
#     sizes.csv        every run
#     summary.txt      energy and time saved at each size
#     fig7_sizes.png   energy saved vs input size, one small chart per benchmark

import os
import subprocess
import sys

# (name, file, function, sizes)
BENCHMARKS = [
    ("Fibonacci (loop)",      "fib.eco",       "fib",       [10, 30, 50, 70, 90]),
    ("Fibonacci (recursive)", "fibrec.eco",    "fib",       [10, 13, 16, 19, 22]),
    ("Bubble sort",           "bubble.eco",    "bubble",    [50, 100, 200, 400, 800]),
    ("Merge sort",            "mergesort.eco", "mergesort", [125, 250, 500, 1000, 2000]),
    ("Quick sort",            "quicksort.eco", "quicksort", [125, 250, 500, 1000, 2000]),
    ("Matrix multiply",       "matmul.eco",    "matmul",    [5, 10, 20, 30, 40]),
    ("BFS",                   "bfs.eco",       "bfs",       [20, 40, 60, 80, 100]),
    ("DFS",                   "dfs.eco",       "dfs",       [20, 40, 60, 80, 100]),
    ("Dijkstra",              "dijkstra.eco",  "dijkstra",  [20, 40, 60, 80, 100]),
]

BUILDS = [
    ("none",   []),
    ("speed",  ["--goal=speed"]),
    ("carbon", ["--goal=carbon"]),
]

FOLDER = os.path.join("results", "sizes")


def run_ecoc(file_name, function, size, options):
    command = ["./ecoc", file_name, function, str(size), "--csv"] + options
    out = subprocess.run(command, capture_output=True, text=True)
    parts = out.stdout.strip().split("\n")[-1].split(",")
    if len(parts) < 14:
        print("ERROR running:", " ".join(command))
        print(out.stdout, out.stderr)
        sys.exit(1)
    return {"result": parts[5], "instructions": int(parts[6]),
            "cycles": int(parts[12]), "energy": int(parts[13])}


def main():
    if not os.path.exists("./ecoc"):
        print("./ecoc not found. Run ./build.sh first.")
        sys.exit(1)

    profile = "profile_cpu.txt"
    if os.path.exists("profile_calibrated.txt"):
        profile = "profile_calibrated.txt"
    if len(sys.argv) > 1:
        profile = sys.argv[1]
    print("Energy profile:", profile)

    if not os.path.exists(FOLDER):
        os.makedirs(FOLDER)

    rows = []
    for b in range(len(BENCHMARKS)):
        name, file_name, function, sizes = BENCHMARKS[b]
        for s in range(len(sizes)):
            size = sizes[s]
            base = None
            for k in range(len(BUILDS)):
                build, options = BUILDS[k]
                r = run_ecoc(file_name, function, size, options + ["--profile=" + profile])
                if base is None:
                    base = r
                elif r["result"] != base["result"]:
                    print("WRONG RESULT:", name, size, build, r["result"], "vs", base["result"])
                    sys.exit(1)
                rows.append({
                    "benchmark": name, "size": size, "build": build, "result": r["result"],
                    "instructions": r["instructions"], "cycles": r["cycles"], "energy": r["energy"],
                    "energy_saving_pct": round(100.0 * (base["energy"] - r["energy"]) / base["energy"], 2),
                    "time_saving_pct": round(100.0 * (base["cycles"] - r["cycles"]) / base["cycles"], 2),
                })
        print("  done:", name)

    header = ["benchmark", "size", "build", "result", "instructions", "cycles", "energy",
              "energy_saving_pct", "time_saving_pct"]
    f = open(os.path.join(FOLDER, "sizes.csv"), "w")
    f.write(",".join(header) + "\n")
    for row in rows:
        values = []
        for h in header:
            values.append(str(row[h]))
        f.write(",".join(values) + "\n")
    f.close()

    lines = []
    lines.append("Energy and time saved vs no optimisation, at each input size (profile: " + profile + ")")
    lines.append("All builds gave the same answer at every size.")
    lines.append("")
    for b in range(len(BENCHMARKS)):
        name, file_name, function, sizes = BENCHMARKS[b]
        lines.append(name)
        lines.append("   %8s %12s %12s %12s %12s" % ("size", "E speed %", "E carbon %", "T speed %", "T carbon %"))
        for s in range(len(sizes)):
            sp = find(rows, name, sizes[s], "speed")
            ca = find(rows, name, sizes[s], "carbon")
            lines.append("   %8d %12.2f %12.2f %12.2f %12.2f" %
                         (sizes[s], sp["energy_saving_pct"], ca["energy_saving_pct"],
                          sp["time_saving_pct"], ca["time_saving_pct"]))
        lines.append("")

    # how much does the carbon saving change from the smallest to the largest size?
    lines.append("Carbon build: energy saved at smallest vs largest size")
    for b in range(len(BENCHMARKS)):
        name, file_name, function, sizes = BENCHMARKS[b]
        small = find(rows, name, sizes[0], "carbon")["energy_saving_pct"]
        large = find(rows, name, sizes[len(sizes) - 1], "carbon")["energy_saving_pct"]
        lines.append("   %-24s %7.2f%% -> %7.2f%%" % (name, small, large))

    f = open(os.path.join(FOLDER, "summary.txt"), "w")
    f.write("\n".join(lines) + "\n")
    f.close()
    print("")
    print("\n".join(lines))
    make_graph(rows)


def find(rows, name, size, build):
    for row in rows:
        if row["benchmark"] == name and row["size"] == size and row["build"] == build:
            return row
    return None


def make_graph(rows):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: no graph drawn")
        return

    TEXT = "#2b2b29"
    MUTED = "#6b6b66"
    GRID = "#e4e3df"
    series = [("speed", "#2a78d6", "o"), ("carbon", "#1baf7a", "s")]

    # one shared y-range so the small charts can be compared
    low = 0.0
    high = 0.0
    for row in rows:
        if row["build"] != "none":
            low = min(low, row["energy_saving_pct"])
            high = max(high, row["energy_saving_pct"])
    pad = (high - low) * 0.08

    fig, axes = plt.subplots(3, 3, figsize=(10, 8.2), sharey=True)
    for b in range(len(BENCHMARKS)):
        name, file_name, function, sizes = BENCHMARKS[b]
        ax = axes[b // 3][b % 3]
        for k in range(len(series)):
            build, color, marker = series[k]
            ys = []
            for s in range(len(sizes)):
                ys.append(find(rows, name, sizes[s], build)["energy_saving_pct"])
            ax.plot(sizes, ys, color=color, linewidth=2, marker=marker, markersize=5,
                    label="--goal=" + build)
        ax.set_title(name, color=TEXT, fontsize=10, loc="left")
        ax.set_ylim(low - pad, high + pad)
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(GRID)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=8)
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        if b % 3 == 0:
            ax.set_ylabel("Energy saved (%)", color=TEXT, fontsize=9)
        if b // 3 == 2:
            ax.set_xlabel("Input size", color=TEXT, fontsize=9)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", frameon=False, fontsize=9, labelcolor=TEXT, ncol=2)
    fig.suptitle("Estimated energy saved vs. input size", color=TEXT, x=0.02, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(os.path.join(FOLDER, "fig7_sizes.png"), dpi=200)
    plt.close(fig)
    print("")
    print("Graph saved: results/sizes/fig7_sizes.png")


main()
