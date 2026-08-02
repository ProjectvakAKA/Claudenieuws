"""
Dagelijkse nieuwsbrief: haalt nieuws op via directe RSS-feeds van
kwaliteitsbronnen (geen Google News search meer -- dat was te fragiel:
ongedocumenteerde zoeksyntax die zonder waarschuwing kan veranderen of
0 resultaten geeft), laat Gemini er een nette HTML-nieuwsbrief van
maken, en verstuurt die via SMTP.
"""

import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
from google import genai

# ---------------------------------------------------------------------------
# RSS-feeds
# ---------------------------------------------------------------------------
# LET OP: verifieer deze URL's zelf in je browser. RSS-feeds kunnen
# verhuizen; sites passen ze soms aan zonder oude URL te laten
# doorverwijzen. Als een feed 404 geeft, zoek "[site] rss feed" op.

GENERAL_FEEDS = [
    "https://www.vrt.be/vrtnws/nl.rss.articles.xml",           # VRT NWS
    "https://www.tijd.be/rss/topstories.xml",                  # De Tijd
    "https://www.theguardian.com/world/rss",                   # The Guardian - World
    "https://feeds.bbci.co.uk/news/world/rss.xml",             # BBC World
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",  # NYT World
    "https://www.economist.com/international/rss.xml",         # The Economist - International
    "https://www.lemonde.fr/international/rss_full.xml",       # Le Monde - International
]


GEO_FEEDS = [
    "https://geospatialworld.net/feed",
	"https://spacenews.com/feed",
	"https://www.bellingcat.com/feed",
]

GEO_KEYWORDS = [
    "satellite", "satelliet", "geospatial", "geo-intelligence", "gis",
    "remote sensing", "aardobservatie", "earth observation", "drone",
    "mapping", "cartograf", "location intelligence",
]



# ---------------------------------------------------------------------------
# Ophalen
# ---------------------------------------------------------------------------

def fetch_feed(url: str, max_age_hours: int, max_items: int):
    """Haalt items op uit één RSS-feed, gefilterd op leeftijd."""
    feed = feedparser.parse(url)

    if feed.bozo:
        print(f"WAARSCHUWING: feed lijkt kapot of geblokkeerd: {url}")
        print(f"  reden: {getattr(feed, 'bozo_exception', 'onbekend')}")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    articles = []

    for entry in feed.entries:
        published_struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)

        if published_struct is not None:
            entry_dt = datetime(*published_struct[:6], tzinfo=timezone.utc)
            if entry_dt < cutoff:
                continue
        # geen datum bekend -> gewoon meenemen, niet skippen

        articles.append({
            "title": getattr(entry, "title", ""),
            "summary": getattr(entry, "summary", ""),
            "link": getattr(entry, "link", ""),
            "published": getattr(entry, "published", ""),
        })

        if len(articles) >= max_items:
            break

    return articles


def fetch_all_general(max_age_hours: int = 30, per_feed: int = 5):
    all_articles = []
    for url in GENERAL_FEEDS:
        items = fetch_feed(url, max_age_hours=max_age_hours, max_items=per_feed)
        print(f"{url} -> {len(items)} artikelen binnen {max_age_hours}u")
        all_articles.extend(items)
    return all_articles


def fetch_all_geo(max_age_hours: int = 48, per_feed: int = 10):
    """
    Ruimer tijdsvenster voor geo-nieuws, want die feeds posten minder frequent.

    Geen trefwoordfilter hier: NASA/ESA/Space.com zijn zelf al 100%
    ruimtevaart/aardobservatie-content, dus een keyword-filter erbovenop
    gooide relevante artikelen weg die toevallig niet exact "satellite"
    of "GIS" in titel/samenvatting hadden staan (bv. een Mars-rover-
    artikel of een telescoopverhaal).
    """
    all_articles = []
    for url in GEO_FEEDS:
        items = fetch_feed(url, max_age_hours=max_age_hours, max_items=per_feed)
        print(f"{url} -> {len(items)} artikelen")
        all_articles.extend(items)
    return all_articles


def format_articles(articles):
    lines = []
    for a in articles:
        title = a.get("title", "")
        link = a.get("link", "")
        if link:
            lines.append(f"- {title} ({link})")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines) if lines else "Geen artikelen gevonden."


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def build_newsletter_with_gemini(general_articles, geo_articles) -> str:
    """
    Laat Gemini een nette HTML-nieuwsbrief genereren op basis van de RSS-artikelen.
    Vereist env var GEMINI_API_KEY.
    """
    client = genai.Client()

    general_text = format_articles(general_articles)
    geo_text = format_articles(geo_articles)
    today_str = datetime.now().strftime("%d-%m-%Y")

    prompt = f"""
    <ROLE>
    Je bent hoofdredacteur van een internationale kwaliteitsredactie, vergelijkbaar met Reuters, de Financial Times of NRC.

    Je schrijft een dagelijkse nieuwsbrief voor een Nederlandstalige lezer in België die geïnteresseerd is in:
    - internationale politiek
    - economie en financiële markten
    - technologie
    - wetenschap
    - geopolitiek
    - geo-intelligence

    Je schrijft journalistiek, objectief en helder. De nieuwsbrief moet aanvoelen als een professionele ochtendbriefing: compact, informatief en gemakkelijk scanbaar.
    </ROLE>

    <GOAL>
    Maak een dagelijkse HTML-nieuwsbrief die uitsluitend gebaseerd is op de aangeleverde nieuwsitems.

    De nieuwsbrief bestaat uit:
    - ongeveer 50% algemeen wereldnieuws
    - ongeveer 50% geo-intelligence / geospatial nieuws

    Selecteer alleen de belangrijkste gebeurtenissen van vandaag.
    Vat dubbele berichten samen tot één artikel.
    </GOAL>

    <INPUT_DATA>

    <ALGEMEEN_NIEUWS>
    Dit zijn ruwe nieuwsitems uit RSS-feeds.

    Gebruik uitsluitend deze informatie als bron.

    {general_text}
    </ALGEMEEN_NIEUWS>

    <GEO_INT_NIEUWS>
    Dit zijn ruwe nieuwsitems uit RSS-feeds over geospatial intelligence en geo-intelligence.

    Gebruik uitsluitend deze informatie als bron.

    {geo_text}
    </GEO_INT_NIEUWS>

    </INPUT_DATA>

    <EDITORIAL_GUIDELINES>

    Bepaal eerst welke gebeurtenissen het belangrijkst zijn.

    Prioriteer op:
    1. Internationale impact
    2. Geopolitieke relevantie
    3. Economische impact
    4. Technologische of wetenschappelijke relevantie
    5. Actualiteit
    6. Betrouwbaarheid van de beschikbare bronnen

    Maak voor algemeen nieuws een mix van:
    - 1 kernartikel dat specifiek over België gaat (Belgisch binnenlands nieuws, Belgisch beleid, of een gebeurtenis in België) — niet zomaar een artikel van een Belgische nieuwssite over een ander land. Ga na dat dit nieuws effectief relevant is, het gaat bijvoorbeeld over politieke zaken of statistieken over grote belgische onderzoeken
    - 4 tot 5 kernartikelen over internationale ontwikkelingen uit de overige betrouwbare bronnen.

    Wanneer meerdere artikelen over dezelfde gebeurtenis gaan:
    - combineer ze tot één samenvatting
    - kies de meest complete bronlink
    - vermeld het onderwerp slechts één keer

    Negeer berichten die weinig internationale relevantie hebben, zoals:
    - entertainment
    - celebritynieuws
    - lifestyle
    - lokaal sportnieuws
    - human-interest verhalen zonder bredere impact

    Geef bij algemeen nieuws voorrang aan:
    - geopolitieke gebeurtenissen
    - internationale veiligheid
    - macro-economie en financiële markten
    - grote technologische of wetenschappelijke doorbraken

    Indien weinig belangrijk nieuws beschikbaar is,
    gebruik minder artikelen in plaats van minder relevante artikelen toe te voegen.
    </EDITORIAL_GUIDELINES>

    <WRITING_STYLE>
    Schrijf in helder professioneel Nederlands.

    Per nieuwsitem:
    - begin met wat er gebeurd is
    - leg kort uit waarom dit belangrijk is
    - vermeld alleen mogelijke gevolgen wanneer deze expliciet uit de bron blijken

    Gebruik:
    - korte zinnen
    - neutrale toon
    - geen clickbait
    - geen overdrijving
    - geen speculatie
    - geen meningen

    Voeg geen informatie toe die niet expliciet uit de input afkomstig is.

    Schrijf alsof dit een nieuwsbrief van een professionele internationale redactie is.
    </WRITING_STYLE>

    <CONTENT_REQUIREMENTS>
    Nieuwsbriefdatum:
    {today_str}

    De nieuwsbrief bevat:
    - 1 belangrijk Belgisch nieuwsartikel
    - 4 tot 5 belangrijke internationale nieuwsartikelen
    - 4 tot 6 geo-intelligence artikelen

    Wanneer geen relevante geo-artikelen beschikbaar zijn,
    toon de sectie toch en vermeld dat er vandaag geen relevante geo-intelligence ontwikkelingen waren.

    Houd de totale verhouding ongeveer:
    - 50% algemeen nieuws (België + internationaal)
    - 50% geo-intelligence.
    </CONTENT_REQUIREMENTS>

    <OUTPUT_FORMAT>
    Produceer uitsluitend geldige HTML.

    Geen markdown.
    Geen uitleg.
    Geen codeblokken.

    Gebruik uitsluitend deze HTML-tags:
    <html> <body> <h1> <h2> <h3> <p> <ul> <li> <a> <small>

    Structuur:

    <html>
    <body>

    <h1>Dagelijkse Wereldnieuwsbrief – {today_str}</h1>

    <p>Journalistieke introductie van 2 à 3 zinnen. Vat de belangrijkste mondiale ontwikkelingen samen. Verwijs kort naar de geo-intelligence sectie.</p>

    <h2>Vandaag in één oogopslag</h2>
    <ul>
    <li>Belangrijkste Belgisch nieuws</li>
    <li>Belangrijkste internationale geopolitieke of economische ontwikkeling</li>
    <li>Belangrijkste technologie- of wetenschapsnieuws</li>
    </ul>

    <h2>Algemeen wereldnieuws</h2>
    <ul>
    <li>
    <h3>Belgisch kernartikel</h3>
    <p>Max drie zinnen: wat gebeurd is, waarom belangrijk, eventuele gevolgen (alleen indien expliciet uit bron).</p>
    <a href="...">Bron</a>
    </li>
    <!-- 4-5 internationale artikelen, zelfde structuur -->
    </ul>

    <h2>Geo-intelligence &amp; geo-wereld</h2>
    <ul>
    <!-- 1 of 2 artikelen, zelfde structuur, korter -->
    </ul>

    Indien geen geo-artikelen beschikbaar:
    <p>Vandaag waren er geen belangrijke ontwikkelingen binnen geo-intelligence of geospatial technologie.</p>

    <p>Neutrale afsluitende alinea van één à twee zinnen. Geen promotie. Geen persoonlijke mening.</p>

    </body>
    </html>
    </OUTPUT_FORMAT>

    <VALIDATION>
    - Gebruik uitsluitend informatie uit de input.
    - Voeg geen feiten toe.
    - Gebruik uitsluitend bronlinks uit de input.
    - Vermeld elke gebeurtenis slechts één keer.
    - Zorg dat er één Belgisch kernartikel en 4-5 internationale artikelen zijn.
    - Houd ongeveer 50% algemeen nieuws en 50% geo-intelligence aan.
    - Sorteer artikelen op belangrijkheid.
    - Produceer uitsluitend geldige HTML.
    - Schrijf geen tekst buiten de HTML-tags.
    - Gebruik geen titels, functies of kwalificaties (zoals 'voormalig', 'huidig', 'minister van...') tenzij deze letterlijk in de brontekst staan. Bij twijfel: laat de titel weg.
    </VALIDATION>
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------

def send_email(html_body: str):
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_username = os.environ["SMTP_USERNAME"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    recipient_email = os.environ["RECIPIENT_EMAIL"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Dagelijkse nieuwsbrief (algemeen + geo-int)"
    msg["From"] = smtp_username
    msg["To"] = recipient_email

    plain_text = "Je e-mailclient ondersteunt geen HTML. Bekijk de nieuwsbrief in een moderne mailapp."
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context) as server:
        server.login(smtp_username, smtp_password)
        server.send_message(msg)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== ALGEMEEN NIEUWS OPHALEN ===")
    general_articles = fetch_all_general()
    print(f"Totaal general_articles: {len(general_articles)}")
    for a in general_articles:
        print("GENERAL:", a["published"], "|", a["title"])

    print("\n=== GEO-INTELLIGENCE NIEUWS OPHALEN ===")
    geo_articles = fetch_all_geo()
    print(f"Totaal geo_articles: {len(geo_articles)}")
    for a in geo_articles:
        print("GEO:", a["published"], "|", a["title"])

    print("\n=== NIEUWSBRIEF GENEREREN MET GEMINI ===")
    newsletter_html = build_newsletter_with_gemini(general_articles, geo_articles)

    print("\n=== VERSTUREN ===")
    send_email(newsletter_html)
    print("Verstuurd.")


if __name__ == "__main__":
    main()
