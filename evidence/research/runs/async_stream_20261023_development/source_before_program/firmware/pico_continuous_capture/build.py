#!/usr/bin/env python3
"""Build the local instrument separately; leave all upstream sources unchanged."""
import hashlib
import datetime as dt
import json
from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parent
SDK = BASE.parent / "pico_ssm_reproduction/pico-ssm/lib/pico-sdk"
SDK_COMMIT = "6a7db34ff63345a7badec79ebea3aaef1712f374"
CMAKE = "/opt/homebrew/bin/cmake"


def main():
    commit = subprocess.check_output(["git", "-C", str(SDK), "rev-parse", "HEAD"], text=True).strip()
    diff = subprocess.check_output(["git", "-C", str(SDK), "diff", "--no-ext-diff"], text=True)
    if commit != SDK_COMMIT or diff:
        raise RuntimeError("Pinned SDK identity/cleanliness changed")
    build = BASE / "build"
    logs = BASE / "build_logs" / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    logs.mkdir(parents=True, exist_ok=False)
    commands = [
        [CMAKE, "-S", str(BASE), "-B", str(build), "-DPICO_BOARD=pico_w",
         "-DPICO_GCC_TRIPLE=arm-zephyr-eabi",
         "-DPICO_TOOLCHAIN_PATH=/opt/nordic/ncs/toolchains/0c0f19d91c/opt/zephyr-sdk/arm-zephyr-eabi",
         "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"],
        [CMAKE, "-S", str(SDK / "tools/pioasm"), "-B", str(build / "pioasm"),
         "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"],
        [CMAKE, "--build", str(build), "-j", "4"],
    ]
    for index, argv in enumerate(commands):
        with (logs / f"{index}.log").open("w") as output:
            subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT, check=True)
    files = [BASE / name for name in ["main.c", "finite.pio", "CMakeLists.txt", "build.py"]]
    manifest = {
        "sdk_commit": commit, "sdk_tracked_diff": diff, "board": "pico_w",
        "nominal_sample_hz": 1000000, "estimated_sample_hz": None,
        "sys_clock_configured_hz": 125000000, "pio_divider": 125.0,
        "instructions_per_sample": 1, "samples_per_block": 65536,
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "images": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in [build / "pico_continuous_capture.uf2", build / "pico_continuous_capture.elf"]},
        "commands": commands,
    }
    (BASE / "build_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": "BUILT", "images": manifest["images"]}, indent=2))


if __name__ == "__main__":
    main()
