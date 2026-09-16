#!/usr/bin/env python3
"""Package current results and verify byte-preserving archives before cleanup."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "正式实验结果"
STAGE = ROOT / "tmp" / "release_upload"
REPO = "glittering-universe/fixed-combustion-inventory-experiment-v2"
TAG = "results-v2.2.0-human-reading-v2.1.0"
SUFFIX = "v2_2_human_v2_1"
RUN_ROOTS = {
    "A": "实验结果/02_实验A_端到端清单编制",
    "B": "实验结果/03_实验B_输入不变性与异常处置",
    "C": "实验结果/04_实验C_规模与资源表现",
    "D": "实验结果/05_实验D_方法组成贡献",
}
VIEW_ROOTS = {"A": "01_实验A", "B": "02_实验B", "C": "03_实验C", "D": "04_实验D"}
SUPPORT = (
    "human_baseline/original_packages", "human_baseline/normalized_v2_1",
    "human_baseline/reading_audit_v2_1", "inputs", "rules", "reference/frozen",
)
SKIP_PARTS = {".git", "__pycache__", "node_modules", ".venv"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def files(root):
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
                  and not (set(p.relative_to(root).parts) & SKIP_PARTS)
                  and p.name != ".DS_Store" and not p.name.endswith((".pyc", ".pyo")))


def record(path, base=ROOT):
    return {"path": str(path.relative_to(base)), "sha256": sha(path), "bytes": path.stat().st_size}


def copy_file(source, destination, ledger):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    info = record(source)
    if sha(destination) != info["sha256"]:
        raise RuntimeError(f"copy verification failed: {destination}")
    ledger.append({**info, "destination": str(destination.relative_to(DEST))})


def archive(paths, destination, base=ROOT):
    destination.parent.mkdir(parents=True, exist_ok=True)
    members = [record(p, base) for p in sorted(set(paths))]
    with destination.open("wb") as out:
        proc = subprocess.Popen(["zstd", "-q", "-T2", "-6", "-c"], stdin=subprocess.PIPE, stdout=out)
        assert proc.stdin
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for item in members:
                    tar.add(base / item["path"], arcname=item["path"], recursive=False)
        finally:
            proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"compression failed: {destination}")
    verify_archive(destination, members)
    print(json.dumps({"archive_verified": destination.name, "members": len(members), "bytes": destination.stat().st_size}), flush=True)
    return {**record(destination, destination.parent), "members": members}


def verify_archive(path, members):
    expected = {r["path"]: r for r in members}
    seen = set()
    proc = subprocess.Popen(["zstd", "-q", "-d", "-c", str(path)], stdout=subprocess.PIPE)
    assert proc.stdout
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if not member.isfile() or member.name not in expected or member.name in seen:
                    raise RuntimeError(f"unexpected archive member: {member.name}")
                body = tar.extractfile(member)
                assert body
                h = hashlib.sha256()
                for chunk in iter(lambda: body.read(1024 * 1024), b""):
                    h.update(chunk)
                item = expected[member.name]
                if h.hexdigest() != item["sha256"] or member.size != item["bytes"]:
                    raise RuntimeError(f"archive byte mismatch: {member.name}")
                seen.add(member.name)
    finally:
        proc.stdout.close()
    if proc.wait() != 0 or seen != set(expected):
        raise RuntimeError(f"archive incomplete: {path}")


def build():
    if DEST.exists():
        raise FileExistsError(f"destination already exists: {DEST}")
    audit = load(ROOT / "human_baseline/reading_audit_v2_1/evaluation_revision_checks.json")
    if audit["status"] != "pass" or audit["sealed_runs_reevaluated"] != 150:
        raise RuntimeError("current evaluation not verified")
    summary_dir = ROOT / "实验结果/00_总汇总与索引"
    summary = load(summary_dir / f"aggregate_summary_{SUFFIX}.json")
    if summary["pending_runs"] != 0:
        raise RuntimeError("pending formal runs")
    DEST.mkdir()
    STAGE.mkdir(parents=True, exist_ok=True)
    ledger = []
    archives = []
    cleanup_paths = []
    for name in (f"aggregate_summary_{SUFFIX}.json", f"run_scores_{SUFFIX}.csv",
                 *(f"experiment_{v}_{SUFFIX}.json" for v in ("B1", "B2", "C", "D")), "CURRENT_EVALUATION.md"):
        copy_file(summary_dir / name, DEST / "00_评价汇总" / name, ledger)
    copy_file(ROOT / "evaluation/metric_spec_v2_2.json", DEST / "00_评价汇总/metric_spec_v2_2.json", ledger)
    human_index = load(ROOT / "human_baseline/normalized_v2_1/normalization_index.json")
    human_packages = {p["output"]: p for p in human_index["packages"]}
    run_index = []
    for experiment, relative in RUN_ROOTS.items():
        source_root = ROOT / relative
        # Retain a complete, byte-identical run tree for offline restoration.
        packed = archive(files(source_root), DEST / "08_完整封存" / f"sealed-runs-{experiment}.tar.zst")
        archives.append({**packed, "destination": f"08_完整封存/{packed['path']}"})
        cleanup_paths.append(relative)
        for manifest_path in sorted(source_root.rglob("run_manifest.json")):
            run_dir = manifest_path.parent
            manifest = load(manifest_path)
            run_id = manifest["run_id"]
            display = DEST / VIEW_ROOTS[experiment] / run_dir.relative_to(source_root)
            if manifest.get("experiment_method", manifest.get("method")) == "expert_led":
                package = human_packages[str(run_dir.relative_to(ROOT))]
                observation = ROOT / "human_baseline/normalized_v2_1" / package["output"]
                selected = [observation / n for n in ("calculation_totals.csv", "source_decisions.csv", "exceptions.csv", "normalization_notes.json")]
                for p in selected:
                    copy_file(p, display / p.name, ledger)
                original = Path(human_index["preserved_original_root"]) / package["workbook"]
                copy_file(original, display / original.name, ledger)
            else:
                for p in files(run_dir / "outputs"):
                    copy_file(p, display / "outputs" / p.relative_to(run_dir / "outputs"), ledger)
            # These are run descriptors, not a new seal of the displayed subset.
            for p in (manifest_path, run_dir / "logs/execution_metrics.json"):
                copy_file(p, display / "运行信息" / p.name, ledger)
            run_index.append({"run_id": run_id, "experiment": experiment,
                              "method": manifest.get("experiment_method", manifest.get("method")),
                              "target": manifest["target"], "display": str(display.relative_to(DEST)),
                              "sealed_original": str(run_dir.relative_to(ROOT)),
                              "archive": f"08_完整封存/sealed-runs-{experiment}.tar.zst"})
    if len(run_index) != 150:
        raise RuntimeError(f"expected 150 run views: {len(run_index)}")
    support_files = [p for prefix in SUPPORT for p in files(ROOT / prefix)]
    packed = archive(support_files, DEST / "08_完整封存/support-current.tar.zst")
    archives.append({**packed, "destination": f"08_完整封存/{packed['path']}"})
    for p in files(ROOT / "human_baseline/reading_audit_v2_1"):
        copy_file(p, DEST / "05_人工读取复核" / p.name, ledger)
    for prefix in ("rules/frozen", "reference/frozen", "inputs"):
        for p in files(ROOT / prefix):
            copy_file(p, DEST / "06_规则参照与共享输入" / p.relative_to(ROOT), ledger)
    code_prefixes = ("baseline", "evaluation", "experiment_control", "human_baseline", "method_package", "plugin", "reproduction", "matrix", "rules/stage_r")
    for prefix in code_prefixes:
        for p in files(ROOT / prefix):
            if any(part in p.parts for part in ("normalized_v2", "normalized_v2_1", "original_packages", "reading_audit_v2_1", "run_console")):
                continue
            if p.suffix not in (".py", ".sh", ".md", ".json", ".yaml", ".yml", ".txt", ".csv", ".jsonl"):
                continue
            copy_file(p, DEST / "07_复现代码" / p.relative_to(ROOT), ledger)
    copy_file(ROOT / "requirements.lock.txt", DEST / "07_复现代码/requirements.lock.txt", ledger)

    historical_paths = files(ROOT / "expert_workflow") + files(ROOT / "human_baseline/normalized_v2")
    for p in files(ROOT / "documentation"):
        if any(part.startswith(("rendered", "font_probe", "qa_manual_system")) or part in ("screenshots", "tutorial_screenshots") for part in p.parts):
            historical_paths.append(p)
    historical_paths.extend(p for p in files(summary_dir) if SUFFIX not in p.name and p.name != "CURRENT_EVALUATION.md")
    historical_paths.extend(files(ROOT / "matrix/run_console"))
    packed = archive(historical_paths, STAGE / "historical-intermediates.tar.zst")
    archives.append({**packed, "historical_only": True})
    # The existing provenance note must travel with the nonformal historical data.
    note = ROOT.parent / "N评分修正与专家流程说明.txt"
    shutil.copy2(note, STAGE / note.name)
    cleanup_paths.extend(["expert_workflow", "human_baseline/normalized_v2", "human_baseline/normalized_v2_1", "matrix/run_console"])
    cleanup_paths.extend(str(p.relative_to(ROOT)) for p in (ROOT / "documentation").iterdir()
                         if p.is_dir() and (p.name.startswith(("rendered", "font_probe", "qa_manual_system")) or p.name in ("screenshots", "tutorial_screenshots")))
    # Retain original human workbooks and frozen shared inputs in the source repo;
    # they are research inputs, not disposable intermediates.
    cleanup_paths.extend(str(p.relative_to(ROOT)) for p in files(summary_dir) if SUFFIX not in p.name and p.name != "CURRENT_EVALUATION.md")
    manifest = {
        "release_tag": TAG, "repository": REPO, "visibility": "PRIVATE",
        "evaluation_version": "2.2.0", "human_reading_version": "2.1.0",
        "formal_run_count": 150, "machine_runs": 136, "human_packages": 14,
        "agent_session_exports": 94, "files": ledger, "archives": archives,
        "cleanup_paths": cleanup_paths,
        "preservation": "Original XLSX bytes, complete sealed runs and historical observations preserved. No calculations rerun.",
    }
    save(DEST / "文件索引.json", manifest)
    save(DEST / "运行索引.json", run_index)
    save(ROOT / "archives" / TAG / "delivery_manifest.json", manifest)
    readme = f"""# 固定燃烧源清单实验结果

当前评价：D/N/E v2.2.0；人工读取：v2.1.0；参照：v2.0.0；规则包：v1.0.1。

本文件夹保留150个封存运行的当前结果视图（136个机器运行、14个人工交付包）。原人工工作簿未经修改；人工N为63.23%，EICPI_core为37.78。实验A人工暂作为历史业务流程对照，新B1/B2人工表现尚缺对应当前输入的真实交付。既有11.52秒/条计时沿用原值，测量范围及外推限制见人工读取复核报告。D/N是与冻结参照的一致程度，本次整理未解决此前规则/参照的领域审计问题。

## 目录

- `00_评价汇总`：当前六份评分与实验诊断文件、指标定义。
- `01_实验A`至`04_实验D`：按源类、方法和重复编号保存最终输出及原始耗时信息。
- `05_人工读取复核`：逐单元核对、修正前后比较、任务条件核查。
- `06_规则参照与共享输入`：冻结规则、独立参照和共享输入。含敏感企业材料，只在现有私有仓库保存，未新增开放许可。
- `07_复现代码`：执行、读取、评价与打包代码；历史实验设置和现行评价版本分别保留。
- `08_完整封存`：完整A—D运行目录及支持数据的无损压缩包，包含94个Agent会话导出、计算轨迹、数据库、封存文件及重复输入。

`运行索引.json`提供150个运行的结果位置与原封存位置；`文件索引.json`列出来源、大小、SHA-256及全部压缩包成员。结果视图不是重新封存的运行目录；原封存以压缩包内文件为准。当前人工视图使用修正后的表头读取，压缩包同时保留原封存观察，不改写历史。

## 恢复与复核

源代码仓库： https://github.com/{REPO}

发布页： https://github.com/{REPO}/releases/tag/{TAG}

在源代码仓库内运行：

```bash
python3 reproduction/curate_results.py restore
```

该命令从本文件夹`08_完整封存`还原被压缩保存的运行和支持材料；同名文件内容不同则停止，不覆盖新工作。恢复后按原评价命令指定`--human-normalization-root human_baseline/normalized_v2_1`重新评价，无需重跑Hermes。旧评分和非正式产物仅见发布页的`historical-intermediates.tar.zst`，不计入当前结果。历史专家流程的来源说明TXT随历史资产保存。

若从GitHub下载，本地结果视图位于`current-results-view.tar.zst`；解压到实验仓库，将`sealed-runs-A/B/C/D.tar.zst`和`support-current.tar.zst`置于`正式实验结果/08_完整封存/`即可得到相同目录。凭发布页的SHA256SUMS核对下载文件。
"""
    (DEST / "README.md").write_text(readme, encoding="utf-8")
    shutil.copy2(DEST / "README.md", STAGE / "README.md")
    save(STAGE / "delivery_manifest.json", manifest)
    print(json.dumps({"built": str(DEST), "run_views": len(run_index), "visible_files": len(ledger)}, ensure_ascii=False), flush=True)


def assets():
    return sorted([*(DEST / "08_完整封存").glob("*.tar.zst"), *STAGE.glob("*")])


def pack_view():
    if (STAGE / "current-results-view.tar.zst").exists():
        raise FileExistsError("current-results-view already exists")
    selected = [p for p in files(DEST) if "08_完整封存" not in p.parts]
    packed = archive(selected, STAGE / "current-results-view.tar.zst", base=ROOT)
    save(STAGE / "current-results-view-manifest.json", packed)
    entries = [record(p, p.parent) for p in assets() if p.is_file() and p.name != "SHA256SUMS"]
    (STAGE / "SHA256SUMS").write_text("".join(f"{p['sha256']}  {p['path']}\n" for p in entries), encoding="utf-8")
    save(ROOT / "archives" / TAG / "upload_assets.json", entries)


def verify_remote():
    release = json.loads(subprocess.check_output(["gh", "release", "view", TAG, "--repo", REPO, "--json", "assets,url,targetCommitish"]))
    remote = {a["name"]: a for a in release["assets"]}
    verified = []
    for path in assets():
        if not path.is_file():
            continue
        r = remote[path.name]
        expected = sha(path)
        if r["state"] != "uploaded" or r["size"] != path.stat().st_size or r.get("digest") != "sha256:" + expected:
            raise RuntimeError(f"remote verification failed: {path.name}")
        verified.append({"asset": path.name, "bytes": r["size"], "sha256": expected, "url": r["url"]})
    receipt = {"status": "pass", "release": release["url"], "assets": verified,
               "verification": "Local full archive member SHA-256 validation plus GitHub server SHA-256 and byte-size match."}
    save(DEST / "GitHub上传核验.json", receipt)
    save(ROOT / "archives" / TAG / "upload_verification.json", receipt)
    print(json.dumps({"remote_verified": len(verified), "release": release["url"]}), flush=True)


def cleanup():
    receipt = load(DEST / "GitHub上传核验.json")
    if receipt["status"] != "pass":
        raise RuntimeError("verified remote upload required")
    manifest = load(DEST / "文件索引.json")
    covered = {i["path"]: i for a in manifest["archives"] for i in a["members"]}
    protected = {i["path"]: i for i in manifest["files"]}
    # Recheck every surviving view and local archive before deleting originals.
    for item in manifest["files"]:
        if sha(DEST / item["destination"]) != item["sha256"]:
            raise RuntimeError(f"view changed: {item['destination']}")
    for a in manifest["archives"]:
        path = STAGE / a["path"] if a.get("historical_only") else DEST / a["destination"]
        if sha(path) != a["sha256"]:
            raise RuntimeError(f"archive changed: {path}")
    delete_files = []
    for relative in manifest["cleanup_paths"]:
        path = ROOT / relative
        if not path.exists():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError(f"unsafe cleanup path: {path}")
        for source in files(path):
            r = str(source.relative_to(ROOT))
            expected = covered.get(r) or protected.get(r)
            if expected is None or sha(source) != expected["sha256"]:
                raise RuntimeError(f"unarchived or changed cleanup file: {r}")
            delete_files.append(source)
    released_bytes = sum(p.stat().st_size for p in delete_files)
    for relative in manifest["cleanup_paths"]:
        path = ROOT / relative
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()
    # Python caches and Finder metadata are disposable and excluded from archives.
    caches = [p for p in ROOT.rglob("__pycache__") if ".git" not in p.parts and ".venv" not in p.parts and DEST.name not in p.parts]
    for path in caches:
        shutil.rmtree(path)
    result = {"status": "complete", "cleaned_paths": manifest["cleanup_paths"],
              "verified_source_files_removed": len(delete_files), "bytes_removed": released_bytes,
              "preserved_original_human_workbooks": "human_baseline/original_packages",
              "preserved_current_results": str(DEST), "github_release": receipt["release"]}
    save(DEST / "清理记录.json", result)
    save(ROOT / "archives" / TAG / "cleanup_record.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def restore():
    manifest = load(DEST / "文件索引.json")
    restored = 0
    for a in manifest["archives"]:
        if a.get("historical_only"):
            continue
        path = DEST / a["destination"]
        if sha(path) != a["sha256"]:
            raise RuntimeError(f"archive hash mismatch: {path}")
        expected = {i["path"]: i for i in a["members"]}
        proc = subprocess.Popen(["zstd", "-q", "-d", "-c", str(path)], stdout=subprocess.PIPE)
        assert proc.stdout
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                for member in tar:
                    target = ROOT / member.name
                    if not member.isfile() or member.name not in expected or not target.resolve().is_relative_to(ROOT.resolve()):
                        raise RuntimeError(f"unsafe archive member: {member.name}")
                    if target.exists():
                        if sha(target) != expected[member.name]["sha256"]:
                            raise RuntimeError(f"existing file differs; not overwritten: {target}")
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    body = tar.extractfile(member)
                    assert body
                    with target.open("xb") as output:
                        shutil.copyfileobj(body, output)
                    if sha(target) != expected[member.name]["sha256"]:
                        raise RuntimeError(f"restored hash mismatch: {target}")
                    os.chmod(target, member.mode)
                    restored += 1
        except BaseException:
            proc.terminate()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
        if proc.wait() != 0:
            raise RuntimeError("decompression failure")
    print(json.dumps({"restored_files": restored}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("build", "pack-view", "verify-remote", "cleanup", "restore"))
    action = parser.parse_args().action
    {"build": build, "pack-view": pack_view, "verify-remote": verify_remote, "cleanup": cleanup, "restore": restore}[action]()
