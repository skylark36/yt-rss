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
import time
import random
import urllib.request
from lxml import etree
from notify import send_bark

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
SLEEP_INTERVAL = int(os.getenv("SLEEP_INTERVAL", "360")) # 360 minutes (6 hours) default
PREFIX = os.getenv("PREFIX")
AFTER_DATE = os.getenv("AFTER_DATE")

# S3 Client for R2
s3_client = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4"),
)

def fetch_rss_info(url: str) -> Optional[Dict]:
    """Fetches and parses YouTube RSS feed."""
    logger.info(f"Fetching RSS feed from {url}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            xml_content = response.read()
        
        root = etree.fromstring(xml_content)
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt': 'http://www.youtube.com/xml/schemas/2015'
        }
        
        title_node = root.find('atom:title', ns)
        title = title_node.text if title_node is not None else "YouTube RSS"
        
        channel_id_node = root.find('yt:channelId', ns)
        channel_id = channel_id_node.text if channel_id_node is not None else url.split("channel_id=")[-1]
        
        entries = []
        for entry_node in root.findall('atom:entry', ns):
            video_id_node = entry_node.find('yt:videoId', ns)
            v_title_node = entry_node.find('atom:title', ns)
            pub_date_node = entry_node.find('atom:published', ns)
            
            if video_id_node is not None:
                video_id = video_id_node.text
                video_title = v_title_node.text if v_title_node is not None else ""
                
                upload_date = None
                if pub_date_node is not None:
                    try:
                        # Normalize ISO date to YYYYMMDD
                        date_str = pub_date_node.text
                        if date_str.endswith('Z'):
                            date_str = date_str[:-1] + '+00:00'
                        dt = datetime.fromisoformat(date_str)
                        upload_date = dt.strftime("%Y%m%d")
                    except Exception as e:
                        logger.warning(f"Error parsing date {pub_date_node.text}: {e}")
                
                entries.append({
                    'id': video_id,
                    'title': video_title,
                    'upload_date': upload_date
                })
        
        return {
            'id': channel_id,
            'title': title,
            'description': f"RSS feed: {title}",
            'entries': entries
        }
    except Exception as e:
        logger.error(f"Error fetching RSS: {e}")
        send_bark("YT-RSS RSS Error", f"Error fetching RSS: {e}")
        return None

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
        send_bark("YT-RSS Error", f"Error fetching state: {e}")
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
        send_bark("YT-RSS Error", f"Error saving state: {e}")

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
        send_bark("YT-RSS Error", f"Error uploading file {local_path.name}: {e}")
        raise

def download_audio(video_url: str, prefix: str) -> Optional[Dict]:
    randomSleep()
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
                audio_path = next(tmp_dir.glob(f"{info['id']}.*"))
            
            title = info.get("title", "")
            description = info.get("description", "")
            if not description or len(description) > 128: # 限制长度，防止 RSS 文件体积过大
                description = title

            return {
                "id": info["id"],
                "title": title,
                "description": description,
                "upload_date": info.get("upload_date"),
                "filename": audio_path.name,
                "local_path": audio_path,
                "url": f"{BASE_URL}/{prefix}/{audio_path.name}"
            }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error downloading {video_url}: {error_msg}")
        send_bark("YT-RSS Error", f"Error downloading {video_url}: {error_msg}")
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
    
    videos = list(state["videos"].values())
    videos = sorted(
        videos,
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
        error_msg = "Missing required environment variables."
        logger.error(error_msg)
        send_bark("YT-RSS Config Error", error_msg)
        return

    # Get RSS entries and metadata
    try:
        playlist_info = fetch_rss_info(PLAYLIST_URL)
        
        if not playlist_info:
            return

        playlist_id = playlist_info.get('id')
        if not playlist_id:
            error_msg = "Could not extract playlist/channel ID from RSS."
            logger.error(error_msg)
            send_bark("YT-RSS Error", error_msg)
            return
        
        prefix = PREFIX or playlist_id
        state = get_state(prefix)
        
        entries = playlist_info.get('entries', [])
        logger.info(f"Source ID: {playlist_id}, Got {len(entries)} entries from RSS.")
    except Exception as e:
        logger.error(f"Error fetching source: {e}")
        send_bark("YT-RSS Error", f"Error fetching source: {e}")
        return

    new_videos_count = 0
    for entry in entries:
        video_id = entry['id']
        video_title = entry.get('title', '')
        
        # 1. Determine if we should skip this video based on date
        should_skip = False
        skip_reason = ""
        
        if AFTER_DATE:
            # Use RSS published date
            effective_date = entry.get('upload_date')
            if effective_date:
                if effective_date < AFTER_DATE:
                    should_skip = True
                    skip_reason = f"date: {effective_date}"
            else:
                logger.warning(f"No date found for {video_id} in RSS, cannot apply AFTER_DATE filter.")

        # 2. Handle skipping
        if should_skip:
            logger.info(f"Skipping video {video_id} by {skip_reason}")
            continue

        # 4. Proceed with download if it's a new video
        if video_id not in state["videos"]:
            if new_videos_count >= MAX_NEW_VIDEOS:
                logger.info(f"Reached limit of {MAX_NEW_VIDEOS} new videos per run.")
                break
                
            logger.info(f"Downloading video: {video_id} ({video_title})")
            video_data = download_audio(f"https://www.youtube.com/watch?v={video_id}", prefix)
            if video_data:
                logger.info(f"Uploading video: {video_id}")
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
        state = get_state(prefix) # get latest state
        generate_rss(state, prefix, playlist_info)
    else:
        logger.info("No new videos found and RSS already exists.")

def randomSleep():
    delay = random.randint(10, 60)
    logger.info(f"Waiting for {delay} seconds before download...")
    time.sleep(delay)

def main():
    logger.info(f"Starting service mode. Syncing every {SLEEP_INTERVAL} minutes.")
    while True:
        try:
            run_sync()
        except Exception as e:
            logger.error(f"Unexpected error in sync loop: {e}")
            send_bark("YT-RSS Critical Error", f"Unexpected error in sync loop: {e}")
        
        logger.info(f"Sleeping for {SLEEP_INTERVAL} minutes...")
        time.sleep(SLEEP_INTERVAL * 60)

def refresh_state():
    s = get_state(PREFIX)
    vids = s['videos']
    for video_id in list(vids.keys()):
        if vids[video_id].get('skipped', False):
            del vids[video_id]
    save_state(s, PREFIX)

if __name__ == "__main__":
    main()
