"""Instantané des ressources visibles depuis le serveur de l’Observatoire."""
import ctypes
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)


def usage(total, available):
    available = max(0, min(available, total))
    return {"total": total, "available": available, "used": total - available}


def memory():
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
                (name, ctypes.c_uint64) for name in (
                    "total_phys", "available_phys", "total_page", "available_page",
                    "total_virtual", "available_virtual", "available_extended_virtual",
                )
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx a échoué")
        return usage(status.total_phys, status.available_phys), None

    if sys.platform == "linux":
        fields = {}
        with Path("/proc/meminfo").open(encoding="ascii") as stream:
            for line in stream:
                name, _, value = line.partition(":")
                if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                    fields[name] = int(value.split()[0]) * 1024
        return usage(fields["MemTotal"], fields["MemAvailable"]), usage(fields["SwapTotal"], fields["SwapFree"])

    return None, None


def snapshot(db_path):
    try:
        ram, swap = memory()
    except (OSError, ValueError, KeyError):
        LOG.warning("Mesure de la mémoire indisponible", exc_info=True)
        ram, swap = None, None

    try:
        disk = shutil.disk_usage(db_path.parent)
        volume = {"total": disk.total, "used": disk.used, "available": disk.free}
    except OSError:
        LOG.warning("Mesure du volume SQLite indisponible", exc_info=True)
        volume = None

    size = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            size += db_path.with_name(db_path.name + suffix).stat().st_size
        except FileNotFoundError:
            continue
        except OSError:
            LOG.warning("Taille des fichiers SQLite indisponible", exc_info=True)
            size = None
            break

    return {"ram": ram, "swap": swap, "disk": volume, "database_bytes": size,
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
