"""Generates docs/architecture.drawio. Usage: python tools/gen_arch.py docs/architecture.drawio
Arrows carry explicit waypoints so they run in the gaps between rows (the draw.io viewer has no auto-router)."""
import sys
from xml.sax.saxutils import escape

cells = []
I = "https://icons.diagrams.net/"
ICON = {
    "cart": "image;html=1;image=https://app.diagrams.net/img/lib/clip_art/finance/Shopping_Cart_128x128.png",
    "news": "shape=image;html=1;imageAspect=0;aspect=fixed;image=" + I + "icon-cache1/bitsies_-2960/News-1457.svg",
    "globe": "sketch=0;outlineConnect=0;fillColor=#232F3D;strokeColor=none;dashed=0;html=1;aspect=fixed;pointerEvents=1;shape=mxgraph.aws4.globe",
    "claude": "shape=image;html=1;imageAspect=0;aspect=fixed;image=" + I + "assets/font-awesome/1/Claude_brand.svg",
    "docker": "shape=image;html=1;imageAspect=0;aspect=fixed;image=" + I + "assets/azure/1/Docker.svg",
    "sql": "shape=image;html=1;imageAspect=0;aspect=fixed;image=" + I + "assets/databases/1/SQL.svg",
    "obsidian": "shape=image;html=1;imageAspect=0;aspect=fixed;image=" + I + "assets/font-awesome/1/Obsidian_brand.svg",
    "laptop": "image;html=1;image=https://app.diagrams.net/img/lib/clip_art/computers/Laptop_128x128.png",
    "web": "shape=image;html=1;imageAspect=0;aspect=fixed;image=" + I + "icon-cache1/User_Interface-2098/UI_Internet_web_network_browser_globe-1446.svg",
}
GROUP = "rounded=1;whiteSpace=wrap;html=1;arcSize=3;verticalAlign=top;align=left;spacingLeft=14;spacingTop=8;fontSize=15;fontStyle=1;strokeWidth=2;"
ATTR = {'"': "&quot;"}


def right(w):   # label to the right of an icon, w px wide
    return f";labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=8;fontSize=12;labelWidth={w}"


def left(w):   # label to the left of an icon, so arrows leaving the icon's right side never cross it
    return f";labelPosition=left;verticalLabelPosition=middle;align=right;verticalAlign=middle;spacingRight=8;fontSize=12;labelWidth={w}"


BELOW = ";labelPosition=center;verticalLabelPosition=bottom;align=center;verticalAlign=top;fontSize=12;labelWidth=140"


def v(id, label, x, y, w, h, style):
    cells.append(f'<mxCell id="{id}" value="{escape(label, ATTR)}" style="{escape(style, ATTR)}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def icon(id, kind, label, x, y, size=52, pos=None):
    v(id, label, x, y, size, size, ICON[kind] + (pos or right(220)))


def box(id, label, x, y, w, h, fill, stroke, extra=""):
    v(id, label, x, y, w, h, f"rounded=1;whiteSpace=wrap;html=1;arcSize=14;fillColor={fill};strokeColor={stroke};strokeWidth=2;fontSize=12;{extra}")


def e(src, dst, label="", color="#555555", dashed=False, both=False, ex=None, en=None, pts=()):
    """ex/en = (x, y) relative exit/entry point on the source/target; pts = absolute waypoints."""
    st = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeWidth=2;strokeColor={color};fontColor={color};fontStyle=1;fontSize=11;"
          "endArrow=block;endFill=1;labelBackgroundColor=#ffffff;" + ("dashed=1;" if dashed else "") + ("startArrow=block;startFill=1;" if both else ""))
    if ex:
        st += f"exitX={ex[0]};exitY={ex[1]};exitDx=0;exitDy=0;"
    if en:
        st += f"entryX={en[0]};entryY={en[1]};entryDx=0;entryDy=0;"
    pts_xml = ('<Array as="points">' + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in pts) + "</Array>") if pts else ""
    cells.append(f'<mxCell id="e{len(cells)}" value="{escape(label, ATTR)}" style="{escape(st, ATTR)}" edge="1" parent="1" source="{src}" '
                 f'target="{dst}"><mxGeometry relative="1" as="geometry">{pts_xml}</mxGeometry></mxCell>')


v("title", "<b>🛒 SUPERMARKET HUNTER · how it works</b><br>Daily grocery prices for the family, on a web page "
  "on the home network (no messages are sent anywhere)", 20, 10, 1460, 50, "text;html=1;align=left;verticalAlign=middle;fontSize=15")

# ---- internet ----
v("g_net", "🌐 Internet · only pages the sites allow", 20, 70, 390, 790, GROUP + "fillColor=#EAF2FB;strokeColor=#1D4FA3;fontColor=#1D4FA3")
icon("cs", "cart", "<b>Cold Storage</b><br>/search?q=eggs", 190, 110, pos=left(150))
icon("fp", "cart", "<b>FairPrice</b><br>/search + /promotions", 190, 190, pos=left(150))
v("jina", "<b>Jina Reader</b><br>page → Markdown", 330, 205, 52, 52, ICON["globe"] + BELOW)
icon("claude", "claude", "<b>Claude</b> (Anthropic API)<br>reads the flyer image", 330, 290, pos=left(260))
icon("giant", "news", "<b>Giant</b> promotion page", 330, 380, pos=left(260))
icon("sp", "news", "<b>singpromos.com</b> posts", 330, 460, pos=left(260))
icon("ss", "news", "<b>Sheng Siong</b> flyer<br>(RSS → JPG)", 330, 540, pos=left(260))
icon("shop", "web", "<b>Shop product pages</b><br>+ product photos", 330, 720, pos=left(260))

# ---- NAS ----
v("g_nas", "🏠 Home NAS · UGREEN DXP4800 Pro", 450, 70, 650, 790, GROUP + "fillColor=#F5F5F5;strokeColor=#555555;fontColor=#333333")
v("g_ctr", "", 470, 160, 610, 540, "rounded=1;whiteSpace=wrap;html=1;arcSize=3;fillColor=#FFF8E6;strokeColor=#E6A72A;strokeWidth=2;dashed=1")
icon("docker", "docker", "<b>Docker container supermarket-hunter</b> · python -m smh serve", 480, 112, 40, pos=right(420))
box("sched", "⏰ <b>Full refresh</b><br>daily 08:00 · all stores + flyers", 490, 185, 175, 60, "#FFFFFF", "#E6A72A")
box("watch", "🔁 <b>Regular checks</b><br>hourly · all stores every 3 h", 690, 185, 175, 60, "#FFFFFF", "#E6A72A")
box("pw", "🎭 <b>Playwright server</b><br>shared container · scrape-net", 890, 185, 175, 60, "#F3ECFA", "#6B3FA0", "dashed=1;")
box("flyers", "<b>flyers.py</b><br>flyer &amp; promo-page items", 490, 290, 175, 66, "#DAE8FC", "#1D4FA3")
box("scrape", "<b>scrape.py</b><br>products · price per 100g · photo", 690, 290, 175, 66, "#DAE8FC", "#1D4FA3")
icon("db", "sql", "<b>prices.db</b><br>SQLite · history + photos", 755, 430, 44, pos=right(140))
box("fj", "📰 <b>flyers.json</b><br>last flyer run", 590, 520, 110, 50, "#FFFFFF", "#888888")
box("cfg", "📋 <b>config.json</b><br>staples + brands", 945, 430, 120, 50, "#FFFFFF", "#888888")
box("web", "🌐 <b>web.py</b> · built-in web server<br>Promotions · 14 categories · 7 supermarkets · flyers", 600, 610, 380, 66, "#FBE3DD", "#C2391B", "fontSize=13;")
icon("vault", "obsidian", "<b>Obsidian vault</b> · records every movement: refreshes, each staple, new promotions, browser fetches and blocks, flyers, logos, page visits", 490, 740, 40, pos=right(560))

# ---- family ----
v("g_fam", "👪 Family · home Wi-Fi", 1140, 70, 340, 790, GROUP + "fillColor=#EAF6EC;strokeColor=#2C6E3F;fontColor=#2C6E3F")
v("legend", "<b>Flow</b><br>1 The 08:00 run and a quick check every 3 h start a refresh<br>2 Jina returns allowed pages as Markdown; "
  "FairPrice search, and any page Jina cannot read, go through the Playwright browser (a captcha or 429 = stop)<br>"
  "3 Rows (price, unit price, promo, photo) go into prices.db<br>"
  "4 The laptop asks the NAS for the page (port 8790)<br>5 web.py reads the database and draws it<br>6 Tap a card → the shop's own product page",
  1160, 150, 305, 260, "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#2C6E3F;align=left;verticalAlign=top;spacing=10;fontSize=12")
icon("laptop", "laptop", "<b>Parents' laptop</b><br>http://&lt;nas-ip&gt;:8790<br><i>Today's Best Buys · 今日好价</i>", 1160, 611, 64, pos=right(190))

B, R, Y, P = "#1D4FA3", "#C2391B", "#E6A72A", "#6B3FA0"
# 1 triggers
e("sched", "flyers", "1", Y, ex=(0.5, 1), en=(0.5, 0))
e("sched", "scrape", "", Y, ex=(0.8, 1), en=(0.3, 0), pts=[(630, 262), (742, 262)])
e("watch", "scrape", "", Y, ex=(0.5, 1), en=(0.5, 0))
# 2 internet -> modules
e("cs", "jina", "", B, ex=(1, 0.5), en=(0, 0.5), pts=[(290, 136), (290, 231)])
e("fp", "jina", "", B, ex=(1, 0.5), en=(0, 0.5), pts=[(290, 216), (290, 231)])
e("jina", "scrape", "2 Markdown", B, ex=(1, 0.5), en=(0.7, 0), pts=[(430, 231), (430, 268), (812, 268)])
e("pw", "fp", "FairPrice search (daily) · Jina fallback", P, dashed=True, ex=(0.5, 0), en=(0.5, 0), pts=[(977, 176), (216, 176)])
e("scrape", "pw", "", P, both=True, ex=(0.95, 0), en=(0.3, 1), pts=[(856, 278), (942, 278)])
e("claude", "flyers", "flyer JPG", B, both=True, ex=(1, 0.5), en=(0, 0.4))
e("giant", "flyers", "", B, ex=(1, 0.5), en=(0.15, 1), pts=[(516, 406)])
e("sp", "flyers", "", B, ex=(1, 0.5), en=(0.3, 1), pts=[(542, 486)])
e("ss", "flyers", "", B, ex=(1, 0.5), en=(0.45, 1), pts=[(569, 566)])
# 3 save
e("scrape", "db", "3 save rows", B, ex=(0.37, 1), en=(0.5, 0))
e("flyers", "fj", "", B, ex=(0.85, 1), en=(0.45, 0))
# 5-7 web page
e("laptop", "web", "4 GET /  (8790 → 8000)", R, both=True, ex=(0, 0.5), en=(1, 0.5))
e("db", "web", "5", R, ex=(0.5, 1), en=(0.47, 0))
e("fj", "web", "", R, ex=(0.45, 1), en=(0.115, 0))
e("cfg", "web", "", R, ex=(0.5, 1), en=(0.85, 0), pts=[(1005, 590), (923, 590)])
e("laptop", "shop", "6 tap a card → shop page", R, dashed=True, ex=(0.5, 1), en=(0.5, 1), pts=[(1192, 840), (356, 840)])

xml = ('<mxGraphModel dx="1500" dy="900" grid="1" gridSize="10" guides="1" page="1" pageWidth="1500" pageHeight="880" background="#FFFFFF">'
       '<root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel>")
if __name__ == "__main__":
    open(sys.argv[1], "w", encoding="utf-8").write(xml)
    print(len(xml), "chars")
