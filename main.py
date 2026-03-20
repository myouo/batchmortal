import argparse
import sys
import logging
import time
import os
from datetime import datetime

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
    
    for mode in modes:
        print(f"\n▶ Processing mode={mode} ────────────────────────────────")
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
            
            print(f"\n[{i + 1}/{len(items)}] mode={mode} uuid={uuid}")
            print(f"  PaipuURL: {paipu_url}")
            
            if args.dry_run:
                print("  [dry-run] Skipping browser analysis.")
                continue
                
            mode_dir = os.path.join(output_root, f"mode_{mode}")
            
            result = automator.analyze_game(paipu_url, uuid, args.model_tag, mode_dir, save_screenshot=args.save_screenshot)
            
            if not result:
                total_failed += 1
                logging.warning(f"  [SKIP] Analysis failed for {uuid}")
                
                append_row(out_path, {
                    'nickname': args.nickname,
                    'mode': mode,
                    'uuid': uuid,
                    'paipuUrl': paipu_url,
                    'modelTag': args.model_tag,
                    'rating': 'ERROR',
                    'timestamp': datetime.utcnow().isoformat() + 'Z'
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
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            }, args.output)
            
            total_processed += 1
            print(f"  ✓ rating={parsed.get('rating', 'N/A')}  AI一致率={parsed.get('aiConsistencyRate', 'N/A')}")

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
