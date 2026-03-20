import logging
import os
import queue
import time

from seleniumbase import SB

REVIEW_URL = "https://mjai.ekyu.moe/zh-cn.html"
INPUT_SELECTOR = 'input[name="log-url"]'
SUBMIT_SELECTOR = 'button[name="submitBtn"]'
FORM_SELECTOR = 'form[name="reviewForm"]'
TURNSTILE_RESPONSE_SELECTOR = 'input[name="cf-turnstile-response"]'
RESULT_SELECTOR = "details > dl"
REPORT_URL_FRAGMENT = "/report/"


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
                    tasks_processed = 0

                    while True:
                        try:
                            task = task_queue.get(timeout=3)
                        except queue.Empty:
                            return

                        result = None
                        fatal_error = False
                        try:
                            result = self.analyze_single(sb, task)
                        except Exception as exc:
                            err_str = str(exc).lower()
                            logging.error(f"  [ERROR] {task['uuid']} exception: {exc}")

                            if any(
                                marker in err_str
                                for marker in ("no such window", "closed", "invalid session", "disconnected")
                            ):
                                fatal_error = True

                            try:
                                error_screenshot = os.path.join(task["mode_dir"], f"{task['uuid']}_error.png")
                                sb.save_screenshot(error_screenshot)
                            except Exception:
                                pass

                        if result:
                            result_queue.put({"status": "success", "task": task, "result": result})
                        else:
                            task["retries"] = task.get("retries", 0) + 1
                            if task["retries"] <= max_retries:
                                logging.warning(
                                    f"  [RETRY] Analysis failed for {task['uuid']}. "
                                    f"Retrying ({task['retries']}/{max_retries})."
                                )
                                task_queue.put(task)
                            else:
                                logging.error(
                                    f"  [SKIP] Analysis permanently failed for {task['uuid']} "
                                    f"after {max_retries} retries."
                                )
                                result_queue.put({"status": "fail", "task": task})

                        task_queue.task_done()
                        tasks_processed += 1

                        if tasks_processed >= 10:
                            logging.info("  [MEMORY] Worker hit 10 tasks limit. Recycling browser to flush memory...")
                            break

                        if fatal_error:
                            logging.warning("  [RECOVER] Browser instance dead. Respawning...")
                            break

            except Exception as spawn_err:
                logging.error(f"  [FATAL] Browser spawn failed: {spawn_err}. Retrying in 5s...")
                time.sleep(5)

    def analyze_single(self, sb, task):
        paipu_url = task["paipu_url"]
        uuid = task["uuid"]
        model_tag = task["model_tag"]
        output_dir = task["mode_dir"]
        save_screenshot = task.get("save_screenshot", False)

        os.makedirs(output_dir, exist_ok=True)
        screenshot_path = os.path.join(output_dir, f"{uuid}.png")
        started_at = time.perf_counter()

        logging.info(f"[{uuid}] Opening fresh review form")
        self._open_fresh_review_page(sb, uuid)
        self._populate_form(sb, paipu_url, model_tag)

        logging.info(f"[{uuid}] Waiting for Turnstile token")
        self._wait_for_turnstile_token(sb, uuid, timeout=35)

        logging.info(f"[{uuid}] Submitting review")
        self._submit_review(sb, uuid)

        logging.info(f"[{uuid}] Waiting for result page")
        self._wait_for_result_or_error(sb, uuid, timeout=45)
        result_url = sb.get_current_url()
        metadata = self._extract_metadata(sb)
        logging.info(f"[{uuid}] Result ready in {time.perf_counter() - started_at:.1f}s: {result_url}")

        saved_screenshot_path = ""
        if save_screenshot:
            self._expand_metadata_panel(sb, uuid)
            sb.save_screenshot(screenshot_path)
            saved_screenshot_path = screenshot_path
            logging.info(f"[{uuid}] Screenshot saved to {screenshot_path}")

        return {
            "resultUrl": result_url,
            "screenshotPath": saved_screenshot_path,
            "metadata": metadata,
        }

    def _open_fresh_review_page(self, sb, label):
        current_url = ""
        try:
            current_url = sb.get_current_url()
        except Exception:
            current_url = ""

        try:
            if "mjai.ekyu.moe" in current_url:
                sb.open(REVIEW_URL)
            else:
                sb.uc_open_with_reconnect(REVIEW_URL, reconnect_time=2)
        except Exception:
            sb.open(REVIEW_URL)

        try:
            sb.wait_for_ready_state_complete()
        except Exception:
            pass

        try:
            sb.wait_for_element(INPUT_SELECTOR, timeout=20)
        except Exception:
            logging.warning(f"[{label}] Review page not ready, refreshing once...")
            sb.refresh()
            time.sleep(2)
            sb.wait_for_element(INPUT_SELECTOR, timeout=20)

        self._prepare_review_form(sb)
        self._poke_captcha(sb)

    def _prepare_review_form(self, sb):
        sb.execute_script(
            """
            const submit = document.querySelector(arguments[0]);
            if (submit) {
              submit.classList.remove('is-loading');
              submit.disabled = false;
              submit.style.pointerEvents = '';
            }

            const form = document.querySelector(arguments[1]);
            if (form) {
              form.target = '_self';
            }
            """,
            SUBMIT_SELECTOR,
            FORM_SELECTOR,
        )

    def _populate_form(self, sb, paipu_url, model_tag):
        success = sb.execute_script(
            """
            const paipuUrl = arguments[0];
            const modelTag = arguments[1];

            const dispatch = (el) => {
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            };

            const radio = document.querySelector('input[name="input-method"][value="log-url"]');
            if (radio && !radio.checked) {
              radio.click();
            }

            const input = document.querySelector('input[name="log-url"]');
            if (!input) {
              return false;
            }
            if (input.value !== paipuUrl) {
              input.value = paipuUrl;
              dispatch(input);
            }

            const setSelect = (selector, value) => {
              const el = document.querySelector(selector);
              if (!el || el.value === value) {
                return;
              }
              el.value = value;
              dispatch(el);
            };

            setSelect('select[name="engine"]', 'mortal');
            setSelect('select[name="mortal-model-tag"]', modelTag);
            setSelect('select[name="ui"]', 'classic');

            const details = document.querySelector('details.details.mb-3');
            if (details) {
              details.open = true;
            }

            const showRating = document.querySelector('input[name="show-rating"]');
            if (showRating && !showRating.checked) {
              showRating.click();
            }

            const form = document.querySelector(arguments[2]);
            if (form) {
              form.target = '_self';
            }

            return true;
            """,
            paipu_url,
            model_tag,
            FORM_SELECTOR,
        )

        if not success:
            raise RuntimeError("Could not populate review form")

    def _wait_for_turnstile_token(self, sb, uuid, timeout):
        deadline = time.time() + timeout
        next_poke_at = time.time() + 8

        while time.time() < deadline:
            state = self._read_review_state(sb)
            if state["token_length"] > 0:
                return

            if time.time() >= next_poke_at:
                logging.info(f"[{uuid}] Turnstile token still missing, retrying captcha click")
                self._poke_captcha(sb)
                next_poke_at = time.time() + 8

            time.sleep(0.5)

        raise RuntimeError(f"[{uuid}] Timed out waiting for Turnstile token")

    def _submit_review(self, sb, uuid):
        submitted = sb.execute_script(
            """
            const form = document.querySelector(arguments[0]);
            const submit = document.querySelector(arguments[1]);
            const token = document.querySelector(arguments[2]);
            if (!form || !submit) {
              return 'missing-form';
            }
            if (!token || !token.value) {
              return 'missing-token';
            }

            form.target = '_self';
            submit.disabled = false;
            submit.classList.remove('is-loading');
            submit.style.pointerEvents = '';

            if (typeof form.requestSubmit === 'function') {
              form.requestSubmit(submit);
            } else {
              submit.click();
            }
            return 'submitted';
            """,
            FORM_SELECTOR,
            SUBMIT_SELECTOR,
            TURNSTILE_RESPONSE_SELECTOR,
        )

        if submitted != "submitted":
            raise RuntimeError(f"[{uuid}] Review form submission failed before navigation: {submitted}")

        time.sleep(0.2)

    def _extract_metadata(self, sb):
        metadata = sb.execute_script(
            """
            const data = {};
            for (const dl of document.querySelectorAll('details > dl')) {
              const dts = dl.querySelectorAll('dt');
              const dds = dl.querySelectorAll('dd');
              const count = Math.min(dts.length, dds.length);
              for (let i = 0; i < count; i += 1) {
                data[dts[i].innerText.trim()] = dds[i].innerText.trim();
              }
            }
            return data;
            """
        )
        return metadata or {}

    def _wait_for_result_or_error(self, sb, uuid, timeout):
        deadline = time.time() + timeout
        form_stall_deadline = time.time() + 15
        left_form_page = False

        while time.time() < deadline:
            state = self._read_review_state(sb)
            current_url = state["url"]
            page_text = state["page_text"]

            if REPORT_URL_FRAGMENT in current_url:
                left_form_page = True

            if sb.is_element_present(RESULT_SELECTOR):
                return

            if "invalid captcha response" in page_text or "timeout-or-duplicate" in page_text:
                raise RuntimeError(f"[{uuid}] Turnstile token was rejected")

            if "too many requests" in page_text or "rate limit" in page_text:
                raise RuntimeError(f"[{uuid}] Review site rate limited this request")

            if not left_form_page and time.time() >= form_stall_deadline:
                raise RuntimeError(f"[{uuid}] Review submission never left the form page")

            time.sleep(0.5)

        raise RuntimeError(f"[{uuid}] Timed out waiting for review results")

    def _read_review_state(self, sb):
        return sb.execute_script(
            """
            const submit = document.querySelector(arguments[0]);
            const token = document.querySelector(arguments[1]);
            return {
              url: window.location.href,
              token_length: token && token.value ? token.value.length : 0,
              page_text: document.body ? document.body.innerText.toLowerCase() : '',
              submit_disabled: submit ? !!submit.disabled : true,
              submit_busy: submit ? submit.classList.contains('is-loading') : false,
            };
            """,
            SUBMIT_SELECTOR,
            TURNSTILE_RESPONSE_SELECTOR,
        )

    def _expand_metadata_panel(self, sb, uuid):
        try:
            is_open = sb.execute_script(
                """
                const details = document.querySelector('body > details:nth-child(6)');
                return details ? details.open : false;
                """
            )
            if not is_open:
                sb.click("body > details:nth-child(6) > summary")
                time.sleep(0.5)
        except Exception as exc:
            logging.warning(f"[{uuid}] Could not expand metadata menu: {exc}")

    def _poke_captcha(self, sb):
        try:
            sb.uc_gui_click_captcha()
        except Exception:
            pass
