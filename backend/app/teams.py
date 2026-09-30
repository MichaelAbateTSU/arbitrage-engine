"""League-scoped curated identity aliases; ambiguous city names are not mapped."""

from collections import Counter

from app.matching import Alias, normalized

TEAM_DATA = {
    "NFL": """
ARI|Arizona Cardinals
ATL|Atlanta Falcons
BAL|Baltimore Ravens
BUF|Buffalo Bills
CAR|Carolina Panthers
CHI|Chicago Bears
CIN|Cincinnati Bengals
CLE|Cleveland Browns
DAL|Dallas Cowboys
DEN|Denver Broncos
DET|Detroit Lions
GB|Green Bay Packers
HOU|Houston Texans
IND|Indianapolis Colts
JAX|Jacksonville Jaguars
KC|Kansas City Chiefs
LAC|Los Angeles Chargers
LAR|Los Angeles Rams
LV|Las Vegas Raiders
MIA|Miami Dolphins
MIN|Minnesota Vikings
NE|New England Patriots
NO|New Orleans Saints
NYG|New York Giants
NYJ|New York Jets
PHI|Philadelphia Eagles
PIT|Pittsburgh Steelers
SEA|Seattle Seahawks
SF|San Francisco 49ers
TB|Tampa Bay Buccaneers
TEN|Tennessee Titans
WAS|Washington Commanders
""",
    "NBA": """
ATL|Atlanta Hawks
BOS|Boston Celtics
BKN|Brooklyn Nets
CHA|Charlotte Hornets
CHI|Chicago Bulls
CLE|Cleveland Cavaliers
DAL|Dallas Mavericks
DEN|Denver Nuggets
DET|Detroit Pistons
GSW|Golden State Warriors
HOU|Houston Rockets
IND|Indiana Pacers
LAC|Los Angeles Clippers
LAL|Los Angeles Lakers
MEM|Memphis Grizzlies
MIA|Miami Heat
MIL|Milwaukee Bucks
MIN|Minnesota Timberwolves
NOP|New Orleans Pelicans
NYK|New York Knicks
OKC|Oklahoma City Thunder
ORL|Orlando Magic
PHI|Philadelphia 76ers
PHX|Phoenix Suns
POR|Portland Trail Blazers
SAC|Sacramento Kings
SAS|San Antonio Spurs
TOR|Toronto Raptors
UTA|Utah Jazz
WAS|Washington Wizards
""",
    "MLB": """
ARI|Arizona Diamondbacks
ATL|Atlanta Braves
BAL|Baltimore Orioles
BOS|Boston Red Sox
CHC|Chicago Cubs
CWS|Chicago White Sox
CIN|Cincinnati Reds
CLE|Cleveland Guardians
COL|Colorado Rockies
DET|Detroit Tigers
HOU|Houston Astros
KC|Kansas City Royals
LAA|Los Angeles Angels
LAD|Los Angeles Dodgers
MIA|Miami Marlins
MIL|Milwaukee Brewers
MIN|Minnesota Twins
NYM|New York Mets
NYY|New York Yankees
ATH|Athletics
PHI|Philadelphia Phillies
PIT|Pittsburgh Pirates
SD|San Diego Padres
SEA|Seattle Mariners
SF|San Francisco Giants
STL|St Louis Cardinals
TB|Tampa Bay Rays
TEX|Texas Rangers
TOR|Toronto Blue Jays
WSH|Washington Nationals
""",
    "NHL": """
ANA|Anaheim Ducks
BOS|Boston Bruins
BUF|Buffalo Sabres
CAR|Carolina Hurricanes
CBJ|Columbus Blue Jackets
CGY|Calgary Flames
CHI|Chicago Blackhawks
COL|Colorado Avalanche
DAL|Dallas Stars
DET|Detroit Red Wings
EDM|Edmonton Oilers
FLA|Florida Panthers
LAK|Los Angeles Kings
MIN|Minnesota Wild
MTL|Montreal Canadiens
NJD|New Jersey Devils
NSH|Nashville Predators
NYI|New York Islanders
NYR|New York Rangers
OTT|Ottawa Senators
PHI|Philadelphia Flyers
PIT|Pittsburgh Penguins
SEA|Seattle Kraken
SJS|San Jose Sharks
STL|St Louis Blues
TBL|Tampa Bay Lightning
TOR|Toronto Maple Leafs
UTA|Utah Mammoth
VAN|Vancouver Canucks
VGK|Vegas Golden Knights
WPG|Winnipeg Jets
WSH|Washington Capitals
""",
}


def builtin_aliases() -> list[Alias]:
    result = []
    multiform = [
        "Red Sox",
        "White Sox",
        "Blue Jays",
        "Trail Blazers",
        "Maple Leafs",
        "Red Wings",
        "Blue Jackets",
        "Golden Knights",
    ]

    def split(name: str) -> tuple[str, str]:
        for suffix in multiform:
            if name.endswith(" " + suffix):
                return name[: -(len(suffix) + 1)], suffix
        if " " in name:
            city, nickname = name.rsplit(" ", 1)
            return city, nickname
        return name, name

    for league, data in TEAM_DATA.items():
        rows = [line.split("|") for line in data.strip().splitlines()]
        cities = Counter(split(name)[0] for _, name in rows)
        for code, name in rows:
            city, nickname = split(name)
            variants = {name, code, f"{code} {nickname}", nickname}
            if cities[city] == 1:
                variants.add(city)
            else:
                variants.add(f"{city} {nickname[0]}")
            if city == "Los Angeles":
                variants.add(f"LA {nickname}")
            if city == "New York":
                variants.add(f"NY {nickname}")
            for value in variants:
                result.append(
                    Alias(
                        league=league,
                        original=value,
                        canonical=name,
                        source="curated-team-identity",
                        version="2026-09-v1",
                    )
                )
    identities: dict[tuple[str, str], set[str]] = {}
    for alias in result:
        identities.setdefault((alias.league, normalized(alias.original)), set()).add(
            alias.canonical
        )
    return [
        alias
        for alias in result
        if len(identities[(alias.league, normalized(alias.original))]) == 1
    ]
