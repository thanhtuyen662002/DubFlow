"""Agreement of two independent labelers ($0, offline): Cohen's kappa on the fit class, plus range agreement
(same class and acceptable ranges overlapping >= 50 % of the shorter) as the protocol defines it. Lists what goes to
the adjudicator.

usage: python -I agreement.py <labels_L1.json> <labels_L2.json>   (each: a list of label_template objects)
"""
import json, sys
from collections import Counter

def overlap(a, b):
    best = 0.0
    for x in a:
        for y in b:
            if x["source_id"] != y["source_id"]: continue
            inter = max(0.0, min(x["end_s"], y["end_s"]) - max(x["start_s"], y["start_s"]))
            shorter = max(1e-9, min(x["end_s"] - x["start_s"], y["end_s"] - y["start_s"]))
            best = max(best, inter / shorter)
    return best

def kappa(pairs):
    n = len(pairs); po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)

if __name__ == "__main__":
    def load(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    one = {l["sentence_id"]: l for l in load(sys.argv[1])}; two = {l["sentence_id"]: l for l in load(sys.argv[2])}
    ids = sorted(set(one) & set(two)); pairs = [(one[i]["fit"], two[i]["fit"]) for i in ids]
    agree = [i for i in ids if one[i]["fit"] == two[i]["fit"] and (one[i]["fit"] in ("NONE", "UNKNOWN") or overlap(one[i]["ranges"], two[i]["ranges"]) >= 0.5)
             and one[i].get("claim_shown") == two[i].get("claim_shown")]
    print(json.dumps({"n": len(ids), "kappa_fit": round(kappa(pairs), 3) if ids else None, "agree": len(agree),
                      "to_adjudicate": [i for i in ids if i not in agree], "only_one_labeler": sorted(set(one) ^ set(two))}, indent=1))
