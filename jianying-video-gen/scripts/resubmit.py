#!/usr/bin/env python3
"""重新提交 Seedance 视频并可靠地捕获 thread_id"""
import asyncio, json, os, re, sys
from playwright.async_api import async_playwright

OUTPUT_DIR = '/root/.openclaw/workspace/video_output'
REF_IMAGE = '/root/.openclaw/media/inbound/file_26---513d53c7-fbbe-4097-af52-3561d49ca170.jpg'
PROMPT = "变身特效，画面从@图片1的休闲牛仔夹克造型开始，光影闪烁中衣服逐渐变为酒红色丝绸吊带晚礼服，妆容变得精致浓艳，眼神妩媚迷人，散发妖媚气质，电影级光影，慢动作变身特效，强烈的反差感"

async def main():
    with open('cookies.json') as f:
        cookies_data = json.load(f)
    
    valid_same_site = {'Strict', 'Lax', 'None'}
    filtered_cookies = []
    for c in cookies_data:
        cookie = dict(c)
        if 'sameSite' in cookie and cookie['sameSite'] not in valid_same_site:
            cookie['sameSite'] = 'Lax'
        filtered_cookies.append(cookie)
    
    thread_id = None
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(filtered_cookies)
        page = await context.new_page()
        
        # Intercept ALL responses to find thread_id
        async def handle_response(response):
            nonlocal thread_id
            if thread_id:
                return
            try:
                text = await response.text()
            except:
                return
            m = re.search(r'"thread_id"\s*:\s*"([0-9a-f-]{36})"', text)
            if m:
                thread_id = m.group(1)
                print(f"🎯 Found thread_id in response: {thread_id}")
        
        page.on('response', handle_response)
        
        print("🔑 Injecting cookies...")
        await page.goto("https://xyq.jianying.com/home?tab_name=home&source=home&mode=create&feature=seedance2.0", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)
        print("✅ Logged in")
        
        # Select Seedance 2.0 mode
        print("🎬 Selecting Seedance 2.0...")
        seedance_card = page.locator('text=Seedance 2.0').first
        if await seedance_card.count() > 0:
            await seedance_card.click()
            await page.wait_for_timeout(2000)
        
        # Upload reference image
        print("🖼️ Uploading reference image...")
        file_input = page.locator('input[type="file"]').first
        if await file_input.count() > 0:
            await file_input.set_input_files(REF_IMAGE)
        else:
            # Try to find upload button
            upload_btn = page.locator('text=本地上传').first
            if await upload_btn.count() > 0:
                await upload_btn.click()
                await page.wait_for_timeout(1000)
                file_input = page.locator('input[type="file"]').first
                await file_input.set_input_files(REF_IMAGE)
        
        await page.wait_for_timeout(5000)
        print("✅ Image uploaded")
        
        # Select model
        print("🤖 Selecting model...")
        model_btn = page.locator('text=Seedance 2.0 Fast').first
        if await model_btn.count() == 0:
            model_btn = page.locator('text=Seedance 2.0').first
        if await model_btn.count() > 0:
            await model_btn.click()
            await page.wait_for_timeout(1000)
            # Select Seedance 2.0 (not Fast)
            model_option = page.locator('text=Seedance 2.0').nth(1)
            if await model_option.count() > 0:
                await model_option.click()
                await page.wait_for_timeout(1000)
        
        # Select duration and ratio
        print("⏱️ Selecting duration and ratio...")
        duration_btn = page.locator('text=5s').first
        if await duration_btn.count() > 0:
            await duration_btn.click()
            await page.wait_for_timeout(1000)
        
        ratio_btn = page.locator('text=横屏').first
        if await ratio_btn.count() > 0:
            await ratio_btn.click()
            await page.wait_for_timeout(1000)
        
        # Enter prompt
        print("📝 Entering prompt...")
        prompt_input = page.locator('textarea').first
        if await prompt_input.count() > 0:
            await prompt_input.fill(PROMPT)
            await page.wait_for_timeout(1000)
        
        # Click send
        print("🖱️ Clicking send...")
        submit_btn = page.locator('button:has(svg.lucide-arrow-up)').first
        if await submit_btn.count() == 0:
            submit_btn = page.locator('button').filter(has_text="发送").first
        if await submit_btn.count() > 0:
            await submit_btn.click()
            print("✅ Submitted!")
        else:
            print("❌ Could not find send button")
            await page.screenshot(path='submit_debug.png')
            await browser.close()
            return
        
        # Wait for thread_id
        print("⏳ Waiting for thread_id (up to 30s)...")
        for i in range(15):
            await page.wait_for_timeout(2000)
            if thread_id:
                break
            # Check URL
            current_url = page.url
            m = re.search(r'thread_id=([0-9a-f-]{36})', current_url)
            if m:
                thread_id = m.group(1)
                print(f"🎯 Found thread_id in URL: {thread_id}")
                break
        
        if not thread_id:
            # Check page content
            html = await page.content()
            m = re.search(r'thread_id["\s:=]+([0-9a-f-]{36})', html)
            if m:
                thread_id = m.group(1)
                print(f"🎯 Found thread_id in HTML: {thread_id}")
        
        if not thread_id:
            print(f"❌ Could not get thread_id. Current URL: {page.url}")
            await page.screenshot(path='submit_failed2.png')
        
        await browser.close()
    
    return thread_id

if __name__ == '__main__':
    tid = asyncio.run(main())
    if tid:
        print(f"\n✅ thread_id: {tid}")
    else:
        print("\n❌ Failed to get thread_id")
        sys.exit(1)
