# compare_gcc.py - EcoLang compared with gcc's own optimisation levels
#
# How to run (inside the ecolang folder, after ./build.sh):
#     sudo apt install valgrind        (only once)
#     python3 compare_gcc.py
#
# Every benchmark is turned into C by EcoLang (--emit-c) and compiled in
# several ways. For each build we MEASURE on this computer:
#     instructions  real x86 instructions executed by the program (inside
#                   run_once()), counted by valgrind. Start-up/printing not counted.
#     time          real run time (fastest of several timed runs)
# and check that every build gives the same answer.
#
# Builds:
#     gcc -O0                  the program, not optimised by anyone (baseline)
#     gcc -O1 / -O2 / -O3      gcc optimises the program itself (speed only)
#     EcoLang speed            EcoLang --goal=speed, then gcc -O0
#     EcoLang carbon           EcoLang --goal=carbon, then gcc -O0
#     EcoLang budget 5%        EcoLang --max-slowdown=5, then gcc -O0
#     EcoLang speed + gcc -O2  EcoLang --goal=speed, then gcc -O2
#     EcoLang carbon + gcc -O2 EcoLang --goal=carbon, then gcc -O2
#
# gcc -O1/-O2/-O3 and the "+ gcc -O2" builds are compiled with -DECO_PLAIN,
# so gcc can see every number and optimise as it would for normal C code.
# The EcoLang + gcc -O0 builds keep EcoLang's choices exactly as made.
#
# Results go to results/gcc/: compare.csv, summary.txt, fig9_gcc.png
#     python3 compare_gcc.py --graph-only    redraws the graph from compare.csv

import os
import re
import shutil
import subprocess
import sys

PROFILE_FILE = "profile_calibrated.txt"
FOLDER = os.path.join("results", "gcc")
WORK = os.path.join(FOLDER, "build")      # temporary C files and programs

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

# (build name, EcoLang options, which EcoLang C file, gcc options)
BUILDS = [
    ("gcc -O0",                  "none",   ["-O0"]),
    ("gcc -O1",                  "none",   ["-O1", "-DECO_PLAIN"]),
    ("gcc -O2",                  "none",   ["-O2", "-DECO_PLAIN"]),
    ("gcc -O3",                  "none",   ["-O3", "-DECO_PLAIN"]),
    ("EcoLang speed",            "speed",  ["-O0"]),
    ("EcoLang carbon",           "carbon", ["-O0"]),
    ("EcoLang budget 5%",        "budget", ["-O0"]),
    ("EcoLang speed + gcc -O2",  "speed",  ["-O2", "-DECO_PLAIN"]),
    ("EcoLang carbon + gcc -O2", "carbon", ["-O2", "-DECO_PLAIN"]),
]

ECOLANG_OPTIONS = {
    "none":   [],
    "speed":  ["--goal=speed"],
    "carbon": ["--goal=carbon"],
    "budget": ["--max-slowdown=5"],
}

TIMING_ROUNDS = 3     # each program times itself 7 times per round; fastest is kept


def run(command, env=None):
    out = subprocess.run(command, capture_output=True, text=True, env=env)
    return out.stdout, out.stderr, out.returncode


def make_c_files(file_name, function, size, short):
    """Ask EcoLang for one C file per goal. Returns {goal: path}."""
    paths = {}
    for goal in ECOLANG_OPTIONS:
        path = os.path.join(WORK, short + "_" + goal + ".c")
        command = ["./ecoc", file_name, function, str(size), "--quiet",
                   "--profile=" + PROFILE_FILE, "--emit-c=" + path] + ECOLANG_OPTIONS[goal]
        stdout, stderr, code = run(command)
        if code != 0 or not os.path.exists(path):
            print("ERROR:", " ".join(command))
            print(stdout, stderr)
            sys.exit(1)
        paths[goal] = path
    return paths


def compile_c(c_file, gcc_options, exe):
    command = ["gcc"] + gcc_options + ["-fwrapv", "-w", c_file, "-o", exe]
    stdout, stderr, code = run(command)
    if code != 0:
        print("gcc failed:", " ".join(command))
        print(stderr)
        sys.exit(1)


def count_instructions(exe, size):
    """Real x86 instructions executed by the program itself (inside run_once(),
    so program start-up and printing are not counted), using valgrind."""
    out_file = os.path.join(WORK, "callgrind.out")
    env = dict(os.environ)
    env["ECO_NO_TIMING"] = "1"
    stdout, stderr, code = run(["valgrind", "--tool=callgrind",
                                "--callgrind-out-file=" + out_file, exe, str(size)], env)
    result = ""
    for line in stdout.split("\n"):
        if line.startswith("result"):
            result = line.split()[1]
    stdout, stderr, code = run(["callgrind_annotate", "--inclusive=yes", "--threshold=100", out_file])
    os.remove(out_file)
    for line in stdout.split("\n"):
        m = re.match(r"^\s*([\d,]+)\s+\(\s*[\d.]+%\)\s+\S*:run_once(\.\S+)?\s", line)
        if m:
            return int(m.group(1).replace(",", "")), result
    print("could not find run_once() in the valgrind output for", exe)
    sys.exit(1)


def measure_time(exe, size):
    """Fastest run time in nanoseconds (the program times itself)."""
    best = -1.0
    for r in range(TIMING_ROUNDS):
        stdout, stderr, code = run([exe, str(size)])
        for line in stdout.split("\n"):
            words = line.split()
            if len(words) == 2 and words[0] == "min_ns":
                t = float(words[1])
                if best < 0 or t < best:
                    best = t
    return best


def main():
    if shutil.which("valgrind") is None or shutil.which("callgrind_annotate") is None:
        print("valgrind is not installed. Install it with:  sudo apt install valgrind")
        sys.exit(1)
    os.makedirs(WORK, exist_ok=True)

    rows = []
    for b in range(len(BENCHMARKS)):
        name, file_name, function, size = BENCHMARKS[b]
        short = file_name.replace(".eco", "")
        c_files = make_c_files(file_name, function, size, short)

        answer = ""
        for k in range(len(BUILDS)):
            build_name, goal, gcc_options = BUILDS[k]
            exe = os.path.join(WORK, short + "_" + str(k))
            compile_c(c_files[goal], gcc_options, exe)
            instructions, result = count_instructions(exe, size)
            time_ns = measure_time(exe, size)
            if k == 0:
                answer = result
            elif result != answer:
                print("  WRONG RESULT:", name, build_name, result, "vs", answer)
                sys.exit(1)
            rows.append({"benchmark": name, "build": build_name,
                         "result": result, "instructions": instructions,
                         "time_ns": round(time_ns, 1)})
            os.remove(exe)
        print("  done:", name, "(result", answer + ", same in all", len(BUILDS), "builds)")

    # ---- CSV ----
    header = ["benchmark", "build", "result", "instructions", "time_ns"]
    f = open(os.path.join(FOLDER, "compare.csv"), "w")
    f.write(",".join(header) + "\n")
    for row in rows:
        values = []
        for h in header:
            values.append(str(row[h]))
        f.write(",".join(values) + "\n")
    f.close()

    text = make_tables(rows)
    print("")
    print(text)
    f = open(os.path.join(FOLDER, "summary.txt"), "w")
    f.write(text + "\n")
    f.close()

    make_graph(rows)
    shutil.rmtree(WORK)
    print("")
    print("Results are in", FOLDER)


def find(rows, benchmark, build):
    for row in rows:
        if row["benchmark"] == benchmark and row["build"] == build:
            return row
    return None


def make_tables(rows):
    lines = []
    lines.append("EcoLang vs gcc - measured on this computer")
    lines.append("")
    lines.append("TABLE A - executed x86 instructions (program only), as % of gcc -O0 (lower = less work, less energy)")
    title = "%-22s" % "Benchmark"
    for k in range(len(BUILDS)):
        title = title + "%12s" % short_name(BUILDS[k][0])
    lines.append(title)
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        base = find(rows, name, "gcc -O0")["instructions"]
        line = "%-22s" % name
        for k in range(len(BUILDS)):
            value = find(rows, name, BUILDS[k][0])["instructions"]
            line = line + "%12.1f" % (100.0 * value / base)
        lines.append(line)

    lines.append("")
    lines.append("TABLE B - real run time, as % of gcc -O0 (lower = faster)")
    lines.append(title)
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        base = find(rows, name, "gcc -O0")["time_ns"]
        line = "%-22s" % name
        for k in range(len(BUILDS)):
            value = find(rows, name, BUILDS[k][0])["time_ns"]
            line = line + "%12.1f" % (100.0 * value / base)
        lines.append(line)

    lines.append("")
    lines.append("TABLE C - what the carbon goal changes (instructions, % change; negative = fewer)")
    lines.append("%-22s%26s%30s" % ("Benchmark", "EcoLang carbon vs speed", "carbon+gcc -O2 vs gcc -O2"))
    for b in range(len(BENCHMARKS)):
        name = BENCHMARKS[b][0]
        speed = find(rows, name, "EcoLang speed")["instructions"]
        carbon = find(rows, name, "EcoLang carbon")["instructions"]
        gcc2 = find(rows, name, "gcc -O2")["instructions"]
        carbon2 = find(rows, name, "EcoLang carbon + gcc -O2")["instructions"]
        lines.append("%-22s%25.1f%%%29.1f%%" % (name, 100.0 * (carbon - speed) / speed,
                                               100.0 * (carbon2 - gcc2) / gcc2))
    lines.append("")
    lines.append("Builds: " + ", ".join(b[0] for b in BUILDS))
    lines.append("gcc -O1/-O2/-O3 and '+ gcc -O2' builds use -DECO_PLAIN (gcc sees all constants).")
    return "\n".join(lines)


def short_name(build):
    names = {
        "gcc -O0": "gcc-O0", "gcc -O1": "gcc-O1", "gcc -O2": "gcc-O2", "gcc -O3": "gcc-O3",
        "EcoLang speed": "Eco-speed", "EcoLang carbon": "Eco-carbon",
        "EcoLang budget 5%": "Eco-bud5%",
        "EcoLang speed + gcc -O2": "EcoS+O2", "EcoLang carbon + gcc -O2": "EcoC+O2",
    }
    return names[build]


def make_graph(rows):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed, so no graph was drawn.")
        return

    COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
    TEXT = "#2b2b29"
    MUTED = "#6b6b66"
    GRID = "#e4e3df"

    shown = ["EcoLang speed", "EcoLang carbon", "gcc -O2", "EcoLang carbon + gcc -O2"]
    names = []
    for b in range(len(BENCHMARKS)):
        names.append(BENCHMARKS[b][0])
    x = list(range(len(names)))
    width = 0.2

    fig, ax = plt.subplots(figsize=(10, 4.8))
    for s in range(len(shown)):
        values = []
        positions = []
        for b in range(len(names)):
            base = find(rows, names[b], "gcc -O0")["instructions"]
            value = find(rows, names[b], shown[s])["instructions"]
            values.append(100.0 * value / base)
            positions.append(x[b] + (s - 1.5) * width)
        ax.bar(positions, values, width=width - 0.03, color=COLORS[s], label=shown[s])
    ax.axhline(100, color=MUTED, linewidth=1, linestyle="--")
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=20, ha="right", color=TEXT)
    ax.set_ylabel("Executed instructions (% of gcc -O0)", color=TEXT)
    ax.set_title("Real x86 instructions executed (valgrind) - lower is less work", color=TEXT,
                 loc="left", fontsize=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT, ncol=4,
              loc="upper center", bbox_to_anchor=(0.5, -0.30))
    fig.tight_layout()
    fig.savefig(os.path.join(FOLDER, "fig9_gcc.png"), dpi=200)
    plt.close(fig)


def graph_only():
    """Redraw fig9_gcc.png from results/gcc/compare.csv (no measuring)."""
    f = open(os.path.join(FOLDER, "compare.csv"))
    lines = f.read().strip().split("\n")
    f.close()
    header = lines[0].split(",")
    rows = []
    for i in range(1, len(lines)):
        parts = lines[i].split(",")
        row = {}
        for h in range(len(header)):
            row[header[h]] = parts[h]
        row["instructions"] = int(row["instructions"])
        row["time_ns"] = float(row["time_ns"])
        rows.append(row)
    make_graph(rows)
    print("Graph redrawn:", os.path.join(FOLDER, "fig9_gcc.png"))


if len(sys.argv) > 1 and sys.argv[1] == "--graph-only":
    graph_only()
else:
    main()
