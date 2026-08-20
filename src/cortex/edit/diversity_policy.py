from __future__ import annotations

# Versioned single-source-of-truth for the camera plan's diversity rules. Bump
# this whenever any constant below changes so camera plan caches invalidate.
DIVERSITY_POLICY_VERSION = "1.1.0"

# Minimum ReactionCandidate.confidence required before a candidate may ever be
# reused as a reaction shot. ReactionCandidateService computes:
#   confidence = min(
#       min(speaker_confidences),      # weakest confirmed-identity match backing the window
#       visual_quality_score,          # 1 - mean(black/blur/freeze/occlusion share)
#       0.5 + lip_safety / 2.0,        # lip_safety in [0, 1]; this term is always >= 0.5
#   )
# The lip-safety term never drops below 0.5, so in practice the binding
# constraint is either identity confidence or visual quality. A candidate
# below 0.55 means its weakest signal (identity match or frame quality) is
# barely above a coin flip, which is not safe to fabricate as a reaction cut.
# 0.55 keeps a narrow safety margin above the 0.5 floor without discarding the
# common case of visual_quality_score sitting in the 0.55-0.70 band due to
# minor blur/occlusion that does not affect legibility.
MINIMUM_REACTION_CONFIDENCE = 0.55

# Reaction inserts should read as a quick editorial beat, not as fabricated
# coverage. The product contract allows 0.7–1.6 seconds only.
MINIMUM_REACTION_DURATION_US = 700_000
MAXIMUM_REACTION_DURATION_US = 1_600_000

# Fraction of the total editorial timeline that may be covered by reused
# reaction footage. Keeps reaction reuse a garnish, not the dish.
MAXIMUM_REACTION_SHARE = 0.2

# Minimum spacing, in microseconds, required between two reaction shots so
# reuse does not read as a repeating tic. 15s is long enough to separate two
# distinct reaction beats in a typical interview pacing.
MINIMUM_GAP_BETWEEN_REACTIONS_US = 15_000_000

# Fraction of the editorial timeline dominated by a single confirmed identity
# above which the plan is considered visually monotonous and a reaction
# insertion becomes desirable even outside a "fallback" shot.
MONOTONY_THRESHOLD = 0.85

# Minimum total editorial duration, in microseconds, before the monotony
# check applies. Below this, a single-source plan is just a short interview
# opener, not monotony.
MONOTONY_MINIMUM_DURATION_US = 20_000_000
