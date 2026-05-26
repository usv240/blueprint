"""
Geocoding via OpenStreetMap Nominatim — no API key needed.
Returns normalized address, lat/lng, county, state, and a short address_hash.
"""
import hashlib
import logging

import httpx

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "BlueprintPropertyIntel/1.0 (contact: blueprint@propertyi.io)"}

# US county/state label normalization
_COUNTY_SUFFIXES = [" county", " parish", " borough", " city and county", " municipality"]


def _normalize_county(raw: str) -> str:
    raw = raw.lower().strip()
    for suffix in _COUNTY_SUFFIXES:
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].strip()
    return raw.title()


async def geocode(address: str) -> dict:
    """
    Geocode an address string.
    Returns a dict with: normalized_address, lat, lng, county, state, postcode,
    address_hash, country_code, city, data_tier ("nyc"|"austin"|"la"|"generic").
    Raises ValueError if the address cannot be geocoded.
    """
    params = {
        "q": address,
        "format": "json",
        "addressdetails": 1,
        "limit": 1,
        "countrycodes": "us",
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.get(_NOMINATIM_URL, params=params)
            resp.raise_for_status()
            results = resp.json()
    except Exception as e:
        raise ValueError(f"Geocoding request failed: {e}") from e

    if not results:
        raise ValueError(f"Address not found: {address!r}")

    hit = results[0]
    addr = hit.get("address", {})

    lat = float(hit["lat"])
    lng = float(hit["lon"])
    display = hit.get("display_name", address)

    # Pull county / state
    raw_county = (
        addr.get("county")
        or addr.get("borough")
        or addr.get("city")
        or addr.get("town")
        or ""
    )
    county = _normalize_county(raw_county)
    state = addr.get("state", "").title()
    city = addr.get("city") or addr.get("town") or addr.get("village") or ""
    postcode = addr.get("postcode", "")
    country_code = addr.get("country_code", "us").upper()

    # Normalise to first line + city + state + zip for display
    house = addr.get("house_number", "")
    road = addr.get("road", "")
    normalized_address = f"{house} {road}".strip() + f", {city}, {state} {postcode}".rstrip()
    if not road:
        normalized_address = display.split(",")[0].strip() + f", {city}, {state}"

    address_hash = hashlib.md5(normalized_address.lower().encode()).hexdigest()[:12]

    # Determine which data tier is available for this location
    data_tier = _pick_data_tier(city, county, state)

    return {
        "normalized_address": normalized_address,
        "display_address": display,
        "lat": lat,
        "lng": lng,
        "city": city,
        "county": county,
        "state": state,
        "postcode": postcode,
        "country_code": country_code,
        "address_hash": address_hash,
        "data_tier": data_tier,
        "house_number": house,
        "road": road,
        "borough": addr.get("borough") or addr.get("city_district", ""),
    }


def _pick_data_tier(city: str, county: str, state: str) -> str:
    """
    Determine which open-data API set is available for this location.
    Checks city name first (most specific), then county, then falls back to generic.
    Generic tier still gets full nationwide FEMA + USGS + EPA + OSM coverage.
    """
    city_l   = city.lower()
    county_l = county.lower()
    state_l  = state.lower()

    # ── New York City ──────────────────────────────────────────────────────────
    if ("new york" in county_l
            or any(b in county_l for b in ["manhattan", "brooklyn", "queens", "bronx", "richmond"])
            or city_l in ("new york", "brooklyn", "manhattan", "queens", "the bronx", "staten island")):
        return "nyc"

    # ── Texas ─────────────────────────────────────────────────────────────────
    if "texas" in state_l:
        if "travis" in county_l or "austin" in city_l:
            return "austin"
        if "dallas" in city_l or "dallas" in county_l:
            return "dallas"
        if "san antonio" in city_l or "bexar" in county_l:
            return "san_antonio"
        if "fort worth" in city_l or "tarrant" in county_l:
            return "fort_worth"
        if "houston" in city_l or "harris" in county_l:
            return "houston"
        if "el paso" in city_l or "el paso" in county_l:
            return "el_paso"
        if "lubbock" in city_l or "lubbock" in county_l:
            return "lubbock"

    # ── California ────────────────────────────────────────────────────────────
    if "california" in state_l:
        if "long beach" in city_l:
            return "long_beach"
        if "oakland" in city_l or "alameda" in county_l:
            return "oakland"
        if "fresno" in city_l or "fresno" in county_l:
            return "fresno"
        if "sacramento" in city_l or "sacramento" in county_l:
            return "sacramento"
        if "los angeles" in county_l or "los angeles" in city_l:
            return "la"
        if "san francisco" in city_l or "san francisco" in county_l:
            return "san_francisco"
        if "san jose" in city_l or "santa clara" in county_l:
            return "san_jose"
        if "san diego" in city_l or "san diego" in county_l:
            return "san_diego"

    # ── Illinois ──────────────────────────────────────────────────────────────
    if "illinois" in state_l and ("chicago" in city_l or "cook" in county_l):
        return "chicago"

    # ── Washington (state) ────────────────────────────────────────────────────
    if "washington" in state_l and "district of columbia" not in state_l:
        if "seattle" in city_l or "king" in county_l:
            return "seattle"
        if "spokane" in city_l or "spokane" in county_l:
            return "spokane"

    # ── DC ────────────────────────────────────────────────────────────────────
    if "district of columbia" in state_l or city_l in ("washington", "washington dc"):
        return "dc"

    # ── Massachusetts ─────────────────────────────────────────────────────────
    if "massachusetts" in state_l:
        if "boston" in city_l or "suffolk" in county_l:
            return "boston"
        if "providence" in city_l:
            return "providence"

    # ── Colorado ──────────────────────────────────────────────────────────────
    if "colorado" in state_l:
        if "denver" in city_l or "denver" in county_l:
            return "denver"
        if "colorado springs" in city_l:
            return "colorado_springs"

    # ── Tennessee ─────────────────────────────────────────────────────────────
    if "tennessee" in state_l:
        if "nashville" in city_l or "davidson" in county_l:
            return "nashville"
        if "memphis" in city_l or "shelby" in county_l:
            return "memphis"

    # ── Minnesota ─────────────────────────────────────────────────────────────
    if "minnesota" in state_l and ("minneapolis" in city_l or "hennepin" in county_l):
        return "minneapolis"

    # ── Maryland ──────────────────────────────────────────────────────────────
    if "maryland" in state_l and "baltimore" in city_l:
        return "baltimore"

    # ── Oregon ────────────────────────────────────────────────────────────────
    if "oregon" in state_l and ("portland" in city_l or "multnomah" in county_l):
        return "portland"

    # ── Arizona ───────────────────────────────────────────────────────────────
    if "arizona" in state_l:
        if "phoenix" in city_l:
            return "phoenix"
        if "tucson" in city_l or "pima" in county_l:
            return "tucson"
        if "mesa" in city_l or "maricopa" in county_l:
            return "mesa"

    # ── Ohio ──────────────────────────────────────────────────────────────────
    if "ohio" in state_l:
        if "columbus" in city_l or "franklin" in county_l:
            return "columbus"
        if "cincinnati" in city_l or "hamilton" in county_l:
            return "cincinnati"
        if "cleveland" in city_l or "cuyahoga" in county_l:
            return "cleveland"

    # ── Michigan ──────────────────────────────────────────────────────────────
    if "michigan" in state_l and "detroit" in city_l:
        return "detroit"

    # ── Georgia ───────────────────────────────────────────────────────────────
    if "georgia" in state_l and "atlanta" in city_l:
        return "atlanta"

    # ── North Carolina ────────────────────────────────────────────────────────
    if "north carolina" in state_l:
        if "charlotte" in city_l:
            return "charlotte"
        if "raleigh" in city_l or "wake" in county_l:
            return "raleigh"

    # ── Florida ───────────────────────────────────────────────────────────────
    if "florida" in state_l:
        if "jacksonville" in city_l or "duval" in county_l:
            return "jacksonville"
        if "miami" in city_l or "miami-dade" in county_l:
            return "miami"
        if "tampa" in city_l or "hillsborough" in county_l:
            return "tampa"
        if "orlando" in city_l or "orange" in county_l:
            return "orlando"

    # ── Pennsylvania ──────────────────────────────────────────────────────────
    if "pennsylvania" in state_l:
        if "philadelphia" in city_l or "philadelphia" in county_l:
            return "philadelphia"
        if "pittsburgh" in city_l or "allegheny" in county_l:
            return "pittsburgh"

    # ── Indiana ───────────────────────────────────────────────────────────────
    if "indiana" in state_l and ("indianapolis" in city_l or "marion" in county_l):
        return "indianapolis"

    # ── Nevada ────────────────────────────────────────────────────────────────
    if "nevada" in state_l and ("las vegas" in city_l or "clark" in county_l or "henderson" in city_l):
        return "las_vegas"

    # ── Kentucky ──────────────────────────────────────────────────────────────
    if "kentucky" in state_l and ("louisville" in city_l or "jefferson" in county_l):
        return "louisville"

    # ── Louisiana ─────────────────────────────────────────────────────────────
    if "louisiana" in state_l and ("new orleans" in city_l or "orleans" in county_l):
        return "new_orleans"

    # ── New Mexico ────────────────────────────────────────────────────────────
    if "new mexico" in state_l and ("albuquerque" in city_l or "bernalillo" in county_l):
        return "albuquerque"

    # ── Oklahoma ──────────────────────────────────────────────────────────────
    if "oklahoma" in state_l and ("oklahoma city" in city_l or "oklahoma" in county_l):
        return "oklahoma_city"

    # ── Missouri ──────────────────────────────────────────────────────────────
    if "missouri" in state_l:
        if "kansas city" in city_l or "jackson" in county_l:
            return "kansas_city"
        if "st. louis" in city_l or "saint louis" in city_l or "st louis" in city_l:
            return "st_louis"

    # ── Virginia ──────────────────────────────────────────────────────────────
    if "virginia" in state_l and "west virginia" not in state_l:
        if "virginia beach" in city_l:
            return "virginia_beach"
        if "richmond" in city_l:
            return "richmond"

    # ── Wisconsin ─────────────────────────────────────────────────────────────
    if "wisconsin" in state_l and ("milwaukee" in city_l or "milwaukee" in county_l):
        return "milwaukee"

    # ── Nebraska ──────────────────────────────────────────────────────────────
    if "nebraska" in state_l and ("omaha" in city_l or "douglas" in county_l):
        return "omaha"

    # ── Kansas ────────────────────────────────────────────────────────────────
    if "kansas" in state_l and ("wichita" in city_l or "sedgwick" in county_l):
        return "wichita"

    # ── Hawaii ────────────────────────────────────────────────────────────────
    if "hawaii" in state_l and ("honolulu" in city_l or "honolulu" in county_l):
        return "honolulu"

    # ── Alaska ────────────────────────────────────────────────────────────────
    if "alaska" in state_l and "anchorage" in city_l:
        return "anchorage"

    # ── South Carolina ────────────────────────────────────────────────────────
    if "south carolina" in state_l and ("columbia" in city_l or "richland" in county_l):
        return "columbia_sc"

    # ── Iowa ──────────────────────────────────────────────────────────────────
    if "iowa" in state_l and ("des moines" in city_l or "polk" in county_l):
        return "des_moines"

    # ── Rhode Island ──────────────────────────────────────────────────────────
    if "rhode island" in state_l and "providence" in city_l:
        return "providence"

    # ── Connecticut ───────────────────────────────────────────────────────────
    if "connecticut" in state_l and "hartford" in city_l:
        return "hartford"

    # ── New Jersey ────────────────────────────────────────────────────────────
    if "new jersey" in state_l and "newark" in city_l:
        return "newark"

    # ── New Hampshire ─────────────────────────────────────────────────────────
    if "new hampshire" in state_l and "manchester" in city_l:
        return "manchester_nh"

    return "generic"
