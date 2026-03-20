import logging
import os
import queue
import threading
import time
from collections import deque

from seleniumbase import SB

REVIEW_URL = "https://mjai.ekyu.moe/zh-cn.html"
INPUT_SELECTOR = 'input[name="log-url"]'
SUBMIT_SELECTOR = 'button[name="submitBtn"]'
FORM_SELECTOR = 'form[name="reviewForm"]'
TURNSTILE_RESPONSE_SELECTOR = 'input[name="cf-turnstile-response"]'
RESULT_SELECTOR = "details > dl"
REPORT_URL_FRAGMENT = "/report/"


class ReviewSubmissionCoordinator:
    def __init__(
        self,
        base_interval=6.0,
        max_interval=20.0,
        cooldown_seconds=30.0,
        failure_threshold=2,
    ):
        self.base_interval = base_interval
        self.current_interval = base_interval
        self.max_interval = max_interval
        self.cooldown_seconds = cooldown_seconds
        self.failure_threshold = failure_threshold
        self.condition = threading.Condition()
        self.active_uuid = None
        self.next_submit_time = 0.0
        self.cooldown_until = 0.0
        self.consecutive_failures = 0

    def wait_for_submit_slot(self, uuid):
        with self.condition:
            while True:
                now = time.monotonic()
                if self.active_uuid is None and now >= self.next_submit_time and now >= self.cooldown_until:
                    self.active_uuid = uuid
                    return

                wake_at = max(self.next_submit_time, self.cooldown_until)
                if self.active_uuid is not None:
                    timeout = 0.5
                else:
                    timeout = max(0.2, wake_at - now)
                self.condition.wait(timeout=min(timeout, 1.0))

    def release_submit_slot(self, uuid, token_wait_seconds=0.0):
        with self.condition:
            if self.active_uuid == uuid:
                self.active_uuid = None

            self._adapt_interval_from_token(token_wait_seconds)
            self.next_submit_time = max(time.monotonic(), self.next_submit_time) + self.current_interval
            self.condition.notify_all()

    def report_outcome(
        self,
        uuid,
        success,
        error_text="",
        token_wait_seconds=0.0,
        submit_wait_seconds=0.0,
        result_wait_seconds=0.0,
    ):
        del uuid
        with self.condition:
            if success:
                self.consecutive_failures = 0
                if token_wait_seconds <= 8 and submit_wait_seconds <= 8 and result_wait_seconds <= 18:
                    self.current_interval = max(self.base_interval, self.current_interval - 0.5)
                elif token_wait_seconds >= 6 or submit_wait_seconds >= 10 or result_wait_seconds >= 18:
                    self.current_interval = min(self.max_interval, self.current_interval + 1.5)
            else:
                normalized = (error_text or "").lower()
                if any(
                    marker in normalized
                    for marker in (
                        "captcha",
                        "turnstile",
                        "rate limit",
                        "stalled before token issuance",
                        "timed out waiting for turnstile token",
                        "review submission never left the form page",
                        "timed out waiting for review results",
                    )
                ):
                    self.consecutive_failures += 1
                    self.current_interval = min(self.max_interval, self.current_interval + 2.5)
                    if self.consecutive_failures >= self.failure_threshold:
                        self.cooldown_until = max(
                            self.cooldown_until,
                            time.monotonic() + self.cooldown_seconds,
                        )
                        logging.warning(
                            "[Throttle] Consecutive review failures detected. "
                            f"Cooling down submissions for {self.cooldown_seconds:.0f}s."
                        )
                else:
                    self.consecutive_failures = 0

            self.condition.notify_all()

    def _adapt_interval_from_token(self, token_wait_seconds):
        if token_wait_seconds <= 0:
            return
        if token_wait_seconds >= 20:
            self.current_interval = min(self.max_interval, self.current_interval + 2.0)
        elif token_wait_seconds >= 12:
            self.current_interval = min(self.max_interval, self.current_interval + 1.0)
        elif token_wait_seconds <= 6:
            self.current_interval = max(self.base_interval, self.current_interval - 0.25)


class BrowserAutomator:
    def __init__(self, headless=True, proxy=None, submission_coordinator=None, controlled_submission=True):
        self.headless = headless
        self.proxy = proxy
        self.controlled_submission = controlled_submission
        if controlled_submission:
            self.submission_coordinator = submission_coordinator or ReviewSubmissionCoordinator()
        else:
            self.submission_coordinator = None

    def run_worker(self, task_queue, result_queue, max_retries=3):
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
                            logging.error(f"{task.get('log_prefix', '[' + task['uuid'] + ']')} ERROR exception: {exc}")

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
                                    f"{task.get('log_prefix', '[' + task['uuid'] + ']')} RETRY Analysis failed. "
                                    f"Retrying ({task['retries']}/{max_retries}) with a fresh page load."
                                )
                                task_queue.put(task)
                            else:
                                logging.error(
                                    f"{task.get('log_prefix', '[' + task['uuid'] + ']')} SKIP Analysis permanently failed "
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

    def iter_alternating_windows(self, tasks, max_retries=3):
        pending = deque(tasks)

        with SB(uc=True, headless=self.headless, proxy=self.proxy) as sb:
            slots = [
                {
                    "name": "window-a",
                    "handle": sb.driver.current_window_handle,
                    "ready": False,
                },
                {
                    "name": "window-b",
                    "handle": None,
                    "ready": False,
                },
            ]

            slot_index = 0
            while pending:
                task = pending.popleft()
                slot = slots[slot_index]
                slot_index = (slot_index + 1) % len(slots)

                try:
                    started_at = time.perf_counter()
                    self._ensure_rotation_slot_ready(sb, slot, task.get("log_prefix", '[' + task["uuid"] + ']'))
                    result = self._analyze_loaded_form(
                        sb,
                        task,
                        started_at=started_at,
                        ready_message=f"{slot['name']} ready for assigned task",
                    )
                    yield {"status": "success", "task": task, "result": result}
                except Exception as exc:
                    failure_event = self._handle_rotation_failure(
                        sb,
                        slot,
                        task,
                        exc,
                        max_retries,
                        pending,
                    )
                    if failure_event:
                        yield failure_event
                finally:
                    slot["ready"] = False

    def iter_dual_window_pipeline(self, tasks, max_retries=3):
        pending = deque(tasks)

        with SB(uc=True, headless=self.headless, proxy=self.proxy) as sb:
            active_slot = {
                "name": "active",
                "handle": sb.driver.current_window_handle,
                "task": None,
                "prepared": False,
                "started_at": 0.0,
                "submitted_at": 0.0,
            }
            standby_slot = {
                "name": "standby",
                "handle": self._open_pipeline_tab(sb),
                "task": None,
                "prepared": False,
                "started_at": 0.0,
                "submitted_at": 0.0,
            }

            while pending or active_slot["task"] or standby_slot["task"]:
                if active_slot["task"] is None:
                    if standby_slot["task"] and standby_slot["prepared"]:
                        active_slot, standby_slot = standby_slot, active_slot
                    elif standby_slot["task"] and not standby_slot["prepared"]:
                        self._reset_pipeline_slot(standby_slot)
                        continue
                    elif pending:
                        task = pending.popleft()
                        failure_event = self._prepare_pipeline_slot(sb, active_slot, task, max_retries, pending)
                        if failure_event:
                            yield failure_event
                            continue
                    else:
                        break

                if active_slot["task"] is None:
                    break

                active_task = active_slot["task"]

                try:
                    token_wait_seconds = self._submit_pipeline_slot(sb, active_slot, active_task)

                    if standby_slot["task"] is None and pending:
                        next_task = pending.popleft()
                        failure_event = self._prepare_pipeline_slot(sb, standby_slot, next_task, max_retries, pending)
                        if failure_event:
                            yield failure_event

                    result = self._collect_pipeline_result(sb, active_slot, active_task)
                    yield {"status": "success", "task": active_task, "result": result}
                except Exception as exc:
                    failure_event = self._handle_pipeline_failure(
                        sb,
                        active_slot,
                        active_task,
                        exc,
                        max_retries,
                        pending,
                    )
                    if failure_event:
                        yield failure_event
                finally:
                    active_slot["task"] = None
                    active_slot["prepared"] = False
                    active_slot["started_at"] = 0.0
                    active_slot["submitted_at"] = 0.0

                if standby_slot["task"] and standby_slot["prepared"]:
                    active_slot, standby_slot = standby_slot, active_slot

    def analyze_single(self, sb, task):
        uuid = task["uuid"]
        log_prefix = task.get("log_prefix", f"[{uuid}]")
        started_at = time.perf_counter()

        logging.info(f"{log_prefix} Opening fresh review form")
        self._open_fresh_review_page(sb, log_prefix)
        return self._analyze_loaded_form(
            sb,
            task,
            started_at=started_at,
            ready_message="Form ready and waiting for submit slot",
        )

    def _analyze_loaded_form(self, sb, task, started_at, ready_message):
        paipu_url = task["paipu_url"]
        uuid = task["uuid"]
        log_prefix = task.get("log_prefix", f"[{uuid}]")
        model_tag = task["model_tag"]
        output_dir = task["mode_dir"]
        save_screenshot = task.get("save_screenshot", False)

        os.makedirs(output_dir, exist_ok=True)
        screenshot_path = os.path.join(output_dir, f"{uuid}.png")
        token_wait_seconds = 0.0
        submit_wait_seconds = 0.0
        result_wait_seconds = 0.0
        submit_slot_held = False
        submit_slot_released = False

        self._populate_form(sb, paipu_url, model_tag)
        logging.info(f"{log_prefix} {ready_message}")

        try:
            if self.submission_coordinator is not None:
                self.submission_coordinator.wait_for_submit_slot(log_prefix)
                submit_slot_held = True
                logging.info(f"{log_prefix} Submit slot granted, starting Turnstile")
            else:
                logging.info(f"{log_prefix} Unthrottled mode, starting Turnstile")
            self._prepare_review_form(sb)
            self._poke_captcha(sb)

            token_started_at = time.perf_counter()
            self._wait_for_turnstile_token(sb, log_prefix, timeout=35)
            token_wait_seconds = time.perf_counter() - token_started_at
            logging.info(f"{log_prefix} Turnstile token ready in {token_wait_seconds:.1f}s")

            submit_started_at = time.perf_counter()
            self._submit_review(sb, log_prefix)
            self._wait_for_submission_departure_or_error(sb, log_prefix, timeout=15)
            submit_wait_seconds = time.perf_counter() - submit_started_at
            if self.submission_coordinator is not None:
                self.submission_coordinator.release_submit_slot(log_prefix, token_wait_seconds=token_wait_seconds)
                submit_slot_released = True

            logging.info(f"{log_prefix} Review submitted, waiting for final result")
            result_started_at = time.perf_counter()
            self._wait_for_result_or_error(sb, log_prefix, timeout=45)
            result_wait_seconds = time.perf_counter() - result_started_at
            result_url = sb.get_current_url()
            metadata = self._extract_metadata(sb)
            if self.submission_coordinator is not None:
                self.submission_coordinator.report_outcome(
                    log_prefix,
                    success=True,
                    token_wait_seconds=token_wait_seconds,
                    submit_wait_seconds=submit_wait_seconds,
                    result_wait_seconds=result_wait_seconds,
                )
            logging.info(
                f"{log_prefix} Result ready in {time.perf_counter() - started_at:.1f}s: {result_url}"
            )

            saved_screenshot_path = ""
            if save_screenshot:
                self._expand_metadata_panel(sb, log_prefix)
                sb.save_screenshot(screenshot_path)
                saved_screenshot_path = screenshot_path
                logging.info(f"{log_prefix} Screenshot saved to {screenshot_path}")

            return {
                "resultUrl": result_url,
                "screenshotPath": saved_screenshot_path,
                "metadata": metadata,
            }
        except Exception as exc:
            if self.submission_coordinator is not None:
                self.submission_coordinator.report_outcome(
                    log_prefix,
                    success=False,
                    error_text=str(exc),
                    token_wait_seconds=token_wait_seconds,
                    submit_wait_seconds=submit_wait_seconds,
                    result_wait_seconds=result_wait_seconds,
                )
            raise
        finally:
            if self.submission_coordinator is not None and submit_slot_held and not submit_slot_released:
                self.submission_coordinator.release_submit_slot(
                    log_prefix,
                    token_wait_seconds=token_wait_seconds,
                )

    def _handle_rotation_failure(self, sb, slot, task, exc, max_retries, pending):
        err_str = str(exc).lower()
        logging.error(f"{task.get('log_prefix', '[' + task['uuid'] + ']')} ERROR exception: {exc}")

        try:
            self._switch_to_slot(sb, slot)
            error_screenshot = os.path.join(task["mode_dir"], f"{task['uuid']}_error.png")
            sb.save_screenshot(error_screenshot)
        except Exception:
            pass

        if any(marker in err_str for marker in ("no such window", "closed", "invalid session", "disconnected")):
            raise exc

        task["retries"] = task.get("retries", 0) + 1
        if task["retries"] <= max_retries:
            logging.warning(
                f"{task.get('log_prefix', '[' + task['uuid'] + ']')} RETRY Analysis failed. "
                f"Retrying ({task['retries']}/{max_retries}) on the next window turn."
            )
            pending.append(task)
            return None

        logging.error(
            f"{task.get('log_prefix', '[' + task['uuid'] + ']')} SKIP Analysis permanently failed after {max_retries} retries."
        )
        return {"status": "fail", "task": task}

    def _open_pipeline_window(self, sb):
        current_handle = sb.driver.current_window_handle
        try:
            sb.driver.switch_to.new_window("window")
            new_handle = sb.driver.current_window_handle
            sb.driver.switch_to.window(current_handle)
            return new_handle
        except Exception:
            existing_handles = set(sb.driver.window_handles)
            sb.execute_script("window.open('about:blank', '_blank');")

            deadline = time.time() + 5
            while time.time() < deadline:
                current_handles = set(sb.driver.window_handles)
                new_handles = current_handles - existing_handles
                if new_handles:
                    new_handle = new_handles.pop()
                    sb.driver.switch_to.window(current_handle)
                    return new_handle
                time.sleep(0.2)

        raise RuntimeError("Could not open standby browser window")

    def _prepare_pipeline_slot(self, sb, slot, task, max_retries, pending):
        try:
            self._switch_to_slot(sb, slot)
            self._prepare_task_in_current_tab(sb, task)
            self._refresh_slot_handle(sb, slot)
            slot["task"] = task
            slot["prepared"] = True
            slot["started_at"] = time.perf_counter()
            slot["submitted_at"] = 0.0
            logging.info(f"{task.get('log_prefix', '[' + task['uuid'] + ']')} Form prewarmed in {slot['name']} tab")
            return None
        except Exception as exc:
            return self._handle_pipeline_failure(sb, slot, task, exc, max_retries, pending)

    def _submit_pipeline_slot(self, sb, slot, task):
        self._switch_to_slot(sb, slot)
        uuid = task["uuid"]
        log_prefix = task.get("log_prefix", f"[{uuid}]")

        logging.info(f"{log_prefix} {slot['name']} tab entering Turnstile")
        token_started_at = time.perf_counter()
        self._wait_for_turnstile_token(sb, log_prefix, timeout=35)
        token_wait_seconds = time.perf_counter() - token_started_at
        logging.info(f"{log_prefix} Turnstile token ready in {token_wait_seconds:.1f}s")

        self._submit_review(sb, log_prefix)
        self._wait_for_submission_departure_or_error(sb, log_prefix, timeout=15)
        self._refresh_slot_handle(sb, slot)
        slot["submitted_at"] = time.perf_counter()
        logging.info(f"{log_prefix} Review submitted from {slot['name']} tab")
        return token_wait_seconds

    def _collect_pipeline_result(self, sb, slot, task):
        self._switch_to_slot(sb, slot)
        uuid = task["uuid"]
        log_prefix = task.get("log_prefix", f"[{uuid}]")
        screenshot_path = os.path.join(task["mode_dir"], f"{uuid}.png")
        save_screenshot = task.get("save_screenshot", False)

        logging.info(f"{log_prefix} Waiting for result page")
        self._wait_for_result_or_error(sb, log_prefix, timeout=45)
        result_url = sb.get_current_url()
        metadata = self._extract_metadata(sb)

        total_elapsed = time.perf_counter() - slot["started_at"] if slot["started_at"] else 0.0
        logging.info(f"{log_prefix} Result ready in {total_elapsed:.1f}s: {result_url}")

        saved_screenshot_path = ""
        if save_screenshot:
            self._expand_metadata_panel(sb, log_prefix)
            sb.save_screenshot(screenshot_path)
            saved_screenshot_path = screenshot_path
            logging.info(f"{log_prefix} Screenshot saved to {screenshot_path}")

        return {
            "resultUrl": result_url,
            "screenshotPath": saved_screenshot_path,
            "metadata": metadata,
        }

    def _handle_pipeline_failure(self, sb, slot, task, exc, max_retries, pending):
        err_str = str(exc).lower()
        logging.error(f"{task.get('log_prefix', '[' + task['uuid'] + ']')} ERROR exception: {exc}")

        try:
            self._switch_to_slot(sb, slot)
            error_screenshot = os.path.join(task["mode_dir"], f"{task['uuid']}_error.png")
            sb.save_screenshot(error_screenshot)
        except Exception:
            pass

        if any(marker in err_str for marker in ("no such window", "closed", "invalid session", "disconnected")):
            raise exc

        self._reset_pipeline_slot(slot)
        task["retries"] = task.get("retries", 0) + 1
        if task["retries"] <= max_retries:
            logging.warning(
                f"{task.get('log_prefix', '[' + task['uuid'] + ']')} RETRY Analysis failed. "
                f"Retrying ({task['retries']}/{max_retries}) with a fresh page load."
            )
            pending.append(task)
            return None

        logging.error(
            f"{task.get('log_prefix', '[' + task['uuid'] + ']')} SKIP Analysis permanently failed after {max_retries} retries."
        )
        return {"status": "fail", "task": task}

    def _reset_pipeline_slot(self, slot):
        slot["task"] = None
        slot["prepared"] = False
        slot["started_at"] = 0.0
        slot["submitted_at"] = 0.0

    def _switch_to_slot(self, sb, slot):
        handles = list(sb.driver.window_handles)
        if slot["handle"] not in handles:
            current_handle = None
            try:
                current_handle = sb.driver.current_window_handle
            except Exception:
                current_handle = None

            if current_handle in handles:
                slot["handle"] = current_handle
            elif handles:
                slot["handle"] = handles[-1]
            else:
                raise RuntimeError(f"{slot['name']} tab is no longer available")

        sb.driver.switch_to.window(slot["handle"])
        try:
            sb.wait_for_ready_state_complete()
        except Exception:
            pass

    def _refresh_slot_handle(self, sb, slot):
        try:
            slot["handle"] = sb.driver.current_window_handle
        except Exception:
            pass

    def _prepare_task_in_current_tab(self, sb, task):
        uuid = task["uuid"]
        log_prefix = task.get("log_prefix", f"[{uuid}]")
        logging.info(f"{log_prefix} Opening fresh review form")
        self._open_fresh_review_page(sb, log_prefix)
        self._populate_form(sb, task["paipu_url"], task["model_tag"])
        self._prepare_review_form(sb)
        self._poke_captcha(sb)

    def _prime_rotation_slot(self, sb, slot, label):
        if not slot.get("handle"):
            self._spawn_rotation_window(sb, slot, label)
        self._switch_to_slot(sb, slot)
        logging.info(f"{label} Refreshing {slot['name']} back to review form")
        self._open_fresh_review_page(sb, label)
        self._refresh_slot_handle(sb, slot)
        slot["ready"] = True

    def _ensure_rotation_slot_ready(self, sb, slot, label):
        if not slot.get("handle"):
            self._spawn_rotation_window(sb, slot, label)
        self._switch_to_slot(sb, slot)
        if not slot.get("ready", False) or not self._is_review_form_ready(sb):
            logging.info(f"{label} Preparing {slot['name']} for the next assigned task")
            self._prime_rotation_slot(sb, label=label, slot=slot)
            slot["ready"] = True
        self._refresh_slot_handle(sb, slot)

    def _is_review_form_ready(self, sb):
        try:
            return bool(
                sb.execute_script(
                    """
                    const input = document.querySelector(arguments[0]);
                    return !!(input && document.readyState !== 'loading');
                    """,
                    INPUT_SELECTOR,
                )
            )
        except Exception:
            return False

    def _spawn_rotation_window(self, sb, slot, label):
        current_handle = sb.driver.current_window_handle
        existing_handles = set(sb.driver.window_handles)
        logging.info(f"{label} Spawning {slot['name']} from the active review context")

        try:
            sb.execute_script("window.open(arguments[0], '_blank');", REVIEW_URL)
        except Exception:
            sb.driver.switch_to.new_window("window")
            slot["handle"] = sb.driver.current_window_handle
            self._open_fresh_review_page(sb, label)
            self._refresh_slot_handle(sb, slot)
            sb.driver.switch_to.window(current_handle)
            return

        deadline = time.time() + 8
        while time.time() < deadline:
            current_handles = set(sb.driver.window_handles)
            new_handles = list(current_handles - existing_handles)
            if new_handles:
                slot["handle"] = new_handles[-1]
                self._switch_to_slot(sb, slot)
                logging.info(f"{label} {slot['name']} spawned successfully")
                self._refresh_slot_handle(sb, slot)
                try:
                    sb.driver.switch_to.window(current_handle)
                except Exception:
                    pass
                return
            time.sleep(0.2)

        raise RuntimeError(f"{label} Could not spawn {slot['name']}")

    def _open_fresh_review_page(self, sb, label):
        last_exc = None
        for attempt in range(2):
            current_url = ""
            try:
                current_url = sb.get_current_url()
            except Exception:
                current_url = ""

            try:
                if "mjai.ekyu.moe" in current_url:
                    sb.execute_script("window.location.replace(arguments[0]);", REVIEW_URL)
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
                return
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    logging.warning(f"{label} Review page not ready, retrying open once...")
                    try:
                        sb.execute_script("window.location.replace(arguments[0]);", REVIEW_URL)
                    except Exception:
                        try:
                            sb.refresh()
                        except Exception:
                            pass
                    time.sleep(2)

        raise last_exc

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

    def _wait_for_turnstile_token(self, sb, log_prefix, timeout):
        deadline = time.time() + timeout
        next_poke_at = time.time() + 8
        recoveries = 0

        while time.time() < deadline:
            state = self._read_review_state(sb)
            if state["token_length"] > 0:
                return

            if state["page_text"] and (
                "invalid captcha response" in state["page_text"]
                or "timeout-or-duplicate" in state["page_text"]
            ):
                raise RuntimeError(f"{log_prefix} Turnstile token was rejected before submission")

            if time.time() >= next_poke_at:
                recoveries += 1
                logging.info(f"{log_prefix} Turnstile token still missing, retrying captcha click")
                self._recover_turnstile_widget(sb)
                self._poke_captcha(sb)
                if recoveries >= 2:
                    raise RuntimeError(f"{log_prefix} Turnstile widget stalled before token issuance")
                next_poke_at = time.time() + 8

            time.sleep(0.5)

        raise RuntimeError(f"{log_prefix} Timed out waiting for Turnstile token")

    def _submit_review(self, sb, log_prefix):
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
            raise RuntimeError(f"{log_prefix} Review form submission failed before navigation: {submitted}")

        time.sleep(0.2)

    def _wait_for_submission_departure_or_error(self, sb, log_prefix, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self._read_review_state(sb)
            current_url = state["url"]
            page_text = state["page_text"]

            if REPORT_URL_FRAGMENT in current_url and current_url != REVIEW_URL:
                return

            if "invalid captcha response" in page_text or "timeout-or-duplicate" in page_text:
                raise RuntimeError(f"{log_prefix} Turnstile token was rejected")

            if "too many requests" in page_text or "rate limit" in page_text:
                raise RuntimeError(f"{log_prefix} Review site rate limited this request")

            time.sleep(0.5)

        raise RuntimeError(f"{log_prefix} Review submission never left the form page")

    def _extract_metadata(self, sb):
        metadata = sb.execute_script(
            """
            const data = {};
            for (const dl of document.querySelectorAll('details > dl')) {
              const dts = dl.querySelectorAll('dt');
              const dds = dl.querySelectorAll('dd');
              const count = Math.min(dts.length, dds.length);
              for (let i = 0; i < count; i += 1) {
                data[dts[i].textContent.trim()] = dds[i].textContent.trim();
              }
            }
            return data;
            """
        )
        return metadata or {}

    def _wait_for_result_or_error(self, sb, log_prefix, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if sb.is_element_present(RESULT_SELECTOR):
                return

            state = self._read_review_state(sb)
            page_text = state["page_text"]

            if "invalid captcha response" in page_text or "timeout-or-duplicate" in page_text:
                raise RuntimeError(f"{log_prefix} Turnstile token was rejected")

            if "too many requests" in page_text or "rate limit" in page_text:
                raise RuntimeError(f"{log_prefix} Review site rate limited this request")

            time.sleep(0.5)

        raise RuntimeError(f"{log_prefix} Timed out waiting for review results")

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

    def _expand_metadata_panel(self, sb, log_prefix):
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
            logging.warning(f"{log_prefix} Could not expand metadata menu: {exc}")

    def _poke_captcha(self, sb):
        try:
            sb.uc_gui_click_captcha()
        except Exception:
            pass

    def _recover_turnstile_widget(self, sb):
        try:
            sb.execute_script(
                """
                const token = document.querySelector(arguments[0]);
                if (token) {
                  token.value = '';
                }

                const submit = document.querySelector(arguments[1]);
                if (submit) {
                  submit.disabled = false;
                  submit.classList.remove('is-loading');
                  submit.style.pointerEvents = '';
                }

                if (window.turnstile) {
                  const widgets = Array.from(document.querySelectorAll('.cf-turnstile'));
                  for (const widget of widgets) {
                    const widgetId = widget.getAttribute('data-widget-id');
                    try {
                      if (widgetId) {
                        window.turnstile.reset(widgetId);
                      } else {
                        window.turnstile.reset();
                      }
                    } catch (e) {
                    }
                  }
                }
                """,
                TURNSTILE_RESPONSE_SELECTOR,
                SUBMIT_SELECTOR,
            )
        except Exception:
            pass
