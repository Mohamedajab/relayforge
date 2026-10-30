"""Run the real worker and example receiver in a disposable, in-process demo."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from relayforge.demo_scenario import recovery_steps


async def demonstrate(recording: Path) -> None:
    started = time.monotonic()
    frames: list[list[object]] = []

    def report(message: str) -> None:
        print(message, flush=True)
        frames.append([round(time.monotonic() - started, 3), "o", message + "\r\n"])

    async for step in recovery_steps():
        report(str(step["message"]))
    recording.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "version": 2,
        "width": 100,
        "height": 14,
        "title": "RelayForge local recovery demonstration",
    }
    recording.write_text("\n".join(json.dumps(row) for row in [header, *frames]) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", type=Path, default=Path("docs/evidence/recovery.cast"))
    asyncio.run(demonstrate(parser.parse_args().recording))
