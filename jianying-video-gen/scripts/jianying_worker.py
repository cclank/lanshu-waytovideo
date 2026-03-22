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

async def screenshot(page, name):
    if not DEBUG_SCREENSHOTS:
        return
    path = os.path.join(DOWNLOAD_DIR, f'step_{name}.png')
    await page.screenshot(path=path)
    print(f"  📸 Screenshot: {path}")

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
        # 3.5a: 点击 "Agent 模式" 下拉按钮
        mode_dropdown_opened = await safe_click(
            page, page.locator('text=Agent 模式').first, 'Agent 模式下拉', timeout=8000
        )
        await page.wait_for_timeout(2000)
        await screenshot(page, '3_5a_mode_dropdown')

        if mode_dropdown_opened:
            # 3.5b: 在下拉菜单中选择 "沉浸式短片"
            immersive_clicked = await safe_click(
                page, page.locator('text=沉浸式短片').first, '沉浸式短片', timeout=5000
            )
            if not immersive_clicked:
                print("  ⚠️ Fallback: trying JS click for '沉浸式短片'")
                await page.evaluate('''() => {
                    const items = Array.from(document.querySelectorAll('*'));
                    const el = items.find(e => {
                        const t = (e.innerText || '').trim();
                        return t === '沉浸式短片' && e.offsetHeight < 40 && e.offsetHeight > 10;
                    });
                    if (el) el.click();
                }''')
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

            # 点击输入区域的 "+" 按钮 (工具栏最左边, title="上传参考素材")
            plus_clicked = False
            try:
                # 新 UI: 按钮有 title="上传参考素材"
                plus_locator = page.locator('button[title="上传参考素材"]').first
                box = await plus_locator.bounding_box()
                if not box:
                    # 备用: 通过 SVG class 定位
                    plus_locator = page.locator('button:has(svg.lucide-plus)').first
                await plus_locator.click(timeout=3000)
                plus_clicked = True
                print(f"  + 按钮: OK (Playwright locator)")
            except Exception as e:
                print(f"  + 按钮: locator_fail ({e})")
                
            if not plus_clicked:
                # 最后的 evaluate 兜底方案
                plus_result = await page.evaluate('''() => {
                    const svgs = Array.from(document.querySelectorAll('svg.lucide-plus'));
                    const targetSvg = svgs.find(svg => {
                        const r = svg.getBoundingClientRect();
                        return r.top > 300 && r.top < 600 && r.left > 400 && r.left < 800;
                    });
                    if (targetSvg) {
                        const btn = targetSvg.closest('button') || targetSvg.parentElement;
                        btn.click();
                        return 'OK_EVAL (svg.lucide-plus found)';
                    }
                    return 'NOT_FOUND';
                }''')
                print(f"  + 按钮: eval fallback -> {plus_result}")
                plus_clicked = plus_result.startswith('OK')
            await page.wait_for_timeout(2000)
            await screenshot(page, '3_6_plus_menu')

            if plus_clicked:
                # 点击 "本地上传" 并上传图片
                try:
                    async with page.expect_file_chooser(timeout=10000) as fc_info:
                        upload_clicked = await page.evaluate('''() => {
                            const all = Array.from(document.querySelectorAll('*'));
                            const candidates = all.filter(el => {
                                const text = el.innerText && el.innerText.trim();
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
                                const t = el.innerText && el.innerText.trim();
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

        # 5a: 点击工具栏的模型按钮 (显示 "2.0 Fast" 或 "2.0")
        # 关键: 不能用 Playwright text locator，因为底部卡片也含 "2.0" 文字
        # 必须限制到工具栏区域 (y在400-550, x>800)
        model_click = await page.evaluate('''() => {
            const items = Array.from(document.querySelectorAll('*'));
            const btn = items.find(el => {
                const text = el.innerText && el.innerText.trim();
                if (!text || !text.includes('2.0')) return false;
                // 文本长度 < 15, 排除整个工具栏容器
                if (text.length > 15) return false;
                const rect = el.getBoundingClientRect();
                // 工具栏区域: y 在 400-700, x > 800, 小元素 (放宽因为图片预览导致下移)
                return rect.top > 400 && rect.top < 700 && rect.left > 800 &&
                       el.offsetHeight < 50 && el.offsetHeight > 15;
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
                    const text = el.innerText && el.innerText.trim();
                    if (!text) return false;
                    if (!/^Seedance/.test(text)) return false;
                    if (/[\u4e00-\u9fff]/.test(text)) return false;
                    if (el.offsetHeight > 40 || el.offsetHeight < 10) return false;
                    const rect = el.getBoundingClientRect();
                    // 放宽高度上限到 850 避免由于顶部有预览图导致菜单向下偏移被忽略
                    // 增加 X 轴限制 (> 900) 以过滤掉位于下方的底部 Seedance2.0 介绍卡片 (其 x 约等于 822)
                    return rect.left > 900 && rect.left < 1100 && rect.top > 350 && rect.top < 850;
                });
                for (const el of candidates) {
                    const text = el.innerText.trim();
                    const isFast = text.includes('Fast');
                    if (wantFast === isFast) {
                        el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true}));
                        el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true}));
                        el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
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

        # === Step 7: 选时长 ===
        step7_label = '7' if ref_video else '6'
        print(f"⏱️ [Step {step7_label}] Selecting duration: {duration}...")
        
        # 点击当前时长按钮 (显示 "5s"、"10s" 或 "15s")
        dur_btn = page.locator('text=/^\\d+s$/').first
        dur_opened = await safe_click(page, dur_btn, '时长按钮')
        await page.wait_for_timeout(1500)
        await screenshot(page, f'{step7_label}a_duration_dropdown')

        if dur_opened:
            try:
                dur_item = page.locator(f'text=/^{duration}$/').first
                await dur_item.click(timeout=3000)
                print(f"  ✅ 时长选择: {duration}")
            except Exception as e:
                print(f"  ⚠️ 时长选择: {e}")
            await page.wait_for_timeout(1000)
        await screenshot(page, f'{step7_label}b_duration_selected')

        # === Step 8: 注入 Prompt ===
        step8_label = '8' if ref_video else '7'
        print(f"📝 [Step {step8_label}] Injecting prompt: {prompt}")
        inject_result = await page.evaluate('''([text]) => {
            const el = document.querySelector('div[contenteditable="true"]');
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
                    const t = el.innerText && el.innerText.trim();
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

        # === Step 8: 设置 thread_id 拦截器 + 提交 ===
        thread_id = None
        async def sniff_thread(response):
            nonlocal thread_id
            if thread_id:
                return
            try:
                text = await response.text()
                if 'thread_id' in text:
                    import json as _json
                    # 尝试从 JSON 中提取 thread_id
                    data = _json.loads(text)
                    # thread_id 可能在不同层级
                    tid = None
                    if isinstance(data, dict):
                        tid = data.get('thread_id') or data.get('data', {}).get('thread_id')
                        if not tid and 'data' in data:
                            d = data['data']
                            if isinstance(d, dict):
                                tid = d.get('thread_id')
                                # 可能嵌套更深
                                for v in d.values():
                                    if isinstance(v, dict) and 'thread_id' in v:
                                        tid = v['thread_id']
                                        break
                    if not tid:
                        # 暴力正则
                        m = re.search(r'"thread_id"\s*:\s*"([^"]+)"', text)
                        if m:
                            tid = m.group(1)
                    if tid:
                        thread_id = tid
                        print(f"\n  🎯 Sniffed thread_id: {tid}")
            except Exception:
                pass

        page.on('response', sniff_thread)

        print("🖱️ [Step 8] Clicking send button (arrow)...")
        # 新 UI: 发送按钮是右下角的箭头图标 (lucide-arrow-up)
        submit_clicked = await safe_click(
            page, page.locator('button:has(svg.lucide-arrow-up)').first, '发送(箭头)', timeout=5000
        )
        await page.wait_for_timeout(5000)
        await screenshot(page, '8_submitted')

        if not submit_clicked:
            print("  ❌ Submit failed. Aborting.")
            await browser.close()
            return

        # 等待 thread_id 被拦截
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
            await browser.close()
            return

        # === Step 9: 导航到 thread 详情页 + 轮询视频 ===
        detail_url = f"https://xyq.jianying.com/home?tab_name=integrated-agent&thread_id={thread_id}"
        print(f"🔗 [Step 9] Navigating to thread detail page...")
        print(f"  URL: {detail_url}")
        await page.goto(detail_url, wait_until='domcontentloaded')
        await page.wait_for_timeout(8000)

        safe_name = ''.join(c for c in prompt[:15] if c.isalnum() or c in '_ ')
        filename = f"{safe_name}_{duration}.mp4"
        filepath = os.path.join(DOWNLOAD_DIR, filename)

        print("⏳ Polling for video on detail page...")
        mp4_url = None
        for i in range(240):  # 延长至 240 次 (约 20 分钟)
            await page.wait_for_timeout(5000)

            # 双通道提取: DOM + 正则
            mp4_url = await page.evaluate(r'''() => {
                // 通道1: <video> 标签 src
                const v = document.querySelector('video');
                if (v && v.src && v.src.includes('.mp4')) return v.src;
                const s = document.querySelector('video source');
                if (s && s.src && s.src.includes('.mp4')) return s.src;
                // 通道2: 暴力正则
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
                # 刷新详情页
                await page.reload(wait_until='domcontentloaded')
                await page.wait_for_timeout(5000)
            print(".", end="", flush=True)

        if not mp4_url:
            print("\n  ❌ Timeout after 10 min")
            await screenshot(page, '9_timeout')
            await browser.close()
            return

        await screenshot(page, '9_video_ready')

        # === Step 10: curl 下载 ===
        print(f"📥 [Step 10] Downloading to {filepath}...")
        import subprocess
        result = subprocess.run(
            ['curl', '-L', '-o', filepath, '-s', '-w', '%{http_code}', mp4_url],
            capture_output=True, text=True, timeout=120
        )
        http_code = result.stdout.strip()

        if os.path.exists(filepath) and os.path.getsize(filepath) > 10000:
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            print(f"  ✅ Saved: {os.path.abspath(filepath)} ({size_mb:.1f}MB) [HTTP {http_code}]")
        else:
            print(f"  ❌ Download failed: HTTP {http_code}")
            if result.stderr:
                print(f"  Error: {result.stderr[:200]}")
            print(f"  📋 Manual link: {mp4_url}")

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
        asyncio.run(run(args.prompt, args.duration, args.ratio, args.model, args.dry_run, args.ref_video, args.ref_image))
