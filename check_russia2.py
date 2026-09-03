from client import Client
from bs4 import BeautifulSoup

c = Client()

# Try Russia's startseite page
resp = c.get_html("https://www.transfermarkt.com/russland/startseite/wettbewerb/RU1")
soup = BeautifulSoup(resp.text, "html.parser")

# Find all competition links
comp_links = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    text = a.get_text(strip=True)
    if "/wettbewerb/" in href:
        lid = href.split("/wettbewerb/")[-1].split("/")[0].split("?")[0]
        if lid and lid.startswith("RU"):
            comp_links.append((text, lid))

print("Russia competitions from RU1 page:")
for text, lid in sorted(set(comp_links)):
    print(f"  {lid}: {text}")

# Also check if there's a direct listing
resp2 = c.get_html("https://www.transfermarkt.com/russland/wettbewerbevereine")
soup2 = BeautifulSoup(resp2.text, "html.parser")
ru2_ids = set()
for a in soup2.find_all("a", href=True):
    href = a["href"]
    if "/wettbewerb/" in href:
        lid = href.split("/wettbewerb/")[-1].split("/")[0].split("?")[0]
        if lid and lid.startswith("RU"):
            ru2_ids.add(lid)
print(f"\nRussia leagues from wettbewerbevereine: {sorted(ru2_ids)}")
