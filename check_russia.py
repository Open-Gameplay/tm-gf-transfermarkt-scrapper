from client import Client
from bs4 import BeautifulSoup

c = Client()

# Check Russia's country page
resp = c.get_html("https://www.transfermarkt.com/wettbewerbe/europa")
soup = BeautifulSoup(resp.text, "html.parser")

# Find all links on the page
russia_links = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    text = a.get_text(strip=True)
    if "russland" in href.lower() or "russia" in text.lower():
        russia_links.append((text, href))

print("Russia links on europa page:")
for text, href in russia_links[:20]:
    print(f"  {text}: {href}")

# Try to find Russia's country page
resp2 = c.get_html("https://www.transfermarkt.com/wettbewerbe/europa?kontinent_id=1")
soup2 = BeautifulSoup(resp2.text, "html.parser")
ru_ids = set()
for a in soup2.find_all("a", href=True):
    href = a["href"]
    if "/wettbewerb/" in href and "RU" in href:
        lid = href.split("/wettbewerb/")[-1].split("/")[0].split("?")[0]
        if lid:
            ru_ids.add(lid)
print(f"\nRussia leagues from europa (kontinent=1): {sorted(ru_ids)}")
