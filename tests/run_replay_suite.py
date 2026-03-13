import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "replay_manifest.json"


def build_command(case, python_executable, output_path):
    audio_path = (ROOT / case["input"]).resolve()
    command = [
        python_executable,
        str((ROOT / "replay_audio.py").resolve()),
        str(audio_path),
        "--quiet",
        "--summary-json",
        str(output_path),
    ]

    options = {
        "--model": case.get("model"),
        "--device": case.get("device"),
        "--source-language": case.get("source_language"),
        "--task": case.get("task"),
        "--vad-energy-threshold": case.get("vad_energy_threshold"),
        "--utterance-end-silence-seconds": case.get("utterance_end_silence_seconds"),
        "--min-utterance-seconds": case.get("min_utterance_seconds"),
        "--max-utterance-seconds": case.get("max_utterance_seconds"),
        "--block-seconds": case.get("block_seconds"),
    }
    for flag, value in options.items():
        if value is not None:
            command.extend([flag, str(value)])

    if case.get("debug"):
        command.append("--debug")
    if case.get("realtime"):
        command.append("--realtime")

    return command


def evaluate_case(case, summary):
    errors = []
    expected = case.get("expect", {})

    if "flush_completed" in expected and summary.get("flush_completed") != expected["flush_completed"]:
        errors.append(
            f"expected flush_completed={expected['flush_completed']}, got {summary.get('flush_completed')}"
        )

    if "emission_count" in expected and summary.get("emission_count") != expected["emission_count"]:
        errors.append(
            f"expected emission_count={expected['emission_count']}, got {summary.get('emission_count')}"
        )

    if "min_emission_count" in expected and summary.get("emission_count", 0) < expected["min_emission_count"]:
        errors.append(
            f"expected emission_count>={expected['min_emission_count']}, got {summary.get('emission_count')}"
        )

    if "max_emission_count" in expected and summary.get("emission_count", 0) > expected["max_emission_count"]:
        errors.append(
            f"expected emission_count<={expected['max_emission_count']}, got {summary.get('emission_count')}"
        )

    if "emitted_text_exact" in expected and summary.get("emitted_text") != expected["emitted_text_exact"]:
        errors.append(
            f"expected emitted_text={expected['emitted_text_exact']}, got {summary.get('emitted_text')}"
        )

    if "detected_languages_exact" in expected and summary.get("detected_languages") != expected["detected_languages_exact"]:
        errors.append(
            "expected detected_languages="
            f"{expected['detected_languages_exact']}, got {summary.get('detected_languages')}"
        )

    if "detected_language_all" in expected:
        detected_languages = summary.get("detected_languages", [])
        expected_language = expected["detected_language_all"]
        if any(language != expected_language for language in detected_languages):
            errors.append(
                f"expected all detected_languages to be {expected_language}, got {detected_languages}"
            )

    emitted_text = summary.get("emitted_text", [])
    joined_text = " ".join(emitted_text).lower()
    final_committed_text = summary.get("final_committed_text", "")
    final_text_lower = final_committed_text.lower()

    if expected.get("append_only_valid") is True and not summary.get("append_only_valid", False):
        errors.append("expected append_only_valid=True, got False")

    if expected.get("monotonic_revision_ids") is True:
        revision_ids = summary.get("revision_ids", [])
        if revision_ids != sorted(revision_ids):
            errors.append(f"expected monotonic revision_ids, got {revision_ids}")

    if expected.get("monotonic_commit_ids") is True:
        commit_ids = summary.get("commit_ids", [])
        if commit_ids != sorted(commit_ids):
            errors.append(f"expected monotonic commit_ids, got {commit_ids}")

    if expected.get("monotonic_clause_ids") is True:
        clause_ids = [clause_id for clause_id in summary.get("clause_ids", []) if clause_id is not None]
        if clause_ids != sorted(clause_ids):
            errors.append(f"expected monotonic clause_ids, got {clause_ids}")

    contains_all = expected.get("contains_all_text", [])
    for phrase in contains_all:
        if phrase.lower() not in joined_text:
            errors.append(f"expected emitted text to contain '{phrase}'")

    contains_any = expected.get("contains_any_text", [])
    if contains_any and not any(phrase.lower() in joined_text for phrase in contains_any):
        errors.append(f"expected emitted text to contain one of {contains_any}")

    final_contains_all = expected.get("final_contains_all_text", [])
    for phrase in final_contains_all:
        if phrase.lower() not in final_text_lower:
            errors.append(f"expected final committed text to contain '{phrase}'")

    final_contains_any = expected.get("final_contains_any_text", [])
    if final_contains_any and not any(phrase.lower() in final_text_lower for phrase in final_contains_any):
        errors.append(f"expected final committed text to contain one of {final_contains_any}")

    for phrase in expected.get("forbid_text", []):
        if phrase.lower() in joined_text or phrase.lower() in final_text_lower:
            errors.append(f"expected emitted text not to contain '{phrase}'")

    for state in expected.get("require_status", []):
        if state not in summary.get("status_sequence", []):
            errors.append(f"expected status_sequence to include '{state}'")

    runtime_config = summary.get("runtime_config", {})
    for key, value in expected.get("runtime_config_fields", {}).items():
        if runtime_config.get(key) != value:
            errors.append(f"expected runtime_config['{key}']={value}, got {runtime_config.get(key)}")

    final_status = summary.get("final_status", {})
    for key, value in expected.get("final_status_fields", {}).items():
        if final_status.get(key) != value:
            errors.append(f"expected final_status['{key}']={value}, got {final_status.get(key)}")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Run replay_audio.py against a manifest of audio fixtures.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--python", default=sys.executable, help="Python executable to use for replay runs.")
    parser.add_argument(
        "--keep-summaries",
        action="store_true",
        help="Keep per-case summary JSON files in tests/replay-output.",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])
    if not cases:
        raise SystemExit(f"No replay cases found in {manifest_path}")

    output_dir = manifest_path.parent / "replay-output"
    output_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    for case in cases:
        case_name = case["name"]
        summary_path = output_dir / f"{case_name}.summary.json"
        command = build_command(case, args.python, summary_path)
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

        if result.returncode != 0:
            failures.append(
                {
                    "name": case_name,
                    "errors": [f"replay process failed with exit code {result.returncode}"],
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
            continue

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        errors = evaluate_case(case, summary)
        if errors:
            failures.append(
                {
                    "name": case_name,
                    "errors": errors,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "summary": summary,
                }
            )

        if not args.keep_summaries and summary_path.exists():
            summary_path.unlink()

    if failures:
        print(json.dumps({"failures": failures}, indent=2))
        raise SystemExit(1)

    print(f"Replay suite passed: {len(cases)} case(s)")


if __name__ == "__main__":
    main()
