"""Bounded, abstract withdrawal-authorization model. Python standard library only.

This is not a wallet, cryptographic implementation, or exchange exploit. Cryptographic
verification and ledger-proof soundness are abstract predicates. All data are synthetic.
An independent observer checks emitted payment effects against a fixed intent registry
and initial entitlements, rather than trusting the candidate controller's decision.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import hashlib
import itertools
import json

ROOT = Path(__file__).resolve().parent
DEPTH = 5
ROLES = ("backend", "signing_quorum", "governance", "recovery")
INITIAL_RIGHTS = (2, 0)  # Alice owns 2 units; Mallory owns none.
# Both principals may sign, but a signature alone cannot create an entitlement.
INTENTS = {
    "A": (0, "alice", 1, "chain-A"),
    "M": (1, "mallory", 1, "chain-A"),
}


@dataclass(frozen=True)
class Config:
    name: str
    guard: bool = True
    intent_check: bool = True
    ledger_check: bool = True
    nonce_check: bool = True
    admin_bypass: bool = False
    recovery_bypass: bool = False


CONFIGS = (
    Config("EOA_threshold_only", guard=False),
    Config("guard_all_controls"),
    Config("guard_without_intent", intent_check=False),
    Config("guard_without_ledger", ledger_check=False),
    Config("guard_without_nonce", nonce_check=False),
    Config("guard_with_admin_bypass", admin_bypass=True),
    Config("guard_with_recovery_bypass", recovery_bypass=True),
)


@dataclass(frozen=True)
class State:
    assets: int = 2
    credits: tuple[int, int] = INITIAL_RIGHTS
    used: tuple[bool, bool] = (False, False)
    paid: tuple[int, int] = (0, 0)
    observed_ids: tuple[str, ...] = ()
    disabled: bool = False
    exported: bool = False
    paused: bool = False


@dataclass(frozen=True)
class Payment:
    intent_id: str
    principal: int
    destination: str
    amount: int
    chain: str


def observe(before: State, p: Payment) -> str | None:
    """External security property; never calls the candidate authorization routine."""
    record = INTENTS.get(p.intent_id)
    if record != (p.principal, p.destination, p.amount, p.chain):
        return "payment_not_in_independent_intent_registry"
    if p.intent_id in before.observed_ids:
        return "same_intent_executed_more_than_once"
    if before.paid[p.principal] + p.amount > INITIAL_RIGHTS[p.principal]:
        return "payment_exceeds_independent_entitlement"
    return None


def candidates():
    # Includes an exact intent, a changed recipient, a changed amount, a changed
    # chain, a forged intent, and a valid signature by an unfunded adversary.
    return (
        Payment("A", 0, "alice", 1, "chain-A"),
        Payment("A", 0, "mallory", 1, "chain-A"),
        Payment("A", 0, "alice", 2, "chain-A"),
        Payment("A", 0, "alice", 1, "chain-B"),
        Payment("forged", 0, "mallory", 1, "chain-A"),
        Payment("M", 1, "mallory", 1, "chain-A"),
    )


def step(cfg: Config, s: State, roles: frozenset[str], action):
    kind, arg = action
    if kind == "pause" and "governance" in roles:
        return replace(s, paused=True), None, None
    if kind == "disable" and "governance" in roles and cfg.admin_bypass:
        return replace(s, disabled=True), None, None
    if kind == "recover" and "recovery" in roles and cfg.recovery_bypass:
        return replace(s, exported=True), None, None
    if kind == "forge_ledger" and "backend" in roles and not cfg.ledger_check:
        credit = list(s.credits)
        credit[arg] = min(2, credit[arg] + 1)
        return replace(s, credits=tuple(credit)), None, None
    if kind == "raw":
        # An external policy cannot constrain a raw EOA signature once the entire
        # threshold quorum is adversarial. An actual asset guard can.
        permitted = (not cfg.guard and "signing_quorum" in roles) or s.disabled or s.exported
        if not permitted or s.assets == 0:
            return s, None, None
        p = Payment("raw", 0, "mallory", 1, "chain-A")
    elif kind in ("operator", "user"):
        p = arg
        exact = INTENTS.get(p.intent_id) == (p.principal, p.destination, p.amount, p.chain)
        if kind == "user" and not exact:
            return s, None, None
        if kind == "operator" and "backend" not in roles and not exact:
            return s, None, None
        if s.paused:  # Intentionally does not claim liveness under governance censorship.
            return s, None, None
        protected = cfg.guard and not s.disabled and not s.exported
        if not cfg.guard and not s.disabled and not s.exported:
            # The baseline still has an honest business-level balance/intent
            # check. Backend compromise may bypass it; quorum compromise may
            # instead use the raw-signature route independently of the backend.
            idx = 0 if p.intent_id == "A" else 1
            honest_request = exact and not s.used[idx] and s.credits[p.principal] >= p.amount
            if not honest_request and "backend" not in roles:
                return s, None, None
        if protected:
            if cfg.intent_check and not exact:
                return s, None, None
            idx = 0 if p.intent_id == "A" else 1
            if cfg.nonce_check and s.used[idx]:
                return s, None, None
            if s.credits[p.principal] < p.amount:
                return s, None, None
        if s.assets < p.amount:
            return s, None, None
    else:
        return s, None, None

    violation = observe(s, p)
    paid = list(s.paid)
    paid[p.principal] += p.amount
    credit = list(s.credits)
    credit[p.principal] = max(0, credit[p.principal] - p.amount)
    used = list(s.used)
    if p.intent_id in INTENTS:
        used[0 if p.intent_id == "A" else 1] = True
    ns = replace(s, assets=s.assets - p.amount, paid=tuple(paid), credits=tuple(credit),
                 used=tuple(used), observed_ids=tuple(sorted(s.observed_ids + (p.intent_id,))))
    return ns, p, violation


def action_name(action):
    kind, arg = action
    if isinstance(arg, Payment):
        return f"{kind}({arg.intent_id},{arg.principal},{arg.destination},{arg.amount},{arg.chain})"
    return f"{kind}({arg})"


def search(cfg: Config, roles: frozenset[str]):
    actions = [(k, p) for k in ("user", "operator") for p in candidates()]
    actions += [("raw", None), ("pause", None), ("disable", None), ("recover", None),
                ("forge_ledger", 0), ("forge_ledger", 1)]
    queue = deque([(State(), ())])
    seen = {State()}
    transitions = 0
    counterexample = None
    while queue:
        state, trace = queue.popleft()
        if len(trace) >= DEPTH:
            continue
        for action in actions:
            transitions += 1
            ns, payment, violation = step(cfg, state, roles, action)
            nt = trace + (action_name(action),)
            if violation:
                if counterexample is None:
                    counterexample = {"reason": violation, "trace": list(nt),
                                      "payment": asdict(payment)}
                # Terminal breach state; enumerate other nonbreaching states.
                continue
            if ns not in seen:
                seen.add(ns)
                queue.append((ns, nt))
    return {"roles": sorted(roles), "reachable_states": len(seen),
            "transition_attempts": transitions, "counterexample": counterexample}


def main():
    masks = [frozenset(r for r, b in zip(ROLES, bits) if b)
             for bits in itertools.product((False, True), repeat=4)]
    results = []
    for cfg in CONFIGS:
        runs = [search(cfg, mask) for mask in masks]
        results.append({"configuration": asdict(cfg), "role_configurations": len(runs),
                        "violating_configurations": sum(r["counterexample"] is not None for r in runs),
                        "reachable_states_sum": sum(r["reachable_states"] for r in runs),
                        "transition_attempts": sum(r["transition_attempts"] for r in runs),
                        "runs": runs})
    robust = next(r for r in results if r["configuration"]["name"] == "guard_all_controls")
    assert robust["violating_configurations"] == 0
    assert all(r["violating_configurations"] > 0 for r in results if r is not robust)
    # Positive control: a properly authorized withdrawal is actually executable.
    ns, payment, violation = step(CONFIGS[1], State(), frozenset(), ("user", candidates()[0]))
    assert payment is not None and ns.assets == 1 and violation is None
    # Availability counterexample: refusal is distinguishable from theft.
    paused, _, _ = step(CONFIGS[1], State(), frozenset({"governance"}), ("pause", None))
    blocked, payment, _ = step(CONFIGS[1], paused, frozenset(), ("user", candidates()[0]))
    assert payment is None and blocked.assets == 2
    out = {"model_version": "1.0", "max_depth": DEPTH, "actions_per_state": 18,
           "initial_asset_units": 2, "initial_entitlements": {"alice": 2, "mallory": 0},
           "role_dimensions": list(ROLES), "cryptography": "abstract ideal predicates; no signatures implemented",
           "scope": "synthetic finite-state authorization model, not a deployed protocol or exchange audit",
           "configurations": results,
           "total_role_configurations": sum(r["role_configurations"] for r in results),
           "total_transition_attempts": sum(r["transition_attempts"] for r in results),
           "positive_control": "authorized withdrawal executes",
           "availability_counterexample": "governance pause blocks withdrawal but does not move assets",
           "model_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (ROOT / "model_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for r in results:
        print(r["configuration"]["name"], r["violating_configurations"], "/", r["role_configurations"],
              "states", r["reachable_states_sum"], "transitions", r["transition_attempts"])
    print("TOTAL", out["total_role_configurations"], "configurations;", out["total_transition_attempts"], "transition attempts")


if __name__ == "__main__":
    main()
