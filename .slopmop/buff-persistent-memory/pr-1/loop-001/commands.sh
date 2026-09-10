#!/usr/bin/env bash
set -euo pipefail

# Protocol: pr-feedback-v1
# PR: 1

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SCENARIO MENU — choose one per thread after investigating
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# fixed_in_code            — Code addresses the feedback. Cite the commit.
# invalid_with_explanation  — Feedback is incorrect. Explain with evidence.
# no_longer_applicable     — Code changed since comment. Note what changed.
# out_of_scope_ticketed    — Valid but not this PR. File issue, link it.
# needs_human_feedback     — Intent unclear; ask for clarification. Uses --no-resolve.

# ── [1] PRRT_kwDOUPBtK86gIh3H ──
# Category: 🐛 Logic/Correctness (impact=95)
# Signals: thread_marked_outdated
# Location: vision/preview.py:196
# Comment: Pressing 'd' rebuilds the detector with make_detector(), which drops tracking in the non-raw preview path; it can also crash when starting from a detector not in the cycle list (e.g. --detector yunet/...
# >> Investigate this thread, choose a scenario, replace <SCENARIO> and <YOUR_EVIDENCE>:
sm buff resolve 1 PRRT_kwDOUPBtK86gIh3H --scenario <SCENARIO> --message "<YOUR_EVIDENCE>"

# ── [2] PRRT_kwDOUPBtK86gIh33 ──
# Category: 💭 General (impact=45)
# Signals: thread_marked_outdated
# Location: vision/detector.py
# Comment: The Haar fallback warning prints the literal text 'YUNET_URL' instead of the actual URL, which makes the message less actionable. Interpolate the constant so users can follow the link directly.
# >> Investigate this thread, choose a scenario, replace <SCENARIO> and <YOUR_EVIDENCE>:
sm buff resolve 1 PRRT_kwDOUPBtK86gIh33 --scenario <SCENARIO> --message "<YOUR_EVIDENCE>"

# ── [3] PRRT_kwDOUPBtK86gIh3g ──
# Category: 📚 Documentation (impact=35)
# Signals: thread_marked_outdated
# Location: gloom.py
# Comment: The comments/README describe 'background' as the default detector, but DETECTOR is currently set to "face" (and eyes.py inherits that default via gloom.DETECTOR). Either update the docs to say face is...
# >> Investigate this thread, choose a scenario, replace <SCENARIO> and <YOUR_EVIDENCE>:
sm buff resolve 1 PRRT_kwDOUPBtK86gIh3g --scenario <SCENARIO> --message "<YOUR_EVIDENCE>"

# Re-enter the post-PR rail after resolving this batch
sm buff inspect 1
