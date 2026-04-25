import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import boto3
import yt_dlp
from botocore.config import Config
from dotenv import load_dotenv
from feedgen.feed import FeedGenerator

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Configuration from environment
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
PLAYLIST_URL = os.getenv("PLAYLIST_URL")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # Normalize BASE_URL
RSS_FILENAME = os.getenv("RSS_FILENAME", "rss.xml")
STATE_FILENAME = os.getenv("STATE_FILENAME", "state.json")
MAX_NEW_VIDEOS = int(os.getenv("MAX_NEW_VIDEOS", "5"))
ITUNES_IMAGE = os.getenv("ITUNES_IMAGE", "")
ITUNES_AUTHOR = os.getenv("ITUNES_AUTHOR", "")
COOKIES_FILE = os.getenv("COOKIES_FILE") # Optional path to cookies.txt
SLEEP_INTERVAL = int(os.getenv("SLEEP_INTERVAL", "21600")) # 6 hours default
RUN_ONCE = os.getenv("RUN_ONCE", "true").lower() == "true"

# S3 Client for R2
s3_client = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4"),
)

def get_state(prefix: str) -> Dict:
    key = f"{prefix}/{STATE_FILENAME}"
    try:
        response = s3_client.get_object(Bucket=R2_BUCKET_NAME, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except s3_client.exceptions.NoSuchKey:
        logger.info(f"State file {key} not found on R2, starting fresh.")
        return {"videos": {}}
    except Exception as e:
        logger.error(f"Error fetching state: {e}")
        return {"videos": {}}

def save_state(state: Dict, prefix: str):
    key = f"{prefix}/{STATE_FILENAME}"
    try:
        s3_client.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=key,
            Body=json.dumps(state, indent=2, ensure_ascii=False),
            ContentType="application/json",
        )
        logger.info(f"State saved to {key} on R2.")
    except Exception as e:
        logger.error(f"Error saving state: {e}")

def upload_file(local_path: Path, remote_key: str, content_type: str):
    try:
        s3_client.upload_file(
            str(local_path),
            R2_BUCKET_NAME,
            remote_key,
            ExtraArgs={"ContentType": content_type}
        )
        logger.info(f"Uploaded {local_path} to {remote_key}")
    except Exception as e:
        logger.error(f"Error uploading file {local_path}: {e}")
        raise

def download_audio(video_url: str, prefix: str) -> Optional[Dict]:
    tmp_dir = Path("downloads")
    tmp_dir.mkdir(exist_ok=True)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'm4a',
        }],
        'outtmpl': str(tmp_dir / '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }
    if COOKIES_FILE:
        ydl_opts['cookiefile'] = COOKIES_FILE
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            audio_path = tmp_dir / f"{info['id']}.m4a"
            if not audio_path.exists():
                audio_path = next(tmp_dir.glob(f"{info['id']}.*"))
            
            return {
                "id": info["id"],
                "title": info["title"],
                "description": info.get("description", ""),
                "upload_date": info.get("upload_date"),
                "filename": audio_path.name,
                "local_path": audio_path,
                "url": f"{BASE_URL}/{prefix}/{audio_path.name}"
            }
    except Exception as e:
        logger.error(f"Error downloading {video_url}: {e}")
        return None

def generate_rss(state: Dict, prefix: str, playlist_info: Dict):
    fg = FeedGenerator()
    fg.load_extension('podcast')
    fg.id(PLAYLIST_URL)
    fg.title(playlist_info.get('title', "YouTube Playlist RSS"))
    fg.author({'name': 'yt-rss'})
    fg.link(href=PLAYLIST_URL, rel='alternate')
    fg.description(playlist_info.get('description', "Generated RSS from YouTube Playlist"))
    if ITUNES_IMAGE:
        fg.podcast.itunes_image(ITUNES_IMAGE)
    if ITUNES_AUTHOR:
        fg.podcast.itunes_author(ITUNES_AUTHOR)
    
    videos = sorted(
        state["videos"].values(),
        key=lambda x: x.get("upload_date", ""),
        reverse=True
    )
    
    for video in videos:
        fe = fg.add_entry()
        fe.id(video["id"])
        fe.title(video["title"])
        fe.description(video["description"])
        fe.link(href=video["url"])
        fe.enclosure(video["url"], 0, 'audio/mp4')
        
        if video.get("upload_date"):
            try:
                date_obj = datetime.strptime(video["upload_date"], "%Y%m%d")
                fe.pubDate(date_obj.strftime("%a, %d %b %Y %H:%M:%S +0000"))
            except:
                pass

    local_rss = Path(RSS_FILENAME)
    fg.rss_file(str(local_rss), encoding='UTF-8', pretty=True)
    upload_file(local_rss, f"{prefix}/{RSS_FILENAME}", "application/rss+xml; charset=utf-8")
    local_rss.unlink()

def run_sync():
    if not all([R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL, R2_BUCKET_NAME, PLAYLIST_URL, BASE_URL]):
        logger.error("Missing required environment variables.")
        return

    # Get playlist entries and metadata
    ydl_opts = {'extract_flat': True, 'quiet': True}
    if COOKIES_FILE:
        ydl_opts['cookiefile'] = COOKIES_FILE
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            playlist_info = ydl.extract_info(PLAYLIST_URL, download=False)
            playlist_id = playlist_info.get('id')
            if not playlist_id:
                logger.error("Could not extract playlist ID.")
                return
            entries = playlist_info.get('entries', [])
    except Exception as e:
        logger.error(f"Error fetching playlist: {e}")
        return

    prefix = playlist_id
    state = get_state(prefix)
    
    new_videos_count = 0
    for entry in entries:
        if new_videos_count >= MAX_NEW_VIDEOS:
            logger.info(f"Reached limit of {MAX_NEW_VIDEOS} new videos per run.")
            break
            
        video_id = entry['id']
        if video_id not in state["videos"]:
            logger.info(f"Processing new video: {video_id}")
            video_data = download_audio(f"https://www.youtube.com/watch?v={video_id}", prefix)
            if video_data:
                upload_file(video_data["local_path"], f"{prefix}/{video_data['filename']}", "audio/mp4")
                video_data["local_path"].unlink()
                
                state["videos"][video_id] = {
                    "id": video_data["id"],
                    "title": video_data["title"],
                    "description": video_data["description"],
                    "upload_date": video_data["upload_date"],
                    "url": video_data["url"]
                }
                new_videos_count += 1
                save_state(state, prefix)

    # Check if RSS exists on R2 by checking if we should update
    rss_key = f"{prefix}/{RSS_FILENAME}"
    rss_exists = False
    try:
        s3_client.head_object(Bucket=R2_BUCKET_NAME, Key=rss_key)
        rss_exists = True
    except:
        pass

    if new_videos_count > 0 or not rss_exists:
        logger.info(f"Updating RSS feed in {prefix}/ with {new_videos_count} new entries.")
        generate_rss(state, prefix, playlist_info)
    else:
        logger.info("No new videos found and RSS already exists.")

def main():
    logger.info(f"Starting yt-rss. RUN_ONCE={RUN_ONCE}")
    if RUN_ONCE:
        run_sync()
    else:
        import time
        logger.info(f"Starting service mode. Syncing every {SLEEP_INTERVAL} seconds.")
        while True:
            try:
                run_sync()
            except Exception as e:
                logger.error(f"Unexpected error in sync loop: {e}")
            
            logger.info(f"Sleeping for {SLEEP_INTERVAL} seconds...")
            time.sleep(SLEEP_INTERVAL)

if __name__ == "__main__":
    main()
