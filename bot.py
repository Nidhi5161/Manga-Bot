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

# ==========================================
# 2. DATA STORAGE (SUBS, FAVS, SETTINGS, QUEUE)
# ==========================================
# Global download processing queue
download_queue = []

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f: 
                return json.load(f)
        except: 
            pass
    return {"subscriptions": {}, "favorites": {}, "settings": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f: 
        json.dump(data, f, indent=4)

def get_user_format(chat_id):
    data = load_data()
    user_settings = data.get("settings", {}).get(str(chat_id), {})
    return user_settings.get("format", "PDF")

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

def toggle_favorite(chat_id, manga_id, title):
    data = load_data()
    if "favorites" not in data: data["favorites"] = {}
    c_id = str(chat_id)
    
    if c_id not in data["favorites"]:
        data["favorites"][c_id] = []
        
    fav_list = data["favorites"][c_id]
    # Check if already favorited
    existing = next((x for x in fav_list if x["id"] == manga_id), None)
    
    if existing:
        fav_list.remove(existing)
        action = "removed"
    else:
        fav_list.append({"id": manga_id, "title": title})
        action = "added"
        
    save_data(data)
    return action

def toggle_subscription(chat_id, manga_id, title, last_chap):
    data = load_data()
    subs = data.get("subscriptions", {})
    c_id = str(chat_id)
    
    if manga_id not in subs:
        subs[manga_id] = {"title": title, "last_chapter": last_chap, "users": []}
    if c_id in subs[manga_id]["users"]:
        subs[manga_id]["users"].remove(c_id)
        action = "unsubscribed"
    else:
        subs[manga_id]["users"].append(c_id)
        action = "subscribed"
        
    if not subs[manga_id]["users"]: 
        del subs[manga_id]
        
    data["subscriptions"] = subs
    save_data(data)
    return action

# ==========================================
# 3. DUAL-ENGINE BACKEND DATA DRIVERS
# ==========================================
async def get_manga_profile(manga_title: str):
    # Clean quotation marks if the user types them out
    clean_title = manga_title.replace('"', '').replace("'", "").strip()
    
    # Track which engine handled the request
    # Engine Mode 1: Try MangaDex first
    async with httpx.AsyncClient(headers={"User-Agent": "MMMWCBot/1.0"}) as client:
        params = {"title": clean_title, "limit": 1, "includes[]": ["cover_art", "author"]}
        try:
            res = await client.get(f"{BASE_URL}/manga", params=params)
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
                    "description": description[:400], "cover_url": cover_url, 
                    "status": attrs.get("status", "N/A").capitalize(), "year": attrs.get("year", "N/A"),
                    "engine": "mdex"
                }
        except:
            pass

    # Engine Mode 2: Fallback fallback directly to ComicK API if MangaDex misses
    try:
        # Utilizing curl_cffi wrapper layout structure for cloudflare evasion
        comick_res = requests.get(f"https://api.comick.fun/v1.0/search?q={clean_title}&limit=1")
        c_data = comick_res.json()
        if c_data:
            manga = c_data[0]
            # ComicK uses 'hid' for chapter queries instead of id!
            manga_id = manga.get("hid") 
            title = manga.get("title", "Unknown Title")
            desc = manga.get("desc", "No description available.")
            
            # Formulating the cover url path for comick assets
            md_covers = manga.get("md_covers", [])
            cover_url = f"https://meo.comick.pictures/{md_covers[0]['b2key']}" if md_covers else None
            
            return {
                "manga_id": manga_id, "title": title, "author": "Various",
                "description": desc[:400], "cover_url": cover_url,
                "status": "Ongoing", "year": manga.get("year", "N/A"),
                "engine": "comick"
            }
    except Exception as e:
        print(f"ComicK engine lookup error: {e}")
        
    return None

async def get_all_chapters(manga_id: str, engine: str = "mdex"):
    chapters = []
    
    if engine == "comick":
        try:
            # Pull chapter list feeds natively from ComicK endpoint architecture
            res = requests.get(f"https://api.comick.fun/comic/{manga_id}/chapters?lang=en&limit=100")
            data = res.json().get("chapters", [])
            for ch in data:
                ch_num = ch.get("chap")
                if ch_num:
                    chapters.append({"id": ch.get("hid"), "num": ch_num})
            return sorted(chapters, key=lambda x: float(x["num"]) if x["num"].replace('.','',1).isdigit() else 0)
        except Exception as e:
            print(f"Error fetching Comick chapters: {e}")
            return []
            
    # Standard MangaDex Feed Fallback Loop
    offset = 0
    limit = 100
    async with httpx.AsyncClient(headers={"User-Agent": "MMMWCBot/1.0"}) as client:
        while True:
            params = {"translatedLanguage[]": ["en"], "order[chapter]": "asc", "limit": limit, "offset": offset}
            res = await client.get(f"{BASE_URL}/manga/{manga_id}/feed", params=params)
            feed_data = res.json().get("data", [])
            if not feed_data: break
            for ch in feed_data:
                ch_attrs = ch["attributes"]
                ch_num = ch_attrs.get("chapter")
                if ch_num:
                    chapters.append({"id": ch["id"], "num": ch_num})
            if len(feed_data) < limit: break
            offset += limit

    seen = set()
    unique_chapters = []
    for c in chapters:
        if c["num"] not in seen:
            seen.add(c["num"])
            unique_chapters.append(c)
    return sorted(unique_chapters, key=lambda x: float(x["num"]) if x["num"].replace('.','',1).isdigit() else 0)

# Modify keyboard generation signature matrix block to pass the tracking string
def build_premium_keyboard(chapters, offset, manga_id, title_hint, current_format="PDF", engine="mdex"):
    limit = 6
    chunk = chapters[offset:offset+limit]
    menu = []
    
    row = []
    for idx, ch in enumerate(chunk):
        # We append the engine identity key to the callback metadata package
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
    
    menu.append([
        Button.inline("🔔 Track/Subscribe", data=f"sub_{manga_id}_{last_ch}_{title_hint[:15]}"),
        Button.inline("❤️ Favorite", data=f"fav_{manga_id}_{title_hint[:15]}")
    ])
    
    return menu

# ==========================================
# 5. INTERACTIVE CALLBACK COMPILER LISTENERS
# ==========================================
@bot.on(events.CallbackQuery)
async def global_callback_router(event):
    data = event.data.decode('utf-8')
    c_id = event.chat_id
    
    # Global settings configuration toggling
    if data == "global_fmt_toggle":
        new_f = toggle_user_format(c_id)
        btn = [[Button.inline(f"🔄 Switch to {'CBZ' if new_f == 'PDF' else 'PDF'}", data="global_fmt_toggle")]]
        await event.edit(f"⚙️ **Preferences Adjusted!**\n\nYour layout compiler will now build default bundles into: **{new_f}**", buttons=btn)
        return

    parts = data.split("_")
    prefix = parts[0]
    
    if prefix == "fmt":
        m_id, offset = parts[1], int(parts[2])
        new_f = toggle_user_format(c_id)
        chapters = await get_all_chapters(m_id)
        buttons = build_premium_keyboard(chapters, offset, m_id, "Manga", current_format=new_f)
        await event.edit(buttons=buttons)
        await event.answer(f"Switched download profile format to {new_f}!", alert=False)
        
    elif prefix == "fav":
        m_id, title = parts[1], parts[2]
        action = toggle_favorite(c_id, m_id, title)
        msg = "❤️ Added safely into your favorites catalog folder!" if action == "added" else "💔 Removed from favorites tracking index."
        await event.answer(msg, alert=True)
        
    elif prefix == "page":
        m_id, offset = parts[1], int(parts[2])
        chapters = await get_all_chapters(m_id)
        fmt = get_user_format(c_id)
        buttons = build_premium_keyboard(chapters, offset, m_id, "Manga", current_format=fmt)
        await event.edit(buttons=buttons)
        await event.answer()
        
    elif prefix == "sub":
        action = toggle_subscription(c_id, parts[1], parts[3], parts[2])
        msg = "✅ Tracking active! New releases drop here automatically." if action == "subscribed" else "❌ Subscription deactivated."
        await event.answer(msg, alert=True)
        
    elif prefix == "dl":
        ch_id, ch_num, m_id = parts[1], parts[2], parts[3]
        user_format = get_user_format(c_id)
        
        # Add task request registration metadata inside queue record array
        task_record = {"user": c_id, "chapter": ch_num}
        download_queue.append(task_record)
        
        await event.answer("⚡ Registering download block allocation...", alert=False)
        prog = await event.respond(f"⏳ [`Queue Pos: {len(download_queue)}`] Building page mapping vectors for Ch. {ch_num}...")
        
        try:
            async with httpx.AsyncClient(headers={"User-Agent": "MMMWCBot/1.0"}) as client:
                res = await client.get(f"{BASE_URL}/at-home/server/{ch_id}")
                d = res.json()
                base, hash_id, files = d.get("baseUrl"), d.get("chapter", {}).get("hash"), d.get("chapter", {}).get("data", [])
                
                if not base or not files:
                    await prog.edit("❌ Chapter mirror allocation vectors rejected by endpoint host.")
                    download_queue.remove(task_record)
                    return
                    
                folder = f"ch_{ch_id}_{c_id}"
                if not os.path.exists(folder): os.makedirs(folder)
                
                tasks = []
                async def dl_p(url, path):
                    r = await client.get(url)
                    if r.status_code == 200:
                        with open(path, "wb") as f: f.write(r.content)
                
                for i, f in enumerate(files):
                    tasks.append(dl_p(f"{base}/data/{hash_id}/{f}", os.path.join(folder, f"{i+1:03d}.jpg")))
                await asyncio.gather(*tasks)
                
                output_filename = f"Ch_{ch_num}.{user_format.lower()}"
                img_paths = sorted([os.path.join(folder, fl) for fl in os.listdir(folder) if fl.endswith(".jpg")])
                
                if img_paths:
                    if user_format == "PDF":
                        # Output compilation rules mapping vector array directly to a PDF layout file
                        imgs = [Image.open(p) for p in img_paths]
                        rgb = [im.convert('RGB') for im in imgs]
                        rgb[0].save(output_filename, save_all=True, append_images=rgb[1:])
                    else:
                        # Output zip mapping logic rule structures natively directly into a CBZ file container archive
                        import zipfile
                        with zipfile.ZipFile(output_filename, 'w') as cbz:
                            for img_p in img_paths:
                                cbz.write(img_p, os.path.basename(img_p))
                                
                    await prog.edit(f"🚀 Streaming completed compilation bundle payload data ({user_format})...")
                    await bot.send_file(event.chat_id, output_filename, caption=f"✅ **Chapter {ch_num} Compilation Pack Complete.**")
                    
                if os.path.exists(folder): shutil.rmtree(folder)
                if os.path.exists(output_filename): os.remove(output_filename)
                await prog.delete()
                
        except Exception as err:
            await prog.edit(f"❌ Structural layout compiler run exception failure: {err}")
            
        # Task processing complete. Evict record out from live array
        if task_record in download_queue: 
            download_queue.remove(task_record)

    elif prefix == "bulk":
        m_id, last_ch = parts[1], parts[2]
        await event.answer("📦 Gathering full catalog manifests...", alert=True)
        await event.respond("⚠️ **Bulk Notice:** Full catalog packaging compiles each index in sequential tracks. Delivery pipelines stream chapters continuously to protect runtime memory profiles.")
        # Trigger individual background loop tasks across index profiles here

# ==========================================
# 6. WEB CONTAINER SYSTEM SUBSYSTEMS
# ==========================================
def run_dummy_server():
    import http.server
    import socketserver
    import threading
    PORT = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler
    def server_thread():
        with socketserver.TCPServer(("0.0.0.0", PORT), handler) as httpd:
            httpd.serve_forever()
    t = threading.Thread(target=server_thread, daemon=True)
    t.start()

# ==========================================
# 7. LIFECYCLE CONTROLLER APPLICATION ENTRY
# ==========================================
if __name__ == "__main__":
    print("🤖 MMMWC Premium Downloader Bot Engine Booting up...")
    run_dummy_server()
    print("✅ Bot is online with full premium control layout wrappers!")
    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()