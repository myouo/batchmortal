import os
import time
import logging
from bs4 import BeautifulSoup
from seleniumbase import SB

class BrowserAutomator:
    def __init__(self, headless=True):
        self.headless = headless

    def analyze_game(self, paipu_url: str, uuid: str, model_tag: str, output_dir: str, save_screenshot: bool = False):
        result = None
        os.makedirs(output_dir, exist_ok=True)
        screenshot_path = os.path.join(output_dir, f"{uuid}.png")
        
        logging.info(f"[{uuid}] Launching SeleniumBase for {paipu_url}")
        
        with SB(uc=True, headless=self.headless) as sb:
            try:
                # 1. Navigate and handle Cloudflare (UC naturally handles it, but open_with_reconnect can help)
                sb.uc_open_with_reconnect("https://mjai.ekyu.moe/zh-cn.html", reconnect_time=4)
                
                # Check if we need to click captcha
                try:
                    sb.uc_gui_click_captcha()
                except Exception:
                    pass

                # Pre-checking if page is block or needs extra time
                sb.wait_for_element('input[name="log-url"]', timeout=30)
                
                # 2. Select Paipu URL Radio
                sb.click('input[name="input-method"][value="log-url"]')
                
                # 3. Fill URL
                sb.type('input[name="log-url"]', paipu_url)
                
                # 4. Engine -> Mortal
                sb.select_option_by_value('select[name="engine"]', 'mortal')
                
                # 5. Model Tag
                sb.select_option_by_value('select[name="mortal-model-tag"]', model_tag)
                
                # 6. Classic UI
                sb.select_option_by_value('select[name="ui"]', 'classic')
                
                # 7. Open advanced details and tick "show rating" checkbox
                # specific selector requested:
                # #review > div > form > details.details.mb-3 > div > div:nth-child(3) > div > p:nth-child(4) > label > input[type=checkbox]
                show_rating_sel = '#review > div > form > details.details.mb-3 > div > div:nth-child(3) > div > p:nth-child(4) > label > input[type=checkbox]'
                
                if not sb.execute_script("return document.querySelector('details.details.mb-3').open"):
                    sb.click('details.details.mb-3 summary')
                    time.sleep(0.5)

                if sb.is_element_visible(show_rating_sel):
                    if not sb.is_selected(show_rating_sel):
                        sb.click(show_rating_sel)
                else:
                    # fallback to name="show-rating"
                    if not sb.is_selected('input[name="show-rating"]'):
                        sb.click('input[name="show-rating"]')

                # 8. Wait for Turnstile: The submit button is disabled until Cloudflare verification completes
                logging.info(f"[{uuid}] Waiting for Turnstile verification to complete...")
                sb.wait_for_element_clickable('button[name="submitBtn"]', timeout=120)
                
                # Submit using the specific selector requested by the user: 
                # #review > div > form > div:nth-child(10) > div > p > button
                submit_btn = '#review > div > form > div:nth-child(10) > div > p > button'
                if sb.is_element_visible(submit_btn):
                    sb.click(submit_btn)
                else:
                    # fallback
                    sb.click('button[name="submitBtn"]')

                logging.info(f"[{uuid}] Request submitted, waiting for results to generate...")
                
                # 9. Wait for Success Indicator: details > dl
                sb.wait_for_element_present('details > dl', timeout=180)
                result_url = sb.get_current_url()
                logging.info(f"[{uuid}] Results loaded. Result URL: {result_url}")
                
                # 10. Extract metadata
                html_source = sb.get_page_source()
                soup = BeautifulSoup(html_source, 'html.parser')
                
                metadata = {}
                for dl in soup.select('details > dl'):
                    dts = [dt.get_text(strip=True) for dt in dl.find_all('dt')]
                    dds = [dd.get_text(strip=True) for dd in dl.find_all('dd')]
                    for dt, dd in zip(dts, dds):
                        metadata[dt] = dd
                
                # 11. Optionally screenshot and open metadata
                saved_screenshot_path = ""
                if save_screenshot:
                    # Expand metadata menu per user request
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
                
            except Exception as e:
                logging.error(f"[{uuid}] Error during analysis: {str(e)}")
                try:
                    error_screenshot = os.path.join(output_dir, f"{uuid}_error.png")
                    sb.save_screenshot(error_screenshot)
                    logging.info(f"[{uuid}] Error screenshot saved to {error_screenshot}")
                except:
                    pass
                
        return result
