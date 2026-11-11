"""Cancellable, bounded subprocess execution for downloaders and ffmpeg."""
import asyncio
import os
import shutil
import signal
import subprocess
from pathlib import Path

OUTPUT_LIMIT = 8 * 1024 * 1024


async def run(args, timeout, *, budget_root=None, budget_bytes=None, reserve_bytes=0):
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=(os.name != "nt"),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    async def collect():
        chunks, size = [], 0
        while chunk := await process.stdout.read(65536):
            size += len(chunk)
            if size > OUTPUT_LIMIT:
                raise RuntimeError("Download process exceeded the diagnostic-output limit")
            chunks.append(chunk)
        await process.wait()
        return process.returncode or 0, b"".join(chunks).decode(errors="replace")

    async def monitor():
        while True:
            if budget_root is not None:
                total = 0
                for file in Path(budget_root).rglob("*"):
                    try:
                        if file.is_file():
                            total += file.stat().st_size
                    except FileNotFoundError:
                        pass
                if budget_bytes and total > budget_bytes:
                    raise RuntimeError("Download exceeded the total job-size limit")
                if shutil.disk_usage(budget_root).free < reserve_bytes:
                    raise RuntimeError("Disk reserve reached during download")
            await asyncio.sleep(1)

    reader = asyncio.create_task(collect())
    guard = asyncio.create_task(monitor())
    try:
        done, _ = await asyncio.wait({reader, guard}, timeout=timeout,
                                     return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise RuntimeError(f"Download process exceeded the {timeout}-second timeout")
        if guard in done:
            await guard
        return await reader
    finally:
        if process.returncode is None:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        for task in (reader, guard):
            task.cancel()
        await asyncio.gather(reader, guard, return_exceptions=True)
