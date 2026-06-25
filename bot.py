import asyncio
import os
import json
import shutil
import httpx
from PIL import Image
from telethon import TelegramClient, events, Button
from curl_cffi import requests

# ==========================================
# 1. TELEGRAM API CREDENTIALS & INITIALIZATION
# ==========================================
API_ID = 33777175     
API_HASH = "39385c53937e92e13e6b1f9477a531c3"  
BOT_TOKEN = "8982142957:AAGJf8YSnst9rvpEpFbGQysGn7Q48Zk1LBk"

bot = TelegramClient('mmmwc_bot_session', API_ID, API_HASH)

BASE_URL = "https://api.mangadex.org"
DATA_FILE = "bot_data.json"

# Global download processing queue
download_queue = []

# ==========================================
# 2. DATA STORAGE LAYER
# ==========================================
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f: return json.load(f)
        except: pass
    return {"subscriptions": {}, "favorites": {}, "settings": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f: json.dump(data, f, indent=4)

def get_user_format(chat_id):
    data = load_data()
    return data.get("settings", {}).get(str(chat_id), {}).get("format", "PDF")

def toggle_user_format(chat_id):
    data = load_data()
    if "settings" not in data: data["settings"] = {}
    c_id = str(chat_id)
    if c_id not in data["settings"]: data["settings"][c_id] = {"format": "PDF"}
    current = data["settings"][c_id].get("format", "PDF")
    new_format = "CBZ" if current == "PDF" else "PDF"
    data["settings"][c_id]["format"] = new_format
    save_data(data)
    return new_format

# ==========================================
# 3. ADVANCED SIMULTANEOUS SEARCH CORE
# ==========================================
async def fetch_mangadex_profile(clean_title: str):
    async with httpx.AsyncClient(headers={"User-Agent": "MMMWCBot/1.0"}) as client:
        try:
            params = {"title": clean_title, "limit": 1, "includes[]": ["cover_art", "author"]}
            res = await client.get(f"{BASE_URL}/manga", params=params, timeout=8.0)
            data = res.json().get("data", [])
            if data:
                manga = data[0]
                manga_id = manga["id"]
                attrs = manga["attributes"]
                title = attrs["title"].get("en", "Unknown Title")
                description = attrs["description"].get("en", "No description available.").split("\n")[0]
                
                author = "Unknown"
                cover_filename = None
                for rel in manga.get("relationships", []):
                    if rel["type"] == "author" and "attributes" in rel:
                        author = rel["attributes"].get("name", "Unknown")
                    elif rel["type"] == "cover_art" and "attributes" in rel:
                        cover_filename = rel["attributes"].get("fileName")
                
                cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{cover_filename}" if cover_filename else None
                return {
                    "manga_id": manga_id, "title": title, "author": author, 
                    "description": description[:300] + "...", "cover_url": cover_url, 
                    "status": attrs.get("status", "N/A").capitalize(), "year": attrs.get("year", "N/A"),
                    "engine": "mdex"
                }
        except:
            pass
    return None

async def fetch_comick_profile(clean_title: str):
    # Spoof full browser environment headers to secure ComicK endpoint bypass
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://comick.io/",
        "Accept": "application/json"
    }
    try:
        # Use loop.run_in_executor to handle the synchronous curl_cffi requests safely in async thread
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None, 
            lambda: requests.get(f"https://api.comick.fun/v1.0/search?q={clean_title}&limit=1", headers=headers, timeout=8.0)
        )
        c_data = res.json()
        if c_data:
            manga = c_data[0]
            manga_id = manga.get("hid") 
            if manga_id:
                title = manga.get("title", "Unknown Title")
                desc = manga.get("desc", "No description available.")
                md_covers = manga.get("md_covers", [])
                cover_url = f"https://meo.comick.pictures/{md_covers[0]['b2key']}" if md_covers else None
                
                return {
                    "manga_id": manga_id, "title": title, "author": "Various",
                    "description": desc[:300] + "...", "cover_url": cover_url,
                    "status": "Ongoing", "year": manga.get("year", "N/A"),
                    "engine": "comick"
                }
    except:
        pass
    return None

async def get_all_chapters(manga_id: str, engine: str):
    chapters = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://comick.io/",
        "Accept": "application/json"
    }
    
    if engine == "comick":
        try:
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None,
                lambda: requests.get(f"https://api.comick.fun/comic/{manga_id}/chapters?lang=en&limit=200", headers=headers, timeout=10.0)
            )
            data = res.json().get("chapters", [])
            for ch in data:
                ch_num = ch.get("chap")
                if ch_num:
                    chapters.append({"id": ch.get("hid"), "num": ch_num})
            return sorted(chapters, key=lambda x: float(x["num"]) if x["num"].replace('.','',1).isdigit() else 0)
        except:
            return []
            
    # Standard MangaDex Feed Engine
    offset = 0
    limit = 100
    async with httpx.AsyncClient(headers={"User-Agent": "MMMWCBot/1.0"}) as client:
        while True:
            params = {"translatedLanguage[]": ["en"], "order[chapter]": "asc", "limit": limit, "offset": offset}
            res = await client.get(f"{BASE_URL}/manga/{manga_id}/feed", params=params)
            feed_data = res.json().get("data", [])
            if not feed_data: break
            for ch in feed_data:
                ch_num = ch["attributes"].get("chapter")
                if ch_num: chapters.append({"id": ch["id"], "num": ch_num})
            if len(feed_data) < limit: break
            offset += limit

    seen = set()
    unique_chapters = []
    for c in chapters:
        if c["num"] not in seen:
            seen.add(c["num"])
            unique_chapters.append(c)
    return sorted(unique_chapters, key=lambda x: float(x["num"]) if x["num"].replace('.','',1).isdigit() else 0)

def build_premium_keyboard(chapters, offset, manga_id, title_hint, current_format="PDF", engine="mdex"):
    limit = 6
    chunk = chapters[offset:offset+limit]
    menu = []
    
    row = []
    for idx, ch in enumerate(chunk):
        row.append(Button.inline(f"📖 Ch. {ch['num']}", data=f"dl_{ch['id']}_{ch['num']}_{manga_id}_{engine}"))
        if len(row) == 2 or idx == len(chunk) - 1:
            menu.append(row)
            row = []
            
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(Button.inline("⬅️ Previous", data=f"page_{manga_id}_{offset-limit}_{engine}"))
    if offset + limit < len(chapters):
        nav_buttons.append(Button.inline("Next ➡️", data=f"page_{manga_id}_{offset+limit}_{engine}"))
    if nav_buttons:
        menu.append(nav_buttons)
        
    last_ch = chapters[-1]["num"] if chapters else "0"
    menu.append([
        Button.inline("📦 Bulk Download", data=f"bulk_{manga_id}_{last_ch}_{engine}"),
        Button.inline(f"⚙️ Format: {current_format}", data=f"fmt_{manga_id}_{offset}_{engine}")
    ])
    return menu

# ==========================================
# 4. USER ACTION INTERACTION LISTENERS
# ==========================================
@bot.on(events.NewMessage(pattern='/start'))
async def start_cmd(event):
    await event.reply(
        "👋 **Welcome to MMMWC Premium Manga Space!**\n\n"
        "🔍 To look up a title, use the search command:\n"
        "👉 `/search [manga name]` (e.g. `/search The Broken Ring`)\n\n"
        "⚙️ `/settings` - Swap global file compilation targets\n"
        "⏳ `/queue` - Track heavy file rendering tasks."
    )

# Strict search command router
@bot.on(events.NewMessage)
async def strict_search_router(event):
    text = event.text.strip()
    
    # Let standard operational commands through untouched
    if text.startswith('/start') or text.startswith('/settings') or text.startswith('/queue'):
        return
        
    # Block raw message queries if they don't explicitly pass the command validation
    if not text.startswith('/search'):
        await event.reply("⚠️ **Access Blocked:** Raw chat strings disabled. Please use `/search [title]` directly.")
        return
        
    query = text.replace('/search', '').strip()
    if not query:
        await event.reply("❌ Please provide a target manga name! Example: `/search Solo Leveling`")
        return

    status_msg = await event.reply("⚡ Running simultaneous dual-engine index search...")
    clean_title = query.replace('"', '').replace("'", "").strip()
    
    # FIRES CORES TO BOTH DATABASES SIMULTANEOUSLY!
    md_task = fetch_mangadex_profile(clean_title)
    comick_task = fetch_comick_profile(clean_title)
    
    md_info, comick_info = await asyncio.gather(md_task, comick_task)
    
    # Priority selection or fallback grouping
    info = md_info if md_info else comick_info
    
    if not info:
        await status_msg.edit("❌ Title not found across MangaDex or ComicK index channels.")
        return
        
    chapters = await get_all_chapters(info["manga_id"], info["engine"])
    if not chapters:
        await status_msg.edit(f"❌ Found '{info['title']}' on {info['engine'].upper()}, but no active English chapters could load.")
        return
        
    caption = (
        f"📖 **{info['title']}**\n"
        f"✍️ **Author:** {info['author']} | 📅 **Year:** {info['year']}\n"
        f"🟢 **Status:** {info['status']} | 🛠️ **Active Engine Source:** `{info['engine'].upper()}`\n\n"
        f"📋 **Description:**\n{info['description']}\n\n"
        f"🌟 **Active Pipeline Preference:** `{get_user_format(event.chat_id)}`"
    )
    
    fmt = get_user_format(event.chat_id)
    buttons = build_premium_keyboard(chapters, 0, info["manga_id"], info["title"], current_format=fmt, engine=info["engine"])
    
    if info["cover_url"]:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            if info["engine"] == "comick":
                res = requests.get(info["cover_url"], headers=headers)
            else:
                async with httpx.AsyncClient() as c: res = await c.get(info["cover_url"])
                
            if res.status_code == 200:
                with open("temp_cov.jpg", "wb") as f: f.write(res.content)
                await status_msg.delete()
                await bot.send_file(event.chat_id, "temp_cov.jpg", caption=caption, buttons=buttons)
                os.remove("temp_cov.jpg")
                return
        except: pass
            
    await status_msg.delete()
    await bot.send_message(event.chat_id, caption, buttons=buttons)

@bot.on(events.NewMessage(pattern='/settings'))
async def settings_cmd(event):
    current_fmt = get_user_format(event.chat_id)
    btn = [[Button.inline(f"🔄 Switch to {'CBZ' if current_fmt == 'PDF' else 'PDF'}", data="global_fmt_toggle")]]
    await event.reply(f"⚙️ **Preferences Manager**\n\nCurrent Output Target: **{current_fmt}**", buttons=btn)

@bot.on(events.NewMessage(pattern='/queue'))
async def queue_cmd(event):
    if not download_queue:
        await event.reply("🟢 **Task Queue Empty:** The rendering blocks are idle!")
    else:
        msg = "⏳ **System Compilation Queue:**\n\n"
        for idx, task in enumerate(download_queue, start=1):
            msg += f"`#{idx}` - User `{task['user']}`: Ch. {task['chapter']}\n"
        await event.reply(msg)

# ==========================================
# 5. DUAL-DOWNLOAD RENDERING WORKER PIPELINE
# ==========================================
@bot.on(events.CallbackQuery)
async def global_callback_router(event):
    data = event.data.decode('utf-8')
    c_id = event.chat_id
    
    if data == "global_fmt_toggle":
        new_f = toggle_user_format(c_id)
        btn = [[Button.inline(f"🔄 Switch to {'CBZ' if new_f == 'PDF' else 'PDF'}", data="global_fmt_toggle")]]
        await event.edit(f"⚙️ **Updated!** New compilation target set to: **{new_f}**", buttons=btn)
        return

    parts = data.split("_")
    prefix = parts[0]
    
    if prefix == "fmt":
        m_id, offset, engine = parts[1], int(parts[2]), parts[3]
        new_f = toggle_user_format(c_id)
        chapters = await get_all_chapters(m_id, engine)
        buttons = build_premium_keyboard(chapters, offset, m_id, "Manga", current_format=new_f, engine=engine)
        await event.edit(buttons=buttons)
        
    elif prefix == "page":
        m_id, offset, engine = parts[1], int(parts[2]), parts[3]
        chapters = await get_all_chapters(m_id, engine)
        fmt = get_user_format(c_id)
        buttons = build_premium_keyboard(chapters, offset, m_id, "Manga", current_format=fmt, engine=engine)
        await event.edit(buttons=buttons)
        
    elif prefix == "dl":
        ch_id, ch_num, m_id, engine = parts[1], parts[2], parts[3], parts[4]
        user_format = get_user_format(c_id)
        
        task_record = {"user": c_id, "chapter": ch_num}
        download_queue.append(task_record)
        
        prog = await event.respond(f"⏳ [`Queue: {len(download_queue)}`] Extracting image array structures from {engine.upper()}...")
        folder = f"ch_{ch_id}_{c_id}"
        if not os.path.exists(folder): os.makedirs(folder)
        
        try:
            image_urls = []
            
            # --- COMICK DOWNLOAD LOGIC ROUTE ---
            if engine == "comick":
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://comick.io/"
                }
                loop = asyncio.get_event_loop()
                res = await loop.run_in_executor(None, lambda: requests.get(f"https://api.comick.fun/chapter/{ch_id}", headers=headers))
                chapter_data = res.json().get("chapter", {})
                md_images = chapter_data.get("md_images", [])
                for img in md_images:
                    if img.get("b2key"):
                        image_urls.append(f"https://meo.comick.pictures/{img['b2key']}")
            
            # --- MANGADEX DOWNLOAD LOGIC ROUTE ---
            else:
                async with httpx.AsyncClient(headers={"User-Agent": "MMMWCBot/1.0"}) as client:
                    res = await client.get(f"{BASE_URL}/at-home/server/{ch_id}")
                    d = res.json()
                    base, hash_id, files = d.get("baseUrl"), d.get("chapter", {}).get("hash"), d.get("chapter", {}).get("data", [])
                    for f in files:
                        image_urls.append(f"{base}/data/{hash_id}/{f}")

            if not image_urls:
                await prog.edit("❌ Failed to pull the page allocation map for this entry.")
                shutil.rmtree(folder)
                download_queue.remove(task_record)
                return

            # Parallel Page Assembly Core Engine
            await prog.edit(f"📥 Downloading {len(image_urls)} page assets into cloud cache layers...")
            
            async def download_page(url, path):
                # Apply custom bypass header structures onto direct asset download paths
                h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                async with httpx.AsyncClient(headers=h) as client:
                    r = await client.get(url, timeout=15.0)
                    if r.status_code == 200:
                        with open(path, "wb") as f: f.write(r.content)

            dl_tasks = []
            for idx, url in enumerate(image_urls):
                dl_tasks.append(download_page(url, os.path.join(folder, f"{idx+1:03d}.jpg")))
            await asyncio.gather(*dl_tasks)
            
            # Formatting Compilation Pipeline
            output_filename = f"Ch_{ch_num}.{user_format.lower()}"
            img_paths = sorted([os.path.join(folder, fl) for fl in os.listdir(folder) if fl.endswith(".jpg")])
            
            if img_paths:
                await prog.edit(f"📦 Compiling page vectors directly into target `{user_format}` bundle...")
                if user_format == "PDF":
                    imgs = [Image.open(p) for p in img_paths]
                    rgb = [im.convert('RGB') for im in imgs]
                    rgb[0].save(output_filename, save_all=True, append_images=rgb[1:])
                else:
                    import zipfile
                    with zipfile.ZipFile(output_filename, 'w') as cbz:
                        for img_p in img_paths:
                            cbz.write(img_p, os.path.basename(img_p))
                            
                await prog.edit("🚀 Delivering data payload to Telegram chat...")
                await bot.send_file(event.chat_id, output_filename, caption=f"✅ **Chapter {ch_num} Download Complete.**")
            
        except Exception as err:
            await prog.edit(f"❌ Core runtime pipeline exception: {err}")
            
        if os.path.exists(folder): shutil.rmtree(folder)
        if os.path.exists(output_filename): os.remove(output_filename)
        await prog.delete()
        if task_record in download_queue: download_queue.remove(task_record)

# ==========================================
# 6. LIFECYCLE MANAGEMENT SUB-ROUTINES
# ==========================================
def run_dummy_server():
    import http.server, socketserver, threading
    PORT = int(os.environ.get("PORT", 10000))
    def server_thread():
        with socketserver.TCPServer(("0.0.0.0", PORT), http.server.SimpleHTTPRequestHandler) as httpd:
            httpd.serve_forever()
    threading.Thread(target=server_thread, daemon=True).start()

if __name__ == "__main__":
    run_dummy_server()
    print("🤖 Premium Dual-Engine Manga Space Online!")
    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()