#!/usr/bin/env python3
"""Low-overhead per-target CPU, RSS, block-I/O and state-disk sampling."""
import os
import statistics
import subprocess
import threading
import time

HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
INTERVAL_S = 1.0


def _proc_stat(pid):
    with open(f"/proc/{pid}/stat") as handle:
        return handle.read().rsplit(")", 1)[1].split()


def descendants(roots):
    """Root pids and every descendant visible in the host pid namespace."""
    wanted = {int(pid) for pid in roots if str(pid).isdigit()}
    changed = True
    while changed:
        changed = False
        for entry in os.listdir("/proc") if os.path.isdir("/proc") else []:
            if not entry.isdigit() or int(entry) in wanted:
                continue
            try:
                parent = int(_proc_stat(entry)[1])
            except Exception:
                continue
            if parent in wanted:
                wanted.add(int(entry))
                changed = True
    return sorted(wanted)


def target_pids(target, run_root=None):
    """Processes owned by one target, including a container's descendants."""
    run_root = os.path.expanduser(run_root or "~/stremio-addon-bench/run")
    pidfile = os.path.join(run_root, target, "target.pid")
    roots = []
    if os.path.exists(pidfile):
        try:
            roots.append(int(open(pidfile).read().strip()))
        except Exception:
            pass
    inspect = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Pid}}", f"sab-{target}"],
        capture_output=True, text=True)
    if inspect.returncode == 0 and inspect.stdout.strip().isdigit():
        pid = int(inspect.stdout.strip())
        if pid:
            roots.append(pid)
    return descendants(roots)


def cpu_by_pid(pids):
    out = {}
    for pid in pids:
        try:
            parts = _proc_stat(pid)
            out[int(pid)] = int(parts[11]) + int(parts[12])
        except Exception:
            pass
    return out


def rss_kb(pids):
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/status") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
                        break
        except Exception:
            pass
    return total


def io_by_pid(pids):
    out = {}
    for pid in pids:
        values = {"read_bytes": 0, "write_bytes": 0}
        try:
            with open(f"/proc/{pid}/io") as handle:
                for line in handle:
                    key, _, value = line.partition(":")
                    if key in values:
                        values[key] = int(value.strip())
            out[int(pid)] = (values["read_bytes"], values["write_bytes"])
        except Exception:
            pass
    return out


def disk_usage_bytes(root):
    """Allocated bytes below a state directory, without following symlinks."""
    if not root or not os.path.lexists(root):
        return 0
    total, seen = 0, set()
    paths = [root]
    if os.path.isdir(root) and not os.path.islink(root):
        for directory, names, files in os.walk(root, followlinks=False):
            paths.extend(os.path.join(directory, name) for name in names + files)
    for path in paths:
        try:
            stat = os.lstat(path)
        except OSError:
            continue
        identity = (stat.st_dev, stat.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        total += getattr(stat, "st_blocks", 0) * 512
    return total


def summarise(samples, cpu_s, read_bytes, write_bytes, disk_start, disk_end):
    out = {
        "samples": len(samples),
        "sample_interval_s": INTERVAL_S,
        "cpu_s": round(cpu_s, 2),
        "disk_read_MB": round(read_bytes / (1 << 20), 2),
        "disk_write_MB": round(write_bytes / (1 << 20), 2),
        "state_disk_start_MB": round(disk_start / (1 << 20), 2),
        "state_disk_end_MB": round(disk_end / (1 << 20), 2),
        "state_disk_delta_MB": round((disk_end - disk_start) / (1 << 20), 2),
    }
    rss = [sample[2] for sample in samples if sample[2]]
    if rss:
        out["rss_peak_MB"] = round(max(rss) / 1024, 2)
        out["rss_median_MB"] = round(statistics.median(rss) / 1024, 2)
        out["rss_final_MB"] = round(rss[-1] / 1024, 2)
    cores = [sample[1] for sample in samples]
    if cores:
        ordered = sorted(cores)
        index = max(0, int(round(0.95 * len(ordered))) - 1)
        out["cpu_cores_p95"] = round(ordered[index], 3)
        out["cpu_cores_peak"] = round(max(cores), 3)
        out["cpu_cores_median"] = round(statistics.median(cores), 3)
    return out


class Sampler:
    def __init__(self, pids_fn, interval=INTERVAL_S):
        self.pids_fn = pids_fn
        self.interval = interval
        self.samples = []
        self.cpu_s = 0.0
        self.read_bytes = 0
        self.write_bytes = 0
        self._stop = threading.Event()
        self._thread = None

    @staticmethod
    def _deltas(current, previous):
        first = second = 0
        for pid, values in current.items():
            before = previous.get(pid)
            if before is None:
                before = (0, 0) if isinstance(values, tuple) else 0
            if isinstance(values, tuple):
                first += max(0, values[0] - before[0])
                second += max(0, values[1] - before[1])
            else:
                first += max(0, values - before)
        return first, second

    def _run(self):
        started = last_at = time.perf_counter()
        pids = self.pids_fn()
        last_cpu, last_io = cpu_by_pid(pids), io_by_pid(pids)
        while not self._stop.wait(self.interval):
            now = time.perf_counter()
            pids = self.pids_fn()
            current_cpu, current_io = cpu_by_pid(pids), io_by_pid(pids)
            cpu_ticks, _ = self._deltas(current_cpu, last_cpu)
            read_bytes, write_bytes = self._deltas(current_io, last_io)
            span = now - last_at
            self.cpu_s += cpu_ticks / HZ
            self.read_bytes += read_bytes
            self.write_bytes += write_bytes
            cores = cpu_ticks / HZ / span if span else 0.0
            self.samples.append((round(now - started, 3), round(cores, 4), rss_kb(pids)))
            last_cpu, last_io, last_at = current_cpu, current_io, now

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self, disk_start=0, disk_end=0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval * 3)
        return summarise(self.samples, self.cpu_s, self.read_bytes,
                         self.write_bytes, disk_start, disk_end)
