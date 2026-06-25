import os
from curl_cffi import requests

# --- THE ENGINE FUNCTIONS WE CRACKED ---

def search_comick(manga_title):
    """Searches ComicK and returns a clean list of dictionaries for our bot menus."""
    url = "https://api.comick.dev/v1.0/search"
    params = {"q": manga_title, "limit": "5", "t": "false"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://comick.dev",
        "Referer": "https://comick.dev/search"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, impersonate="chrome120")
        if response.status_code == 200:
            results = response.json()
            # Parse down into exactly what our Telegram buttons need to display
            return [{"title": item.get("title"), "slug": item.get("slug")} for item in results]
    except Exception as e:
        print(f"⚠️ Search error: {e}")
    return []

def get_comick_chapters(comic_slug):
    """Fetches the latest 10 chapters for a manga to show in selection buttons."""
    # Note: ComicK's internal ID structure often prefers a hidden 'hid' string
    # We query the comic details profile route to get their clean chapter list mapping
    url = f"https://api.comick.dev/comic/{comic_slug}/chapters"
    params = {"lang": "en", "limit": "10"} # Just grab the top 10 for the menu
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://comick.dev"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, impersonate="chrome120")
        if response.status_code == 200:
            data = response.json()
            chapters = data.get("chapters", [])
            
            parsed_chapters = []
            for ch in chapters:
                # Handle cases where 'chap' might be missing for oneshots
                chap_num = ch.get("chap", "Oneshot")
                title = ch.get("title") or f"Chapter {chap_num}"
                hid = ch.get("hid") # This hidden ID is critical for downloading pages
                
                parsed_chapters.append({
                    "display": f"Ch. {chap_num} - {title[:20]}",
                    "hid": hid
                })
            return parsed_chapters
    except Exception as e:
        print(f"⚠️ Chapter fetch error: {e}")
    return []

def get_chapter_pages(chapter_hid):
    """Fetches the actual image download URLs for a specific chapter ID."""
    # This endpoint extracts the structural images list array
    url = f"https://api.comick.dev/chapter/{chapter_hid}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://comick.dev"
    }
    
    try:
        response = requests.get(url, headers=headers, impersonate="chrome120")
        if response.status_code == 200:
            data = response.json()
            # ComicK stores image metadata under chapter -> md_images
            chapter_data = data.get("chapter", {})
            images = chapter_data.get("md_images", [])
            
            image_urls = []
            for img in images:
                file_path = img.get("b") # 'b' is the core image file hash path identifier
                if file_path:
                    # Construct ComicK's official, high-speed CDN image download link
                    full_url = f"https://meo.comick.pictures/{file_path}"
                    image_urls.append(full_url)
            return image_urls
    except Exception as e:
        print(f"⚠️ Page fetch error: {e}")
    return []