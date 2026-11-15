"""Offline checks of an X2 dataset manifest ($0, no network). Exit 1 with reasons when the manifest may not be used.

usage: python -I validate_manifest.py <manifest.json> [--stage draft|eval --split REAL-DEV|REAL-HOLDOUT] [--verify-files DIR]
  (no --stage)  structure and leakage only
  --stage draft READY FOR PAID SCRIPT DRAFT: the inputs of the paid narration-draft step are real and cleared
                (rights, sha256, third-party processing, local-ASR cues). Narration and labels are NOT required.
                Checks only the named split: DEV draft readiness does not need HOLDOUT sources (only no overlap).
                A source with no speech passes when ASR completed (asr_status ok) with cue_count 0 and speech_ratio 0.
  --verify-files DIR  recompute the media sha256 (path) and cues_sha256, cue_count, cue bounds and speech_ratio
                from the cue file (cues_path), both relative to DIR, instead of trusting the manifest; speech_ratio
                is computed and not rounded, never typed (README §3.3).
                Passing it does not approve any spending: the Owner approves cost separately.
  --stage eval  READY FOR EVALUATION: everything of draft, plus narration/cues locked, human labels valid and
                locked, scoring rules (HOLDOUT: also parameters) locked to a commit.
Anything marked synthetic, and AI-judged labels, never count toward READY.
"""
import hashlib, json, os, sys

MIX = (("direct", "direct"), ("general", "general_commentary"), ("illustrative", "illustrative_broll"), ("none", "no_footage"))


def structure(m):
    out = []
    dev, hold = m["splits"]["REAL-DEV"], m["splits"]["REAL-HOLDOUT"]
    ids = lambda d: {s["source_id"] for s in d["sources"]}
    shas = lambda d: {s.get("sha256") for s in d["sources"] if s.get("sha256")}
    if ids(dev) & ids(hold) or shas(dev) & shas(hold):
        out.append("DEV and HOLDOUT share a source (split by video)")
    for split, data in (("REAL-DEV", dev), ("REAL-HOLDOUT", hold)):
        for s in data["sources"]:
            if s.get("rights_confirmed_by") and not s.get("license"):
                out.append(f"{split} {s['source_id']}: rights confirmed without a license")
        if data["narration"].get("writer_saw_footage"):
            out.append(f"{split}: the narration writer saw the footage (leakage)")
        labelers = data["labels"].get("labelers") or []
        if data["narration"].get("writer") and data["narration"]["writer"] in labelers:
            out.append(f"{split}: a labeler wrote the narration")
        if data["narration"].get("none_writer") and data["narration"]["none_writer"] == data["narration"].get("writer"):
            out.append(f"{split}: the no-footage sentences were written by the narration writer")
    if m.get("ai_cost_usd", 0) != 0 and m.get("status") == "PREPARATION_ONLY":
        out.append("AI cost recorded in a preparation-only manifest")
    return out


def draft(split, data):
    out = []
    want = (2, 2) if split == "REAL-DEV" else (4, 6)
    if not want[0] <= len(data["sources"]) <= want[1]:
        out.append(f"{split}: needs {want[0]}-{want[1]} sources, has {len(data['sources'])}")
    for s in data["sources"]:
        sid = s["source_id"]
        if s.get("synthetic"):
            out.append(f"{split} {sid}: synthetic source")
        if not (s.get("rights_confirmed_by") and s.get("rights_confirmed_at") and s.get("license") and s.get("license_evidence")):
            out.append(f"{split} {sid}: rights not confirmed by a named person with license evidence")
        if not s.get("sha256") or not s.get("path") or not s.get("duration_ms"):
            out.append(f"{split} {sid}: file not recorded (path, sha256, duration)")
        if s.get("third_party_processing_allowed") is not True:
            out.append(f"{split} {sid}: sending frames/transcript to an API not cleared")
        if not s.get("cues_sha256") or s.get("cues_method") != "local-asr":
            out.append(f"{split} {sid}: no local-ASR cue file recorded")
        # A source without speech is valid: local ASR ran and found nothing (cue_count 0, an empty cue file
        # still hashed). A failed or unknown ASR run is not "no speech" and is rejected.
        if s.get("asr_status") != "ok":
            out.append(f"{split} {sid}: ASR did not complete (asr_status must be 'ok'; 'failed' or missing is not 'no speech')")
        if not s.get("asr_model"):
            out.append(f"{split} {sid}: ASR model not recorded")
        count, ratio = s.get("cue_count"), s.get("speech_ratio")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            out.append(f"{split} {sid}: cue_count not recorded")
        elif not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            out.append(f"{split} {sid}: speech_ratio not recorded")
        elif not 0 <= ratio <= 1:
            out.append(f"{split} {sid}: speech_ratio {ratio} outside 0..1")
        elif (count == 0) != (ratio == 0):
            out.append(f"{split} {sid}: cue_count {count} and speech_ratio {ratio} disagree")
    if split == "REAL-HOLDOUT" and len({s.get("genre") for s in data["sources"] if s.get("genre")}) < data.get("genres_min", 3):
        out.append("REAL-HOLDOUT: fewer genres than required")
    return out


def evaluation(split, data):
    out = draft(split, data)
    nar, lab, sents = data["narration"], data["labels"], data["sentences"]
    if not (nar.get("sha256") and nar.get("locked_at") and nar.get("method") and nar.get("writer")):
        out.append(f"{split}: narration/cues not locked (sha256, locked_at, method, writer)")
    if nar.get("cue_rejections") is None:
        out.append(f"{split}: invalid-cue rejections not recorded")
    if any(s.get("synthetic") for s in sents):
        out.append(f"{split}: synthetic sentences")
    n = len(sents)
    lo, hi = (20, 30) if split == "REAL-DEV" else (100, 10 ** 9)
    if not lo <= n <= hi:
        out.append(f"{split}: {n} sentences ({lo}-{hi if hi < 10 ** 9 else 'more'})")
    labelers = lab.get("labelers") or []
    if not labelers:
        out.append(f"{split}: no human labeler")
    if lab.get("ai_judged"):
        out.append(f"{split}: AI-judged labels are not human labels")
    if not (lab.get("sha256") and lab.get("locked_at")):
        out.append(f"{split}: labels not locked")
    if not data.get("scoring_rules_commit"):
        out.append(f"{split}: scoring rules not locked to a commit")
    if split == "REAL-HOLDOUT":
        if len(labelers) < 2 or not lab.get("adjudicator") or lab.get("adjudicator") in labelers:
            out.append("REAL-HOLDOUT: needs two independent labelers and a separate adjudicator")
        if lab.get("kappa") is None:
            out.append("REAL-HOLDOUT: kappa not computed")
        if not data.get("parameters_locked_commit"):
            out.append("REAL-HOLDOUT: parameters not locked to a commit (chosen on DEV only)")
        if n:
            mix, types = data["sentence_mix_min"], [s["planned_type"] for s in sents]
            for kind, key in MIX:
                if types.count(kind) < mix[key] * n:
                    out.append(f"REAL-HOLDOUT: too few '{kind}' sentences")
            none = [s for s in sents if s["planned_type"] == "none"]
            if sum(bool(s.get("hard_negative")) for s in none) < mix["hard_negative_share_of_no_footage"] * len(none):
                out.append("REAL-HOLDOUT: fewer than half of the no-footage sentences are hard negatives")
            seen = {k for s in sents for k in ([s.get("mismatch_trap")] + list(s.get("claim_kinds") or [])) if k}
            missing = [t for t in data["error_types_required"] if t not in seen]
            if missing:
                out.append("REAL-HOLDOUT: no sentence tests " + ", ".join(missing))
    return out


def cue_problems(cues, duration_ms):
    """Cue times must be whole milliseconds with 0 <= start < end <= duration_ms. Problems are reported, never repaired."""
    out = []
    for i, c in enumerate(cues):
        start, end = c.get("start_ms"), c.get("end_ms")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (start, end)):
            out.append(f"cue {i}: start_ms/end_ms must be whole milliseconds")
        elif start < 0 or end <= start:
            out.append(f"cue {i}: invalid span {start}..{end} ms (needs 0 <= start < end)")
        elif duration_ms and end > duration_ms:
            out.append(f"cue {i}: ends at {end} ms, after the source ends ({duration_ms} ms)")
    return out


def speech_ratio(cues, duration_ms):
    """Share of the source covered by the union of cue intervals (README §3.3).

    Not rounded: rounding to a few places turned a short real cue in a long source into 0.000, which reads as
    "no speech" (Owner protocol 2026-11-15 §IV.C4). Display may round; the stored value never does.
    """
    spans = sorted((int(c["start_ms"]), int(c["end_ms"])) for c in cues)
    total, cur_start, cur_end = 0, None, None
    for start, end in spans:
        if end < start:
            raise ValueError("cue ends before it starts")
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        total += cur_end - cur_start
    return min(total, duration_ms) / duration_ms


def sha256_file(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_files(split, data, base):
    """Recompute from the real files under ``base``: the media sha256, and cues_sha256, cue_count, cue bounds and
    speech_ratio from the cue file. A validator PASS on typed values alone is never enough (protocol §IV.D4)."""
    out = []
    for s in data["sources"]:
        sid, rel = s["source_id"], s.get("cues_path")
        media = s.get("path")
        if not media:
            out.append(f"{split} {sid}: path not recorded, cannot verify the source file")
        elif not os.path.isfile(os.path.join(base, media)):
            out.append(f"{split} {sid}: source file {media} not found")
        elif sha256_file(os.path.join(base, media)) != s.get("sha256"):
            out.append(f"{split} {sid}: sha256 does not match {media}")
        if not rel:
            out.append(f"{split} {sid}: cues_path not recorded, cannot verify the cue file")
            continue
        path = os.path.join(base, rel)
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            out.append(f"{split} {sid}: cue file {rel} not found")
            continue
        if hashlib.sha256(raw).hexdigest() != s.get("cues_sha256"):
            out.append(f"{split} {sid}: cues_sha256 does not match {rel}")
        try:
            cues = json.loads(raw)["cues"]
            bad = cue_problems(cues, s.get("duration_ms"))
            ratio = speech_ratio(cues, s["duration_ms"]) if s.get("duration_ms") and not bad else None
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            out.append(f"{split} {sid}: cue file {rel} unreadable ({error})")
            continue
        out += [f"{split} {sid}: {p}" for p in bad]
        if s.get("cue_count") != len(cues):
            out.append(f"{split} {sid}: cue_count {s.get('cue_count')} but the file has {len(cues)} cues")
        if bad:
            continue
        stored = s.get("speech_ratio")
        if ratio is None or not isinstance(stored, (int, float)) or isinstance(stored, bool) or abs(stored - ratio) > 1e-9:
            out.append(f"{split} {sid}: speech_ratio {stored} but the cue file gives {ratio}")
    return out


def problems(m, stage=None, split=None, files=None):
    out = structure(m)
    if stage:
        out += (draft if stage == "draft" else evaluation)(split, m["splits"][split])
    if files is not None:
        out += verify_files(split, m["splits"][split], files)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    stage = args[args.index("--stage") + 1] if "--stage" in args else None
    split = args[args.index("--split") + 1] if "--split" in args else None
    files = args[args.index("--verify-files") + 1] if "--verify-files" in args else None
    if files is not None and not split:
        sys.exit("--verify-files needs --split")
    if stage and (stage not in ("draft", "eval") or split not in ("REAL-DEV", "REAL-HOLDOUT")):
        sys.exit("usage: --stage draft|eval --split REAL-DEV|REAL-HOLDOUT")
    with open(args[0], encoding="utf-8") as fh:
        m = json.load(fh)
    found = problems(m, stage, split, files)
    label = {"draft": "READY FOR PAID SCRIPT DRAFT", "eval": "READY FOR EVALUATION"}.get(stage)
    if found:
        print("\n".join(found)); print(f"{split}: NOT {label}" if label else "STRUCTURE PROBLEMS")
    else:
        print(f"{split}: {label} (data only; cost needs separate Owner approval)" if label else "STRUCTURE OK (no READY state checked)")
    sys.exit(1 if found else 0)
