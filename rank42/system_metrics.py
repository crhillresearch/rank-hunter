"""Lightweight host telemetry for the Rank Hunter UI.

The UI should treat this module as the collection boundary: it asks for a
small normalized snapshot and only renders the result.  No Streamlit imports
live here.  Linux proc/sysfs are preferred so the UI environment does not need
psutil; NVIDIA telemetry is optional and bounded by a short subprocess timeout.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


_PROC_STAT = Path('/proc/stat')
_PROC_MEMINFO = Path('/proc/meminfo')
_HWMON = Path('/sys/class/hwmon')
_THERMAL = Path('/sys/class/thermal')


def _read_cpu_times():
    try:
        line = _PROC_STAT.read_text(encoding='utf-8', errors='replace').splitlines()[0]
        fields = line.split()
        if not fields or fields[0] != 'cpu':
            return None
        values = [int(x) for x in fields[1:]]
        while len(values) < 8:
            values.append(0)
        total = sum(values)
        idle = values[3] + values[4]
        iowait = values[4]
        return total, idle, iowait
    except (OSError, ValueError, IndexError):
        return None


_CPU_PREV = _read_cpu_times()
_CPU_PREV_AT = time.monotonic()


def _cpu_percentages():
    global _CPU_PREV, _CPU_PREV_AT
    now = _read_cpu_times()
    now_at = time.monotonic()
    prev = _CPU_PREV
    _CPU_PREV, _CPU_PREV_AT = now, now_at
    if now is None or prev is None:
        return None, None
    total_delta = now[0] - prev[0]
    if total_delta <= 0:
        return None, None
    idle_delta = max(0, now[1] - prev[1])
    iowait_delta = max(0, now[2] - prev[2])
    busy = 100.0 * max(0, total_delta - idle_delta) / total_delta
    iowait = 100.0 * iowait_delta / total_delta
    return min(100.0, busy), min(100.0, iowait)


def _read_meminfo():
    try:
        values = {}
        for line in _PROC_MEMINFO.read_text(encoding='utf-8', errors='replace').splitlines():
            if ':' not in line:
                continue
            key, raw = line.split(':', 1)
            parts = raw.strip().split()
            if parts:
                values[key] = int(parts[0]) * 1024
        total = int(values.get('MemTotal') or 0)
        available = int(values.get('MemAvailable') or values.get('MemFree') or 0)
        if total <= 0:
            return None, None
        used_pct = 100.0 * max(0, total - available) / total
        return min(100.0, used_pct), available / (1024 ** 3)
    except (OSError, ValueError):
        return None, None


def _read_hwmon_temp():
    candidates = []
    if _HWMON.exists():
        for hw in _HWMON.glob('hwmon*'):
            try:
                device_name = (hw / 'name').read_text().strip().lower()
            except OSError:
                device_name = ''
            for temp_file in hw.glob('temp*_input'):
                stem = temp_file.stem[:-6] if temp_file.stem.endswith('_input') else temp_file.stem
                label_file = hw / f'{stem}_label'
                try:
                    label = label_file.read_text().strip().lower()
                except OSError:
                    label = ''
                descriptor = f'{device_name} {label}'
                priority = 0
                if any(token in descriptor for token in ('package', 'tctl', 'tdie', 'cpu', 'core')):
                    priority = 2
                elif any(token in descriptor for token in ('k10temp', 'coretemp', 'zenpower')):
                    priority = 1
                try:
                    raw = float(temp_file.read_text().strip())
                except (OSError, ValueError):
                    continue
                celsius = raw / 1000.0 if raw > 500 else raw
                if -20.0 <= celsius <= 130.0:
                    candidates.append((priority, celsius))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][1]

    if _THERMAL.exists():
        vals = []
        for zone in _THERMAL.glob('thermal_zone*'):
            try:
                raw = float((zone / 'temp').read_text().strip())
            except (OSError, ValueError):
                continue
            celsius = raw / 1000.0 if raw > 500 else raw
            if -20.0 <= celsius <= 130.0:
                vals.append(celsius)
        if vals:
            return max(vals)
    return None


def _nvidia_smi_path():
    found = shutil.which('nvidia-smi')
    if found:
        return found
    wsl = Path('/usr/lib/wsl/lib/nvidia-smi')
    return str(wsl) if wsl.exists() else None


def _gpu_snapshot():
    binary = _nvidia_smi_path()
    if not binary:
        return None
    cmd = [
        binary,
        '--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total',
        '--format=csv,noheader,nounits',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    line = next((x.strip() for x in result.stdout.splitlines() if x.strip()), '')
    if not line:
        return None
    try:
        util, temp, used, total = [float(x.strip()) for x in line.split(',')[:4]]
    except (ValueError, IndexError):
        return None
    return {
        'percent': max(0.0, min(100.0, util)),
        'temp_c': temp,
        'used_gib': used / 1024.0,
        'total_gib': total / 1024.0,
    }


def _level_for_usage(percent, *, warning=75.0, danger=90.0):
    if percent is None:
        return 'muted'
    if percent >= danger:
        return 'danger'
    if percent >= warning:
        return 'warning'
    return 'success'


def _level_for_temp(temp_c, *, warning=80.0, danger=90.0):
    if temp_c is None:
        return 'muted'
    if temp_c >= danger:
        return 'danger'
    if temp_c >= warning:
        return 'warning'
    return 'success'


def _worst_level(*levels):
    order = {'muted': 0, 'success': 1, 'warning': 2, 'danger': 3}
    known = [x for x in levels if x]
    return max(known, key=lambda x: order.get(x, 0)) if known else 'muted'


def _fmt_percent(value):
    return '—' if value is None else f'{value:.0f}%'


def _fmt_temp(value):
    return 'temp unavailable' if value is None else f'{value:.0f}°C'


def collect_system_metrics():
    """Return normalized telemetry rows for a dumb UI renderer.

    Percentages are snapshots, not scientific measurements.  On WSL and some
    virtualized hosts CPU temperature is not exposed by the kernel; that is
    represented explicitly rather than guessed.
    """
    cpu_pct, iowait_pct = _cpu_percentages()
    cpu_temp = _read_hwmon_temp()
    ram_pct, ram_free = _read_meminfo()
    gpu = _gpu_snapshot()

    cpu_level = _worst_level(_level_for_usage(cpu_pct), _level_for_temp(cpu_temp))
    ram_level = _level_for_usage(ram_pct, warning=78.0, danger=92.0)
    io_level = _level_for_usage(iowait_pct, warning=5.0, danger=15.0)

    rows = [
        {
            'key': 'cpu', 'label': 'CPU', 'percent': cpu_pct,
            'value': _fmt_percent(cpu_pct), 'detail': _fmt_temp(cpu_temp),
            'level': cpu_level, 'show_bar': True,
        },
    ]
    if gpu is not None:
        gpu_level = _worst_level(
            _level_for_usage(gpu['percent'], warning=80.0, danger=95.0),
            _level_for_temp(gpu['temp_c'], warning=80.0, danger=90.0),
        )
        rows.append({
            'key': 'gpu', 'label': 'GPU', 'percent': gpu['percent'],
            'value': _fmt_percent(gpu['percent']),
            'detail': f"{gpu['temp_c']:.0f}°C · {gpu['used_gib']:.1f}/{gpu['total_gib']:.0f} GB",
            'level': gpu_level, 'show_bar': True,
        })
    else:
        rows.append({
            'key': 'gpu', 'label': 'GPU', 'percent': None, 'value': '—',
            'detail': 'not detected', 'level': 'muted', 'show_bar': True,
        })

    rows.extend([
        {
            'key': 'ram', 'label': 'RAM', 'percent': ram_pct,
            'value': _fmt_percent(ram_pct),
            'detail': 'free unavailable' if ram_free is None else f'{ram_free:.1f} GB free',
            'level': ram_level, 'show_bar': True,
        },
        {
            'key': 'iowait', 'label': 'I/O Wait', 'percent': iowait_pct,
            'value': '—' if iowait_pct is None else f'{iowait_pct:.1f}%',
            'detail': '', 'level': io_level, 'show_bar': False,
        },
    ])
    return rows
