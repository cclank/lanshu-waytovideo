#!/usr/bin/env python3
"""查找剪映 Seedance 最新任务的 thread_id - 通过导航详情页查找"""
import asyncio, json, sys, re
from playwright.async_api import async_playwright

async def main():
    with open('cookies.json') as f:
        cookies_data = json.load(f)
    
    # Filter cookies
    valid_same_site = {'Strict', 'Lax', 'None'}
    filtered_cookies = []
    for c in cookies_data:
        cookie = dict(c)
        if 'sameSite' in cookie and cookie['sameSite'] not in valid_same_site:
            cookie['sameSite'] = 'Lax'
        filtered_cookies.append(cookie)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(filtered_cookies)
        page = await context.new_page()
        
        # Listen to all responses
        responses = []
        def handle_response(response):
            responses.append({
                'url': response.url,
                'status': response.status
            })
        page.on('response', handle_response)
        
        print("🔍 Navigating to Seedance integrated agent...")
        await page.goto("https://xyq.jianying.com/home?tab_name=integrated-agent&agent_name=pippit_video_part_agent", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        
        print(f"Captured {len(responses)} responses")
        
        # Check localStorage and sessionStorage
        local_storage = await page.evaluate("() => JSON.stringify(localStorage)")
        session_storage = await page.evaluate("() => JSON.stringify(sessionStorage)")
        
        # Search for thread_id in storage
        for storage_name, storage_data in [('localStorage', local_storage), ('sessionStorage', session_storage)]:
            m = re.search(r'"thread_id"\s*:\s*"([0-9a-f-]{36})"', storage_data)
            if m:
                print(f"🎯 Found thread_id in {storage_name}: {m.group(1)}")
                await browser.close()
                return m.group(1)
            
            # Also search for any UUID-like patterns
            uuids = re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', storage_data)
            if uuids:
                unique_uuids = list(set(uuids))
                print(f"Found {len(unique_uuids)} unique UUIDs in {storage_name}: {unique_uuids[:5]}")
        
        # Check window.__INITIAL_STATE__ or similar global variables
        initial_state = await page.evaluate("""() => {
            if (window.__INITIAL_STATE__) return JSON.stringify(window.__INITIAL_STATE__);
            if (window.__NUXT__) return JSON.stringify(window.__NUXT__);
            if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__);
            return null;
        }""")
        
        if initial_state:
            m = re.search(r'"thread_id"\s*:\s*"([0-9a-f-]{36})"', initial_state)
            if m:
                print(f"🎯 Found thread_id in window state: {m.group(1)}")
                await browser.close()
                return m.group(1)
        
        # Look at the page structure - find clickable task items
        print("\n🔍 Looking for task list items...")
        
        # Try to find any element that looks like a task card and click the first one
        # The task list is usually in a sidebar
        task_items = await page.evaluate("""() => {
            // Look for elements that might be task items
            const selectors = [
                '[class*="task"]',
                '[class*="card"]',
                '[class*="item"]',
                '[class*="list-item"]',
                'a[href*="thread"]',
                '[role="listitem"]'
            ];
            let items = [];
            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    if (el.textContent.trim().length > 3 && !items.includes(el)) {
                        items.push({
                            text: el.textContent.trim().substring(0, 50),
                            href: el.href || el.getAttribute('href') || '',
                            class: el.className.substring(0, 50)
                        });
                    }
                }
            }
            return items.slice(0, 10);
        }""")
        
        print(f"Found {len(task_items)} task items:")
        for item in task_items:
            print(f"  - {item['text'][:40]}... href: {item['href'][:60] if item['href'] else 'none'}")
        
        # Look for href with thread_id
        for item in task_items:
            if item['href']:
                m = re.search(r'thread_id=([0-9a-f-]{36})', item['href'])
                if m:
                    print(f"\n🎯 Found thread_id in task href: {m.group(1)}")
                    await browser.close()
                    return m.group(1)
        
        # Save debug screenshot
        await page.screenshot(path='debug_tasks2.png', full_page=True)
        print("📸 Saved debug screenshot")
        
        await browser.close()
        return None

if __name__ == '__main__':
    tid = asyncio.run(main())
    if tid:
        print(f"\n✅ thread_id: {tid}")
    else:
        print("\n❌ Could not find thread_id")
        sys.exit(1)
