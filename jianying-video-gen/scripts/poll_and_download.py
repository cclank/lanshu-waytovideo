import asyncio
import json
import os
import html
import argparse
import subprocess
import re
from playwright.async_api import async_playwright

def load_and_clean_cookies(cookies_file):
    with open(cookies_file, 'r') as f:
        raw = json.load(f)
    cleaned = []
    allowed = ['name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure']
    for c in raw:
        clean = {}
        for key in allowed:
            if key == 'expires':
                val = c.get('expirationDate') or c.get('expires')
                if val is not None:
                    clean['expires'] = val
                continue
            if key in c and c[key] is not None:
                clean[key] = c[key]
        cleaned.append(clean)
    return cleaned

async def poll_thread(thread_id, cookies_file, output_dir):
    print(f"🕵️ Polling status for thread: {thread_id}")
    detail_url = f"https://xyq.jianying.com/home?tab_name=integrated-agent&thread_id={thread_id}"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        
        cookies = load_and_clean_cookies(cookies_file)
        await context.add_cookies(cookies)
        
        page = await context.new_page()
        print(f"🔗 Navigating to: {detail_url}")
        await page.goto(detail_url, wait_until='domcontentloaded')
        await page.wait_for_timeout(5000)
        
        mp4_url = None
        for i in range(10): # Just poll 10 times (50s) to check if it's already done
            mp4_url = await page.evaluate('''() => {
                const v = document.querySelector('video');
                if (v && v.src && v.src.includes('.mp4')) return v.src;
                const s = document.querySelector('video source');
                if (s && s.src && s.src.includes('.mp4')) return s.src;
                const html = document.documentElement.innerHTML;
                const m = html.match(/https?:\/\/[^"'\\s\\\\]+\.mp4[^"'\\s\\\\]*/);
                return m ? m[0] : null;
            }''')
            
            if mp4_url:
                mp4_url = html.unescape(mp4_url)
                print(f"✅ Found MP4: {mp4_url[:100]}...")
                break
            
            print(".", end="", flush=True)
            await page.wait_for_timeout(5000)
            if i % 3 == 0:
                await page.reload(wait_until='domcontentloaded')
                await page.wait_for_timeout(3000)

        if mp4_url:
            filename = f"re-downloaded_{thread_id}.mp4"
            filepath = os.path.join(output_dir, filename)
            print(f"📥 Downloading to {filepath}...")
            result = subprocess.run(
                ['curl', '-L', '-o', filepath, '-s', '-w', '%{http_code}', mp4_url],
                capture_output=True, text=True, timeout=120
            )
            if os.path.exists(filepath) and os.path.getsize(filepath) > 10000:
                print(f"✨ Success: {filepath}")
                return filepath
            else:
                print(f"❌ Download failed (HTTP {result.stdout.strip()})")
        else:
            print("\n❌ Video still not ready or not found.")
        
        await browser.close()
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread-id", type=str, required=True)
    parser.add_argument("--cookies", type=str, default="cookies.json")
    parser.add_argument("--output-dir", type=str, default=".")
    args = parser.parse_args()
    asyncio.run(poll_thread(args.thread_id, args.cookies, args.output_dir))
