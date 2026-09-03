from client import Client

c = Client()

# Try searching for Russian leagues
test_ids = ["RU1", "RU2", "R3D1", "R3D2", "RUS1", "RUS2", "RUS3"]
for lid in test_ids:
    try:
        data = c.api(f"competitions/{lid}/clubs")
        clubs = data.get("clubs") or []
        name = data.get("name", "?")
        print(f"{lid}: {name} ({len(clubs)} clubs)")
    except Exception as e:
        print(f"{lid}: FAILED - {type(e).__name__}")

# Also try the competition search
try:
    data = c.api("competitions/search/Russia")
    results = data.get("results") or []
    print(f"\nSearch 'Russia': {len(results)} results")
    for r in results[:10]:
        print(f"  {r.get('id')}: {r.get('name')}")
except Exception as e:
    print(f"Search failed: {e}")
