from client import Client
from bs4 import BeautifulSoup

c = Client()

# Check what leagues are on the national index
resp = c.get_html("https://www.transfermarkt.com/wettbewerbe/national/")
soup = BeautifulSoup(resp.text, "html.parser")
national_ids = set()
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "/wettbewerb/" in href:
        lid = href.split("/wettbewerb/")[-1].split("/")[0].split("?")[0]
        if lid:
            national_ids.add(lid)
print(f"National index: {len(national_ids)} leagues")

# Check Russia specifically
russia_leagues = [lid for lid in national_ids if lid.startswith("RU")]
print(f"Russia leagues from national index: {russia_leagues}")

# Check if R3D1 works via API
try:
    data = c.api("competitions/R3D1/clubs")
    clubs = data.get("clubs") or []
    print(f"R3D1 via API: {len(clubs)} clubs")
except Exception as e:
    print(f"R3D1 via API: FAILED - {e}")

# Check what the TM page for Russia looks like
resp2 = c.get_html("https://www.transfermarkt.com/wettbewerbe/europa")
soup2 = BeautifulSoup(resp2.text, "html.parser")
europa_ids = set()
for a in soup2.find_all("a", href=True):
    href = a["href"]
    if "/wettbewerb/" in href:
        lid = href.split("/wettbewerb/")[-1].split("/")[0].split("?")[0]
        if lid:
            europa_ids.add(lid)
russia_europa = [lid for lid in europa_ids if lid.startswith("RU")]
print(f"Russia leagues from europa index: {russia_europa}")
