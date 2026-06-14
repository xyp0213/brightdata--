#!/usr/bin/env python3
"""
TikTok Creator Scraper — Bright Data Web Scraper API
======================================================
Collects TikTok creator/profile data via Bright Data's TikTok collectors:
- follower count, follower growth rate (delta over recent periods)
- total hearts (likes), video count
- average views per video, engagement rate
- commerce/creator marketplace data (if available)
- recent video performance breakdown

Supports two collection modes:
  1) Profile mode — scrape by @username or user_id
  2) Hashtag mode — discover creators from hashtag search results

API Reference: https://docs.brightdata.com/api-reference/web-scraper
"""

import os
import json
import time
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
BRIGHTDATA_ZONE_TIKTOK = os.getenv(
    "BRIGHTDATA_ZONE_TIKTOK", "tiktok_profiles"
)
API_BASE = "https://api.brightdata.com/dca"
POLL_INTERVAL_SEC = 10
MAX_POLL_ATTEMPTS = 60
HEADERS = {
    "Authorization": f"Bearer {BRIGHTDATA_API_TOKEN}",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Shared trigger + poll (same logic as Instagram scraper)
# ---------------------------------------------------------------------------

def trigger_and_collect(
    zone: str,
    inputs: List[Dict[str, str]],
    dataset_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Trigger a Bright Data collector and poll for results."""
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
            raise RuntimeError(f"Snapshot {snapshot_id} failed")
        print(f"   ... polling ({attempt+1}/{MAX_POLL_ATTEMPTS}) — phase={phase}")

    raise TimeoutError(
        f"Snapshot {snapshot_id} did not complete within "
        f"{MAX_POLL_ATTEMPTS * POLL_INTERVAL_SEC}s"
    )


def fetch_snapshot_records(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract records from completed snapshot."""
    snapshot_id = snapshot.get("snapshot_id")
    records = snapshot.get("records", [])
    if records:
        return records

    download_url = f"{API_BASE}/snapshot/{snapshot_id}/download"
    dl_resp = requests.get(download_url, headers=HEADERS, timeout=60)
    dl_resp.raise_for_status()
    content_type = dl_resp.headers.get("Content-Type", "")

    if "json" in content_type:
        data = dl_resp.json()
        return data if isinstance(data, list) else data.get("records", data.get("data", []))
    lines = dl_resp.text.strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# TikTok creator ingestion
# ---------------------------------------------------------------------------

def scrape_tiktok_creators(
    usernames: Optional[List[str]] = None,
    user_ids: Optional[List[str]] = None,
    hashtag: Optional[str] = None,
    zone_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Scrape TikTok creator profiles.

    Parameters
    ----------
    usernames : list[str] or None
        TikTok @usernames to scrape.
    user_ids : list[str] or None
        TikTok user IDs to scrape.
    hashtag : str or None
        Collect creators from a hashtag search (e.g., "beauty", "tech").
    zone_name : str or None
        Override zone name.

    Returns
    -------
    list[dict]
        Creator data with fields:
        - username, nickname, bio
        - follower_count, following_count, video_count
        - heart_count (total likes received)
        - avg_views, avg_likes, avg_comments, avg_shares
        - engagement_rate, follower_growth_rate
        - commerce_data (if available)
        - recent_videos list
    """
    zone = zone_name or BRIGHTDATA_ZONE_TIKTOK

    # Build inputs based on mode
    inputs = []
    if usernames:
        inputs.extend(
            [{"url": f"https://www.tiktok.com/@{u.strip()}"} for u in usernames]
        )
    if user_ids:
        inputs.extend(
            [{"url": f"https://www.tiktok.com/@user/{uid.strip()}"} for uid in user_ids]
        )
    if hashtag:
        inputs.append({
            "url": f"https://www.tiktok.com/tag/{hashtag.strip()}",
            "depth": 50,   # collect up to 50 creators from tag
        })

    if not inputs:
        raise ValueError("Provide usernames, user_ids, or a hashtag to scrape.")

    print(f"\n{'='*60}")
    mode = "profile(s)" if (usernames or user_ids) else f"hashtag #{hashtag}"
    print(f"TikTok Scraper: {len(inputs)} input(s) — {mode}")
    print(f"  Zone: {zone}")
    print(f"{'='*60}")

    snapshot = trigger_and_collect(zone, inputs)
    raw_records = fetch_snapshot_records(snapshot)

    creators = [_normalize_tiktok_creator(r) for r in raw_records]
    print(f"  Processed {len(creators)} creator(s)\n")
    return creators


def _normalize_tiktok_creator(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw TikTok creator record to canonical schema."""
    # TikTok collector may nest data under different keys
    user = raw.get("userInfo", raw.get("user", raw.get("author", raw)))
    stats = user.get("stats", user)

    creator = {
        "source": "tiktok",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "username": user.get("uniqueId") or user.get("username") or "",
        "nickname": user.get("nickname") or user.get("name") or "",
        "bio": user.get("signature") or user.get("bio") or "",
        "avatar_url": user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatar", ""),
        "verified": user.get("verified", False),
        "region": user.get("region", ""),
        # Core metrics
        "follower_count": _int(stats, "followerCount", "follower_count", "followers"),
        "following_count": _int(stats, "followingCount", "following_count", "following"),
        "video_count": _int(stats, "videoCount", "video_count", "posts"),
        "heart_count": _int(stats, "heartCount", "heart_count", "diggCount", "total_likes"),
        # Engagement
        "avg_views": 0.0,
        "avg_likes": 0.0,
        "avg_comments": 0.0,
        "avg_shares": 0.0,
        "engagement_rate": 0.0,
        "follower_growth_estimate": 0.0,
        # Commerce (creator marketplace / shop data)
        "commerce_indicators": {},
        # Recent videos
        "recent_videos": [],
    }

    # Parse recent videos
    videos = (
        raw.get("videos")
        or raw.get("recent_posts")
        or raw.get("itemList")
        or raw.get("aweme_list")
        or []
    )
    normalized_videos = []
    for v in videos[:30]:
        v_stats = v.get("stats", v)
        normalized_videos.append({
            "video_id": v.get("id") or v.get("aweme_id", ""),
            "description": (v.get("desc") or v.get("description", ""))[:200],
            "views": _int(v_stats, "playCount", "play_count", "views"),
            "likes": _int(v_stats, "diggCount", "digg_count", "likes"),
            "comments": _int(v_stats, "commentCount", "comment_count", "comments"),
            "shares": _int(v_stats, "shareCount", "share_count", "shares"),
            "duration_sec": _int(v_stats, "duration", "video_duration"),
            "create_time": _ts_to_iso(v.get("createTime") or v.get("create_time")),
            "music_title": (
                v.get("music", {}).get("title", "")
                if isinstance(v.get("music"), dict)
                else ""
            ),
        })

    creator["recent_videos"] = normalized_videos

    if normalized_videos:
        creator["avg_views"] = round(
            sum(v["views"] for v in normalized_videos) / len(normalized_videos), 1
        )
        creator["avg_likes"] = round(
            sum(v["likes"] for v in normalized_videos) / len(normalized_videos), 1
        )
        creator["avg_comments"] = round(
            sum(v["comments"] for v in normalized_videos) / len(normalized_videos), 1
        )
        creator["avg_shares"] = round(
            sum(v["shares"] for v in normalized_videos) / len(normalized_videos), 1
        )
        followers = creator["follower_count"]
        if followers > 0:
            creator["engagement_rate"] = round(
                (creator["avg_likes"] + creator["avg_comments"] + creator["avg_shares"])
                / followers * 100,
                2,
            )

        # Estimate follower growth: compare engagement on earliest vs latest videos
        by_time = sorted(
            [v for v in normalized_videos if v.get("create_time")],
            key=lambda v: v["create_time"],
        )
        if len(by_time) >= 2:
            early = by_time[:5]
            late = by_time[-5:]
            early_eng = sum(v["views"] for v in early) / max(1, len(early))
            late_eng = sum(v["views"] for v in late) / max(1, len(late))
            if early_eng > 0:
                creator["follower_growth_estimate"] = round(
                    ((late_eng / early_eng) - 1) * 100, 1
                )

    # Commerce indicators
    commerce = raw.get("commerce_data") or raw.get("shop_data") or raw.get("commerce_user_info", {})
    if commerce:
        creator["commerce_indicators"] = {
            "has_shop": commerce.get("has_shop", False),
            "products_count": _int(commerce, "product_count", "products"),
            "commerce_category": commerce.get("category", ""),
        }

    return creator


def _int(data: dict, *keys: str) -> int:
    """Safely extract integer from dict."""
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


def _ts_to_iso(ts_val):
    """Convert timestamp (int seconds or str) to ISO string."""
    if not ts_val:
        return None
    try:
        return datetime.fromtimestamp(int(ts_val), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return str(ts_val)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape TikTok creator data via Bright Data"
    )
    parser.add_argument(
        "--usernames", nargs="*",
        help="TikTok @usernames to scrape"
    )
    parser.add_argument(
        "--user-ids", nargs="*",
        help="TikTok user IDs to scrape"
    )
    parser.add_argument(
        "--hashtag",
        help="Discover creators from a TikTok hashtag"
    )
    parser.add_argument(
        "--zone", default=None,
        help="Bright Data zone name for TikTok collector"
    )
    parser.add_argument(
        "--output", "-o", default="data/tiktok_creators.json",
        help="Output JSON file path"
    )
    args = parser.parse_args()

    if not args.usernames and not args.user_ids and not args.hashtag:
        parser.error("Provide --usernames, --user-ids, or --hashtag")

    creators = scrape_tiktok_creators(
        usernames=args.usernames,
        user_ids=args.user_ids,
        hashtag=args.hashtag,
        zone_name=args.zone,
    )

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(creators, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(creators)} creator(s) to {args.output}")
