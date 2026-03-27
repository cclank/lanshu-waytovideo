"""
小云雀 (Jianying) 自动化视频生成 v5
引擎: Playwright + Chromium
支持: 文生视频 (T2V) + 参考视频生成 (V2V)
"""
import asyncio
import json
import re
import os
import html
import argparse
import subprocess
import shutil
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright

COOKIES_FILE = 'cookies.json'  # 可通过 --cookies 覆盖
DOWNLOAD_DIR = '.'  # 可通过 --output-dir 覆盖

def load_and_clean_cookies():
    with open(COOKIES_FILE, 'r') as f:
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

DEBUG_SCREENSHOTS = False  # 由 --dry-run 控制

EXTEND_BUTTON_PATTERNS = [
    "向后延伸",
    "后延伸",
    "延伸",
    "续写",
    "继续创作",
    "继续生成",
]

async def screenshot(page, name):
    if not DEBUG_SCREENSHOTS:
        return
    path = os.path.join(DOWNLOAD_DIR, f'step_{name}.png')
    await page.screenshot(path=path)
    print(f"  📸 Screenshot: {path}")

async def goto_with_retry(page, url: str, attempts: int = 3, wait_until: str = 'domcontentloaded'):
    last_error = None
    for idx in range(attempts):
        try:
            await page.goto(url, wait_until=wait_until)
            return True
        except Exception as e:
            last_error = e
            print(f"  ⚠️ 导航失败，第 {idx + 1}/{attempts} 次: {e}")
            if idx < attempts - 1:
                await page.wait_for_timeout(2500)
    if last_error:
        raise last_error
    return False

def extract_thread_id_from_text(text: str):
    try:
        data = json.loads(text)
        tid = None
        if isinstance(data, dict):
            tid = data.get('thread_id') or data.get('data', {}).get('thread_id')
            if not tid and 'data' in data:
                d = data['data']
                if isinstance(d, dict):
                    tid = d.get('thread_id')
                    for v in d.values():
                        if isinstance(v, dict) and 'thread_id' in v:
                            tid = v['thread_id']
                            break
        if tid:
            return tid
    except Exception:
        pass

    m = re.search(r'"thread_id"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    return None

async def submit_and_capture_thread(page, screenshot_name: str):
    thread_id = None

    async def sniff_thread(response):
        nonlocal thread_id
        if thread_id:
            return
        try:
            text = await response.text()
            if 'thread_id' not in text:
                return
            tid = extract_thread_id_from_text(text)
            if tid:
                thread_id = tid
                print(f"\n  🎯 Sniffed thread_id: {tid}")
        except Exception:
            pass

    page.on('response', sniff_thread)

    try:
        submit_clicked = await safe_click(
            page, page.locator('button:has(svg.lucide-arrow-up)').first, '发送(箭头)', timeout=5000
        )
        await page.wait_for_timeout(5000)
        await screenshot(page, screenshot_name)

        if not submit_clicked:
            print("  ❌ Submit failed. Aborting.")
            return None

        for _ in range(10):
            if thread_id:
                break
            await page.wait_for_timeout(2000)

        if not thread_id:
            print("  ⚠️ thread_id not captured from responses, trying page HTML...")
            page_html = await page.content()
            m = re.search(r'thread_id["\s:=]+([0-9a-f-]{36})', page_html)
            if m:
                thread_id = m.group(1)
                print(f"  🎯 Found thread_id in HTML: {thread_id}")

        if not thread_id:
            print("  ❌ Could not get thread_id. Aborting.")
            return None

        return thread_id
    finally:
        page.remove_listener('response', sniff_thread)

async def open_thread_and_download(page, thread_id: str, prompt: str, duration: str):
    detail_url = f"https://xyq.jianying.com/home?tab_name=integrated-agent&thread_id={thread_id}"
    print(f"🔗 Navigating to thread detail page...")
    print(f"  URL: {detail_url}")
    await goto_with_retry(page, detail_url)
    await page.wait_for_timeout(8000)

    safe_name = ''.join(c for c in prompt[:15] if c.isalnum() or c in '_ ')
    filename = f"{safe_name}_{duration}.mp4"
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    print("⏳ Polling for video on detail page...")
    mp4_url = None
    for i in range(240):
        await page.wait_for_timeout(5000)
        mp4_url = await page.evaluate(r'''() => {
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
            print(f"\n  🎉 Found MP4 at attempt {i+1}!")
            print(f"  🔗 {mp4_url[:120]}...")
            break

        if i % 12 == 0 and i > 0:
            print(f"  ⏳ Still generating... ({i*5}s elapsed)")
            await page.reload(wait_until='domcontentloaded')
            await page.wait_for_timeout(5000)
        print(".", end="", flush=True)

    if not mp4_url:
        print("\n  ❌ Timeout after 20 min")
        await screenshot(page, '9_timeout')
        return False

    await screenshot(page, '9_video_ready')

    print(f"📥 Downloading to {filepath}...")
    result = subprocess.run(
        ['curl', '-L', '-o', filepath, '-s', '-w', '%{http_code}', mp4_url],
        capture_output=True, text=True, timeout=120
    )
    http_code = result.stdout.strip()

    if os.path.exists(filepath) and os.path.getsize(filepath) > 10000:
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        print(f"  ✅ Saved: {os.path.abspath(filepath)} ({size_mb:.1f}MB) [HTTP {http_code}]")
        return True

    print(f"  ❌ Download failed: HTTP {http_code}")
    if result.stderr:
        print(f"  Error: {result.stderr[:200]}")
    print(f"  📋 Manual link: {mp4_url}")
    return False

async def check_and_resize_video(video_path: str) -> str:
    """检查视频分辨率，必要时缩放并补边到平台要求范围内。"""
    try:
        # 获取分辨率
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", video_path]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f"  ⚠️ 无法获取视频分辨率: {stderr.decode()}")
            return video_path
        
        dims = stdout.decode().strip().split('x')
        if len(dims) != 2:
            return video_path
        
        w, h = int(dims[0]), int(dims[1])
        print(f"  📊 原始视频分辨率: {w}x{h}")
        
        # 平台限制: 480p (640x640) - 720p (834x1112)
        # 我们以长边不超过 1112 为准进行等比例缩放
        max_dim = 1112
        min_dim = 480
        
        need_resize = max(w, h) > max_dim or min(w, h) < min_dim

        if need_resize:
            scale_ratio = min(1.0, max_dim / max(w, h)) if max(w, h) > max_dim else 1.0
            scaled_w = max(2, int(round(w * scale_ratio)))
            scaled_h = max(2, int(round(h * scale_ratio)))
            if scaled_w % 2 != 0:
                scaled_w -= 1
            if scaled_h % 2 != 0:
                scaled_h -= 1

            pad_w = max(scaled_w, min_dim)
            pad_h = max(scaled_h, min_dim)
            if pad_w % 2 != 0:
                pad_w += 1
            if pad_h % 2 != 0:
                pad_h += 1

            filter_parts = [f"scale={scaled_w}:{scaled_h}"]
            if pad_w != scaled_w or pad_h != scaled_h:
                pad_x = max((pad_w - scaled_w) // 2, 0)
                pad_y = max((pad_h - scaled_h) // 2, 0)
                filter_parts.append(f"pad={pad_w}:{pad_h}:{pad_x}:{pad_y}:black")

            filter_chain = ",".join(filter_parts)
            print(f"  🔧 视频将处理为 {scaled_w}x{scaled_h}，最终画布 {pad_w}x{pad_h}")

            temp_dir = tempfile.gettempdir()
            output_path = os.path.join(temp_dir, f"resized_{os.path.basename(video_path)}")
            
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", filter_chain, "-c:v", "libx264", "-crf", "23", "-preset", "fast", output_path]
            print(f"  🎬 执行缩放: {' '.join(ffmpeg_cmd)}")
            
            f_proc = await asyncio.create_subprocess_exec(*ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            await f_proc.communicate()
            
            if f_proc.returncode == 0:
                print(f"  ✅ 缩放完成: {output_path}")
                return output_path
            else:
                print(f"  ❌ 缩放失败，忽略并使用原文件")
                
    except Exception as e:
        print(f"  ⚠️ 预检查发生错误: {str(e)}")
        
    return video_path

async def safe_click(page, locator_or_selector, label, timeout=5000):
    """用 Playwright locator.click() 点击元素，模拟真实鼠标事件"""
    try:
        if isinstance(locator_or_selector, str):
            loc = page.locator(locator_or_selector).first
        else:
            loc = locator_or_selector
        await loc.click(timeout=timeout)
        print(f"  ✅ {label}: clicked")
        return True
    except Exception as e:
        print(f"  ❌ {label}: {e}")
        return False

async def open_reference_material_panel(page) -> bool:
    """打开 V2V 的参考素材面板，必须优先走工具栏里的“参考”按钮。"""
    selectors = [
        ('button:has-text("参考")', '参考'),
        ('button:has-text("素材")', '素材'),
        ('button[title="上传参考素材"]', '上传参考素材'),
    ]
    for selector, label in selectors:
        if await safe_click(page, page.locator(selector).first, f'{label}按钮', timeout=8000):
            return True

    fallback = await page.evaluate('''() => {
        const editable = document.querySelector('div[contenteditable="true"]');
        const root = editable ? (editable.closest('form') || editable.parentElement || document.body) : document.body;
        const buttons = Array.from(root.querySelectorAll('button'));
        const candidate = buttons.find(btn => {
            const title = (btn.getAttribute('title') || '').trim();
            const text = (btn.innerText || '').trim();
            return title.includes('上传参考素材') || text === '参考' || text === '素材';
        });
        if (!candidate) return 'NOT_FOUND';
        candidate.click();
        return 'OK_JS';
    }''')
    print(f"  参考素材面板兜底: {fallback}")
    return fallback.startswith('OK')

async def upload_reference_media(page, file_path: str, media_kind: str) -> bool:
    """
    上传参考素材。优先直连 input[type=file]，避免依赖“从本地上传”文案。
    media_kind: 'image' | 'video'
    """
    expect_token = 'video' if media_kind == 'video' else 'image'

    if media_kind == 'video':
        print("  ℹ️ V2V 强制走『参考 -> 从本地上传』入口")
        local_upload_texts = ['从本地上传', '本地上传']
        for text in local_upload_texts:
            try:
                async with page.expect_file_chooser(timeout=8000) as fc_info:
                    clicked = await page.evaluate('''([targetText]) => {
                        const all = Array.from(document.querySelectorAll('*'));
                        const candidates = all.filter(el => {
                            const text = (el.innerText || '').trim();
                            if (text !== targetText) return false;
                            const r = el.getBoundingClientRect();
                            return r.left > 350 && r.top > 250 && r.top < 900 && r.width > 20 && r.height > 10;
                        });
                        candidates.sort((a, b) => {
                            const ra = a.getBoundingClientRect();
                            const rb = b.getBoundingClientRect();
                            return (ra.top - rb.top) || (ra.left - rb.left);
                        });
                        const el = candidates[0];
                        if (!el) return 'NOT_FOUND';
                        el.click();
                        return 'CLICKED';
                    }''', [text])
                    print(f"  {text}入口: {clicked}")
                    if clicked != 'CLICKED':
                        continue

                chooser = await fc_info.value
                await chooser.set_files(file_path)
                print(f"  ✅ 通过参考面板本地上传成功: {text}")
                return True
            except Exception as e:
                print(f"  ⚠️ {text}入口失败: {e}")

    file_inputs = page.locator('input[type="file"]')
    input_count = await file_inputs.count()
    for idx in range(input_count):
        locator = file_inputs.nth(idx)
        try:
            accept = (await locator.get_attribute('accept')) or ''
            is_hidden = await locator.evaluate(
                '''el => {
                    const s = window.getComputedStyle(el);
                    return s.display === 'none' || s.visibility === 'hidden';
                }'''
            )
            if accept and expect_token not in accept.lower():
                continue
            await locator.set_input_files(file_path, timeout=10000)
            print(f"  ✅ 通过 file input 上传成功: index={idx}, accept={accept or '*/*'}, hidden={is_hidden}")
            return True
        except Exception as e:
            print(f"  ⚠️ file input[{idx}] 上传失败: {e}")

    print("  ℹ️ 未找到可直接写入的 file input，回退到 file chooser 流程")
    candidate_texts = ['从本地上传', '本地上传', '上传']
    for text in candidate_texts:
        try:
            async with page.expect_file_chooser(timeout=5000) as fc_info:
                clicked = await safe_click(page, page.locator(f'text={text}').first, f'{text}入口', timeout=3000)
                if not clicked:
                    continue
            chooser = await fc_info.value
            await chooser.set_files(file_path)
            print(f"  ✅ 通过 file chooser 上传成功: {text}")
            return True
        except Exception:
            continue

    return False

async def confirm_reference_media(page) -> bool:
    """点击参考弹窗中的确认按钮，把已上传素材真正挂到编辑器。"""
    try:
        confirm_state = await page.evaluate('''() => {
            const btn = Array.from(document.querySelectorAll('button')).find(el => (el.innerText || '').trim() === '确认');
            if (!btn) return 'NOT_FOUND';
            const disabled = btn.hasAttribute('disabled') || btn.getAttribute('aria-disabled') === 'true';
            if (disabled) return 'DISABLED';
            btn.click();
            return 'CLICKED';
        }''')
        print(f"  参考确认按钮: {confirm_state}")
        return confirm_state == 'CLICKED'
    except Exception as e:
        print(f"  ❌ 点击参考确认失败: {e}")
        return False

async def wait_for_reference_media_ready(page, media_kind: str, timeout_ms: int = 300000) -> bool:
    """等待参考素材缩略图或重传入口出现。"""
    step_ms = 5000
    expect_video = media_kind == 'video'
    loops = max(timeout_ms // step_ms, 1)
    print("  ⏳ 等待上传完成...")
    for wait_i in range(loops):
        await page.wait_for_timeout(step_ms)
        upload_status = await page.evaluate(r'''([expectVideo]) => {
            const text = document.body.innerText || '';
            const isUploading = text.includes('上传中') || text.includes('uploading') || /\b\d{1,3}%\b/.test(text);
            const confirmBtn = Array.from(document.querySelectorAll('button')).find(btn => (btn.innerText || '').trim() === '确认');
            const confirmDisabled = confirmBtn ? (confirmBtn.hasAttribute('disabled') || btnHasSpinner(confirmBtn)) : null;

            const editable = document.querySelector('div[contenteditable="true"]');
            const scope = editable ? (editable.closest('form') || editable.parentElement || document.body) : document.body;
            const hasBackgroundThumb = Array.from(document.body.querySelectorAll('*')).some(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) return false;
                const style = window.getComputedStyle(el);
                if (!style.backgroundImage || style.backgroundImage === 'none') return false;
                return rect.top > 150 && rect.top < 500 && rect.left > 450 && rect.left < 900;
            });
            const hasVisual = expectVideo
                ? (!!scope.querySelector('video, img[src*="tos"], canvas') || hasBackgroundThumb)
                : (!!scope.querySelector('img, canvas') || hasBackgroundThumb);
            const all = Array.from(document.querySelectorAll('*'));
            const hasLabel = all.some(el => {
                const t = (el.innerText || '').trim();
                if (!t) return false;
                if (expectVideo) return t === '视频1' || t === '重新上传' || t === '替换';
                return t === '图片1' || t === '重新上传' || t === '替换';
            });
            const sendBtn = Array.from(document.querySelectorAll('button')).find(btn => btn.querySelector('svg.lucide-arrow-up'));
            const sendDisabled = sendBtn ? (sendBtn.hasAttribute('disabled') || sendBtn.getAttribute('aria-disabled') === 'true') : null;

            if (hasVisual || hasLabel) return `DONE|sendDisabled=${sendDisabled}|confirmDisabled=${confirmDisabled}`;
            if (isUploading || confirmDisabled) return 'UPLOADING';
            return `WAITING|sendDisabled=${sendDisabled}|confirmDisabled=${confirmDisabled}`;

            function btnHasSpinner(btn) {
                return !!btn.querySelector('svg, [class*="spin"], [class*="loading"], [class*="loader"]');
            }
        }''', [expect_video])

        if upload_status.startswith('DONE'):
            print(f"  ✅ 上传完成! {upload_status} (elapsed: {(wait_i + 1) * step_ms // 1000}s)")
            return True
        if upload_status == 'UPLOADING':
            continue
        if upload_status.startswith('WAITING|sendDisabled=false') and wait_i >= 11:
            print(f"  ⚠️ 上传完成信号不稳定，按可提交状态继续: {upload_status} (elapsed: {(wait_i + 1) * step_ms // 1000}s)")
            return True
        if wait_i > 0 and wait_i % 6 == 0:
            print(f"    ⏳ 等待中... {upload_status} ({(wait_i + 1) * step_ms // 1000}s)")
    return False

async def collect_editor_state(page):
    """收集 dry-run 末态，便于判断表单是否可提交。"""
    return await page.evaluate('''() => {
        const editable = document.querySelector('div[contenteditable="true"]');
        const scope = editable ? (editable.closest('form') || editable.parentElement || document.body) : document.body;
        const sendBtn = Array.from(document.querySelectorAll('button')).find(btn => btn.querySelector('svg.lucide-arrow-up'));
        const promptText = editable ? (editable.innerText || '').trim() : '';
        const hasBackgroundThumb = Array.from(document.body.querySelectorAll('*')).some(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width < 20 || rect.height < 20) return false;
            const style = window.getComputedStyle(el);
            if (!style.backgroundImage || style.backgroundImage === 'none') return false;
            return rect.top > 150 && rect.top < 500 && rect.left > 450 && rect.left < 900;
        });
        return {
            promptLength: promptText.length,
            hasImageThumb: !!scope.querySelector('img'),
            hasVideoThumb: !!scope.querySelector('video'),
            hasCanvasThumb: !!scope.querySelector('canvas'),
            hasBackgroundThumb,
            hasReplaceAction: Array.from(document.querySelectorAll('*')).some(el => {
                const t = (el.innerText || '').trim();
                return t === '重新上传' || t === '替换';
            }),
            sendDisabled: sendBtn ? (sendBtn.hasAttribute('disabled') || sendBtn.getAttribute('aria-disabled') === 'true') : null,
            sendPresent: !!sendBtn,
        };
    }''')

async def read_toolbar_model_label(page) -> str:
    """读取工具栏当前显示的模型标签。"""
    return await page.evaluate('''() => {
        const items = Array.from(document.querySelectorAll('*'));
        const candidates = items.filter(el => {
            const text = (el.innerText || '').trim();
            if (!text) return false;
            if (!(text === '2.0' || text === '2.0 Fast' || text === 'Seedance 2.0' || text === 'Seedance 2.0 Fast' || text === 'Seedance2.0' || text === 'Seedance2.0Fast')) {
                return false;
            }
            const r = el.getBoundingClientRect();
            return r.left > 500 && r.top > 350 && r.top < 700 && el.offsetHeight < 60 && el.offsetHeight > 10;
        });
        candidates.sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            return Math.abs(ra.left - 700) - Math.abs(rb.left - 700);
        });
        return candidates[0] ? candidates[0].innerText.trim() : '';
    }''')

async def click_extend_button(page) -> bool:
    result = await page.evaluate('''([labels]) => {
        const all = Array.from(document.querySelectorAll('button, a, div, span'));
        const candidates = all.filter(el => {
            const text = (el.innerText || '').trim();
            if (!labels.includes(text)) return false;
            const r = el.getBoundingClientRect();
            return r.left > 350 && r.top > 350 && r.width < 160 && r.height < 40 && r.width > 20 && r.height > 8;
        });
        candidates.sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            return Math.abs(ra.top - 548) - Math.abs(rb.top - 548) || (ra.left - rb.left);
        });
        const el = candidates[0];
        if (!el) return 'NOT_FOUND';
        el.click();
        return 'CLICKED: ' + (el.innerText || '').trim();
    }''', [EXTEND_BUTTON_PATTERNS])
    print(f"  延长入口: {result}")
    if result.startswith('CLICKED'):
        return True

    fallback = await page.evaluate('''() => {
        const media = Array.from(document.querySelectorAll('video, img')).filter(el => {
            const r = el.getBoundingClientRect();
            return r.left > 300 && r.top > 250 && r.width > 180 && r.height > 120;
        }).sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0];
        if (!media) return null;
        const r = media.getBoundingClientRect();
        return {
            x: Math.round(r.left + r.width * 0.58),
            y: Math.round(r.bottom + 30)
        };
    }''')
    if fallback:
        try:
            await page.mouse.click(fallback['x'], fallback['y'])
            print(f"  延长入口坐标兜底: clicked at ({fallback['x']}, {fallback['y']})")
            return True
        except Exception as e:
            print(f"  ❌ 延长入口坐标兜底失败: {e}")
    return False

async def run_extend(prompt: str, duration: str, dry_run: bool, extend_url: str):
    global DEBUG_SCREENSHOTS
    DEBUG_SCREENSHOTS = dry_run
    print("🚀 Starting Playwright + Chromium (headless)... [EXTEND (续写/延长)]")
    print(f"🔗 目标线程: {extend_url}")
    if dry_run:
        print("⚠️ DRY-RUN MODE: will fill form but NOT click '发送'")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        cookies = load_and_clean_cookies()
        await context.add_cookies(cookies)
        page = await context.new_page()

        print("🌐 [Step 1] Navigating to extend thread page...")
        await goto_with_retry(page, extend_url)
        await page.wait_for_timeout(8000)
        await screenshot(page, 'extend_1_detail')

        print("🔍 [Step 2] Checking page status...")
        content = await page.content()
        is_logged_in = '小云雀助你' in content or '新对话' in content or 'thread_id' in page.url
        if not is_logged_in:
            print("  ❌ LOGIN_FAILED_OR_THREAD_NOT_VISIBLE")
            await browser.close()
            return
        print("  ✅ THREAD_PAGE_READY")

        print("🪄 [Step 3] Clicking extend button...")
        extend_clicked = await click_extend_button(page)
        if not extend_clicked:
            await screenshot(page, 'extend_3_button_not_found')
            await browser.close()
            return
        # 向后延伸会跳转回 home 对话页，这里等待输入框真正出现
        editor_ready = False
        for i in range(8):
            await page.wait_for_timeout(1500)
            editables = await page.locator('div[contenteditable="true"]').count()
            if editables > 0:
                editor_ready = True
                break
        print(f"  Extend target url: {page.url}")
        print(f"  Extend editor ready: {editor_ready}")
        await screenshot(page, 'extend_3_clicked')
        if not editor_ready:
            await browser.close()
            return

        print("⏱️ [Step 4] Selecting duration...")
        dur_click_result = await page.evaluate('''() => {
            const all = Array.from(document.querySelectorAll('*'));
            const btn = all.find(el => {
                const text = (el.innerText || '').trim();
                const r = el.getBoundingClientRect();
                return /^\\d+s$/.test(text) && r.left > 300 && r.height > 5 && r.height < 50;
            });
            if(btn) {
                btn.click();
                return 'clicked';
            }
            return 'not found';
        }''')
        await page.wait_for_timeout(1500)
        await screenshot(page, 'extend_4a_duration_dropdown')
        if dur_click_result == 'clicked':
            try:
                dur_item = page.locator(f'text=/^{duration}$/').locator('visible=true').first
                if await dur_item.count() > 0:
                    await dur_item.click(timeout=3000)
                    print(f"  ✅ 时长选择: {duration}")
            except Exception as e:
                print(f"  ⚠️ 时长选择失败: {e}")
        await page.wait_for_timeout(1000)
        await screenshot(page, 'extend_4b_duration_selected')

        print(f"📝 [Step 5] Injecting prompt: {prompt}")
        inject_result = await page.evaluate('''([text]) => {
            const all = Array.from(document.querySelectorAll('div[contenteditable="true"]'));
            const el = all.find(e => e.getBoundingClientRect().left > 300);
            if (!el) return 'FAILED: no contenteditable found';
            el.focus();
            el.innerText = text;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            return 'OK: ' + el.innerText.substring(0, 30) + '...';
        }''', [prompt])
        print(f"  Inject: {inject_result}")
        await page.wait_for_timeout(1000)
        await screenshot(page, 'extend_5_prompt')

        if dry_run:
            await screenshot(page, 'extend_6_DRY_RUN_FINAL')
            editor_state = await collect_editor_state(page)
            print("\n✅ EXTEND DRY-RUN 完成！请检查截图 step_extend_6_DRY_RUN_FINAL.png")
            print(f"🧪 表单状态: {json.dumps(editor_state, ensure_ascii=False)}")
            if editor_state['sendPresent'] and editor_state['sendDisabled']:
                print("⚠️ DRY-RUN 告警: 发送按钮仍是禁用态。")
            await browser.close()
            return

        print("🖱️ [Step 6] Clicking send button...")
        thread_id = await submit_and_capture_thread(page, 'extend_6_submitted')
        if not thread_id:
            await browser.close()
            return

        print(f"🔗 [Step 7] Extend thread_id: {thread_id}")
        await open_thread_and_download(page, thread_id, prompt, duration)
        await browser.close()

async def run(prompt: str, duration: str = "10s", ratio: str = "横屏", model: str = "Seedance 2.0", dry_run: bool = False, ref_video: str = None, ref_image: str = None):
    global DEBUG_SCREENSHOTS
    DEBUG_SCREENSHOTS = dry_run
    ref_video_ready = False
    if ref_image:
        mode_label = "I2V (图生视频)"
    elif ref_video:
        mode_label = "V2V (参考视频)"
    else:
        mode_label = "T2V (文生视频)"
    print(f"🚀 Starting Playwright + Chromium (headless)... [{mode_label}]")
    if ref_video and not os.path.exists(ref_video):
        print(f"❌ 参考视频文件不存在: {ref_video}")
        return
    if ref_image and not os.path.exists(ref_image):
        print(f"❌ 参考图片文件不存在: {ref_image}")
        return
    if ref_video:
        size_mb = os.path.getsize(ref_video) / (1024 * 1024)
        print(f"📎 参考视频: {ref_video} ({size_mb:.1f}MB)")
    if ref_image:
        size_kb = os.path.getsize(ref_image) / 1024
        print(f"🖼️ 参考图片: {ref_image} ({size_kb:.0f}KB)")
    if dry_run:
        print("⚠️ DRY-RUN MODE: will fill form but NOT click '开始创作'")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080}
        )

        # === Step 1: Cookie 注入 ===
        print("🔑 [Step 1] Injecting cookies...")
        cookies = load_and_clean_cookies()
        await context.add_cookies(cookies)
        print(f"  ✅ {len(cookies)} cookies injected")

        page = await context.new_page()

        # === Step 2: 导航 ===
        print("🌐 [Step 2] Navigating to xyq.jianying.com/home...")
        await page.goto('https://xyq.jianying.com/home', wait_until='domcontentloaded')
        await page.wait_for_timeout(8000)
        await screenshot(page, '2_loaded')

        # === Step 3: 登录验证 ===
        print("🔍 [Step 3] Checking login status...")
        content = await page.content()
        # 新 UI: 页面 HTML 中总含 "登录" 文字(在属性中), 改用检测问候语或导航元素
        is_logged_in = '小云雀助你' in content or '新对话' in content
        if is_logged_in:
            print("  ✅ LOGIN_SUCCESS")
        else:
            print("  ❌ LOGIN_FAILED — 请重新导出 cookies.json！")
            await browser.close()
            return

        # === Step 3.5: 从 "Agent 模式" 下拉选择 "沉浸式短片" ===
        print("🎬 [Step 3.5] Selecting '沉浸式短片' from mode dropdown...")
        
        # 为了避免点到左侧导航树里的历史名字，必须限定坐标在中间区域
        mode_btn_pos = await page.evaluate('''() => {
            const all = Array.from(document.querySelectorAll('*'));
            const el = all.find(e => {
                const t = (e.innerText || '').trim();
                const r = e.getBoundingClientRect();
                return t === 'Agent 模式' && r.left > 300 && r.height < 50 && r.height > 10;
            });
            if (el) {
                const r = el.getBoundingClientRect();
                return {x: r.left + r.width/2, y: r.top + r.height/2};
            }
            return null;
        }''')
        
        mode_dropdown_opened = False
        if mode_btn_pos:
            await page.mouse.click(mode_btn_pos['x'], mode_btn_pos['y'])
            mode_dropdown_opened = True
            print("  ✅ Agent 模式下拉: clicked")
        else:
            print("  ⚠️ fail to find Agent mode button")

        await page.wait_for_timeout(2000)
        await screenshot(page, '3_5a_mode_dropdown')

        if mode_dropdown_opened:
            # 3.5b: 在下拉菜单中选择 "沉浸式短片" (同样过滤左侧的项)
            immersive_clicked = await page.evaluate('''() => {
                const all = Array.from(document.querySelectorAll('*'));
                const el = all.find(e => {
                    const t = (e.innerText || '').trim();
                    const r = e.getBoundingClientRect();
                    return t.includes('沉浸式短片') && r.left > 300 && r.height > 30 && r.height < 80;
                });
                if (el) {
                    el.click();
                    return true;
                }
                return false;
            }''')
            if immersive_clicked:
                print("  ✅ 沉浸式短片: clicked")
            else:
                print("  ⚠️ fail to click immersive mode item")
        else:
            # 可能已经在沉浸式短片模式下
            toolbar_text = await page.evaluate('''() => {
                const el = document.querySelector('div[contenteditable="true"]');
                return el ? 'HAS_INPUT' : 'NO_INPUT';
            }''')
            print(f"  ⚠️ Mode dropdown not found, toolbar status: {toolbar_text}")

        await page.wait_for_timeout(3000)
        await screenshot(page, '3_5b_mode_selected')

        # === Step 3.6: 上传参考图片 (仅 I2V 模式) ===
        if ref_image:
            print(f"🖼️ [Step 3.6] Uploading reference image: {os.path.basename(ref_image)}")

            # 点击输入区域的 "+" 或 "上传参考素材" 按钮
            plus_clicked = False
            try:
                plus_result = await page.evaluate('''() => {
                    const svgs = Array.from(document.querySelectorAll('svg.lucide-plus'));
                    let target = svgs.find(svg => {
                        const r = svg.getBoundingClientRect();
                        return r.top > 300 && r.left > 300;
                    });
                    if (!target) {
                        const all = Array.from(document.querySelectorAll('button[title="上传参考素材"], button[title*="添加"]'));
                        target = all.find(el => el.getBoundingClientRect().left > 300);
                    }
                    if (target) {
                        const btn = target.closest('button') || target.parentElement;
                        if (btn) btn.click();
                        else target.click();
                        return 'OK_EVAL';
                    }
                    return 'NOT_FOUND';
                }''')
                print(f"  + 按钮: JS eval -> {plus_result}")
                plus_clicked = plus_result.startswith('OK')
            except Exception as e:
                print(f"  + 按钮: script_fail ({e})")
                
            await page.wait_for_timeout(2000)
            await screenshot(page, '3_6_plus_menu')

            if plus_clicked:
                # 点击 "本地上传" 并上传图片
                try:
                    async with page.expect_file_chooser(timeout=10000) as fc_info:
                        upload_clicked = await page.evaluate('''() => {
                            const all = Array.from(document.querySelectorAll('*'));
                            const candidates = all.filter(el => {
                                const text = (el.innerText || '').trim();
                                if (!text) return false;
                                return text === '本地上传' || text === '从本地上传';
                            });
                            candidates.sort((a, b) => {
                                return (a.offsetWidth * a.offsetHeight) - (b.offsetWidth * b.offsetHeight);
                            });
                            if (candidates.length > 0) {
                                const el = candidates[0];
                                el.click();
                                return 'OK: ' + el.tagName;
                            }
                            return 'NOT_FOUND';
                        }''')
                        print(f"  本地上传: {upload_clicked}")
                        if upload_clicked == 'NOT_FOUND':
                            raise Exception("'本地上传' not found in menu")

                    file_chooser = await fc_info.value
                    await file_chooser.set_files(ref_image)
                    print(f"  ✅ 图片已选择: {os.path.basename(ref_image)}")

                    # 等待图片上传完成 (检测缩略图出现)
                    print("  ⏳ 等待图片上传...")
                    for wait_i in range(30):
                        await page.wait_for_timeout(3000)
                        has_image = await page.evaluate('''() => {
                            // 新 UI: 检查 contenteditable 附近是否有 img
                            const editable = document.querySelector('div[contenteditable="true"]');
                            if (editable) {
                                // 向上查找父容器中的 img
                                const parent = editable.closest('div[class]') || editable.parentElement;
                                if (parent && parent.querySelector('img')) return true;
                            }
                            // 兜底: 查找是否有内容为“图片1”或类似的元素(缩略图标题)
                            const all = Array.from(document.querySelectorAll('*'));
                            const hasPicThumb = all.some(el => {
                                const t = (el.innerText || '').trim();
                                return t === '图片1' || t === '视频1' || (el.tagName === 'IMG' && el.src.includes('tos'));
                            });
                            return hasPicThumb;
                        }''')
                        if has_image:
                            print(f"  ✅ 图片上传完成 (elapsed: {(wait_i+1)*3}s)")
                            break
                        if wait_i > 0 and wait_i % 5 == 0:
                            print(f"    ⏳ 等待中... ({(wait_i+1)*3}s)")
                            
                    # 关闭弹出菜单 (用 Escape 键，不用 mouse.click 避免误触链接)
                    await page.keyboard.press('Escape')

                except Exception as e:
                    print(f"  ❌ 图片上传失败: {e}")

            await page.wait_for_timeout(2000)
            await screenshot(page, '3_6_image_uploaded')

        # === Step 4: 已在 Step 3.5 中选择了沉浸式短片模式，跳过 ===

        # === Step 5: 选模型 ===
        print(f"🤖 [Step 5] Selecting model: {model}...")

        # 5a: 点击工具栏的模型按钮，因为需要严格限定位置在中间 (x > 300)，因此用 JS 提取
        model_click = await page.evaluate('''() => {
            const items = Array.from(document.querySelectorAll('*'));
            const btn = items.find(el => {
                const text = (el.innerText || '').trim();
                const r = el.getBoundingClientRect();
                return (text === 'Seedance 2.0' || text === '2.0' || text === '2.0 Fast' || text === 'Seedance 2.0 Fast') 
                    && (el.tagName === 'DIV' || el.tagName === 'SPAN') && r.left > 300 && r.top > 300 
                    && r.height < 50 && r.height > 10;
            });
            if (btn) {
                btn.click();
                const r = btn.getBoundingClientRect();
                return 'opened: ' + btn.innerText.trim() + ' (x=' + Math.round(r.left) + ', y=' + Math.round(r.top) + ')';
            }
            return 'NOT_FOUND';
        }''')
        print(f"  Model button: {model_click}")
        model_btn_clicked = 'opened' in model_click

        await page.wait_for_timeout(2000)
        await screenshot(page, '5a_model_dropdown')

        if model_btn_clicked:
            # 5b: 在下拉菜单中选目标模型
            want_fast = "Fast" in model
            model_select = await page.evaluate('''([wantFast]) => {
                const items = Array.from(document.querySelectorAll('*'));
                const candidates = items.filter(el => {
                    const text = (el.innerText || '').trim();
                    if (!text) return false;
                    if (!/^Seedance/.test(text)) return false;
                    if (/[\u4e00-\u9fff]/.test(text)) return false;
                    const r = el.getBoundingClientRect();
                    return el.offsetHeight > 5 && el.offsetHeight <= 60 && r.left > 400;
                });
                for (const el of candidates) {
                    const text = el.innerText.trim();
                    const isFast = text.includes('Fast');
                    if (wantFast === isFast) {
                        el.click(); // 标准点击
                        const r = el.getBoundingClientRect();
                        return 'selected: ' + text + ' (x=' + Math.round(r.left) + ', y=' + Math.round(r.top) + ')';
                    }
                }
                return 'NOT_FOUND: candidates=' + candidates.map(el => {
                    const r = el.getBoundingClientRect();
                    return '"' + el.innerText.trim() + '"(x=' + Math.round(r.left) + ',y=' + Math.round(r.top) + ')';
                }).join('; ');
            }''', [want_fast])
            print(f"  Model select: {model_select}")
            await page.wait_for_timeout(1500)
            current_model = await read_toolbar_model_label(page)
            print(f"  Current model label: {current_model or 'NOT_FOUND'}")
            if current_model:
                current_is_fast = 'Fast' in current_model
                if current_is_fast != want_fast:
                    print("  ⚠️ 当前工具栏模型与目标不一致，重试一次选择...")
                    retry_click = await page.evaluate('''() => {
                        const items = Array.from(document.querySelectorAll('*'));
                        const btn = items.find(el => {
                            const text = (el.innerText || '').trim();
                            const r = el.getBoundingClientRect();
                            return (text === 'Seedance 2.0' || text === '2.0' || text === '2.0 Fast' || text === 'Seedance 2.0 Fast' || text === 'Seedance2.0' || text === 'Seedance2.0Fast')
                                && (el.tagName === 'DIV' || el.tagName === 'SPAN') && r.left > 300 && r.top > 300
                                && r.height < 60 && r.height > 10;
                        });
                        if (!btn) return 'NOT_FOUND';
                        btn.click();
                        return 'REOPENED';
                    }''')
                    print(f"  Model retry open: {retry_click}")
                    await page.wait_for_timeout(1200)
                    model_retry = await page.evaluate('''([wantFast]) => {
                        const items = Array.from(document.querySelectorAll('*'));
                        const candidates = items.filter(el => {
                            const text = (el.innerText || '').trim();
                            if (!text) return false;
                            if (!/^Seedance/.test(text)) return false;
                            if (/[\u4e00-\u9fff]/.test(text)) return false;
                            const r = el.getBoundingClientRect();
                            return el.offsetHeight > 5 && el.offsetHeight <= 60 && r.left > 400;
                        });
                        for (const el of candidates) {
                            const text = el.innerText.trim();
                            const isFast = text.includes('Fast');
                            if (wantFast === isFast) {
                                el.click();
                                return 'RESELECTED: ' + text;
                            }
                        }
                        return 'RESELECT_FAILED';
                    }''', [want_fast])
                    print(f"  Model retry select: {model_retry}")
                    await page.wait_for_timeout(1200)
        await screenshot(page, '5b_model_selected')

        # === Step 6: 上传参考视频 (仅 V2V 模式) ===
        if ref_video:
            print(f"📎 [Step 6] Uploading reference video: {os.path.basename(ref_video)}")

            # 预检查并缩放视频
            actual_video_path = await check_and_resize_video(ref_video)
            is_temp = actual_video_path != ref_video

            try:
                # 6a: 点击工具栏的“参考素材”按钮 → 弹出面板
                panel_opened = await open_reference_material_panel(page)
                if not panel_opened:
                    print("  ❌ 未能打开参考素材面板")
                    await screenshot(page, '6a_ref_panel_failed')
                    await browser.close()
                    return
                await page.wait_for_timeout(2000)
                await screenshot(page, '6a_ref_panel')

                # 6b: 上传本地视频
                uploaded = await upload_reference_media(page, actual_video_path, 'video')
                if not uploaded:
                    print("  ❌ 未能触发本地视频上传")
                    await screenshot(page, '6b_ref_upload_trigger_failed')
                    await browser.close()
                    return
                print(f"  ✅ 文件已选择: {os.path.basename(actual_video_path)}")

                await page.wait_for_timeout(1500)
                confirm_clicked = await confirm_reference_media(page)
                if not confirm_clicked:
                    print("  ❌ 参考视频已选择，但确认按钮没有成功点击")
                    await screenshot(page, '6b_ref_confirm_failed')
                    await browser.close()
                    return

                upload_ready = await wait_for_reference_media_ready(page, 'video')
                if not upload_ready:
                    print("  ❌ 参考视频在等待窗口内没有进入已挂载状态")
                    await screenshot(page, '6b_ref_upload_timeout')
                    await browser.close()
                    return
                ref_video_ready = True

                # 关闭参考面板
                await page.keyboard.press('Escape')
                await page.wait_for_timeout(1000)
                await screenshot(page, '6b_ref_uploaded')

            finally:
                if is_temp and os.path.exists(actual_video_path):
                    try:
                        os.remove(actual_video_path)
                        print(f"  🧹 已清理临时缩放视频: {actual_video_path}")
                    except:
                        pass

        # === Step 7: 选时长及比例 ===
        step7_label = '7' if ref_video else '6'
        print(f"⏱️ [Step {step7_label}] Selecting duration: {duration} (ratio fallback via prompt)...")
        
        # 将比例合并至 Prompt 的方案 (因为新 UI 消失了原生组件)，确保最终一定生效
        if ratio and ratio not in prompt:
            prompt = f"[{ratio}] {prompt}"
            
        dur_click_result = await page.evaluate('''() => {
            const all = Array.from(document.querySelectorAll('*'));
            const btn = all.find(el => {
                const text = (el.innerText || '').trim();
                const r = el.getBoundingClientRect();
                return /^\\d+s$/.test(text) && r.left > 300 && r.height > 5 && r.height < 50;
            });
            if(btn) {
                btn.click();
                return 'clicked';
            }
            return 'not found';
        }''')
        
        await page.wait_for_timeout(1500)
        await screenshot(page, f'{step7_label}a_duration_dropdown')

        if dur_click_result == 'clicked':
            try:
                # 尝试选具体的时长
                dur_item = page.locator(f'text=/^{duration}$/').locator('visible=true').first
                if await dur_item.count() > 0:
                    await dur_item.click(timeout=3000)
                    print(f"  ✅ 时长选择: {duration}")
            except Exception as e:
                print(f"  ⚠️ 时长选择兜底失败: {e}")
            await page.wait_for_timeout(1000)
        await screenshot(page, f'{step7_label}b_duration_selected')

        # === Step 8: 注入 Prompt ===
        step8_label = '8' if ref_video else '7'
        print(f"📝 [Step {step8_label}] Injecting prompt: {prompt}")
        inject_result = await page.evaluate('''([text]) => {
            const all = Array.from(document.querySelectorAll('div[contenteditable="true"]'));
            const el = all.find(e => e.getBoundingClientRect().left > 300);
            if (el) {
                el.innerText = text;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                return 'OK: ' + el.innerText.substring(0, 30) + '...';
            }
            return 'FAILED: no contenteditable found';
        }''', [prompt])
        print(f"  Inject: {inject_result}")
        await page.wait_for_timeout(1000)
        await screenshot(page, f'{step8_label}_prompt')

        # === Step 8: 验证/提交 ===
        if dry_run:
            await screenshot(page, '8_DRY_RUN_FINAL')
            status_text = await page.evaluate('''() => {
                const all = Array.from(document.querySelectorAll('*'));
                const info = all.find(el => {
                    const t = (el.innerText || '').trim();
                    // 新 UI: 顶部显示 "沉浸式短片 Seedance 2.0 Fast 按 1 秒 3 积分扣除"
                    return t && t.includes('积分') && el.offsetHeight < 50;
                });
                return info ? info.innerText.trim() : 'NOT_FOUND';
            }''')
            editor_state = await collect_editor_state(page)
            ref_state_ok = True
            if ref_video:
                ref_state_ok = (
                    ref_video_ready or
                    editor_state['hasVideoThumb'] or
                    editor_state['hasImageThumb'] or
                    editor_state['hasCanvasThumb'] or
                    editor_state['hasBackgroundThumb'] or
                    editor_state['hasReplaceAction']
                )
            print(f"\n✅ DRY-RUN 完成！请检查截图 step_8_DRY_RUN_FINAL.png")
            print(f"📊 底部状态栏: {status_text}")
            print(f"🧪 表单状态: {json.dumps(editor_state, ensure_ascii=False)}")
            if ref_video and not ref_state_ok:
                print("❌ DRY-RUN 失败: V2V 参考视频没有出现在编辑器区域，当前流程还没跑通。")
                await browser.close()
                return
            if editor_state['sendPresent'] and editor_state['sendDisabled']:
                print("⚠️ DRY-RUN 告警: 发送按钮仍是禁用态，页面可能还没接受当前表单。")
            print(f"\n确认无误后，去掉 --dry-run 参数重新运行即可提交任务。")
            await browser.close()
            return

        print("🖱️ [Step 8] Clicking send button (arrow)...")
        thread_id = await submit_and_capture_thread(page, '8_submitted')
        if not thread_id:
            await browser.close()
            return

        print(f"🔗 [Step 9] Navigating to thread detail page...")
        await open_thread_and_download(page, thread_id, prompt, duration)

        await browser.close()

    print("\n🏁 Done!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jianying SeeDance 2.0 Video Generator")
    parser.add_argument("--prompt", type=str, default="一个美女在跳舞", help="Video description")
    parser.add_argument("--duration", type=str, default="10s", choices=["5s", "10s", "15s"])
    parser.add_argument("--ratio", type=str, default="横屏", choices=["横屏", "竖屏", "方屏"])
    parser.add_argument("--model", type=str, default="Seedance 2.0",
                        choices=["Seedance 2.0", "Seedance 2.0 Fast"])
    parser.add_argument("--ref-video", type=str, default=None, help="Reference video file path (V2V mode)")
    parser.add_argument("--ref-image", type=str, default=None, help="Reference image file path (I2V mode)")
    parser.add_argument("--extend-url", type=str, default=None, help="Existing thread URL for extend/continue mode")
    parser.add_argument("--cookies", type=str, default="cookies.json", help="Path to cookies.json")
    parser.add_argument("--output-dir", type=str, default=".", help="Directory to save output video")
    parser.add_argument("--dry-run", action="store_true", help="Only fill form, don't submit")
    args = parser.parse_args()

    COOKIES_FILE = args.cookies
    DOWNLOAD_DIR = args.output_dir
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    if not os.path.exists(COOKIES_FILE):
        print(f"⚠️ {COOKIES_FILE} not found!")
    else:
        if args.extend_url:
            asyncio.run(run_extend(args.prompt, args.duration, args.dry_run, args.extend_url))
        else:
            asyncio.run(run(args.prompt, args.duration, args.ratio, args.model, args.dry_run, args.ref_video, args.ref_image))
