"""
Anime Auto-Downloader and Uploader Script
Automatically downloads anime from AnimePahe and uploads to Telegram channels
Using the existing bot's functionality in a standalone script
"""

import os
import re
import time
import json
import random
import asyncio
import logging
import requests
import threading
from datetime import datetime
from bs4 import BeautifulSoup
from pyrogram import Client
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("auto_downloader.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("AnimeAutoDownloader")

# Import from existing files
try:
    from config import API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL, DOWNLOAD_DIR, START_PIC
    from plugins.kwik import extract_kwik_link
    from plugins.direct_link import get_dl_link
    from plugins.headers import session
    from plugins.file import (download_file, sanitize_filename, send_and_delete_file, 
                           remove_directory, random_string, create_short_name)
    from helper.database import (get_thumbnail, save_thumbnail, delete_thumbnail,
                              get_caption, save_caption, delete_caption)
except ImportError as e:
    logger.error(f"Failed to import required modules: {e}")
    exit(1)

# Constants
QUALITIES = ["1080p", "720p", "480p", "360p"]  # Qualities to download in order of preference
ANIME_DIRECTORY = os.path.join(DOWNLOAD_DIR, "auto_download")
PROCESSED_FILE = "processed_episodes.json"
CONFIG_FILE = "auto_downloader_config.json"
THUMBNAIL_PATH = os.path.join(DOWNLOAD_DIR, "default_thumb.jpg")
SUB_TYPE = "Sub"  # Only download subbed episodes
RETRY_DELAY = 300  # 5 minutes between retries
MAX_RETRIES = 3   # Maximum retry attempts
CHANNEL_ID = LOG_CHANNEL  # Channel to upload files to

# Create necessary directories
os.makedirs(ANIME_DIRECTORY, exist_ok=True)

# Load config from file or create default
def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        else:
            # Default configuration
            default_config = {
                "anime_list": [
                    # Example entries, update with real anime to track
                    {"title": "One Piece", "session_id": "One-Piece", "latest_episode": 0},
                    {"title": "Demon Slayer", "session_id": "Kimetsu-no-Yaiba", "latest_episode": 0}
                ],
                "check_interval": 3600,  # 1 hour
                "qualities": QUALITIES,
                "download_limit": 5,  # Max episodes to download per run
                "auto_retry": True,
                "upload_as_document": False
            }
            save_config(default_config)
            return default_config
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return {
            "anime_list": [],
            "check_interval": 3600,
            "qualities": QUALITIES,
            "download_limit": 5,
            "auto_retry": True, 
            "upload_as_document": False
        }

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving config: {e}")

# Load previously processed episodes to avoid duplicates
def load_processed_episodes():
    try:
        if os.path.exists(PROCESSED_FILE):
            with open(PROCESSED_FILE, 'r') as f:
                return json.load(f)
        else:
            return {}
    except Exception as e:
        logger.error(f"Error loading processed episodes: {e}")
        return {}

def save_processed_episodes(processed):
    try:
        with open(PROCESSED_FILE, 'w') as f:
            json.dump(processed, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving processed episodes: {e}")

# Function to get anime info from Anilist API
def get_anilist_info(anime_title):
    try:
        query = '''
        query ($search: String) {
          Media (search: $search, type: ANIME) {
            id
            title {
              romaji
              english
            }
            description
            episodes
            status
            format
            genres
            coverImage {
              large
            }
          }
        }
        '''
        
        variables = {'search': anime_title}
        url = 'https://graphql.anilist.co'
        
        response = requests.post(url, json={'query': query, 'variables': variables})
        if response.status_code == 200:
            data = response.json()
            if data and 'data' in data and 'Media' in data['data']:
                return data['data']['Media']
        logger.warning(f"No anime info found for {anime_title}")
        return None
    except Exception as e:
        logger.error(f"Error fetching anime info from Anilist: {e}")
        return None

# Function to search for anime on AnimePahe
def search_anime(query):
    try:
        search_url = f"https://animepahe.ru/api?m=search&q={query.replace(' ', '+')}"
        response = session.get(search_url).json()
        
        if response['total'] == 0:
            logger.warning(f"No results found for query: {query}")
            return None
        
        return response['data']
    except Exception as e:
        logger.error(f"Error searching anime: {e}")
        return None

# Function to get episode list for an anime
def get_episodes(session_id, page=1):
    try:
        episodes_url = f"https://animepahe.ru/api?m=release&id={session_id}&sort=episode_asc&page={page}"
        response = session.get(episodes_url).json()
        
        last_page = int(response["last_page"])
        episodes = response['data']
        
        # If there are multiple pages, fetch all episodes
        all_episodes = episodes
        if last_page > 1 and page == 1:
            for p in range(2, last_page + 1):
                try:
                    next_page = get_episodes(session_id, p)
                    if next_page:
                        all_episodes.extend(next_page)
                except Exception as e:
                    logger.error(f"Error fetching page {p}: {e}")
        
        return all_episodes
    except Exception as e:
        logger.error(f"Error fetching episodes: {e}")
        return []

# Function to get download links for an episode
def get_download_links(session_id, episode_session):
    try:
        episode_url = f"https://animepahe.ru/play/{session_id}/{episode_session}"
        response = session.get(episode_url)
        soup = BeautifulSoup(response.content, "html.parser")
        
        # Extract all download links and their titles
        download_links = soup.select("#pickDownload a.dropdown-item")
        
        if not download_links:
            logger.warning(f"No download links found for episode {episode_session}")
            return []
        
        links = []
        for link in download_links:
            # Only include Sub links (not Dub)
            title = link.get_text(strip=True)
            if 'eng' not in title.lower():  # This means it's subbed, not dubbed
                links.append({
                    'title': title,
                    'url': link['href'],
                    'resolution': re.search(r"\b\d{3,4}p\b", title).group() if re.search(r"\b\d{3,4}p\b", title) else 'Unknown'
                })
        
        return links
    except Exception as e:
        logger.error(f"Error fetching download links: {e}")
        return []

# Function to download anime episode
def download_episode(anime_info, episode_number, episode_session, config):
    try:
        session_id = anime_info['session_id']
        anime_title = anime_info['title']
        
        # Get download links for the episode
        download_links = get_download_links(session_id, episode_session)
        if not download_links:
            logger.error(f"No download links available for {anime_title} episode {episode_number}")
            return False
        
        # Filter and sort links by preferred quality
        selected_link = None
        for quality in config['qualities']:
            for link in download_links:
                if quality.lower() in link['resolution'].lower():
                    selected_link = link
                    break
            if selected_link:
                break
        
        # If no preferred quality is available, use the highest available
        if not selected_link and download_links:
            selected_link = download_links[0]  # Assume links are already sorted by quality
        
        if not selected_link:
            logger.error(f"No suitable quality found for {anime_title} episode {episode_number}")
            return False
        
        # Extract Kwik link and generate direct download link
        kwik_link = extract_kwik_link(selected_link['url'])
        if not kwik_link or kwik_link.startswith("Error"):
            logger.error(f"Failed to extract Kwik link: {kwik_link}")
            return False
        
        direct_link = get_dl_link(kwik_link)
        if not direct_link:
            logger.error("Failed to get direct download link")
            return False
        
        # Create unique download directory
        random_str = random_string(5)
        user_download_dir = os.path.join(ANIME_DIRECTORY, random_str)
        os.makedirs(user_download_dir, exist_ok=True)
        
        # Prepare filename
        resolution = selected_link['resolution']
        short_name = create_short_name(anime_title)
        file_name = f"[{SUB_TYPE}] [{short_name}] [EP {episode_number}] [{resolution}].mp4"
        file_name = sanitize_filename(file_name)
        download_path = os.path.join(user_download_dir, file_name)
        
        logger.info(f"Downloading {anime_title} episode {episode_number} in {resolution}")
        
        # Download the file
        download_file(direct_link, download_path)
        
        return {
            'anime_title': anime_title,
            'episode_number': episode_number,
            'file_path': download_path,
            'download_dir': user_download_dir,
            'file_name': file_name,
            'resolution': resolution
        }
    except Exception as e:
        logger.error(f"Error downloading episode: {e}")
        return False

# Function to upload file to Telegram channel
async def upload_to_telegram(client, download_info, anilist_info=None):
    try:
        # Process the thumbnail
        thumb_path = None
        if os.path.exists(THUMBNAIL_PATH):
            thumb_path = THUMBNAIL_PATH
        elif anilist_info and 'coverImage' in anilist_info and 'large' in anilist_info['coverImage']:
            # Download cover image from Anilist as thumbnail
            cover_url = anilist_info['coverImage']['large']
            thumb_path = os.path.join(download_info['download_dir'], "thumb.jpg")
            response = requests.get(cover_url, stream=True)
            with open(thumb_path, 'wb') as thumb_file:
                for chunk in response.iter_content(1024):
                    thumb_file.write(chunk)
        
        # Prepare caption
        anime_title = download_info['anime_title']
        episode_number = download_info['episode_number']
        resolution = download_info['resolution']
        
        caption = f"**{anime_title}**\n"
        caption += f"**Episode:** {episode_number}\n"
        caption += f"**Quality:** {resolution}\n"
        
        if anilist_info:
            genres = ", ".join(anilist_info.get('genres', [])[:3])
            status = anilist_info.get('status', 'Unknown')
            
            caption += f"**Status:** {status}\n"
            if genres:
                caption += f"**Genres:** {genres}\n"
        
        caption += f"\nDownloaded and uploaded automatically by Anime PaheBot"
        
        logger.info(f"Uploading {download_info['file_name']} to Telegram")
        
        # Determine upload method (document or video)
        config = load_config()
        upload_as_document = config.get('upload_as_document', False)
        
        # Send file to channel
        if upload_as_document:
            await client.send_document(
                CHANNEL_ID,
                download_info['file_path'],
                thumb=thumb_path,
                caption=caption
            )
        else:
            await client.send_video(
                CHANNEL_ID,
                download_info['file_path'],
                thumb=thumb_path,
                caption=caption,
                supports_streaming=True
            )
        
        logger.info(f"Successfully uploaded {download_info['file_name']}")
        
        # Clean up
        if os.path.exists(download_info['download_dir']):
            remove_directory(download_info['download_dir'])
        
        return True
    except Exception as e:
        logger.error(f"Error uploading to Telegram: {e}")
        return False

# Main function to check for new episodes
async def check_for_new_episodes(client):
    config = load_config()
    processed_episodes = load_processed_episodes()
    
    for anime in config['anime_list']:
        try:
            anime_title = anime['title']
            session_id = anime['session_id']
            latest_processed = anime.get('latest_episode', 0)
            
            logger.info(f"Checking for new episodes of {anime_title}")
            
            # Get episode list
            episodes = get_episodes(session_id)
            if not episodes:
                logger.warning(f"No episodes found for {anime_title}")
                continue
            
            # Sort episodes by number
            episodes.sort(key=lambda x: int(x['episode']))
            
            # Get anime info from Anilist
            anilist_info = get_anilist_info(anime_title)
            
            # Track how many episodes we've processed in this run
            episodes_processed = 0
            
            # Process new episodes
            for ep in episodes:
                episode_number = int(ep['episode'])
                episode_session = ep['session']
                
                # Skip already processed episodes
                if episode_number <= latest_processed:
                    continue
                
                # Skip if we've reached the download limit for this run
                if episodes_processed >= config['download_limit']:
                    logger.info(f"Reached download limit of {config['download_limit']} for this run")
                    break
                
                # Check if already processed
                episode_key = f"{session_id}_{episode_number}"
                if episode_key in processed_episodes:
                    logger.info(f"Episode {episode_number} already processed")
                    continue
                
                logger.info(f"Processing {anime_title} episode {episode_number}")
                
                # Try to download the episode
                retries = 0
                success = False
                
                while retries < MAX_RETRIES and not success:
                    if retries > 0:
                        logger.info(f"Retry attempt {retries} for {anime_title} episode {episode_number}")
                        time.sleep(RETRY_DELAY)
                    
                    download_info = download_episode(anime, episode_number, episode_session, config)
                    
                    if download_info:
                        # Upload to Telegram
                        upload_success = await upload_to_telegram(client, download_info, anilist_info)
                        
                        if upload_success:
                            # Mark as processed
                            processed_episodes[episode_key] = {
                                "timestamp": datetime.now().isoformat(),
                                "quality": download_info['resolution']
                            }
                            
                            # Update latest episode
                            if episode_number > latest_processed:
                                anime['latest_episode'] = episode_number
                            
                            success = True
                            episodes_processed += 1
                    
                    retries += 1
                    
                if not success:
                    logger.error(f"Failed to process {anime_title} episode {episode_number} after {MAX_RETRIES} attempts")
            
            # Save latest processed episode number
            for i, a in enumerate(config['anime_list']):
                if a['session_id'] == session_id:
                    config['anime_list'][i]['latest_episode'] = anime['latest_episode']
                    break
                    
        except Exception as e:
            logger.error(f"Error processing anime {anime['title']}: {e}")
    
    # Save updated config and processed episodes
    save_config(config)
    save_processed_episodes(processed_episodes)

# Function to add new anime to track
def add_anime_to_track(title):
    try:
        # Search for the anime
        results = search_anime(title)
        if not results:
            logger.error(f"No results found for {title}")
            return False
        
        # Use the first result
        anime = results[0]
        anime_title = anime['title']
        session_id = anime['session']
        
        # Load current config
        config = load_config()
        
        # Check if already in list
        for existing in config['anime_list']:
            if existing['session_id'] == session_id:
                logger.info(f"{anime_title} is already being tracked")
                return False
        
        # Add to list
        config['anime_list'].append({
            'title': anime_title,
            'session_id': session_id,
            'latest_episode': 0  # Start from episode 1
        })
        
        # Save updated config
        save_config(config)
        logger.info(f"Added {anime_title} to tracking list")
        return True
    except Exception as e:
        logger.error(f"Error adding anime: {e}")
        return False

# Function to remove anime from tracking
def remove_anime_from_track(title_or_index):
    try:
        config = load_config()
        
        # If index is provided
        if isinstance(title_or_index, int):
            if 0 <= title_or_index < len(config['anime_list']):
                removed = config['anime_list'].pop(title_or_index)
                save_config(config)
                logger.info(f"Removed {removed['title']} from tracking list")
                return True
            else:
                logger.error(f"Invalid index: {title_or_index}")
                return False
        
        # If title is provided
        else:
            for i, anime in enumerate(config['anime_list']):
                if title_or_index.lower() in anime['title'].lower():
                    removed = config['anime_list'].pop(i)
                    save_config(config)
                    logger.info(f"Removed {removed['title']} from tracking list")
                    return True
            
            logger.error(f"Anime not found: {title_or_index}")
            return False
    except Exception as e:
        logger.error(f"Error removing anime: {e}")
        return False

# Function to list all tracked anime
def list_tracked_anime():
    try:
        config = load_config()
        if not config['anime_list']:
            logger.info("No anime currently being tracked")
            return []
        
        result = []
     