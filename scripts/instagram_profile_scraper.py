#!/usr/bin/env python3
"""
Instagram Profile Scraper — Bright Data Web Scraper API
========================================================
Collects Instagram profile data via Bright Data's Instagram collector:
- follower count, following count, post count
- engagement rate (likes + comments per post / followers)
- content frequency (posts per week/month)
- bio, category, contact info
- recent post performance data

Authentication: Bright Data API Token + Zone name (Instagram collector)
API Reference: https://docs.brightdata.com/api-reference/web-scraper
"""

import os
import json
import time
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

import requests
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BRIGHTDATA_API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN", "")
BRIGHTDATA_ZONE_INSTAGRAM = os.getenv(
    "BRIGHTDATA_ZONE_INSTAGRAM", "instagram_profiles"
)
API_BASE = "https://api.brightdata.com/dca"
POLL_INTERVAL_SEC = 10          # seconds between status checks
MAX_POLL_ATTEMPTS = 60          # ~10 minutes max wait
HEADERS = {
    "Authorization": f"Bearer {BRIGHTDATA_API_TOKEN}",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Helper: trigger a Bright Data collector and poll until completion
# ---------------------------------------------------------------------------

def trigger_and_collect(
    zone: str,
    inputs: List[Dict[str, str]],
    dataset_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Trigger a Bright Data collector and poll for results.

    Parameters
    ----------
    zone : str
        The Bright Data zone (collector) name.
    inputs : list[dict]
        List of input items (e.g. [{"url": "https://instagram.com/..."}]).
    dataset_id : str or None
        Optional dataset identifier for tracking.

    Returns
    -------
    dict
        The completed snapshot with results.
    """
    trigger_url = f"{API_BASE}/trigger"
    payload = {"zone": zone, "input": inputs}
    if dataset_id:
        payload["dataset_id"] = dataset_id

    resp = requests.post(trigger_url, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    snapshot_id = resp.json().get("snapshot_id")
    if not snapshot_id:
        raise RuntimeError(f"No snapshot_id in trigger response: {resp.text}")

    print(f"   Snapshot triggered: {snapshot_id}  (zone={zone})")

    # Poll until snapshot completes
    for attempt in range(MAX_POLL_ATTEMPTS):
        time.sleep(POLL_INTERVAL_SEC)
        status_url = f"{API_BASE}/snapshot/{snapshot_id}"
        status_resp = requests.get(status_url, headers=HEADERS, timeout=30)
        status_resp.raise_for_status()
        snap = status_resp.json()

        phase = snap.get("phase", "unknown")
        if phase == "done":
            print(f"   Snapshot complete ({snap.get('total_records', '?')} records)")
            return snap
        if phase == "failed":
            raise RuntimeError(f"Snapshot {snapshot_id} failed: {snap.get('error')}")
        print(f"   ... polling ({attempt+1}/{MAX_POLL_ATTEMPTS}) — phase={phase}")

    raise TimeoutError(
        f"Snapshot {snapshot_id} did not complete within "
        f"{MAX_POLL_ATTEMPTS * POLL_INTERVAL_SEC}s"
    )


def fetch_snapshot_records(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract records from a completed snapshot, downloading if necessary."""
    snapshot_id = snapshot.get("snapshot_id")
    records = snapshot.get("records", [])
    if records:
        return records

    # Records may need to be fetched separately
    download_url = f"{API_BASE}/snapshot/{snapshot_id}/download"
    dl_resp = requests.get(download_url, headers=HEADERS, timeout=60)
    dl_resp.raise_for_status()
    content_type = dl_resp.headers.get("Content-Type", "")

    if "json" in content_type:
        data = dl_resp.json()
        if isinstance(data, list):
            return data
        return data.get("records", data.get("data", []))
    # handle NDJSON / newline-delimited JSON
    lines = dl_resp.text.strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Instagram profile ingestion
# ---------------------------------------------------------------------------

def scrape_instagram_profiles(
    usernames: List[str],
    zone_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Scrape multiple Instagram profiles via Bright Data Instagram collector.

    Parameters
    ----------
    usernames : list[str]
        Instagram usernames (without '@') to scrape.
    zone_name : str or None
        Override the Instagram zone name.

    Returns
    -------
    list[dict]
        List of profile data dicts with these key fields:
        - username, full_name, biography
        - followers_count, following_count, posts_count
        - is_verified, is_business_account
        - category, external_url, profile_pic_url
        - recent_posts (list of {likes, comments, timestamp, caption, ...})
    """
    zone = zone_name or BRIGHTDATA_ZONE_INSTAGRAM
    inputs = [
        {"url": f"https://www.instagram.com/{u.strip()}/"} for u in usernames
    ]

    print(f"\n{'='*60}")
    print(f"Instagram Scraper: {len(usernames)} profile(s)")
    print(f"  Zone: {zone}")
    print(f"{'='*60}")

    snapshot = trigger_and_collect(zone, inputs)
    raw_records = fetch_snapshot_records(snapshot)

    profiles = [_normalize_instagram_profile(r) for r in raw_records]
    print(f"  Processed {len(profiles)} profile(s)\n")
    return profiles


def _normalize_instagram_profile(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize and enrich a raw Instagram profile record."""
    # Bright Data uses varied field names depending on collector version.
    # We map to a canonical schema.
    profile = {
        "source": "instagram",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "username": raw.get("username") or raw.get("profile_id", ""),
        "full_name": raw.get("full_name") or raw.get("name", ""),
        "biography": raw.get("biography") or raw.get("bio", ""),
        "category": raw.get("category") or raw.get("category_name", ""),
        "is_verified": raw.get("is_verified", False),
        "is_business": raw.get("is_business_account", False),
        "external_url": raw.get("external_url") or raw.get("website", ""),
        "profile_pic_url": raw.get("profile_pic_url") or raw.get("avatar", ""),
        # Core metrics
        "followers_count": _int(raw, "followers_count", "followers"),
        "following_count": _int(raw, "following_count", "following"),
        "posts_count": _int(raw, "posts_count", "media_count", "posts"),
        # Engagement: compute from recent posts
        "recent_posts": [],
        "avg_likes": 0,
        "avg_comments": 0,
        "engagement_rate": 0.0,
        "posts_per_week": 0.0,
    }

    # Parse recent posts
    posts = raw.get("recent_posts") or raw.get("posts") or raw.get("edges") or []
    normalized_posts = []
    for p in posts[:30]:
        node = p.get("node", p)
        likes = _int(node, "likes", "like_count", "edge_liked_by", "favorite_count")
        comments = _int(node, "comments", "comment_count", "edge_media_to_comment")
        ts = node.get("timestamp") or node.get("taken_at_timestamp") or node.get("created_time")
        caption = node.get("caption") or node.get("edge_media_to_caption", {}).get("edges", [{}])[0].get("node", {}).get("text", "")

        if ts:
            try:
                ts = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except (ValueError, TypeError, OSError):
                ts = str(ts)

        normalized_posts.append({
            "likes": likes,
            "comments": comments,
            "timestamp": ts,
            "caption": (caption or "")[:200],
        })

    profile["recent_posts"] = normalized_posts

    if normalized_posts:
        profile["avg_likes"] = round(
            sum(p["likes"] for p in normalized_posts) / len(normalized_posts), 1
        )
        profile["avg_comments"] = round(
            sum(p["comments"] for p in normalized_posts) / len(normalized_posts), 1
        )
        followers = profile["followers_count"]
        if followers > 0:
            profile["engagement_rate"] = round(
                (profile["avg_likes"] + profile["avg_comments"]) / followers * 100, 2
            )

    # Estimate content frequency from recent post timestamps
    timestamps = [
        p.get("timestamp") for p in normalized_posts if p.get("timestamp")
    ]
    if len(timestamps) >= 2:
        try:
            dts = sorted(
                [datetime.fromisoformat(t) for t in timestamps], reverse=True
            )
            span_days = max(1, (dts[0] - dts[-1]).days)
            profile["posts_per_week"] = round(
                len(dts) / (span_days / 7.0), 1
            )
        except Exception:
            pass

    return profile


def _int(data: dict, *keys: str) -> int:
    """Safely extract an integer from one of several possible keys."""
    for k in keys:
        v = data.get(k)
        if v is not None:
            if isinstance(v, dict):
                v = v.get("count", 0)
            try:
                return int(v)
            except (ValueError, TypeError):
                pass
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape Instagram profiles via Bright Data Web Scraper API"
    )
    parser.add_argument(
        "usernames", nargs="+",
        help="Instagram usernames to scrape (without @)"
    )
    parser.add_argument(
        "--zone", default=None,
        help="Bright Data zone name for Instagram collector"
    )
    parser.add_argument(
        "--output", "-o", default="data/instagram_profiles.json",
        help="Output JSON file path"
    )
    args = parser.parse_args()

    profiles = scrape_instagram_profiles(
        usernames=args.usernames,
        zone_name=args.zone,
    )

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(profiles)} profile(s) to {args.output}")
