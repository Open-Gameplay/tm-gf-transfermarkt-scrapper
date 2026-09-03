from client import Client
import time

c = Client()

# Try common country codes to find all leagues
country_codes = [
    "RU", "GB", "ES", "DE", "IT", "FR", "BR", "AR", "PT", "NL",
    "BE", "TR", "UA", "GR", "DK", "NO", "SE", "CH", "AT", "CZ",
    "PL", "RO", "HR", "RS", "BG", "HU", "SK", "SI", "CL", "CO",
    "MX", "US", "CA", "JP", "KR", "CN", "IN", "AU", "SA", "AE",
    "EG", "MA", "TN", "NG", "ZA", "DZ", "GH", "CM", "SN", "CI",
]

all_leagues = {}
for code in country_codes:
    try:
        data = c.api(f"competitions/search/{code}")
        results = data.get("results") or []
        for r in results:
            lid = r.get("id")
            name = r.get("name")
            if lid and lid not in all_leagues:
                all_leagues[lid] = name
        time.sleep(0.5)  # Be nice to API
    except:
        pass

print(f"Total unique leagues found: {len(all_leagues)}")

# Check which ones work via API
working = 0
failed = 0
for lid in sorted(all_leagues.keys()):
    try:
        data = c.api(f"competitions/{lid}/clubs")
        clubs = data.get("clubs") or []
        if clubs:
            working += 1
        else:
            failed += 1
    except:
        failed += 1

print(f"Working: {working}, Failed: {failed}")
