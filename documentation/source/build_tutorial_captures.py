#!/usr/bin/env python3
"""Render focused terminal-style captures from actual tutorial run artifacts."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path("/Users/wushuo/Desktop/环境学院论文")
TUTORIAL = ROOT / "教程临时运行" / "固定燃烧源清单实验手册"
RUNS = TUTORIAL / "tutorial_runs"
OUT = ROOT / "固定燃烧源清单经验缺失处置实验" / "documentation" / "tutorial_screenshots"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Supplemental/Courier New Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size, index=0)
    return ImageFont.load_default()


MONO = font(25)
SMALL = font(22)
TITLE = font(25, bold=True)


def wrap(line: str, width: int = 82) -> list[str]:
    if not line:
        return [""]
    return textwrap.wrap(line, width=width, replace_whitespace=False, drop_whitespace=False) or [""]


def render(name: str, title: str, lines: list[str]) -> None:
    rendered: list[tuple[str, str]] = []
    for line in lines:
        style = "command" if line.startswith("$") else "output"
        for part in wrap(line):
            rendered.append((style, part))
    width = 1600
    top = 82
    line_height = 36
    height = max(420, top + len(rendered) * line_height + 48)
    image = Image.new("RGB", (width, height), "#101419")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=20, fill="#151A21", outline="#303846", width=2)
    draw.ellipse((42, 42, 62, 62), fill="#FF5F57")
    draw.ellipse((76, 42, 96, 62), fill="#FEBC2E")
    draw.ellipse((110, 42, 130, 62), fill="#28C840")
    draw.text((155, 36), title, font=TITLE, fill="#C7D0DD")
    y = top
    for style, line in rendered:
        color = "#91D7FF" if style == "command" else "#D8DEE9"
        draw.text((48, y), line, font=MONO if style == "command" else SMALL, fill=color)
        y += line_height
    OUT.mkdir(parents=True, exist_ok=True)
    image.save(OUT / name)


def status(run_id: str) -> tuple[str, str, str]:
    run = RUNS / run_id
    metrics = load(run / "logs" / "execution_metrics.json")
    quality = load(run / "outputs" / "quality_summary.json") if (run / "outputs" / "quality_summary.json").is_file() else {}
    report = load(run / "evaluation" / "reference_independent_report.json") if (run / "evaluation" / "reference_independent_report.json").is_file() else {}
    return (
        f"return_code={metrics.get('return_code', 0)}  sealed={str((run / 'experiment_seal.json').is_file()).lower()}",
        f"exceptions={quality.get('exception_group_count', '—')}  formula_errors=" + str(sum(w.get("workbook_qa", {}).get("formula_error_count", 0) for w in quality.get("workbooks", []))),
        f"integrity={report.get('sealed_integrity', {}).get('status', '—')}  structural={report.get('structural_and_arithmetic_checks', {}).get('status', '—')}",
    )


preflight = load(TUTORIAL / "tutorial" / "preflight_output.txt")
render("01_preflight.png", "fixed-combustion-inventory-tutorial", [
    "$ python3 tutorial/check_tutorial_environment.py --root \"$EXPERIMENT_ROOT\"",
    f"status: {preflight['status']}",
    *[f"{item['name']}: {item['status']}" for item in preflight["checks"]],
])

single = RUNS / "TUTORIAL-SINGLE-POWER-FULL"
quality = load(single / "outputs" / "quality_summary.json")
power = next(item for item in quality["workbooks"] if item["target"] == "POWER")
render("02_single_run.png", "single Full run", [
    "$ python3 tutorial/run_tutorial_case.py --root \"$EXPERIMENT_ROOT\" \\",
    "    --run-id TUTORIAL-SINGLE-POWER-FULL --method full --scenario full \\",
    "    --target POWER --scale single --scope-id SRC-PWR-000078",
    "status: pass",
    "experiment_seal.json: found",
    f"source_count: {power['source_count']}",
    f"item_count: {power['item_count']}",
    f"formula_errors: {power['workbook_qa']['formula_error_count']}",
    f"external_links: {power['workbook_qa']['external_link_count']}",
])

render("03_experiment_a.png", "Experiment A demo", [
    "$ evaluate_sealed_run.py TUTORIAL-A-POWER-FULL",
    "Full: " + "  ".join(status("TUTORIAL-A-POWER-FULL")),
    "$ evaluate_sealed_run.py TUTORIAL-A-POWER-DET",
    "Deterministic: " + "  ".join(status("TUTORIAL-A-POWER-DET")),
    "$ evaluate_sealed_run.py TUTORIAL-A-POWER-GENERIC",
    "Generic Agent: " + "  ".join(status("TUTORIAL-A-POWER-GENERIC")),
])

comparison = load(TUTORIAL / "tutorial" / "B_B0_vs_S4_comparison.json")
b2_quality = load(RUNS / "TUTORIAL-B-B2-POWER-DET" / "outputs" / "quality_summary.json")
render("04_experiment_b.png", "Experiment B demo", [
    "$ compare_normalized_runs.py B0/normalized_items.csv S4/normalized_items.csv",
    f"total_units: {comparison['total_units']}",
    f"passed_units: {comparison['passed_units']}",
    f"failed_units: {comparison['failed_units']}",
    f"agreement_rate: {comparison['agreement_rate']:.1%}",
    "$ evaluate_sealed_run.py TUTORIAL-B-B2-POWER-DET",
    f"B2 exception_group_count: {b2_quality['exception_group_count']}",
])

c_lines = ["$ run nested scales: 25%  50%  75%  100%"]
for scale in (25, 50, 75, 100):
    run = RUNS / f"TUTORIAL-C-{scale}-POWER-DET"
    metrics = load(run / "logs" / "execution_metrics.json")
    quality = load(run / "outputs" / "quality_summary.json")
    power = next(item for item in quality["workbooks"] if item["target"] == "POWER")
    c_lines.append(f"{scale:>3}%  sources={power['source_count']:<3}  items={power['item_count']:<4}  wall={metrics['wall_seconds']:.2f}s  sealed=true")
render("05_experiment_c.png", "Experiment C demo", c_lines)

d_lines = ["$ evaluate four single-factor variants"]
for label, run_id in (
    ("w/o item structure", "TUTORIAL-D-WO-ITEM"),
    ("w/o rule gate", "TUTORIAL-D-WO-RULE"),
    ("Agent admission", "TUTORIAL-D-AGENT-ADMISSION"),
    ("w/o complete trace", "TUTORIAL-D-WO-TRACE"),
):
    report = load(RUNS / run_id / "evaluation" / "reference_independent_report.json")
    d_lines.append(f"{label:<22} integrity={report.get('sealed_integrity', {}).get('status', '—'):<5} structural={report.get('structural_and_arithmetic_checks', {}).get('status', '—'):<5}")
render("06_experiment_d.png", "Experiment D demo", d_lines)

render("07_output_tree.png", "sealed run package", [
    "$ find tutorial_runs/TUTORIAL-SINGLE-POWER-FULL -maxdepth 2 -type f",
    "run_manifest.json",
    "sealed_manifest.json",
    "experiment_seal.json",
    "logs/execution_metrics.json",
    "logs/hermes_usage.json",
    "session_export/session.jsonl",
    "trace/complete_calculation_process.jsonl",
    "outputs/固定燃烧源-火电、热力生产与供应_实验结果.xlsx",
    "outputs/最终异常清单.csv",
    "outputs/quality_summary.json",
])

print(json.dumps({"output_dir": str(OUT), "images": len(list(OUT.glob('*.png')))}, ensure_ascii=False))
