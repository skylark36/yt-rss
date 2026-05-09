import sys
import logging
from typing import Optional
import yt_dlp
from main import (
    fetch_rss_info,
    PLAYLIST_URL,
    PREFIX,
    get_state,
    save_state,
    generate_rss,
    randomSleep,
    logger,
)
from config import load_env


def get_video_upload_date(video_id: str) -> Optional[str]:
    """Fetches the upload date of a video using yt-dlp."""
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info(f"Fetching date for {video_id} via yt-dlp")
    randomSleep()
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            return info.get("upload_date")
    except Exception as e:
        logger.error(f"Error fetching date for {video_id}: {e}")
        return None


def fix_state_dates():
    """Updates existing state videos with raw date strings from the current RSS feed, falling back to yt-dlp."""
    playlist_info = fetch_rss_info(PLAYLIST_URL)
    if not playlist_info:
        logger.error("Could not fetch RSS info to fix dates.")
        return

    playlist_id = playlist_info.get("id")
    prefix = PREFIX or playlist_id
    state = get_state(prefix)

    rss_videos = {
        entry["id"]: entry["upload_date"] for entry in playlist_info.get("entries", [])
    }

    updated_count = 0
    for video_id, video_data in state.get("videos", {}).items():
        old_date = video_data.get("upload_date")
        new_date = None

        if video_id in rss_videos:
            new_date = rss_videos[video_id]
        else:
            # Fallback to yt-dlp if not in RSS
            new_date = get_video_upload_date(video_id)

        if new_date and old_date != new_date:
            video_data["upload_date"] = new_date
            updated_count += 1
            logger.info(f"Updated date for {video_id}: {old_date} -> {new_date}")

    if updated_count > 0:
        save_state(state, prefix)
        logger.info(f"Fixed dates for {updated_count} videos in state.")
    else:
        logger.info("No dates needed updating in state.")


def refresh_state():
    """Cleans up videos marked as skipped from the state."""
    s = get_state(PREFIX)
    vids = s["videos"]
    removed_count = 0
    for video_id in list(vids.keys()):
        if vids[video_id].get("skipped", False):
            del vids[video_id]
            removed_count += 1
    if removed_count > 0:
        save_state(s, PREFIX)
        logger.info(f"Removed {removed_count} skipped videos from state.")
    else:
        logger.info("No skipped videos to remove.")


def refresh_rss():
    """Regenerates the RSS feed based on current state."""
    state = get_state(PREFIX)
    playlist_info = fetch_rss_info(PLAYLIST_URL)
    generate_rss(state, PREFIX, playlist_info)
    logger.info("RSS feed refreshed.")


if __name__ == "__main__":
    load_env()
    fix_state_dates()
    refresh_rss()
