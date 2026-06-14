#!/usr/bin/env python3
"""
KOL Scoring & Ranking Model
=============================
Combines Instagram + TikTok scraped data into a unified influencer scoring
framework.  Weighted criteria:

  Dimension           Weight   Signal
  ─────────────────   ──────   ────────────────────────────────────
  Engagement Rate     30%      (likes+comments+shares) / followers
  Follower Growth     20%      Estimated growth from video/post trends
  Followers           15%      Raw follower count (log-scaled)
  Commerce Potential  10%      Shop presence, product count
  Content Frequency   10%      Posts per week
  Avg Views           15%      Average views per video/post

All dimensions normalized 0–100, then combined with configurable weights.

Usage:
    python kol_scoring_model.py \\
        --instagram data/instagram_profiles.json \\
        --tiktok data/tiktok_creators.json \\
        --output data/kol_scores.csv
"""

import json
import os
import math
import csv
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configurable weights (environment overridable)
# ---------------------------------------------------------------------------

def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


WEIGHTS = {
    "engagement_rate":  _float_env("KOL_WEIGHT_ENGAGEMENT_RATE", 0.30),
    "follower_growth":  _float_env("KOL_WEIGHT_FOLLOWER_GROWTH", 0.20),
    "followers":        _float_env("KOL_WEIGHT_FOLLOWERS", 0.15),
    "commerce":         _float_env("KOL_WEIGHT_COMMERCE_POTENTIAL", 0.10),
    "content_frequency":_float_env("KOL_WEIGHT_CONTENT_FREQUENCY", 0.10),
    "avg_views":        _float_env("KOL_WEIGHT_AVG_VIEWS", 0.15),
}


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _log_normalize(values: np.ndarray, floor: float = 1.0) -> np.ndarray:
    """Log-scale normalize a 1D array to [0, 100]."""
    clipped = np.maximum(values, floor)
    logged = np.log1p(clipped)
    lo, hi = logged.min(), logged.max()
    if hi - lo < 1e-9:
        return np.full_like(logged, 50.0)
    return (logged - lo) / (hi - lo) * 100.0


def _minmax_normalize(values: np.ndarray) -> np.ndarray:
    """Min-max normalize to [0, 100]."""
    lo, hi = values.min(), values.max()
    if hi - lo < 1e-9:
        return np.full_like(values, 50.0)
    return (values - lo) / (hi - lo) * 100.0


# ---------------------------------------------------------------------------
# Merge Instagram + TikTok profiles into unified KOL records
# ---------------------------------------------------------------------------

def merge_profiles(
    instagram: List[Dict[str, Any]],
    tiktok: List[Dict[str, Any]],
    match_on: str = "username",
) -> List[Dict[str, Any]]:
    """
    Merge Instagram and TikTok profiles into unified KOL records.
    Profiles with the same username (lowercase) are merged; others are included standalone.

    Returns a list of merged dicts.
    """
    # Build lookup map
    ig_map: Dict[str, Dict] = {}
    for p in instagram:
        key = p.get(match_on, "").lower().strip()
        if key:
            ig_map[key] = p

    tt_map: Dict[str, Dict] = {}
    for p in tiktok:
        key = p.get(match_on, "").lower().strip()
        if key:
            tt_map[key] = p

    merged = []
    seen_keys: set = set()

    # Merge matching profiles
    for key in set(ig_map.keys()) | set(tt_map.keys()):
        ig = ig_map.get(key)
        tt = tt_map.get(key)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        merged.append({
            "kol_id": key,
            "instagram_username": ig.get("username", "") if ig else "",
            "tiktok_username": tt.get("username", "") if tt else "",
            "full_name": (ig or tt).get("full_name") or (tt or ig).get("nickname", ""),
            "bio": (ig or tt).get("biography") or (tt or ig).get("bio", ""),
            "category": (ig or tt).get("category", ""),
            "region": (tt or {}).get("region", ""),
            "verified": bool((ig or {}).get("is_verified") or (tt or {}).get("verified")),

            # Instagram metrics
            "ig_followers": (ig or {}).get("followers_count", 0),
            "ig_engagement_rate": (ig or {}).get("engagement_rate", 0.0),
            "ig_posts_per_week": (ig or {}).get("posts_per_week", 0.0),
            "ig_avg_likes": (ig or {}).get("avg_likes", 0.0),

            # TikTok metrics
            "tt_followers": (tt or {}).get("follower_count", 0),
            "tt_engagement_rate": (tt or {}).get("engagement_rate", 0.0),
            "tt_avg_views": (tt or {}).get("avg_views", 0.0),
            "tt_follower_growth": (tt or {}).get("follower_growth_estimate", 0.0),
            "tt_video_count": (tt or {}).get("video_count", 0),
            "tt_commerce": bool((tt or {}).get("commerce_indicators", {}).get("has_shop")),
            "tt_products_count": (tt or {}).get("commerce_indicators", {}).get("products_count", 0),

            # Combined
            "total_followers": (ig or {}).get("followers_count", 0) + (tt or {}).get("follower_count", 0),
            "merged_at": datetime.now(timezone.utc).isoformat(),
        })

    return merged


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def score_kols(
    kols: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Compute KOL scores for each influencer.

    For each dimension, values are normalized across the cohort to 0–100,
    then combined with the configured weights.

    Returns the list of KOL dicts, each enriched with:
      - dimension scores (0-100 per dimension)
      - kol_score (weighted composite 0-100)
      - rank (1 = best)
    """
    if not kols:
        return []

    w = weights or WEIGHTS
    df = pd.DataFrame(kols)

    # ── Dimension values ──
    # Engagement rate: use whichever platform's data is available
    df["_engagement"] = np.maximum(
        df["ig_engagement_rate"].fillna(0),
        df["tt_engagement_rate"].fillna(0),
    )

    # Follower growth: from TikTok estimate (0 if unavailable)
    df["_growth"] = df["tt_follower_growth"].fillna(0).clip(lower=-50, upper=200)

    # Followers: log-normalized combined total
    df["_followers"] = df["total_followers"].fillna(0)

    # Commerce: binary shop presence scaled by product count
    df["_commerce"] = df["tt_commerce"].astype(int) * np.log1p(df["tt_products_count"].fillna(0))

    # Content frequency: posts per week from IG
    df["_content_freq"] = df["ig_posts_per_week"].fillna(0).clip(0, 21)

    # Average views: from TikTok
    df["_views"] = df["tt_avg_views"].fillna(0)

    # ── Normalize to 0–100 ──
    df["score_engagement"]    = _minmax_normalize(df["_engagement"].values.astype(float))
    df["score_growth"]        = _minmax_normalize(df["_growth"].values.astype(float))
    df["score_followers"]     = _log_normalize(df["_followers"].values.astype(float))
    df["score_commerce"]      = _minmax_normalize(df["_commerce"].values.astype(float))
    df["score_content_freq"]  = _minmax_normalize(df["_content_freq"].values.astype(float))
    df["score_views"]         = _log_normalize(df["_views"].values.astype(float))

    # ── Weighted composite ──
    df["kol_score"] = (
        df["score_engagement"]    * w.get("engagement_rate", 0.30)
        + df["score_growth"]      * w.get("follower_growth", 0.20)
        + df["score_followers"]   * w.get("followers", 0.15)
        + df["score_commerce"]    * w.get("commerce", 0.10)
        + df["score_content_freq"]* w.get("content_frequency", 0.10)
        + df["score_views"]       * w.get("avg_views", 0.15)
    )

    # Rank (1 = highest score)
    df.sort_values("kol_score", ascending=False, inplace=True)
    df["rank"] = range(1, len(df) + 1)

    # Round for display
    for col in [c for c in df.columns if c.startswith("score_") or c == "kol_score"]:
        df[col] = df[col].round(1)

    return df.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_ranking_table(kols: List[Dict[str, Any]], top_n: int = 30) -> None:
    """Pretty-print a ranked KOL table to stdout."""
    if not kols:
        print("No KOLs to display.")
        return

    print(f"\n{'='*90}")
    print(f"  KOL Ranking — Top {min(top_n, len(kols))} of {len(kols)}")
    print(f"  Weights: {json.dumps(WEIGHTS, indent=2)}")
    print(f"{'='*90}")

    header = (
        f"{'Rank':>4}  {'KOL':<20}  {'Score':>6}  "
        f"{'Eng%':>6}  {'Grow%':>6}  {'Foll%':>6}  "
        f"{'Comm%':>6}  {'Freq%':>6}  {'View%':>6}  "
        f"{'Followers':>10}"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)

    for k in kols[:top_n]:
        print(
            f"{k['rank']:>4}  "
            f"{k['kol_id'][:20]:<20}  "
            f"{k['kol_score']:>6.1f}  "
            f"{k['score_engagement']:>6.1f}  "
            f"{k['score_growth']:>6.1f}  "
            f"{k['score_followers']:>6.1f}  "
            f"{k['score_commerce']:>6.1f}  "
            f"{k['score_content_freq']:>6.1f}  "
            f"{k['score_views']:>6.1f}  "
            f"{k['total_followers']:>10,}"
        )
    print(sep)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Score and rank KOLs from Instagram + TikTok data"
    )
    parser.add_argument(
        "--instagram", "-i",
        help="JSON file from instagram_profile_scraper.py"
    )
    parser.add_argument(
        "--tiktok", "-t",
        help="JSON file from tiktok_creator_scraper.py"
    )
    parser.add_argument(
        "--merged", "-m",
        help="Pre-merged KOL JSON (skips merge step)"
    )
    parser.add_argument(
        "--output", "-o", default="data/kol_scores.csv",
        help="Output CSV file"
    )
    parser.add_argument(
        "--top", type=int, default=30,
        help="Number of top KOLs to display"
    )
    parser.add_argument(
        "--output-json", default=None,
        help="Also export as JSON"
    )
    args = parser.parse_args()

    # Load data
    if args.merged:
        with open(args.merged, "r", encoding="utf-8") as f:
            kols = json.load(f)
    else:
        ig_data, tt_data = [], []
        if args.instagram:
            with open(args.instagram, "r", encoding="utf-8") as f:
                ig_data = json.load(f)
        if args.tiktok:
            with open(args.tiktok, "r", encoding="utf-8") as f:
                tt_data = json.load(f)
        kols = merge_profiles(ig_data, tt_data)

    # Score
    scored = score_kols(kols)
    print_ranking_table(scored, args.top)

    # Export
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    pd.DataFrame(scored).to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"\nSaved {len(scored)} KOLs to {args.output}")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(scored, f, ensure_ascii=False, indent=2)
        print(f"Saved JSON to {args.output_json}")
