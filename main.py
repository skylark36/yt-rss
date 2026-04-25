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
BASE_URL = os.getenv("BASE_URL")  # e.g., https://pub-xxx.r2.dev or custom domain
RSS_FILENAME = os.getenv("RSS_FILENAME", "rss.xml")
STATE_FILENAME = os.getenv("STATE_FILENAME", "state.json")

# S3 Client for R2
s3_client = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4"),
)

def get_state() -> Dict:
    try:
        response = s3_client.get_object(Bucket=R2_BUCKET_NAME, Key=STATE_FILENAME)
        return json.loads(response["Body"].read().decode("utf-8"))
    except s3_client.exceptions.NoSuchKey:
        logger.info("State file not found on R2, starting fresh.")
        return {"videos": {}}
    except Exception as e:
        logger.error(f"Error fetching state: {e}")
        return {"videos": {}}

def save_state(state: Dict):
    try:
        s3_client.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=STATE_FILENAME,
            Body=json.dumps(state, indent=2, ensure_ascii=False),
            ContentType="application/json",
        )
        logger.info("State saved to R2.")
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

def download_audio(video_url: str) -> Optional[Dict]:
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
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            audio_path = tmp_dir / f"{info['id']}.m4a"
            if not audio_path.exists():
                # Some formats might already be m4a or different
                audio_path = next(tmp_dir.glob(f"{info['id']}.*"))
            
            return {
                "id": info["id"],
                "title": info["title"],
                "description": info.get("description", ""),
                "upload_date": info.get("upload_date"),
                "filename": audio_path.name,
                "local_path": audio_path,
                "url": f"{BASE_URL}/{audio_path.name}"
            }
    except Exception as e:
        logger.error(f"Error downloading {video_url}: {e}")
        return None

def generate_rss(state: Dict):
    fg = FeedGenerator()
    fg.id(PLAYLIST_URL)
    fg.title("YouTube Playlist RSS")
    fg.author({'name': 'yt-rss'})
    fg.link(href=PLAYLIST_URL, rel='alternate')
    fg.description("Generated RSS from YouTube Playlist")
    
    # Sort videos by date descending
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
        
        # Enclosure for podcast apps
        fe.enclosure(video["url"], 0, 'audio/mp4') # m4a is audio/mp4
        
        if video.get("upload_date"):
            try:
                date_obj = datetime.strptime(video["upload_date"], "%Y%m%d")
                fe.pubDate(date_obj.strftime("%a, %d %b %Y %H:%M:%S +0000"))
            except:
                pass

    local_rss = Path(RSS_FILENAME)
    fg.rss_file(str(local_rss))
    upload_file(local_rss, RSS_FILENAME, "application/rss+xml")
    local_rss.unlink()

def main():
    if not all([R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL, R2_BUCKET_NAME, PLAYLIST_URL, BASE_URL]):
        logger.error("Missing required environment variables.")
        return

    state = get_state()
    
    # Get playlist entries
    ydl_opts = {'extract_flat': True, 'quiet': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            playlist_info = ydl.extract_info(PLAYLIST_URL, download=False)
            entries = playlist_info.get('entries', [])
    except Exception as e:
        logger.error(f"Error fetching playlist: {e}")
        return
    new_videos_count = 0
    for entry in entries:
        video_id = entry['id']
        if video_id not in state["videos"]:
            logger.info(f"Processing new video: {video_id}")
            video_data = download_audio(f"https://www.youtube.com/watch?v={video_id}")
            if video_data:
                upload_file(video_data["local_path"], video_data["filename"], "audio/mp4")
                video_data["local_path"].unlink()
                
                # Update state
                state["videos"][video_id] = {
                    "id": video_data["id"],
                    "title": video_data["title"],
                    "description": video_data["description"],
                    "upload_date": video_data["upload_date"],
                    "url": video_data["url"]
                }
                new_videos_count += 1
                
                # Save state incrementally to avoid losing progress
                save_state(state)

    if new_videos_count > 0 or not Path(RSS_FILENAME).exists():
        logger.info(f"Updating RSS feed with {new_videos_count} new entries.")
        generate_rss(state)
    else:
        logger.info("No new videos found.")

if __name__ == "__main__":
    main()
