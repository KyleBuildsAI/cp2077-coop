"""Run Python with a time limit, for tests/run_all.ps1.

Usage: python run_with_timeout.py SECONDS (SCRIPT | -c CODE | -m MODULE) [ARGS...]

The child shares this process's stdout and stderr. When it runs longer than
SECONDS, the whole process tree is killed (taskkill /T also ends helpers such as
the relay that test_relay.py starts, which would otherwise keep its port and the
output pipe), a "FAIL  timed out" line is printed and the exit code is 124.
Otherwise the exit code is the child's.
"""
import subprocess
import sys

TIMEOUT_EXIT_CODE = 124


def kill_tree(process):
    if sys.platform == "win32":
        killed = subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
        if killed.returncode != 0:
            print(f"note: taskkill failed ({killed.stderr.decode(errors='replace').strip()}), killing the child only",
                  flush=True)
            process.kill()
    else:
        process.kill()
    process.wait()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    limit = float(sys.argv[1])
    child = subprocess.Popen([sys.executable] + sys.argv[2:])
    try:
        return child.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        kill_tree(child)
        print(f"FAIL  timed out after {limit:g} s (process tree killed): {' '.join(sys.argv[2:4])}", flush=True)
        return TIMEOUT_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
