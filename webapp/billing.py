"""DeepSeek official CNY reference prices, verified 2026-10-23 (not Alibaba bills)."""
from datetime import datetime, timezone, timedelta
from chinese_calendar import is_holiday

SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
BEIJING = timezone(timedelta(hours=8))


def estimate(usage: dict, at: datetime) -> dict:
    local = at.astimezone(BEIJING)
    # The published rule excludes all weekends, including adjusted workdays.
    holiday_known = True
    try:
        holiday = is_holiday(local.date())
    except NotImplementedError:
        holiday_known, holiday = False, False
    peak = local.weekday() < 5 and not holiday and (9 <= local.hour < 12 or 14 <= local.hour < 18)
    prompt = max(0, int(usage.get('prompt_tokens') or 0))
    output = max(0, int(usage.get('completion_tokens') or 0))
    details = usage.get('prompt_tokens_details') or {}
    hit = usage.get('prompt_cache_hit_tokens', details.get('cached_tokens'))
    reported = hit is not None
    if hit is None and usage.get('prompt_cache_miss_tokens') is not None:
        hit = prompt - int(usage['prompt_cache_miss_tokens'])
        reported = True
    hit = min(prompt, max(0, int(hit or 0)))
    miss = prompt - hit
    rates = {'cache_hit': 0.04 if peak else 0.02, 'cache_miss': 2 if peak else 1, 'output': 8 if peak else 4}
    return {
        'currency': 'CNY', 'basis': 'DeepSeek官网参考价（非阿里云实际账单）',
        'source': SOURCE, 'price_verified_date': '2026-10-23',
        'request_time': local.isoformat(), 'period': 'peak' if peak else 'off_peak',
        'holiday_calendar_known': holiday_known,
        'prompt_tokens': prompt, 'cache_hit_tokens': hit, 'cache_miss_tokens': miss,
        'completion_tokens': output, 'cache_reported': reported,
        'rates_cny_per_million': rates,
        'cost_cny': (hit * rates['cache_hit'] + miss * rates['cache_miss'] + output * rates['output']) / 1e6,
    }
