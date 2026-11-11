"""Read-only image inventory: Python 3.10+, Pillow. No visual scoring."""
import argparse
import hashlib
import json
from pathlib import Path


def audit(manifest):
    from PIL import Image
    doc = json.loads(manifest.read_text(encoding="utf-8-sig"))
    entries = doc["images"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("images must be nonempty")
    seen, records = set(), []
    for item in entries:
        key = item["id"]
        if not isinstance(key, str) or not key.strip():
            raise ValueError("invalid id")
        errors = ["duplicate_id"] if key in seen else []
        seen.add(key)
        row = {"id": key, "errors": errors, "source_hash_checked": False}
        try:
            path = manifest.parent / item["path"]
            data = path.read_bytes()
            row["sha256"] = hashlib.sha256(data).hexdigest()
            with Image.open(path) as im:
                im.load()
                row["dimensions"] = list(im.size)
            if "sha256" in item:
                row["source_hash_checked"] = True
                if row["sha256"] != item["sha256"]:
                    errors.append("hash_mismatch")
            if "expected_dimensions" in item and row["dimensions"] != item["expected_dimensions"]:
                errors.append("dimensions_mismatch")
        except (OSError, ValueError, KeyError, TypeError):
            errors.append("unreadable_image")
        records.append(row)
    expected = doc.get("expected_ids", list(seen))
    if not isinstance(expected, list) or not all(isinstance(x, str) for x in expected) or len(set(expected)) != len(expected):
        raise ValueError("invalid expected_ids")
    missing, extra = sorted(set(expected) - seen), sorted(seen - set(expected))
    return {"passed": not missing and not extra and all(not r["errors"] for r in records),
            "count": len(records), "missing_ids": missing, "extra_ids": extra,
            "images": records, "visual_quality_evaluated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit(args.manifest.resolve())
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        print("Invalid manifest, unreadable input, or missing Pillow; no audit completed.")
        return 2
    # Never overwrite inputs or prior evidence.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": report["passed"], "count": report["count"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
