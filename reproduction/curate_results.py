#!/usr/bin/env python3
"""Package the result folder and extract the original experiment materials."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "正式实验结果"
UPLOAD = ROOT / "tmp" / "release_upload"


def pack():
    UPLOAD.mkdir(parents=True, exist_ok=True)
    archive = UPLOAD / "current-results-view.tar.zst"
    subprocess.run([
        "tar", "--zstd", "-cf", str(archive),
        "--exclude=正式实验结果/08_完整封存",
        "-C", str(ROOT), "正式实验结果",
    ], check=True)
    shutil.copy2(RESULTS / "README.md", UPLOAD / "README.md")
    shutil.copy2(RESULTS / "运行索引.json", UPLOAD / "run-index.json")
    print(archive)


def restore(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for archive in sorted((RESULTS / "08_完整封存").glob("*.tar.zst")):
        subprocess.run([
            "tar", "--zstd", "-xf", str(archive), "-C", str(destination),
        ], check=True)
        print(archive.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("pack")
    restore_command = commands.add_parser("restore")
    restore_command.add_argument("destination", nargs="?", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.action == "pack":
        pack()
    else:
        restore(args.destination)
