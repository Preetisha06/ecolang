# EcoLang — a carbon-aware optimising compiler

EcoLang is a small programming language and its compiler. The compiler does more than optimise code for speed. It **estimates the energy and carbon cost** of every program it runs, and it can be told what to aim for:

```
--goal=speed      make the program fast
--goal=carbon     make the program use less energy
--goal=balanced   a mix of both
```

Every run ends with a sustainability report:

```
===== EcoLang Sustainability Report =====
program       : bfs(60)
result        : 160
instructions  : 123866
est. time     : 306488 cycles
energy        : ... units / ... J
carbon        : ... g CO2
goal          : carbon (alpha = 0.00)
passes        : CF CP SR DCE CSE LICM
```

Built as a Compiler Design course project at VIT Vellore.

---

## Quick start

Needs Linux (or WSL on Windows) with `flex`, `bison`, `gcc` and `python3`.

```bash
sudo apt install flex bison gcc python3 python3-matplotlib
./build.sh
./ecoc fib.eco fib 30                    # run, no optimisation
./ecoc fib.eco fib 30 --goal=carbon      # optimise for low energy, then run
```

---

## The language

```
// Fibonacci in EcoLang
fn fib(n: int) -> int {
  let a: int = 0;
  let b: int = 1;
  let i: int = 0;
  while (i < n) {
    let t: int = a + b;
    a = b;
    b = t;
    i = i + 1;
  }
  return a;
}
```

- 64-bit integers, `let`, `while`, `if / else`, functions and recursion
- arrays: `let a: int[n];`, `a[i] = x;`, `fn f(a: int[], n: int)`
- operators: `+ - * / %`, comparisons, negative numbers

---

## How the compiler works

```
source.eco
   │  lexer.l        (flex)   split the code into tokens
   │  parser.y       (bison)  check the grammar, build three-address code
   ▼
three-address code   t1 = a + b ...                          (tac.c)
   │  optimize.c     optimisation passes, decided by the goal
   ▼
optimised code
   ├─ interp.c       run it, count every instruction, report energy and carbon
   └─ cgen.c         or write it out as a real C program (--emit-c=out.c)
```

### Optimisation passes

| Pass | What it does | Switch off with |
|---|---|---|
| Constant folding (CF) | `4 * 8` → `32` at compile time | `--no-cf` |
| Propagation (CP) | reuses known values and copies | `--no-cp` |
| Strength reduction (SR) | `x * 8` → `x << 3`, `x / 11` → multiply + shifts | `--no-sr` |
| Dead code elimination (DCE) | removes unused values and unreachable code | `--no-dce` |
| Common subexpression elimination (CSE) | does not recompute or re-read the same value | `--no-cse` |
| Loop-invariant code motion (LICM) | moves unchanging work out of loops | `--no-licm` |

### How the goal decides

Some rewrites make code faster *or* greener, but not both. Replacing one division with a multiply plus shifts is faster on some CPUs but runs more instructions. For each such choice the compiler compares the new code with the old:

```
cost = alpha × (new time / old time) + (1 − alpha) × (new energy / old energy)
```

The change is kept only if `cost < 1`. `alpha` is 1 for speed, 0.5 for balanced and 0 for carbon (or any value with `--alpha=0.3`).

### Energy model

Instruction costs live in plain-text profile files, so they can be changed without touching the code:

| Profile | Energy per instruction | Time per instruction |
|---|---|---|
| `energy_profile.txt` | original course-review table (not from a paper, kept for comparison) | same table |
| `profile_datapath.txt` | operation only: ADD 0.1 pJ, MUL 3.1 pJ, 8 KB cache read 10 pJ (Horowitz, ISSCC 2014) | Intel Skylake latencies (uops.info) |
| `profile_cpu.txt` | the same + ~70 pJ overhead per instruction (Horowitz, ISSCC 2014) | Intel Skylake latencies (uops.info) |
| `profile_calibrated.txt` | as `profile_cpu.txt` | **measured on our laptop** by `calibrate.py` |
| `profile_calibrated_datapath.txt` | as `profile_datapath.txt` | **measured on our laptop** by `calibrate.py` |

Division energy is not in Horowitz's table and is an assumption, marked in the files.

Carbon (g CO₂) = energy (J) / 3,600,000 × 710, using India's grid emission factor (CEA, FY 2024-25).

---

## Experiments

```bash
python3 validate_timing.py     # compile every build to C and time it on this machine
python3 calibrate.py           # measure time per instruction with micro.eco -> profile_calibrated*.txt
python3 validate_timing.py     # again: also tests builds made with the calibrated model
python3 run_experiments.py     # 9 benchmarks x 4 builds x 5 profiles -> results/
```

Benchmarks: Fibonacci (loop and recursive), bubble sort, merge sort, quick sort, matrix multiplication, BFS, DFS and Dijkstra. Every build of every benchmark gives the same answer, and the answers were checked against separate Python implementations.

All hardware results below are from an **Intel Core Ultra 7 155U** laptop (Ubuntu on WSL, gcc -O0).

### 1. Energy saved by optimisation (cpu-calibrated profile)

Estimated energy saved compared with no optimisation:

| Benchmark | speed | balanced | carbon |
|---|---|---|---|
| Fibonacci (loop) | 22.0% | 22.0% | 22.0% |
| Fibonacci (recursive) | 0% | 0% | 0% |
| Bubble sort | 34.3% | 34.9% | 34.9% |
| Merge sort | 5.0% | 9.3% | 9.3% |
| Quick sort | 8.4% | 15.3% | 15.3% |
| Matrix multiply | 21.6% | 21.6% | 21.6% |
| BFS | **−1.0%** | 13.8% | 13.8% |
| DFS | **−1.2%** | 13.7% | 13.7% |
| Dijkstra | 3.5% | 14.9% | 14.9% |

The optimised programs also ran **1.07–2× faster** when compiled to C and timed on the laptop.

Optimising only for speed can *increase* energy use (BFS, DFS): the speed goal replaces divisions with longer multiply-and-shift sequences, which are faster but run more instructions.

### 2. Calibrating the time model

`calibrate.py` times 11 microbenchmarks (`micro.eco`) that all have the same shape and differ only in the operation, then fits the time per instruction:

| | ADD | MUL | DIV | MEMORY | BRANCH |
|---|---|---|---|---|---|
| measured on Core Ultra 7 155U (× ADD) | 1 | 3.6 | 13.3 | 2.9 | 0.5 |
| old model (Intel Skylake, × ADD) | 1 | 3 | 40 | 5 | 1 |

Tested on the 9 benchmarks, which were **not** used for calibration:

- the model ranks builds well (Spearman 0.78 for predicted vs measured speed-up)
- it under-predicts absolute time by about 24%: real programs have stalls (for example unpredictable branches in sorting) that the microbenchmarks do not

### 3. Speed goal vs carbon goal on real hardware

With the calibrated model, the two goals still produce different code on 6 of 9 benchmarks:

| Benchmark | carbon saves (energy, model) | carbon is slower by (time, model) | carbon is slower by (time, **measured**) |
|---|---|---|---|
| BFS | 14.7% | 6.2% | −2.6% |
| DFS | 14.6% | 6.1% | −2.2% |
| Dijkstra | 11.8% | 5.1% | 0.1% |
| Quick sort | 7.5% | 2.9% | 10.4% |
| Merge sort | 4.6% | 3.2% | −1.7% |
| Bubble sort | 1.0% | 0.4% | 10.7% |

On the graph benchmarks the carbon build saves about 12–15% estimated energy at no measurable time cost. On quick sort and bubble sort it is about 10% slower.

The **uncalibrated** model predicted the carbon builds would be 49–63% slower; the real difference was −3% to +13%. A wrong time model makes the compiler choose badly, so the cost model must be calibrated to the target CPU.

---

## Limitations

- Energy is **estimated** from published per-operation costs, not measured with a power meter (RAPL is not available on WSL).
- Carbon is energy × a fixed grid factor. It does not change with time or place.
- The time model ignores pipeline stalls and branch prediction, so it predicts ~24% too little time and cannot reliably predict differences smaller than ~5%.
- The memory model does not simulate caches.
- The C back end is compiled with gcc -O0 so that gcc does not add its own optimisations; real compilers would keep values in registers.
- Each goal decision is made for one instruction at a time, so it can miss effects of later passes.
- EcoLang is a small teaching language, so results may not carry over directly to C or Python programs.

## Files

| File | Purpose |
|---|---|
| `lexer.l`, `parser.y` | front end (flex + bison) |
| `tac.c`, `tac.h` | three-address code |
| `optimize.c`, `optimize.h` | optimisation passes and goal decisions |
| `interp.c`, `interp.h` | interpreter and sustainability report |
| `cgen.c`, `cgen.h` | C back end |
| `*.eco` | benchmarks and test programs |
| `micro.eco` | microbenchmarks for calibration |
| `profile_*.txt`, `energy_profile.txt` | energy and time cost tables |
| `run_experiments.py`, `validate_timing.py`, `calibrate.py` | experiment scripts |
| `results/` | tables, CSV files and graphs |

## Authors

Preetisha Jain and Bhumi Tulsyan. Guide: Prof. Krishnaraj N. Vellore Institute of Technology.

## References

- M. Horowitz, "Computing's energy problem (and what we can do about it)," ISSCC 2014.
- T. Granlund and P. Montgomery, "Division by invariant integers using multiplication," PLDI 1994.
- H. S. Warren, *Hacker's Delight*, 2nd ed., Addison-Wesley, 2012.
- uops.info, instruction latency measurements.
- Central Electricity Authority (India), CO₂ Baseline Database for the Indian Power Sector.
