"""Run log for generateGraphs.py: graphs/graph_run.log (or --logFile <path>).

Each run is appended as one block: a header (time, host, command, code version, options, inputs),
everything the run prints (it still goes to the screen too), and a summary (duration, status, the graph
files made, notes such as "No wind at ...", Python warnings, and the error if the run failed).
Standard library only.

    with RunLog():
        main()
"""
import os
import socket
import subprocess
import sys
import time
import traceback
import warnings
from datetime import datetime, timezone

DEFAULT_LOG = os.path.join("graphs", "graph_run.log")
GRAPH_DIRECTORY = "graphs"
RULE = "=" * 80
THIN = "-" * 80
# lines of the run's own output that are worth repeating in the summary
NOTE_MARKERS = ("No wind at ", "No STOFS for this period", "needs a wind run", "turning on --obsExists",
                "NEST sensors left out", "Graph banner from")
# command-line options whose value is an input file
INPUT_OPTIONS = ("--stations", "--wind", "--water", "--waveswh", "--rain", "--mesh", "--velocity", "--stofs")


class _Tee:
    """Writes to the original stream and to the log, and keeps the note lines."""

    def __init__(self, stream, log, notes):
        self.stream, self.log, self.notes = stream, log, notes
        self.pending = ""

    def write(self, text):
        self.stream.write(text)
        self.log.write(text)
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if any(marker in line for marker in NOTE_MARKERS) and line.strip() not in self.notes:
                self.notes.append(line.strip())
        return len(text)

    def flush(self):
        self.stream.flush()
        self.log.flush()

    def __getattr__(self, name):        # encoding, isatty, fileno, ... of the original stream
        return getattr(self.stream, name)


def _git(*arguments):
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.run(["git", "-C", here, *arguments], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _option_value(argv, name):
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return None


def _wrap(words, first, width=80, indent=9):
    """'Command  part part ...' wrapped to width with a hanging indent; a part is never split."""
    lines, line = [], first
    for word in words:
        if line != first and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = " " * indent + word
        else:
            line = line + word if line == first else line + " " + word
    lines.append(line)
    return "\n".join(lines)


def _commandParts(argv):
    """['python', 'generateGraphs.py', '--wind /path', '--obsExists true', ...]: each option with its value."""
    parts = ["python", argv[0]]
    k = 1
    while k < len(argv):
        if argv[k].startswith("--") and k + 1 < len(argv) and not argv[k + 1].startswith("--"):
            parts.append(argv[k] + " " + argv[k + 1])
            k += 2
        else:
            parts.append(argv[k])
            k += 1
    return parts


def _stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


class RunLog:
    def __init__(self, argv=None, path=None):
        self.argv = list(sys.argv if argv is None else argv)
        self.path = path or _option_value(self.argv, "--logFile") or DEFAULT_LOG
        self.notes, self.warnings = [], {}

    # ------------------------------------------------------------------ start
    def __enter__(self):
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.log = open(self.path, "a", encoding="utf-8", errors="replace")
        self.started = datetime.now(timezone.utc)
        self.clock = time.time()
        self._writeHeader()
        self.stdout, self.stderr = sys.stdout, sys.stderr
        sys.stdout = _Tee(self.stdout, self.log, self.notes)
        sys.stderr = _Tee(self.stderr, self.log, self.notes)
        self.showwarning = warnings.showwarning
        warnings.showwarning = self._recordWarning
        return self

    def _writeHeader(self):
        argv = self.argv
        options = [argv[k][2:] for k in range(len(argv) - 1)
                   if argv[k].startswith("--") and argv[k + 1].lower() == "true"]
        unit = _option_value(argv, "--unit") or _option_value(argv, "--units") or "metric"
        branch, commit = _git("rev-parse", "--abbrev-ref", "HEAD"), _git("rev-parse", "--short", "HEAD")
        dirty = _git("status", "--porcelain", "--untracked-files=no")
        code = f"{branch} @ {commit}   (uncommitted changes: {'yes' if dirty else 'no'})" if commit else "unknown"
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
        w = self.log.write
        w("\n" + RULE + "\n")
        w(f"RICHAMP graph run   started {_stamp(self.started)}\n")
        w(f"Host     {socket.gethostname()}   (user {user})\n")
        w(f"Folder   {os.getcwd()}\n")
        w(_wrap(_commandParts(argv), "Command  ") + "\n")
        w(f"Code     {code}\n")
        w(_wrap([o + "," for o in options[:-1]] + options[-1:] if options else ["none"], "Options  ")
          + f"     units: {unit}\n")
        inputs = [(name[2:], _option_value(argv, name)) for name in INPUT_OPTIONS if _option_value(argv, name)]
        for k, (name, value) in enumerate(inputs):
            w(("Inputs   " if k == 0 else " " * 9) + f"{name}: {value}\n")
        w(THIN + "\n")
        self.log.flush()

    def _recordWarning(self, message, category, filename, lineno, file=None, line=None):
        key = f"{category.__name__}: {message}  ({os.path.basename(filename)}:{lineno})"
        self.warnings[key] = self.warnings.get(key, 0) + 1
        self.showwarning(message, category, filename, lineno, file, line)

    # ------------------------------------------------------------------ finish
    def __exit__(self, kind, error, trace):
        failed = kind is not None and not (kind is SystemExit and error.code in (0, None))
        if failed:
            # the traceback still reaches the screen when Python re-raises; here it goes to the log only
            self.log.write("".join(traceback.format_exception(kind, error, trace)))
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout, sys.stderr = self.stdout, self.stderr
        warnings.showwarning = self.showwarning
        self._writeSummary(failed, error)
        self.log.close()
        return False                      # never swallow the error

    def _writeSummary(self, failed, error):
        seconds = int(round(time.time() - self.clock))
        duration = f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"
        status = f"FAILED ({type(error).__name__}: {error})" if failed else "OK"
        made = []
        if os.path.isdir(GRAPH_DIRECTORY):
            logName = os.path.basename(self.path)
            for name in sorted(os.listdir(GRAPH_DIRECTORY)):
                full = os.path.join(GRAPH_DIRECTORY, name)
                if name != logName and os.path.isfile(full) and os.path.getmtime(full) >= self.clock - 1:
                    made.append(name)
        w = self.log.write
        w("\n" + THIN + "\nSummary\n")
        w(f"  Finished      {_stamp(datetime.now(timezone.utc))}   ({duration})   status: {status}\n")
        w(f"  Graphs made   {len(made)} file{'s' if len(made) != 1 else ''} in {GRAPH_DIRECTORY}/\n")
        for name in made:
            w(" " * 16 + name + "\n")
        w("  Notes         " + ("none\n" if not self.notes else ""))
        for k, note in enumerate(self.notes):
            w(("" if k == 0 else " " * 16) + note + "\n")
        w("  Warnings      " + ("none\n" if not self.warnings else ""))
        for k, (warning, count) in enumerate(self.warnings.items()):
            w(("" if k == 0 else " " * 16) + warning + (f"   (x{count})" if count > 1 else "") + "\n")
        w(RULE + "\n")
