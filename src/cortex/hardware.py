from __future__ import annotations

import os
from typing import Any

import psutil

from cortex.schemas import CpuSnapshot, GpuSnapshot, HardwareSnapshot


def _gpu_snapshots(nvml: Any | None = None) -> list[GpuSnapshot]:
    try:
        if nvml is None:
            import pynvml as nvml  # type: ignore[no-redef]
        nvml.nvmlInit()
        snapshots = []
        for index in range(nvml.nvmlDeviceGetCount()):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            memory = nvml.nvmlDeviceGetMemoryInfo(handle)
            utilization = nvml.nvmlDeviceGetUtilizationRates(handle)
            name = nvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            encoder = _optional_utilization(nvml, "nvmlDeviceGetEncoderUtilization", handle)
            decoder = _optional_utilization(nvml, "nvmlDeviceGetDecoderUtilization", handle)
            snapshots.append(GpuSnapshot(
                state="available",
                name=str(name),
                usage_percent=float(utilization.gpu),
                memory_used_mb=round(memory.used / 1024 / 1024),
                memory_total_mb=round(memory.total / 1024 / 1024),
                temperature_c=int(nvml.nvmlDeviceGetTemperature(handle, 0)),
                encoder_percent=encoder,
                decoder_percent=decoder,
            ))
        return snapshots or [GpuSnapshot(state="unavailable", error="Nenhuma GPU detectada")]
    except Exception as exc:
        return [GpuSnapshot(state="error", error=f"NVML indisponível: {exc}")]
    finally:
        try:
            if nvml is not None:
                nvml.nvmlShutdown()
        except Exception:
            pass


def _optional_utilization(nvml: Any, method: str, handle: Any) -> float | None:
    try:
        value = getattr(nvml, method)(handle)
        return float(value[0] if isinstance(value, tuple) else value)
    except Exception:
        return None


def collect_hardware_snapshot(nvml: Any | None = None) -> HardwareSnapshot:
    memory = psutil.virtual_memory()
    return HardwareSnapshot(
        cpu=CpuSnapshot(
            usage_percent=psutil.cpu_percent(interval=None),
            logical_cores=os.cpu_count() or 1,
            physical_cores=psutil.cpu_count(logical=False),
            memory_used_percent=memory.percent,
        ),
        gpus=_gpu_snapshots(nvml),
    )

