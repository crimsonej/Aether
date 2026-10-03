#!/usr/bin/env python3
import sys

# Check all providers
providers_to_check = [
    ('aether.core.model_manager', 'ClaudeProvider'),
    ('aether.core.model_manager', 'OpenAIProvider'),
    ('aether.core.model_manager', 'GeminiProvider'),
    ('aether.core.model_manager', 'NVIDIAProvider'),
    ('aether.core.model_manager', 'OpenRouterProvider'),
    ('aether.core.model_manager', 'LocalProvider'),
    ('aether.core.data.providers.yahoo', 'YahooProvider'),
    ('aether.core.data.providers.alphavantage', 'AlphaVantageProvider'),
    ('aether.core.data.providers.twelvedata', 'TwelveDataProvider'),
    ('aether.core.data.providers.oanda', 'OandaProvider'),
    ('aether.core.data.providers.frankfurter', 'FrankfurterProvider'),
    ('aether.core.data.providers.coingecko', 'CoinGeckoProvider'),
    ('aether.core.data.providers.finnhub', 'FinnhubProvider'),
]

print('Checking all providers...\n')
failed = []
for module_name, class_name in providers_to_check:
    try:
        mod = __import__(module_name, fromlist=[class_name])
        cls = getattr(mod, class_name)
        print(f'✓ {class_name:30} OK')
    except Exception as e:
        print(f'✗ {class_name:30} FAILED: {str(e)[:60]}')
        failed.append((class_name, str(e)))

if failed:
    print(f'\n[!] {len(failed)} providers failed to load')
    for name, err in failed:
        print(f'    {name}: {err[:80]}')
    sys.exit(1)
else:
    print(f'\n[✓] All {len(providers_to_check)} providers loaded successfully!')
