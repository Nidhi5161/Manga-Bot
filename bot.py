import asyncio
import os
import json
import shutil
import httpx
from PIL import Image
from telethon import TelegramClient, events, Button

# Import the cracked bypass engine components
from comick_engine import search_comick, get_comick_chapters, get_chapter_pages # type: ignore

# ==========================================
# 1. TELEGRAM API CREDENTIALS & INITIALIZATION
# ==========================================
API_ID = 33777175     
API_HASH = "39385c53937e92e13e6b1f9477a531c3"  
BOT_TOKEN = "8982142957:AAGJf8YSnst9rvpEpFbGQysGn7Q48Zk1LBk"

bot = TelegramClient('mmmwc_bot_session', API_ID, API_HASH)

BASE_URL = "https://api.mangadex.org"
SUBS_FILE = "subscriptions.json"

# ==========================================
# 2. LOCAL DATA STORAGE DATABASE HELPERS
# ==========================================
def load_subs():
    if os.path.exists(SUBS_FILE):
        try:
            with open(SUBS_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_subs(subs):
    with open(SUBS_FILE, "w") as f: json.dump(subs, f, indent=4)

def toggle_subscription(chat_id, manga_id, title, last_chap):
    subs = load_subs()
    c_id = str(chat_id)
    if manga_id not in subs:
        subs[manga_id] = {"title": title, "last_chapter": last_chap, "users": []}
    if c_id in subs[manga_id]["users"]:
        subs[manga_id]["users"].remove(c_id)
        action = "unsubscribed"
    else:
        subs[manga_id]["users"].append(c_id)
        action = "subscribed"
    if not subs[manga_id]["users"]: del subs[manga_id]
    save_subs(subs)
    return action

# ==========================================
# 3. MANGADEX BACKEND DATA ENGINES
# ==========================================
async def get_manga_profile(manga_title: str):
    async with httpx.AsyncClient(headers={"User-Agent": "MMMWCBot/1.0"}) as client:
        params = {"title": manga_title, "limit": 1, "includes[]": ["cover_art", "author"]}
        res = await client.get(f"{BASE_URL}/manga", params=params)
        data = res.json().get("data", [])
        if not data: return None
        
        manga = data[0]
        manga_id = manga["id"]
        attrs = manga["attributes"]
        
        title = attrs["title"].get("en", "Unknown Title")
        description = attrs["description"].get("en", "No description available.").split("\n")[0]
        if len(description) > 400: description = description[:400] + "..."
        
        author = "Unknown"
        cover_filename = None
        for rel in manga.get("relationships", []):
            if rel["type"] == "author" and "attributes" in rel:
                author = rel["attributes"].get("name", "Unknown")
            elif rel["type"] == "cover_art" and "attributes" in rel:
                cover_filename = rel["attributes"].get("fileName")
                
        cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{cover_filename}" if cover_filename else None
        return {"manga_id": manga_id, "title": title, "author": author, "description": description, "cover_url": cover_url, "status": attrs.get("status", "N/A"), "year": attrs.get("year", "N/A")}

async def get_all_chapters(manga_id: str):
    """Fetches all English chapters, removes duplicates, and sorts them numerically."""
    chapters = []
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

def build_chapter_keyboard(chapters, offset, manga_id, title_hint):
    """Creates a clean paginated grid list layout of chapters."""
    limit = 8
    chunk = chapters[offset:offset+limit]
    
    menu = []
    row = []
    for idx, ch in enumerate(chunk):
        row.append(Button.inline(f"📖 Ch. {ch['num']}", data=f"dl_{ch['id']}_{ch['num']}_{manga_id}"))
        if len(row) == 2 or idx == len(chunk) - 1:
            menu.append(row)
            row = []
            
    nav_buttons = []
    if offset > 0:
        nav_buttons.append(Button.inline("⬅️ Prev", data=f"page_{manga_id}_{offset-limit}"))
    if offset + limit < len(chapters):
        nav_buttons.append(Button.inline("Next ➡️", data=f"page_{manga_id}_{offset+limit}"))
    if nav_buttons:
        menu.append(nav_buttons)
        
    last_ch = chapters[-1]["num"] if chapters else "0"
    menu.append([Button.inline("🔔 Track/Subscribe", data=f"sub_{manga_id}_{last_ch}_{title_hint[:20]}")])
    return menu

# ==========================================
# 4. TELEGRAM GLOBAL BOT COMMANDS
# ==========================================
@bot.on(events.NewMessage(pattern='/start'))
async def start_cmd(event):
    await event.reply("👋 **Welcome to MMMWC Manga Space!**\nSend me any manga title to view its profile, browse chapters via MangaDex, or use `/search [title]` to download using the bypassed ComicK engine.")

# ==========================================
# 5. MANGADEX RUNTIME EVENT LISTENERS
# ==========================================
@bot.on(events.NewMessage)
async def manga_search_handler(event):
    if event.text.startswith('/'): return
    query = event.text
    status_msg = await event.reply("🔍 Searching MangaDex indices...")
    
    info = await get_manga_profile(query)
    if not info:
        await status_msg.edit("❌ Title not located.")
        return
    
    chapters = await get_all_chapters(info["manga_id"])
    if not chapters:
        await status_msg.edit("❌ No English chapters listed for this title.")
        return
        
    caption = f"📖 **{info['title']}**\n✍️ **Author:** {info['author']}\n📅 **Year:** {info['year']} | 🟢 **Status:** {info['status']}\n\n📋 **Description:**\n{info['description']}"
    buttons = build_chapter_keyboard(chapters, 0, info["manga_id"], info["title"])
    
    if info["cover_url"]:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(info["cover_url"])
                if res.status_code == 200:
                    with open("temp_cov.jpg", "wb") as f: f.write(res.content)
            await status_msg.delete()
            await bot.send_file(event.chat_id, "temp_cov.jpg", caption=caption, buttons=buttons)
            os.remove("temp_cov.jpg")
            return
        except: pass
        
    await status_msg.delete()
    await bot.send_message(event.chat_id, caption, buttons=buttons)

# Fixed: Explicitly intercepting MangaDex custom prefixes to avoid button crossover bugs
@bot.on(events.CallbackQuery(pattern=r'(page|sub|dl)_.+'))
async def md_callback_handler(event):
    data = event.data.decode('utf-8')
    parts = data.split("_")
    prefix = parts[0]
    
    if prefix == "page":
        manga_id, new_offset = parts[1], int(parts[2])
        chapters = await get_all_chapters(manga_id)
        buttons = build_chapter_keyboard(chapters, new_offset, manga_id, "Manga")
        await event.edit(buttons=buttons)
        await event.answer()
        
    elif prefix == "sub":
        action = toggle_subscription(event.chat_id, parts[1], parts[3], parts[2])
        msg = "✅ Tracking active! New releases drop here automatically." if action == "subscribed" else "❌ Subscription deactivated."
        await event.answer(msg, alert=True)
        
    elif prefix == "dl":
        await event.answer("⚡ Dispatching compilation builders...", alert=False)
        ch_id, ch_num, m_id = parts[1], parts[2], parts[3]
        prog = await event.respond(f"⏳ Constructing layout layers for Chapter {ch_num}...")
        
        async with httpx.AsyncClient(headers={"User-Agent": "MMMWCBot/1.0"}) as client:
            res = await client.get(f"{BASE_URL}/at-home/server/{ch_id}")
            d = res.json()
            base, hash_id, files = d.get("baseUrl"), d.get("chapter", {}).get("hash"), d.get("chapter", {}).get("data", [])
            
            if not base or not files:
                await prog.edit("❌ Chapter source mirror failed.")
                return
                
            folder = f"ch_{ch_id}"
            if not os.path.exists(folder): os.makedirs(folder)
            
            tasks = []
            async def dl_p(url, path):
                r = await client.get(url)
                if r.status_code == 200:
                    with open(path, "wb") as f: f.write(r.content)
            
            for i, f in enumerate(files):
                tasks.append(dl_p(f"{base}/data/{hash_id}/{f}", os.path.join(folder, f"{i+1:03d}.jpg")))
            await asyncio.gather(*tasks)
            
            pdf = f"Ch_{ch_num}.pdf"
            imgs = [Image.open(os.path.join(folder, fl)) for fl in sorted(os.listdir(folder)) if fl.endswith(".jpg")]
            if imgs:
                rgb = [im.convert('RGB') for im in imgs]
                rgb[0].save(pdf, save_all=True, append_images=rgb[1:])
                await prog.edit("🚀 Delivering artifact payload...")
                await bot.send_file(event.chat_id, pdf, caption=f"✅ Chapter {ch_num} compilation complete.")
                
            if os.path.exists(folder): shutil.rmtree(folder)
            if os.path.exists(pdf): os.remove(pdf)
            await prog.delete()

# ==========================================
# 6. COMICK CRACKED INTERFACE LISTENERS
# ==========================================
@bot.on(events.NewMessage(pattern=r'/search (?P<query>.+)'))
async def handle_comick_search(event):
    query = event.pattern_match.group('query')
    await event.respond("🔍 Searching the ComicK network database...")
    
    results = search_comick(query)
    if not results:
        await event.respond("❌ No manga found matching that title on ComicK. Try again!")
        return
        
    buttons = []
    for item in results:
        display_text = f"📖 {item['title'][:30]}"
        callback_data = f"ck_show:{item['slug']}"
        buttons.append([Button.inline(display_text, data=callback_data)])
        
    await event.respond("🎯 ComicK Database: Select the match you want to download:", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=r'ck_show:(?P<slug>.+)'))
async def handle_comick_manga_select(event):
    slug = event.pattern_match.group('slug').decode('utf-8')
    await event.answer("Fetching chapter list...", alert=False)
    
    chapters = get_comick_chapters(slug)
    if not chapters:
        await event.edit("❌ Failed to pull the chapter logs for this entry.")
        return
        
    buttons = []
    for ch in chapters:
        buttons.append([Button.inline(ch['display'], data=f"ck_dl:{ch['hid']}")])
        
    await event.edit("⚡ Select a ComicK chapter to download directly:", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=r'ck_dl:(?P<hid>.+)'))
async def handle_comick_download_trigger(event):
    hid = event.pattern_match.group('hid').decode('utf-8')
    
    await event.answer("📥 Fetching chapter pages...", alert=False)
    await event.edit("🚀 Bypassing Cloudflare CDN to pull image nodes...")
    
    page_urls = get_chapter_pages(hid)
    if not page_urls:
        await event.edit("❌ Failed to decrypt pages for this chapter. Try a different one!")
        return
        
    await event.edit(f"📦 Found {len(page_urls)} pages. Transferring to Telegram stream... Hang tight!")
    
    try:
        for idx, url in enumerate(page_urls, start=1):
            await bot.send_file(
                event.chat_id, 
                file=url, 
                caption=f"📄 Page {idx}/{len(page_urls)}"
            )
        await event.respond("🎉 ComicK Chapter download complete! Enjoy reading!")
        
    except Exception as upload_err:
        print(f"⚠️ Upload error: {upload_err}")
        await event.respond("❌ An error occurred while streaming raw images to Telegram.")

# ==========================================
# 7. WEB CONTAINER SYSTEM SUBSYSTEMS
# ==========================================
def run_dummy_server():
    import http.server
    import socketserver
    import threading
    
    PORT = int(os.environ.get("PORT", 10000))
    handler = http.server.SimpleHTTPRequestHandler
    
    def server_thread():
        with socketserver.TCPServer(("0.0.0.0", PORT), handler) as httpd:
            print(f"🌍 Dummy web server successfully bound to network interface 0.0.0.0:{PORT}")
            httpd.serve_forever()
            
    t = threading.Thread(target=server_thread, daemon=True)
    t.start()

# ==========================================
# 8. LIFECYCLE CONTROLLER APPLICATION ENTRYS
# ==========================================
if __name__ == "__main__":
    print("🤖 MMMWC Downloader Bot is firing up...")
    
    # Start the local environment network loop for Render's health scans
    run_dummy_server()
    
    # Initialize the main listening loop
    print("✅ Bot is online and listening for messages on Telethon wrapper!")
    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()