import os
import time
import logging
from bs4 import BeautifulSoup
from seleniumbase import SB

import queue

class BrowserAutomator:
    def __init__(self, headless=True, proxy=None):
        self.headless = headless
        self.proxy = proxy

    def run_worker(self, task_queue, result_queue, max_retries=2):
        while True:
            if task_queue.empty():
                break
                
            try:
                with SB(uc=True, headless=self.headless, proxy=self.proxy) as sb:
                    while True:
                        try:
                            task = task_queue.get(timeout=3)
                        except queue.Empty:
                            return # Exits worker completely
                            
                        result = None
                        fatal_error = False
                        try:
                            result = self.analyze_single(sb, task)
                        except Exception as e:
                            err_str = str(e).lower()
                            logging.error(f"  [ERROR] {task['uuid']} exception: {e}")
                            
                            if 'no such window' in err_str or 'closed' in err_str or 'invalid session' in err_str or 'disconnected' in err_str:
                                fatal_error = True
                            
                            try:
                                error_screenshot = os.path.join(task['mode_dir'], f"{task['uuid']}_error.png")
                                sb.save_screenshot(error_screenshot)
                            except:
                                pass
                            
                        if result:
                            result_queue.put({'status': 'success', 'task': task, 'result': result})
                        else:
                            task['retries'] = task.get('retries', 0) + 1
                            if task['retries'] <= max_retries:
                                logging.warning(f"  [RETRY] Analysis failed for {task['uuid']}. Retrying ({task['retries']}/{max_retries}).")
                                task_queue.put(task)
                            else:
                                logging.error(f"  [SKIP] Analysis permanently failed for {task['uuid']} after {max_retries} retries.")
                                result_queue.put({'status': 'fail', 'task': task})
                        
                        task_queue.task_done()
                        
                        if fatal_error:
                            logging.warning(f"  [RECOVER] Browser instance dead. Respawning...")
                            break # Break inner loop to re-enter outer `with SB(...)`!
                            
            except Exception as spawn_err:
                logging.error(f"  [FATAL] Browser spawn failed: {spawn_err}. Retrying in 5s...")
                time.sleep(5)
                
    def analyze_single(self, sb, task):
        paipu_url = task['paipu_url']
        uuid = task['uuid']
        model_tag = task['model_tag']
        output_dir = task['mode_dir']
        save_screenshot = task.get('save_screenshot', False)
        
        result = None
        os.makedirs(output_dir, exist_ok=True)
        screenshot_path = os.path.join(output_dir, f"{uuid}.png")
        
        logging.info(f"[{uuid}] Loading SeleniumBase for {paipu_url}")
        
        # 1. Navigate
        current_url = sb.get_current_url()
        if "mjai.ekyu.moe" not in current_url:
            sb.uc_open_with_reconnect("https://mjai.ekyu.moe/zh-cn.html", reconnect_time=4)
        else:
            sb.open("https://mjai.ekyu.moe/zh-cn.html")
        
        # Check captcha
        try:
            sb.uc_gui_click_captcha()
        except Exception:
            pass

        try:
            sb.wait_for_element('input[name="log-url"]', timeout=30)
        except Exception:
            logging.warning(f"[{uuid}] Cloudflare challenge taking too long or page stuck, refreshing...")
            sb.refresh()
            time.sleep(2)
            try:
                sb.uc_gui_click_captcha()
            except Exception:
                pass
            sb.wait_for_element('input[name="log-url"]', timeout=45)
            
        # 2. Select Radio
        sb.click('input[name="input-method"][value="log-url"]')
        sb.type('input[name="log-url"]', paipu_url)
        sb.select_option_by_value('select[name="engine"]', 'mortal')
        sb.select_option_by_value('select[name="mortal-model-tag"]', model_tag)
        sb.select_option_by_value('select[name="ui"]', 'classic')
        
        show_rating_sel = '#review > div > form > details.details.mb-3 > div > div:nth-child(3) > div > p:nth-child(4) > label > input[type=checkbox]'
        
        if not sb.execute_script("return document.querySelector('details.details.mb-3').open"):
            sb.click('details.details.mb-3 summary')
            time.sleep(0.5)

        if sb.is_element_visible(show_rating_sel):
            if not sb.is_selected(show_rating_sel):
                sb.click(show_rating_sel)
        else:
            if not sb.is_selected('input[name="show-rating"]'):
                sb.click('input[name="show-rating"]')

        logging.info(f"[{uuid}] Waiting for Turnstile verification to complete...")
        sb.wait_for_element_clickable('button[name="submitBtn"]', timeout=120)
        
        submit_btn = '#review > div > form > div:nth-child(10) > div > p > button'
        if sb.is_element_visible(submit_btn):
            sb.click(submit_btn)
        else:
            sb.click('button[name="submitBtn"]')

        logging.info(f"[{uuid}] Request submitted, waiting for results to generate...")
        sb.wait_for_element_present('details > dl', timeout=180)
        result_url = sb.get_current_url()
        logging.info(f"[{uuid}] Results loaded. Result URL: {result_url}")
        
        html_source = sb.get_page_source()
        soup = BeautifulSoup(html_source, 'html.parser')
        
        metadata = {}
        for dl in soup.select('details > dl'):
            dts = [dt.get_text(strip=True) for dt in dl.find_all('dt')]
            dds = [dd.get_text(strip=True) for dd in dl.find_all('dd')]
            for dt, dd in zip(dts, dds):
                metadata[dt] = dd
        
        saved_screenshot_path = ""
        if save_screenshot:
            try:
                menu_sel = 'body > details:nth-child(6) > summary'
                is_open = sb.execute_script("return document.querySelector('body > details:nth-child(6)').open")
                if not is_open:
                    sb.click(menu_sel)
                    time.sleep(0.5)
            except Exception as e:
                logging.warning(f"[{uuid}] Could not expand metadata menu: {e}")
            
            sb.save_screenshot(screenshot_path)
            saved_screenshot_path = screenshot_path
            logging.info(f"[{uuid}] Screenshot saved to {screenshot_path}")
        
        result = {
            "resultUrl": result_url,
            "screenshotPath": saved_screenshot_path,
            "metadata": metadata
        }
        
        return result
