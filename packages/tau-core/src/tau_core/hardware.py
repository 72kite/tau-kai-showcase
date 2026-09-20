"""Adaptive model selection (Phase 6.A; multi-GPU aware since Phase 10.5).

The live bring-up exposed the problem: `OLLAMA_MODEL=qwen2.5:14b-instruct` (~9 GB) is bigger than
an RTX 3070's 8 GB VRAM, so Ollama spills layers to CPU/RAM and first-token is slow. The right
model depends on the box and shouldn't be a hand-edited `.env` value.

This module has two halves, deliberately separated so the *decision* is testable without the
*probe*:
- `probe_hardware()` - side-effecting hardware inspection (nvidia-smi, RAM, cores).
- `recommend_models()` - a pure function from a hardware snapshot to a model pick, driven by a
  data-driven tier map. Unit-testable with synthetic inputs, no hardware required.

Run as a CLI to see (and optionally write) the pick:  `python -m tau_core.hardware [--write]`.

The tier thresholds are starting points to **tune against real benchmarks** before being trusted;
they err toward a model that fits rather than one that's marginally too big, because a model that
spills to CPU is the exact failure this fixes.

**Multi-GPU (Phase 10.5).** This project's dev box has one small GPU, but a deployment target
isn't guaranteed to. `Hardware.gpu_vram_mb` is deliberately the *combined* VRAM across every
NVIDIA GPU nvidia-smi reports, not just the biggest card: Ollama (via llama.cpp's multi-GPU
backend) automatically splits a model's layers across every GPU it can see in one process, which
is exactly what `docker-compose.gpu.yml`'s `deploy.resources.reservations.devices: count: all`
already hands it - so combined capacity, not the single biggest card, is what actually bounds
what can be loaded. A box with two 12 GB cards can load a model a single-GPU probe would have
rejected. `Hardware.gpu_count`/`gpu_vram_mb_per_device` carry the per-card breakdown for
reporting (the CLI report and `utility-mcp-server.get_system_status` both show it), but
`recommend_models()` itself only ever looks at the combined total - one number, one tier lookup,
same code path whether the box has zero, one, or eight GPUs.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Hardware:
    gpu_vram_mb: int  # COMBINED VRAM across every detected NVIDIA GPU; 0 if none / nvidia-smi
    # absent. Not the biggest single card - see the module docstring on why the sum, not the
    # max, is the number that bounds what Ollama can actually load.
    system_ram_mb: int  # total system RAM; 0 if undeterminable
    cpu_cores: int  # logical cores
    gpu_visible_to_docker: bool | None  # None = not probed (the bring-up script probes this)
    gpu_count: int = 0  # number of NVIDIA GPUs nvidia-smi reported; 0 if none / absent
    gpu_vram_mb_per_device: tuple[int, ...] = ()  # each GPU's own VRAM, largest first - for
    # reporting/debugging only; recommend_models() never looks at this, only the combined total.


@dataclass(frozen=True)
class ModelRecommendation:
    main: str
    router: str  # same model as main, deliberately - see project-tau-plan.md §10 router notes
    reason: str


# VRAM tier map: (min_vram_mb, model, human reason). Checked high-to-low; the first tier the GPU
# meets wins. A card must clear the threshold with headroom for the KV cache + display, so e.g.
# the 7B tier sits at 7000, comfortably under an 8192 MB 3070 but above a 6 GB card.
# TUNE THESE AGAINST REAL BENCHMARKS before trusting the tiers.
MODEL_TIERS: tuple[tuple[int, str, str], ...] = (
    (24000, "qwen2.5:32b-instruct", "24 GB+ VRAM: a 32B model fits comfortably."),
    (12000, "qwen2.5:14b-instruct", "12-16 GB VRAM: a 14B model fits."),
    (7000, "qwen2.5:7b-instruct", "~8 GB VRAM: a 7B model stays fully resident on the GPU."),
    (5000, "qwen2.5:3b-instruct", "~6 GB VRAM: a 3B-class model fits."),
)

# No usable GPU (or under the smallest tier): a small model that a CPU-only box can still run,
# even if slowly. Never pick something that won't load - that's the whole point of 6.A.
CPU_FALLBACK_MODEL = "qwen2.5:3b-instruct"


def _describe_gpus(hw: Hardware) -> str:
    """'8192 MB GPU VRAM' for one card, '2 GPUs totalling 16384 MB VRAM (8192 + 8192 MB each)'
    for several - the plural case matters here, since 'detected 16384 MB VRAM' alone would read
    like a single big card to whoever is troubleshooting a pick."""
    if hw.gpu_count > 1 and hw.gpu_vram_mb_per_device:
        per_device = " + ".join(str(v) for v in hw.gpu_vram_mb_per_device)
        return f"{hw.gpu_count} GPUs totalling {hw.gpu_vram_mb} MB VRAM ({per_device} MB each)"
    return f"{hw.gpu_vram_mb} MB GPU VRAM"


def recommend_models(hw: Hardware) -> ModelRecommendation:
    """Pure: hardware snapshot -> model pick. No I/O, so it's the unit-testable core of 6.A.

    Tiers against `hw.gpu_vram_mb`, which is already the COMBINED total across every GPU the
    probe found (see the module docstring) - so this one lookup is correct whether the box has
    one GPU or several; there is no separate multi-GPU branch to keep in sync.
    """
    vram = hw.gpu_vram_mb
    if vram and vram > 0:
        gpus = _describe_gpus(hw)
        for min_vram, model, reason in MODEL_TIERS:
            if vram >= min_vram:
                return ModelRecommendation(
                    main=model,
                    router=model,
                    reason=f"Detected {gpus}. {reason}",
                )
        # Has a GPU but it's below the smallest tier - treat like CPU-only.
        return ModelRecommendation(
            main=CPU_FALLBACK_MODEL,
            router=CPU_FALLBACK_MODEL,
            reason=(
                f"Detected only {gpus} (below the smallest GPU tier); using a small "
                "model that fits."
            ),
        )
    return ModelRecommendation(
        main=CPU_FALLBACK_MODEL,
        router=CPU_FALLBACK_MODEL,
        reason="No NVIDIA GPU detected; using a small CPU-runnable model.",
    )


def _probe_nvidia_gpus_mb() -> tuple[int, ...]:
    """Every NVIDIA GPU's total VRAM in MB, via nvidia-smi (one line per GPU in its CSV output,
    so one process reports all of them - no per-device subprocess calls needed). Largest first.
    Empty tuple if nvidia-smi is absent, errors, or reports nothing - every failure means 'no
    usable GPU', never an exception."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ()
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if out.returncode != 0:
        return ()
    values = [int(n) for n in re.findall(r"\d+", out.stdout)]
    return tuple(sorted(values, reverse=True))


def _probe_ram_mb() -> int:
    """Total system RAM in MB, best-effort across platforms. psutil if present, else a
    platform-specific stdlib path, else 0 (undeterminable - never raises)."""
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:  # noqa: BLE001 - psutil absent or failed; fall through to stdlib paths
        pass

    if sys.platform.startswith("win"):
        try:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemStatus()
            stat.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys / (1024 * 1024))
        except Exception:  # noqa: BLE001
            return 0
        return 0

    # Linux / other POSIX with /proc/meminfo.
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(re.findall(r"\d+", line)[0])
                    return int(kb / 1024)
    except (OSError, IndexError, ValueError):
        return 0
    return 0


def probe_hardware() -> Hardware:
    """Inspect the box. gpu_visible_to_docker is left None here - probing it means running a
    container, which is slow and belongs in the bring-up script, not a cheap status call."""
    per_device = _probe_nvidia_gpus_mb()
    return Hardware(
        gpu_vram_mb=sum(per_device),
        system_ram_mb=_probe_ram_mb(),
        cpu_cores=os.cpu_count() or 0,
        gpu_visible_to_docker=None,
        gpu_count=len(per_device),
        gpu_vram_mb_per_device=per_device,
    )


def _default_env_path() -> Path:
    """tau-core/.env - this file is tau-core/src/tau_core/hardware.py, so parents[2] is tau-core."""
    return Path(__file__).resolve().parents[2] / ".env"


def write_env_values(env_path: Path, updates: dict[str, str]) -> None:
    """Set the given key=value pairs in .env in place - a line edit that preserves comments and
    every other setting. Keys not already present are appended. Creates the file if it doesn't
    exist."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        for key, value in updates.items():
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                lines[i] = f"{key}={value}"
                seen.add(key)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_env_models(env_path: Path, rec: ModelRecommendation) -> None:
    """Set OLLAMA_MODEL and OLLAMA_ROUTER_MODEL from a hardware-driven recommendation."""
    write_env_values(env_path, {"OLLAMA_MODEL": rec.main, "OLLAMA_ROUTER_MODEL": rec.router})


def _format_report(hw: Hardware, rec: ModelRecommendation) -> str:
    if hw.gpu_count > 1:
        per_device = " + ".join(f"{v} MB" for v in hw.gpu_vram_mb_per_device)
        gpu = f"{hw.gpu_vram_mb} MB combined across {hw.gpu_count} GPUs ({per_device})"
    elif hw.gpu_vram_mb:
        gpu = f"{hw.gpu_vram_mb} MB"
    else:
        gpu = "none"
    ram = f"{hw.system_ram_mb} MB" if hw.system_ram_mb else "unknown"
    return (
        "Tau hardware probe\n"
        f"  GPU VRAM : {gpu}\n"
        f"  System RAM: {ram}\n"
        f"  CPU cores : {hw.cpu_cores}\n"
        "\nRecommended model:\n"
        f"  OLLAMA_MODEL        = {rec.main}\n"
        f"  OLLAMA_ROUTER_MODEL = {rec.router}\n"
        f"  why: {rec.reason}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tau_core.hardware",
        description="Probe hardware and recommend an Ollama model that fits (Phase 6.A).",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write OLLAMA_MODEL/OLLAMA_ROUTER_MODEL into the .env file (in place).",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=_default_env_path(),
        help="Path to the .env file to update (default: tau-core/.env).",
    )
    parser.add_argument(
        "--cpu-only",
        action="store_true",
        help=(
            "Recommend as if there were no GPU, even if one is present. Use when the model will "
            "run CPU-only (e.g. the GPU isn't passed through to Docker) so the pick matches where "
            "inference actually runs, not the host's idle VRAM."
        ),
    )
    parser.add_argument(
        "--set-model",
        metavar="NAME",
        help=(
            "Skip the hardware-driven recommendation and set OLLAMA_MODEL to this exact name "
            "instead (e.g. from `tau model set`). OLLAMA_ROUTER_MODEL is left untouched. Implies "
            "--write."
        ),
    )
    args = parser.parse_args(argv)

    if args.set_model:
        write_env_values(args.env, {"OLLAMA_MODEL": args.set_model})
        print(f"Set OLLAMA_MODEL={args.set_model} in {args.env}")
        return 0

    hw = probe_hardware()
    if args.cpu_only:
        from dataclasses import replace

        hw = replace(hw, gpu_vram_mb=0)
    rec = recommend_models(hw)
    print(_format_report(hw, rec))
    if args.write:
        write_env_models(args.env, rec)
        print(f"\nWrote model choice to {args.env}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
