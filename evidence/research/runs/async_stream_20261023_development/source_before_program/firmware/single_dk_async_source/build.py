#!/usr/bin/env python3
"""Offline build only. This file has no device, serial, erase or flash command."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parent
SDK = Path("/opt/nordic/ncs/v3.3.1")
TOOLCHAIN = Path("/opt/nordic/ncs/toolchains/0c0f19d91c")
BOARD = "nrf54lm20dk/nrf54lm20b/cpuapp"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    env = dict(os.environ)
    for item in json.loads((TOOLCHAIN / "environment.json").read_text())["env_vars"]:
        key = item["key"]
        value = (os.pathsep.join(str(TOOLCHAIN / p) for p in item["values"])
                 if item["type"] == "relative_paths" else item["value"])
        if item.get("existing_value_treatment", "overwrite") == "prepend_to" and env.get(key):
            value += os.pathsep + env[key]
        env[key] = value
    env["ZEPHYR_BASE"] = str(SDK / "zephyr")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    evidence = BASE / "build_logs" / stamp
    evidence.mkdir(parents=True)
    argv = [str(TOOLCHAIN / "bin/python3"), "-m", "west", "build", "--sysbuild", "-b", BOARD,
            "-p", "auto", "-d", str(BASE / "build"), str(BASE)]
    result = {"scope": "offline_compile_only_not_flashed_or_pin_verified", "board": BOARD,
              "sdk": str(SDK), "toolchain": str(TOOLCHAIN), "argv": argv,
              "source_sha256": {str(p.relative_to(BASE)): sha(p) for p in
                                  [BASE / "src/main.c", BASE / "CMakeLists.txt", BASE / "prj.conf", BASE / "build.py"]}}
    with (evidence / "build.log").open("w") as log:
        proc = subprocess.run(argv, cwd=SDK, env=env, stdout=log, stderr=subprocess.STDOUT)
    result["exit_code"] = proc.returncode
    result["status"] = "COMPILED" if proc.returncode == 0 else "BUILD_FAILED"
    result["artifacts"] = {str(p.relative_to(BASE)): sha(p)
                           for p in (BASE / "build").rglob("*") if p.is_file()
                           and p.name in ("zephyr.hex", "zephyr.elf", "zephyr.dts", ".config")}
    (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (BASE / "build_manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "evidence": str(evidence),
                      "artifacts": result["artifacts"]}, indent=2))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
