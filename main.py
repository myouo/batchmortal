import argparse
import sys
import logging
import time
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from api import search_player, get_player_records, build_paipu_urls
from browser import BrowserAutomator
from results import parse_metadata, append_row

logging.basicConfig(level=logging.INFO, format='%(message)s')

def parse_args():
    parser = argparse.ArgumentParser(description="Batch Mortal Analysis Script (Python/SeleniumBase Edition)")
    parser.add_argument('nickname', help='Player nickname')
    parser.add_argument('--limit', type=int, default=10, help='Max records per mode (default: 10)')
    parser.add_argument('--modes', default='9', help='Comma-separated mode IDs (default: 9)')
    parser.add_argument('--model-tag', default='4.1b', help='Mortal network version (default: 4.1b)')
    parser.add_argument('--headless', action='store_true', help='Run browser headlessly')
    parser.add_argument('--dry-run', action='store_true', help='Only print URLs, skip browser')
    parser.add_argument('--no-manual-verification', action='store_true', help='(Legacy) Ignore, no longer applies to SeleniumBase')
    parser.add_argument('--flare-url', help='(Legacy) Ignore, FlareSolverr is replaced by SeleniumBase')
    parser.add_argument('--save-screenshot', action='store_true', help='Save screenshot of the results (default: False)')
    parser.add_argument('--output', choices=['csv', 'xlsx'], default='xlsx', help='Output format: csv or xlsx (default: xlsx)')
    parser.add_argument('--proxy', help='Proxy URL (e.g. http://127.0.0.1:7890). If omitted, attempts to use system proxy.')
    parser.add_argument('--workers', type=int, default=3, help='Max parallel browser instances (default: 3)')
    return parser.parse_args()

def main():
    import time
    start_time = time.time()
    
    args = parse_args()
    
    modes = [int(m.strip()) for m in args.modes.split(',')]
    
    print("\n═══ Batch Mortal Analysis (SeleniumBase) ═════════════════════")
    print(f"  Player:    {args.nickname}")
    print(f"  Modes:     {modes}")
    print(f"  Limit:     {args.limit} per mode")
    print(f"  ModelTag:  {args.model_tag}")
    print(f"  Headless:  {args.headless}")
    print(f"  DryRun:    {args.dry_run}")
    print("════════════════════════════════════════════════════════════\n")
    
    try:
        account_id = search_player(args.nickname)
    except Exception as e:
        logging.error(f"[FATAL] {e}")
        sys.exit(1)
        
    safe_nick = "".join(c if c.isalnum() or c in ("_","-","\u4e00","\u9fa5") else "_" for c in args.nickname)
    output_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results', safe_nick)
    out_path = os.path.join(output_root, f'results.{args.output}')
    
    total_processed = 0
    total_failed = 0
    
    import urllib.request
    proxy = args.proxy
    if not proxy:
        sys_proxies = urllib.request.getproxies()
        proxy = sys_proxies.get('https') or sys_proxies.get('http')

    if proxy:
        logging.info(f"[Proxy] Using proxy for browser: {proxy}")
    else:
        logging.info(f"[Proxy] No system proxy detected, running directly.")
        
    automator = BrowserAutomator(headless=args.headless, proxy=proxy)
    
    tasks = []
    
    for mode in modes:
        print(f"\n▶ Fetching records for mode={mode} ────────────────────────────────")
        try:
            records = get_player_records(account_id, args.limit, mode)
        except Exception as e:
            logging.error(f"[ERROR] mode={mode}: {e} - skipping this mode")
            continue
            
        if not records:
            logging.info(f"[mode={mode}] No records found. Skipping.")
            continue
            
        items = build_paipu_urls(records, account_id)
        
        for i, item in enumerate(items):
            uuid = item['uuid']
            paipu_url = item['paipuUrl']
            mode_dir = os.path.join(output_root, f"mode_{mode}")
            
            tasks.append({
                'idx': i + 1,
                'total': len(items),
                'mode': mode,
                'uuid': uuid,
                'paipu_url': paipu_url,
                'mode_dir': mode_dir
            })

    if args.dry_run:
        for t in tasks:
            print(f"\n[{t['idx']}/{t['total']}] mode={t['mode']} uuid={t['uuid']}")
            print(f"  [dry-run] PaipuURL: {t['paipu_url']}")
            total_processed += 1
    elif tasks:
        max_workers = min(args.workers, len(tasks))
        print(f"\n▶ Starting parallel analysis with {max_workers} workers ────────────────────────────────")
        
        MAX_RETRIES = 2
        
        while tasks:
            if len(tasks) > 0:
                print(f"\n▶ Executing batch of {len(tasks)} tasks...")
            next_tasks = []
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_task = {
                    executor.submit(
                        automator.analyze_game,
                        t['paipu_url'],
                        t['uuid'],
                        args.model_tag,
                        t['mode_dir'],
                        save_screenshot=args.save_screenshot
                    ): t for t in tasks
                }
                
                for future in as_completed(future_to_task):
                    t = future_to_task[future]
                    uuid = t['uuid']
                    mode = t['mode']
                    paipu_url = t['paipu_url']
                    
                    try:
                        result = future.result()
                    except Exception as exc:
                        logging.error(f"  [ERROR] {uuid} generated an exception: {exc}")
                        result = None
                    
                    if not result:
                        t['retries'] = t.get('retries', 0) + 1
                        if t['retries'] <= MAX_RETRIES:
                            logging.warning(f"  [RETRY] Analysis failed for {uuid}. Added back to queue (Retry {t['retries']}/{MAX_RETRIES}).")
                            next_tasks.append(t)
                        else:
                            total_failed += 1
                            logging.error(f"  [SKIP] Analysis permanently failed for {uuid} after {MAX_RETRIES} retries.")
                            
                            append_row(out_path, {
                                'nickname': args.nickname,
                                'mode': mode,
                                'uuid': uuid,
                                'paipuUrl': paipu_url,
                                'modelTag': args.model_tag,
                                'rating': 'ERROR',
                                'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                            }, args.output)
                        continue
                        
                    parsed = parse_metadata(result['metadata'])
                    
                    append_row(out_path, {
                        'nickname': args.nickname,
                        'mode': mode,
                        'uuid': uuid,
                        'paipuUrl': paipu_url,
                        'resultUrl': result['resultUrl'],
                        'modelTag': parsed.get('modelTag') or args.model_tag,
                        'rating': parsed.get('rating', ''),
                        'aiConsistencyRate': parsed.get('aiConsistencyRate', ''),
                        'aiConsistencyNumerator': parsed.get('aiConsistencyNumerator', ''),
                        'aiConsistencyDenominator': parsed.get('aiConsistencyDenominator', ''),
                        'temperature': parsed.get('temperature', ''),
                        'gameLength': parsed.get('gameLength', ''),
                        'playerId': parsed.get('playerId', ''),
                        'reviewDuration': parsed.get('reviewDuration', ''),
                        'screenshotPath': result.get('screenshotPath', ''),
                        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                    }, args.output)
                    
                    total_processed += 1
                    print(f"  ✓ rating={parsed.get('rating', 'N/A')}  AI一致率={parsed.get('aiConsistencyRate', 'N/A')} ({uuid})")
                    
            tasks = next_tasks

    end_time = time.time()
    elapsed = end_time - start_time
    
    print(f"\n═══ Done ═════════════════════════════════════════════════")
    print(f"  Succeeded: {total_processed}")
    print(f"  Failed:    {total_failed}")
    print(f"  Time:      {elapsed:.2f}s")
    if not args.dry_run:
        print(f"  Output:    {out_path}")
    print(f"══════════════════════════════════════════════════════════\n")

if __name__ == '__main__':
    main()
