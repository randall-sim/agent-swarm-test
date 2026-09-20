"""Derive the next increment from current evidence, not stale repair labels."""

from .context import current_checks

def next_step(state):
    candidate = state.get("candidate")
    latest = next((h for h in reversed(state["history"]) if h.get("review") and h.get("outcome") == "kept"), {})
    review = (candidate.get("review") or {}) if candidate else latest.get("review", {})
    checks, source = current_checks(state)
    passed = bool(checks) and all(r["returncode"] == 0 and not r["timed_out"] for r in checks)
    defects = review.get("defects")
    if not checks and latest and not candidate:
        mode = "reassess"
        instruction = "Accepted code has no saved verification output. Inspect or verify current code; do not infer failures from the original baseline."
    elif not passed:
        mode = "repair"
        instruction = "Fix current verification failures and concrete defects; preserve working behavior."
    elif defects:
        mode = "repair"
        instruction = "Checks pass, but the reviewer identified defects in implemented behavior. Fix those specific defects."
    elif candidate and "defects" not in review:
        mode = "reassess"
        instruction = ("Current checks pass. The saved review predates structured defect tracking or is pending. "
                       "Inspect whether it identifies an actual defect or only missing future features. "
                       "Do not repeat old fixes already present. If only features are missing, implement the next "
                       "unmet requirement while preserving this candidate. It is not yet accepted.")
    else:
        mode = "advance"
        instruction = ("Current checks pass and no concrete defects are recorded. Implement the next unmet "
                       "requirement from the original goal. Maintaining already-working behavior is not an increment. "
                       "Name one new observable capability, assign its implementation files and tests, and defer "
                       "only the other remaining requirements. Use one worker if interfaces are not settled.")
    return {"mode": mode, "checks_pass": passed, "defects": defects,
            "remaining_work": review.get("remaining_work", review.get("lesson", "")),
            "next_increment": review.get("next_increment", ""),
            "previous_attempt_changed_code": state["history"][-1].get("changed_code") if state["history"] else None,
            "instruction": instruction,
            "evidence": "Latest candidate verification/review" if candidate else source}
