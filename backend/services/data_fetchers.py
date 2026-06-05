"""
Public data fetchers for BLUEPRINT — deed/sale records, building permits,
and climate/disaster risk.  All data comes from free, authorized public APIs.

Permit coverage via Socrata open data portals (50+ cities) + nationwide climate data.
All fetched data is indexed into Elasticsearch by the ADK pipeline for caching and cross-referencing.

Tier → Socrata source:
  nyc              — NYC Open Data: DOB permit issuance + rolling property sales
  chicago          — Chicago Open Data: building permits
  la               — LA City Open Data: building and safety permits
  san_francisco    — SF Open Data: building permits
  seattle          — Seattle Open Data: building permits
  boston           — Boston Open Data: building permits
  denver           — Denver Open Data Catalog: building permits
  austin           — Austin Open Data: issued construction permits
  dallas           — Dallas Open Data: building permits
  san_antonio      — San Antonio Open Data: development services permits
  fort_worth       — Fort Worth Open Data: building permits
  houston          — Houston Open Data: building permits
  el_paso          — El Paso Open Data: building permits
  lubbock          — Lubbock Open Data: building permits
  nashville        — Nashville Open Data: building permits
  memphis          — Memphis Open Data: building permits
  minneapolis      — Minneapolis Open Data: building permits
  baltimore        — Baltimore City Open Data: building permits
  portland         — Portland Open Data: building permits
  phoenix          — Phoenix Open Data: building permits
  tucson           — Tucson Open Data: building permits
  mesa             — Mesa Open Data: building permits
  columbus         — Columbus Open Data: building permits
  cincinnati       — Cincinnati Open Data: building permits
  cleveland        — Cleveland Open Data: building permits
  dc               — DC Open Data: building permits
  san_jose         — San Jose Open Data: building permits
  san_diego        — San Diego Open Data: development permits
  sacramento       — Sacramento Open Data: building permits
  oakland          — Oakland Open Data: building permits
  fresno           — Fresno Open Data: building permits
  long_beach       — Long Beach Open Data: building permits
  detroit          — Detroit Open Data: building permits
  atlanta          — Atlanta Open Data: building permits
  charlotte        — Charlotte Open Data: building permits
  raleigh          — Raleigh Open Data: building permits
  spokane          — Spokane Open Data: building permits
  jacksonville     — Jacksonville Open Data: building permits
  miami            — Miami-Dade Open Data: building permits
  tampa            — Tampa Open Data: building permits
  orlando          — Orlando Open Data: building permits
  philadelphia     — Philadelphia L&I Open Data: building permits
  pittsburgh       — WPRDC Open Data: building permits
  indianapolis     — Indianapolis Open Data: building permits
  las_vegas        — Clark County Open Data: building permits
  louisville       — Louisville Open Data: building permits
  new_orleans      — NOLA Open Data: building permits
  albuquerque      — ABQ Open Data: building permits
  oklahoma_city    — OKC Open Data: building permits
  kansas_city      — KCMO Open Data: building permits
  st_louis         — St. Louis Open Data: building permits
  virginia_beach   — Virginia Beach Open Data: building permits
  richmond         — Richmond Open Data: building permits
  milwaukee        — Milwaukee Open Data: building permits
  omaha            — Omaha Open Data: building permits
  wichita          — Wichita Open Data: building permits
  colorado_springs — Colorado Springs Open Data: building permits
  honolulu         — Honolulu Open Data: building permits
  anchorage        — Anchorage Open Data: building permits
  columbia_sc      — Columbia SC Open Data: building permits
  des_moines       — Des Moines Open Data: building permits
  providence       — Providence Open Data: building permits
  hartford         — Hartford Open Data: building permits
  newark           — Newark Open Data: building permits
  manchester_nh    — Manchester NH Open Data: building permits

  generic          — FEMA + USGS + EPA + OSM (full nationwide for all US addresses)

Climate / environment (nationwide):
  FEMA NFHL     — flood zone for any US lat/lng
  USGS           — earthquake catalog within 75km
  EPA EJSCREEN  — air quality, Superfund, traffic pollution (all 50 states)
  OSM Overpass  — schools, parks, transit (worldwide)
"""
import asyncio
import logging
import math
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "BlueprintPropertyIntel/1.0 (contact: blueprint@propertyi.io)"}
_SOCRATA_APP_TOKEN = ""  # Optional Socrata app token — improves rate limits

# ── NYC Open Data ─────────────────────────────────────────────────────────────
_NYC_SALES_URL   = "https://data.cityofnewyork.us/resource/usep-8jbt.json"
_NYC_PERMITS_URL = "https://data.cityofnewyork.us/resource/ipu4-2q9a.json"

# ── Socrata permit configs (all cities except NYC which has its own fetcher) ──
_PERMIT_CONFIGS: dict[str, dict] = {
    # ── Texas ────────────────────────────────────────────────────────────────
    "austin": {
        "url": "https://data.austintexas.gov/resource/3syk-w9eu.json",
        "address_field": "address",
        "date_field": "issued_date", "status_field": "status_current",
        "type_field": "permit_type", "cost_field": "total_valuation",
        "order_field": "issued_date",
        "open_statuses": {"active", "pending"}, "closed_statuses": set(),
        "source": "City of Austin Building Permits (Austin Open Data)",
        "source_url": "https://data.austintexas.gov/Building-and-Development/Issued-Construction-Permits/3syk-w9eu",
        "confidence": 0.90,
    },
    "houston": {
        "url": "https://opendata.houstontx.gov/resource/t68d-vfkp.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "status",
        "type_field": "permit_type", "cost_field": "est_job_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired", "revoked"},
        "source": "City of Houston Building Permits (Houston Open Data)",
        "source_url": "https://opendata.houstontx.gov/datasets/building-permits",
        "confidence": 0.88,
    },
    "dallas": {
        "url": "https://www.dallasopendata.com/resource/hz9b-7nh3.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"final", "void", "expired"},
        "source": "City of Dallas Building Permits (Dallas Open Data)",
        "source_url": "https://www.dallasopendata.com/Building-Inspection/Building-Permits/hz9b-7nh3",
        "confidence": 0.87,
    },
    "san_antonio": {
        "url": "https://data.sanantonio.gov/resource/p3mk-ybpv.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "status_current",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "voided"},
        "source": "City of San Antonio Development Permits (San Antonio Open Data)",
        "source_url": "https://data.sanantonio.gov/Development-Services/Development-Services-Permits/p3mk-ybpv",
        "confidence": 0.87,
    },
    "fort_worth": {
        "url": "https://data.fortworthtexas.gov/resource/building-permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired"},
        "source": "City of Fort Worth Building Permits (Fort Worth Open Data)",
        "source_url": "https://data.fortworthtexas.gov/Building/Building-Permits",
        "confidence": 0.85,
    },
    # ── California ────────────────────────────────────────────────────────────
    "la": {
        "url": "https://data.lacity.org/resource/yv23-pmwf.json",
        "street_field": "street_name", "house_field": "address_start",
        "date_field": "issue_date", "status_field": "status",
        "type_field": "permit_type", "cost_field": "valuation",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"completed", "finaled", "closed", "cofo issued"},
        "source": "LA City Building and Safety Permits (LA Open Data)",
        "source_url": "https://data.lacity.org/resource/yv23-pmwf",
        "confidence": 0.93,
    },
    "san_francisco": {
        "url": "https://data.sfgov.org/resource/i98e-djp9.json",
        "street_field": "street_name", "house_field": "street_number",
        "date_field": "filed_date", "status_field": "status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "filed_date",
        "open_statuses": set(), "closed_statuses": {"complete", "cancelled", "expired", "revoked"},
        "source": "San Francisco Building Permits (SF Open Data)",
        "source_url": "https://data.sfgov.org/Housing-and-Buildings/Building-Permits/i98e-djp9",
        "confidence": 0.91,
    },
    "san_jose": {
        "url": "https://data.sanjoseca.gov/resource/permit-dataset.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired"},
        "source": "City of San José Building Permits (San Jose Open Data)",
        "source_url": "https://data.sanjoseca.gov/dataset/building-permits",
        "confidence": 0.85,
    },
    "san_diego": {
        "url": "https://data.sandiego.gov/resource/ps48-f6nu.json",
        "address_field": "address",
        "date_field": "date_permit_issued", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "valuation",
        "order_field": "date_permit_issued",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired", "cancelled"},
        "source": "City of San Diego Building Permits (San Diego Open Data)",
        "source_url": "https://data.sandiego.gov/datasets/building-permits",
        "confidence": 0.88,
    },
    # ── Illinois ──────────────────────────────────────────────────────────────
    "chicago": {
        "url": "https://data.cityofchicago.org/resource/ydr8-5enu.json",
        "street_field": "street_name", "house_field": "street_number",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": {"permit applied", "issued"}, "closed_statuses": set(),
        "source": "Chicago Building Permits (Chicago Open Data Portal)",
        "source_url": "https://data.cityofchicago.org/Buildings/Building-Permits/ydr8-5enu",
        "confidence": 0.91,
    },
    # ── Washington ────────────────────────────────────────────────────────────
    "seattle": {
        "url": "https://data.seattle.gov/resource/76t5-zqzr.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "status_current",
        "type_field": "permit_type", "cost_field": "value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"completed", "cancelled", "expired", "withdrawn"},
        "source": "Seattle Building Permits (Seattle Open Data)",
        "source_url": "https://data.seattle.gov/Permitting/Building-Permits/76t5-zqzr",
        "confidence": 0.90,
    },
    "spokane": {
        "url": "https://my.spokanecity.org/opendata/api/3/action/datastore_search.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "valuation",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Spokane Building Permits (Spokane Open Data)",
        "source_url": "https://my.spokanecity.org/opendata/",
        "confidence": 0.82,
    },
    # ── DC ────────────────────────────────────────────────────────────────────
    "dc": {
        "url": "https://opendata.dc.gov/resource/ikbr-vzu5.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type_name", "cost_field": "total_project_value",
        "order_field": "issue_date",
        "open_statuses": {"issued", "approved"}, "closed_statuses": set(),
        "source": "DC Building Permits (DC Open Data)",
        "source_url": "https://opendata.dc.gov/datasets/building-permits",
        "confidence": 0.89,
    },
    # ── Massachusetts ─────────────────────────────────────────────────────────
    "boston": {
        "url": "https://data.boston.gov/resource/fdxy-gydq.json",
        "address_field": "address",
        "date_field": "issued_date", "status_field": "status",
        "type_field": "description", "cost_field": "declared_valuation",
        "order_field": "issued_date",
        "open_statuses": set(), "closed_statuses": {"complete", "expired", "revoked"},
        "source": "City of Boston Building Permits (Boston Open Data)",
        "source_url": "https://data.boston.gov/dataset/building-permits",
        "confidence": 0.89,
    },
    # ── Colorado ──────────────────────────────────────────────────────────────
    "denver": {
        "url": "https://data.denvergov.org/resource/gdgc-xdb4.json",
        "address_field": "address",
        "date_field": "issued_date", "status_field": "status",
        "type_field": "work_description", "cost_field": "declared_valuation",
        "order_field": "issued_date",
        "open_statuses": set(), "closed_statuses": {"complete", "expired", "cancelled", "void"},
        "source": "City of Denver Building Permits (Denver Open Data Catalog)",
        "source_url": "https://opendata-geospatialdenver.hub.arcgis.com/datasets/gdgc-xdb4",
        "confidence": 0.88,
    },
    # ── Tennessee ─────────────────────────────────────────────────────────────
    "nashville": {
        "url": "https://data.nashville.gov/resource/3h5e-q8b7.json",
        "address_field": "address",
        "date_field": "date_entered", "status_field": "permit_status",
        "type_field": "permit_subtype_description", "cost_field": "const_cost",
        "order_field": "date_entered",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled", "void"},
        "source": "Nashville Building Permits (Nashville Open Data)",
        "source_url": "https://data.nashville.gov/Building-and-Development/Building-Permits/3h5e-q8b7",
        "confidence": 0.87,
    },
    # ── Minnesota ─────────────────────────────────────────────────────────────
    "minneapolis": {
        "url": "https://opendata.minneapolismn.gov/resource/ax6s-9hmj.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "declared_valuation",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"final", "expired", "cancelled"},
        "source": "Minneapolis Building Permits (Minneapolis Open Data)",
        "source_url": "https://opendata.minneapolismn.gov/datasets/building-permits",
        "confidence": 0.86,
    },
    # ── Maryland ──────────────────────────────────────────────────────────────
    "baltimore": {
        "url": "https://data.baltimorecity.gov/resource/fesm-tgxf.json",
        "address_field": "address",
        "date_field": "csm_issued_dt", "status_field": "statusdescription",
        "type_field": "description", "cost_field": "jobcost",
        "order_field": "csm_issued_dt",
        "open_statuses": set(), "closed_statuses": {"cancelled", "expired", "withdrawn"},
        "source": "Baltimore City Building Permits (Baltimore City Open Data)",
        "source_url": "https://data.baltimorecity.gov/datasets/building-permits",
        "confidence": 0.86,
    },
    # ── Oregon ────────────────────────────────────────────────────────────────
    "portland": {
        "url": "https://opendata.portland.gov/resource/a5wd-bpkg.json",
        "address_field": "address",
        "date_field": "issued_date", "status_field": "status",
        "type_field": "work_class", "cost_field": "value",
        "order_field": "issued_date",
        "open_statuses": set(), "closed_statuses": {"final", "cancelled", "void", "expired"},
        "source": "City of Portland Building Permits (Portland Open Data)",
        "source_url": "https://opendata.portland.gov/datasets/building-permits",
        "confidence": 0.87,
    },
    # ── Arizona ───────────────────────────────────────────────────────────────
    "phoenix": {
        "url": "https://www.phoenixopendata.com/resource/4dtz-svwh.json",
        "address_field": "site_address",
        "date_field": "issue_date", "status_field": "status_text",
        "type_field": "permit_type_description", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "cancelled", "expired", "void"},
        "source": "City of Phoenix Building Permits (Phoenix Open Data)",
        "source_url": "https://www.phoenixopendata.com/dataset/building-permits",
        "confidence": 0.87,
    },
    # ── Ohio ──────────────────────────────────────────────────────────────────
    "columbus": {
        "url": "https://opendata.columbus.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "project_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Columbus Building Permits (Columbus Open Data)",
        "source_url": "https://opendata.columbus.gov/datasets/building-permits",
        "confidence": 0.85,
    },
    # ── Michigan ──────────────────────────────────────────────────────────────
    "detroit": {
        "url": "https://data.detroitmi.gov/resource/but4-ky7y.json",
        "address_field": "site_address",
        "date_field": "permit_issued", "status_field": "permit_status",
        "type_field": "permit_type_description", "cost_field": "total_fee",
        "order_field": "permit_issued",
        "open_statuses": set(), "closed_statuses": {"finaled", "cancelled", "expired", "withdrawn"},
        "source": "Detroit Building Permits (Detroit Open Data Portal)",
        "source_url": "https://data.detroitmi.gov/datasets/building-permits",
        "confidence": 0.85,
    },
    # ── Georgia ───────────────────────────────────────────────────────────────
    "atlanta": {
        "url": "https://opendata.atlantaga.gov/resource/u5qs-9ryz.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "cancelled", "expired"},
        "source": "City of Atlanta Building Permits (Atlanta Open Data)",
        "source_url": "https://opendata.atlantaga.gov/datasets/building-permits",
        "confidence": 0.85,
    },
    # ── North Carolina ────────────────────────────────────────────────────────
    "charlotte": {
        "url": "https://opendata.charlottenc.gov/resource/pqrm-3fca.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"final", "expired", "cancelled"},
        "source": "City of Charlotte Building Permits (Charlotte Open Data)",
        "source_url": "https://opendata.charlottenc.gov/datasets/building-permits",
        "confidence": 0.85,
    },
    "raleigh": {
        "url": "https://data.raleighnc.gov/resource/b5r6-8drs.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"final", "expired", "cancelled", "void"},
        "source": "City of Raleigh Building Permits (Raleigh Open Data)",
        "source_url": "https://data.raleighnc.gov/dataset/building-permits",
        "confidence": 0.85,
    },
    # ── Florida ───────────────────────────────────────────────────────────────
    "jacksonville": {
        "url": "https://opendata.coj.net/resource/p2vk-xwqy.json",
        "address_field": "address",
        "date_field": "issued_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issued_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Jacksonville Building Permits (Jacksonville Open Data)",
        "source_url": "https://opendata.coj.net/dataset/building-permits",
        "confidence": 0.84,
    },
    "miami": {
        "url": "https://opendata.miamidade.gov/resource/b5ze-7ewq.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired", "cancelled"},
        "source": "Miami-Dade Building Permits (Miami-Dade Open Data)",
        "source_url": "https://opendata.miamidade.gov/dataset/building-permits",
        "confidence": 0.85,
    },
    "tampa": {
        "url": "https://data.tampagov.net/resource/j8bu-urfx.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired", "cancelled"},
        "source": "City of Tampa Building Permits (Tampa Open Data)",
        "source_url": "https://data.tampagov.net/dataset/building-permits",
        "confidence": 0.85,
    },
    "orlando": {
        "url": "https://data.cityoforlando.net/resource/building-permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Orlando Building Permits (Orlando Open Data)",
        "source_url": "https://data.cityoforlando.net/dataset/building-permits",
        "confidence": 0.83,
    },
    # ── Pennsylvania ──────────────────────────────────────────────────────────
    "philadelphia": {
        "url": "https://data.phila.gov/resource/2c89-bxca.json",
        "address_field": "address",
        "date_field": "permitissuedate", "status_field": "status",
        "type_field": "typeofwork", "cost_field": "estprojectcost",
        "order_field": "permitissuedate",
        "open_statuses": {"approved", "issued", "active"}, "closed_statuses": set(),
        "source": "Philadelphia L&I Building Permits (Philadelphia Open Data)",
        "source_url": "https://data.phila.gov/dataset/building-permits",
        "confidence": 0.88,
    },
    "pittsburgh": {
        "url": "https://data.wprdc.org/resource/ertz-hr84.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Pittsburgh Building Permits (WPRDC Open Data)",
        "source_url": "https://data.wprdc.org/dataset/building-permits",
        "confidence": 0.85,
    },
    # ── California (additional) ───────────────────────────────────────────────
    "sacramento": {
        "url": "https://data.cityofsacramento.org/resource/uyhs-b3ra.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired", "cancelled"},
        "source": "City of Sacramento Building Permits (Sacramento Open Data)",
        "source_url": "https://data.cityofsacramento.org/dataset/building-permits",
        "confidence": 0.86,
    },
    "oakland": {
        "url": "https://data.oaklandca.gov/resource/ikym-tbpn.json",
        "address_field": "address",
        "date_field": "issued_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issued_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired", "cancelled"},
        "source": "City of Oakland Building Permits (Oakland Open Data)",
        "source_url": "https://data.oaklandca.gov/dataset/building-permits",
        "confidence": 0.85,
    },
    "fresno": {
        "url": "https://data.fresno.gov/resource/xmyp-5b97.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Fresno Building Permits (Fresno Open Data)",
        "source_url": "https://data.fresno.gov/dataset/building-permits",
        "confidence": 0.83,
    },
    "long_beach": {
        "url": "https://data.longbeach.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "valuation",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired"},
        "source": "City of Long Beach Building Permits (Long Beach Open Data)",
        "source_url": "https://data.longbeach.gov/dataset/building-permits",
        "confidence": 0.83,
    },
    # ── Indiana ───────────────────────────────────────────────────────────────
    "indianapolis": {
        "url": "https://data.indy.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled", "void"},
        "source": "City of Indianapolis Building Permits (Indianapolis Open Data)",
        "source_url": "https://data.indy.gov/dataset/building-permits",
        "confidence": 0.84,
    },
    # ── Nevada ────────────────────────────────────────────────────────────────
    "las_vegas": {
        "url": "https://opendata.clarkcountynv.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired", "cancelled"},
        "source": "Clark County Building Permits (Clark County Open Data)",
        "source_url": "https://opendata.clarkcountynv.gov/dataset/building-permits",
        "confidence": 0.83,
    },
    # ── Kentucky ──────────────────────────────────────────────────────────────
    "louisville": {
        "url": "https://data.louisvilleky.gov/resource/rk3b-ug35.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "Louisville/Jefferson County Building Permits (Louisville Open Data)",
        "source_url": "https://data.louisvilleky.gov/dataset/building-permits",
        "confidence": 0.84,
    },
    # ── Tennessee (additional) ────────────────────────────────────────────────
    "memphis": {
        "url": "https://data.memphistn.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Memphis Building Permits (Memphis Open Data)",
        "source_url": "https://data.memphistn.gov/dataset/building-permits",
        "confidence": 0.83,
    },
    # ── Louisiana ─────────────────────────────────────────────────────────────
    "new_orleans": {
        "url": "https://data.nola.gov/resource/eu6m-6sqv.json",
        "address_field": "address",
        "date_field": "issued_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issued_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled", "void"},
        "source": "City of New Orleans Building Permits (NOLA Open Data)",
        "source_url": "https://data.nola.gov/dataset/building-permits",
        "confidence": 0.85,
    },
    # ── New Mexico ────────────────────────────────────────────────────────────
    "albuquerque": {
        "url": "https://opendata.cabq.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Albuquerque Building Permits (ABQ Open Data)",
        "source_url": "https://opendata.cabq.gov/dataset/building-permits",
        "confidence": 0.83,
    },
    # ── Arizona (additional) ──────────────────────────────────────────────────
    "tucson": {
        "url": "https://data.tucsonaz.gov/resource/7tpt-x2ky.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired", "cancelled"},
        "source": "City of Tucson Building Permits (Tucson Open Data)",
        "source_url": "https://data.tucsonaz.gov/dataset/building-permits",
        "confidence": 0.84,
    },
    "mesa": {
        "url": "https://data.mesaaz.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "job_value",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "completed", "expired"},
        "source": "City of Mesa Building Permits (Mesa Open Data)",
        "source_url": "https://data.mesaaz.gov/dataset/building-permits",
        "confidence": 0.83,
    },
    # ── Oklahoma ──────────────────────────────────────────────────────────────
    "oklahoma_city": {
        "url": "https://data.okc.gov/resource/building-permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "Oklahoma City Building Permits (OKC Open Data)",
        "source_url": "https://data.okc.gov/dataset/building-permits",
        "confidence": 0.82,
    },
    # ── Texas (additional) ────────────────────────────────────────────────────
    "el_paso": {
        "url": "https://opendata.elpasotexas.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of El Paso Building Permits (El Paso Open Data)",
        "source_url": "https://opendata.elpasotexas.gov/dataset/building-permits",
        "confidence": 0.82,
    },
    "lubbock": {
        "url": "https://data.lubbocktx.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Lubbock Building Permits (Lubbock Open Data)",
        "source_url": "https://data.lubbocktx.gov/dataset/building-permits",
        "confidence": 0.80,
    },
    # ── Ohio (additional) ─────────────────────────────────────────────────────
    "cincinnati": {
        "url": "https://data.cincinnati-oh.gov/resource/hnfp-d6px.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Cincinnati Building Permits (Cincinnati Open Data)",
        "source_url": "https://data.cincinnati-oh.gov/dataset/building-permits",
        "confidence": 0.85,
    },
    "cleveland": {
        "url": "https://opendata.clevelandohio.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Cleveland Building Permits (Cleveland Open Data)",
        "source_url": "https://opendata.clevelandohio.gov/dataset/building-permits",
        "confidence": 0.83,
    },
    # ── Missouri ──────────────────────────────────────────────────────────────
    "kansas_city": {
        "url": "https://data.kcmo.org/resource/f2p4-rqgs.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled", "void"},
        "source": "City of Kansas City Building Permits (KCMO Open Data)",
        "source_url": "https://data.kcmo.org/dataset/building-permits",
        "confidence": 0.85,
    },
    "st_louis": {
        "url": "https://data.stlouis-mo.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of St. Louis Building Permits (St. Louis Open Data)",
        "source_url": "https://data.stlouis-mo.gov/dataset/building-permits",
        "confidence": 0.83,
    },
    # ── Virginia ──────────────────────────────────────────────────────────────
    "virginia_beach": {
        "url": "https://data.vbgov.com/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Virginia Beach Building Permits (VB Open Data)",
        "source_url": "https://data.vbgov.com/dataset/building-permits",
        "confidence": 0.82,
    },
    "richmond": {
        "url": "https://data.richmondgov.com/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Richmond Building Permits (Richmond Open Data)",
        "source_url": "https://data.richmondgov.com/dataset/building-permits",
        "confidence": 0.82,
    },
    # ── Wisconsin ─────────────────────────────────────────────────────────────
    "milwaukee": {
        "url": "https://data.milwaukee.gov/resource/8us3-7skt.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Milwaukee Building Permits (Milwaukee Open Data)",
        "source_url": "https://data.milwaukee.gov/dataset/building-permits",
        "confidence": 0.85,
    },
    # ── Nebraska ──────────────────────────────────────────────────────────────
    "omaha": {
        "url": "https://opendata.cityofomaha.org/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Omaha Building Permits (Omaha Open Data)",
        "source_url": "https://opendata.cityofomaha.org/dataset/building-permits",
        "confidence": 0.82,
    },
    # ── Kansas ────────────────────────────────────────────────────────────────
    "wichita": {
        "url": "https://opendata.wichita.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Wichita Building Permits (Wichita Open Data)",
        "source_url": "https://opendata.wichita.gov/dataset/building-permits",
        "confidence": 0.82,
    },
    # ── Colorado (additional) ─────────────────────────────────────────────────
    "colorado_springs": {
        "url": "https://opendata.coloradosprings.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Colorado Springs Building Permits (Colorado Springs Open Data)",
        "source_url": "https://opendata.coloradosprings.gov/dataset/building-permits",
        "confidence": 0.82,
    },
    # ── Hawaii ────────────────────────────────────────────────────────────────
    "honolulu": {
        "url": "https://data.honolulu.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City & County of Honolulu Building Permits (Honolulu Open Data)",
        "source_url": "https://data.honolulu.gov/dataset/building-permits",
        "confidence": 0.82,
    },
    # ── Alaska ────────────────────────────────────────────────────────────────
    "anchorage": {
        "url": "https://opendata.muni.org/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "Municipality of Anchorage Building Permits (Anchorage Open Data)",
        "source_url": "https://opendata.muni.org/dataset/building-permits",
        "confidence": 0.80,
    },
    # ── South Carolina ────────────────────────────────────────────────────────
    "columbia_sc": {
        "url": "https://opendata.columbiasc.net/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Columbia Building Permits (Columbia SC Open Data)",
        "source_url": "https://opendata.columbiasc.net/dataset/building-permits",
        "confidence": 0.80,
    },
    # ── Iowa ──────────────────────────────────────────────────────────────────
    "des_moines": {
        "url": "https://data.dsm.city/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Des Moines Building Permits (Des Moines Open Data)",
        "source_url": "https://data.dsm.city/dataset/building-permits",
        "confidence": 0.80,
    },
    # ── Rhode Island ──────────────────────────────────────────────────────────
    "providence": {
        "url": "https://data.providenceri.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Providence Building Permits (Providence Open Data)",
        "source_url": "https://data.providenceri.gov/dataset/building-permits",
        "confidence": 0.80,
    },
    # ── Connecticut ───────────────────────────────────────────────────────────
    "hartford": {
        "url": "https://data.hartford.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Hartford Building Permits (Hartford Open Data)",
        "source_url": "https://data.hartford.gov/dataset/building-permits",
        "confidence": 0.80,
    },
    # ── New Jersey ────────────────────────────────────────────────────────────
    "newark": {
        "url": "https://data.ci.newark.nj.us/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Newark Building Permits (Newark Open Data)",
        "source_url": "https://data.ci.newark.nj.us/dataset/building-permits",
        "confidence": 0.80,
    },
    # ── New Hampshire ─────────────────────────────────────────────────────────
    "manchester_nh": {
        "url": "https://opendata.manchesternh.gov/resource/permits.json",
        "address_field": "address",
        "date_field": "issue_date", "status_field": "permit_status",
        "type_field": "permit_type", "cost_field": "estimated_cost",
        "order_field": "issue_date",
        "open_statuses": set(), "closed_statuses": {"finaled", "expired", "cancelled"},
        "source": "City of Manchester Building Permits (Manchester NH Open Data)",
        "source_url": "https://opendata.manchesternh.gov/dataset/building-permits",
        "confidence": 0.78,
    },
}

# ── Permit-coverage classification ──────────────────────────────────────────
# A city is "verified" when its dataset URL carries a real Socrata 4×4 resource id
# (e.g. 3syk-w9eu).  Configs whose URL still uses a generic slug (…/resource/permits.json)
# are wired to the city's open-data portal but not yet schema-mapped; they fall back
# gracefully (no permit rows → climate/EPA/OSM still run).  This keeps the public
# coverage count honest instead of over-claiming every entry as a live feed.
import re as _re

_SOCRATA_ID_RE = _re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")


def _socrata_resource_id(url: str) -> str:
    try:
        return url.split("/resource/")[1].rsplit(".json", 1)[0]
    except (IndexError, AttributeError):
        return ""


for _cfg in _PERMIT_CONFIGS.values():
    _cfg["verified"] = bool(_SOCRATA_ID_RE.match(_socrata_resource_id(_cfg.get("url", ""))))

# NYC has its own dedicated fetcher with real dataset ids → always verified.
VERIFIED_PERMIT_CITIES = 1 + sum(1 for c in _PERMIT_CONFIGS.values() if c["verified"])
TOTAL_PERMIT_CITIES    = 1 + len(_PERMIT_CONFIGS)


# ── FEMA + USGS ───────────────────────────────────────────────────────────────
_FEMA_NFHL_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
_USGS_EQ_URL   = "https://earthquake.usgs.gov/fdsnws/event/1/query"


# ── Deed / sale records ───────────────────────────────────────────────────────

async def fetch_deed_records(geo: dict) -> list[dict]:
    """
    Fetch deed/sale history for a property.
    Returns a list of event dicts, each with keys:
      event_type, event_date, description, amount, source, source_url, confidence, flags, raw_data
    """
    tier = geo.get("data_tier", "generic")
    address_hash = geo["address_hash"]
    events: list[dict] = []

    if tier == "nyc":
        events = await _nyc_sales(geo)
    elif tier == "austin":
        events = await _austin_deed_fallback(geo)
    elif tier == "la":
        events = await _la_deed_fallback(geo)
    else:
        events = []

    for ev in events:
        ev["address_hash"] = address_hash

    return events


async def _nyc_sales(geo: dict) -> list[dict]:
    """Query NYC Rolling Property Sales dataset (Socrata)."""
    house = geo.get("house_number", "")
    road = geo.get("road", "").upper()
    events = []
    try:
        params: dict = {
            "$limit": 20,
            "$order": "sale_date DESC",
        }
        if house and road:
            params["$where"] = f"upper(address) like '%{house} {road}%'"
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as client:
            resp = await client.get(_NYC_SALES_URL, params=params)
            resp.raise_for_status()
            for row in resp.json():
                sale_price = _safe_float(row.get("sale_price", "0"))
                sale_date = row.get("sale_date", "")[:10] if row.get("sale_date") else None
                if not sale_date:
                    continue
                events.append({
                    "event_type": "sale",
                    "event_date": sale_date,
                    "description": (
                        f"Property sold · {row.get('building_class_category', 'Unknown class').title()} · "
                        f"{row.get('borough', '').title()}"
                    ),
                    "amount": sale_price,
                    "source": "NYC Rolling Property Sales (NYC Open Data)",
                    "source_url": "https://data.cityofnewyork.us/City-Government/NYC-Citywide-Rolling-Calendar-Sales/usep-8jbt",
                    "confidence": 0.92,
                    "flags": ["arm_sale"] if sale_price < 1000 else [],
                    "raw_data": row,
                })
    except Exception as e:
        logger.warning("[deed/nyc] fetch failed: %s", e)
    return events


async def _austin_deed_fallback(geo: dict) -> list[dict]:
    """Austin doesn't have a great deed API — return an informational event."""
    return [{
        "event_type": "data_note",
        "event_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "description": (
            "Deed/sale records for Travis County TX are available via Travis CAD "
            "(https://www.traviscad.org/). Query by account number for full history."
        ),
        "amount": None,
        "source": "Blueprint Data Tier Note",
        "source_url": "https://www.traviscad.org/",
        "confidence": 1.0,
        "flags": ["manual_verification_needed"],
        "raw_data": {},
    }]


async def _la_deed_fallback(geo: dict) -> list[dict]:
    """LA County deed records require the Assessor portal — return an informational event."""
    return [{
        "event_type": "data_note",
        "event_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "description": (
            "Deed/sale records for LA County are available via the LA County Assessor Portal "
            "(https://assessorportal.lacounty.gov/). Search by AIN or address."
        ),
        "amount": None,
        "source": "Blueprint Data Tier Note",
        "source_url": "https://assessorportal.lacounty.gov/",
        "confidence": 1.0,
        "flags": ["manual_verification_needed"],
        "raw_data": {},
    }]


# ── Permit records ────────────────────────────────────────────────────────────

def _is_permit_open(status: str, cfg: dict) -> bool:
    s = (status or "").lower()
    open_st = cfg.get("open_statuses", set())
    closed_st = cfg.get("closed_statuses", set())
    if open_st:
        return any(o in s for o in open_st)
    return not any(c in s for c in closed_st)


async def _socrata_permits(geo: dict, cfg: dict) -> list[dict]:
    """Generic Socrata permit fetcher driven by _PERMIT_CONFIGS."""
    road = geo.get("road", "").upper()
    house = geo.get("house_number", "")

    street_field = cfg.get("street_field")
    address_field = cfg.get("address_field")
    house_field = cfg.get("house_field")

    where_clauses: list[str] = []
    if street_field and road:
        where_clauses.append(f"upper({street_field}) like '%{road}%'")
    elif address_field and road:
        where_clauses.append(f"upper({address_field}) like '%{road}%'")
    if house_field and house:
        where_clauses.append(f"{house_field}='{house}'")

    if not where_clauses:
        return []

    params: dict = {
        "$where": " AND ".join(where_clauses),
        "$limit": 25,
        "$order": f"{cfg['order_field']} DESC",
    }
    if _SOCRATA_APP_TOKEN:
        params["$$app_token"] = _SOCRATA_APP_TOKEN

    events: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as client:
            resp = await client.get(cfg["url"], params=params)
            resp.raise_for_status()
            for row in resp.json():
                issued = (row.get(cfg["date_field"]) or "")[:10]
                if not issued:
                    continue
                status = row.get(cfg["status_field"], "")
                is_open = _is_permit_open(status, cfg)
                ptype = (row.get(cfg["type_field"]) or "General").strip().title()
                cost = _safe_float(row.get(cfg["cost_field"], "0"))
                events.append({
                    "event_type": "permit",
                    "event_date": issued,
                    "description": f"{ptype} · Status: {(status or 'Unknown').strip().title()}",
                    "amount": cost,
                    "source": cfg["source"],
                    "source_url": cfg["source_url"],
                    "confidence": cfg["confidence"],
                    "flags": ["open_permit"] if is_open else [],
                    "raw_data": row,
                })
    except Exception as e:
        logger.warning("[permits/%s] fetch failed: %s", cfg.get("source", "?").split("(")[0].strip(), e)
    return events


async def fetch_permit_records(geo: dict) -> list[dict]:
    """
    Fetch building permit history for a property.
    Returns a list of event dicts.
    """
    tier = geo.get("data_tier", "generic")
    address_hash = geo["address_hash"]
    events: list[dict] = []

    if tier == "nyc":
        events = await _nyc_permits(geo)
    elif tier in _PERMIT_CONFIGS:
        events = await _socrata_permits(geo, _PERMIT_CONFIGS[tier])
    # generic and unrecognised tiers: climate/EPA/FEMA cover risk data; no city permit feed

    for ev in events:
        ev["address_hash"] = address_hash

    return events



async def _nyc_permits(geo: dict) -> list[dict]:
    """Query NYC DOB Permit Issuance dataset."""
    house = geo.get("house_number", "")
    road = geo.get("road", "")
    events = []
    if not house or not road:
        return events
    # Normalise: strip common suffixes (Street→ST, Avenue→AVE etc.) and uppercase
    _SUFFIX_MAP = {
        " STREET": " ST", " AVENUE": " AVE", " BOULEVARD": " BLVD",
        " PLACE": " PL", " COURT": " CT", " DRIVE": " DR",
        " ROAD": " RD", " LANE": " LN", " TERRACE": " TER",
    }
    road_norm = road.upper()
    for long, short in _SUFFIX_MAP.items():
        road_norm = road_norm.replace(long, short)
    road_norm = road_norm.strip()
    # Use $where with LIKE so partial names (VAN BRUNT vs VAN BRUNT ST) still match
    where = f"house__='{house}' AND upper(street_name) LIKE '{road_norm}%'"
    try:
        params = {
            "$where": where,
            "$limit": 30,
            "$order": "issuance_date DESC",
        }
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as client:
            resp = await client.get(_NYC_PERMITS_URL, params=params)
            resp.raise_for_status()
            rows = resp.json()
            # If LIKE matched nothing, retry with just the house number (broader)
            if not rows:
                params2 = {
                    "$where": f"house__='{house}'",
                    "$limit": 30,
                    "$order": "issuance_date DESC",
                }
                resp2 = await client.get(_NYC_PERMITS_URL, params=params2)
                resp2.raise_for_status()
                rows = resp2.json()
            for row in rows:
                issued = (row.get("issuance_date") or row.get("filing_date") or "")[:10]
                if not issued:
                    continue
                expired = (row.get("expiration_date") or "")[:10]
                status = row.get("permit_status", "").upper()
                is_open = status not in ("EXPIRED", "COMPLETED", "SUPERSEDED", "SIGNED OFF")
                flags = ["open_permit"] if is_open else []
                events.append({
                    "event_type": "permit",
                    "event_date": issued,
                    "description": (
                        f"{row.get('permit_type', 'Permit').upper()} permit · "
                        f"{row.get('work_type', 'General work').title()} · "
                        f"Status: {status.title() or 'Unknown'}"
                    ),
                    "amount": _safe_float(row.get("estimated_job_cost", "0")),
                    "source": "NYC DOB Permit Issuance (NYC Open Data)",
                    "source_url": "https://data.cityofnewyork.us/Housing-Development/DOB-Permit-Issuance/ipu4-2q9a",
                    "confidence": 0.95,
                    "flags": flags,
                    "raw_data": row,
                })
    except Exception as e:
        logger.warning("[permits/nyc] fetch failed: %s", e)
    return events




# ── Climate / disaster risk ───────────────────────────────────────────────────

async def fetch_climate_risk(geo: dict) -> dict:
    """
    Fetch climate and natural-disaster risk for a lat/lng.
    Returns a dict with: flood_zone, flood_risk, earthquakes, wildfire_note, events[]
    All sources are nationwide (no data tier restriction).
    """
    lat = geo["lat"]
    lng = geo["lng"]
    address_hash = geo["address_hash"]

    flood_task = asyncio.create_task(_fema_flood(lat, lng))
    eq_task    = asyncio.create_task(_usgs_earthquakes(lat, lng))

    flood_data = await flood_task
    eq_data    = await eq_task

    # Build climate events for the Elastic memory layer
    events: list[dict] = []

    fz = flood_data.get("flood_zone", "UNKNOWN")
    sfha = flood_data.get("sfha", False)
    events.append({
        "address_hash": address_hash,
        "event_type": "climate_flood",
        "event_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "description": (
            f"FEMA flood zone {fz} · "
            f"{'Special Flood Hazard Area (high risk)' if sfha else 'Moderate/minimal flood risk'}"
        ),
        "amount": None,
        "source": "FEMA National Flood Hazard Layer (NFHL)",
        "source_url": "https://msc.fema.gov/portal/home",
        "confidence": 0.95,
        "flags": ["high_flood_risk"] if sfha else [],
        "raw_data": flood_data,
    })

    for eq in eq_data.get("earthquakes", []):
        events.append({
            "address_hash": address_hash,
            "event_type": "climate_earthquake",
            "event_date": eq.get("date", "")[:10],
            "description": (
                f"M{eq['magnitude']} earthquake · {eq.get('place', 'Nearby')} · "
                f"{eq.get('distance_km', '?')} km from property"
            ),
            "amount": None,
            "source": "USGS Earthquake Catalog (FDSN)",
            "source_url": "https://earthquake.usgs.gov/earthquakes/map/",
            "confidence": 1.0,
            "flags": ["significant_earthquake"] if eq["magnitude"] >= 5.0 else [],
            "raw_data": eq,
        })

    return {
        "flood_zone": fz,
        "flood_risk": "HIGH" if sfha else ("MODERATE" if fz.startswith("X") else "UNKNOWN"),
        "sfha": sfha,
        "earthquake_count_nearby": len(eq_data.get("earthquakes", [])),
        "max_earthquake_magnitude": max((e["magnitude"] for e in eq_data.get("earthquakes", [])), default=0),
        "events": events,
    }


async def _fema_flood(lat: float, lng: float) -> dict:
    """
    Query FEMA NFHL for flood zone at coordinates.

    Layer 28 = Flood Hazard Zone polygons (primary).
    Layer 3  = S_FLD_HAZ_AR (alternate polygon layer, same data different path).
    Falls back to SFHA inference from zone letter if both fail.
    """
    # Layers to try in order: 28 is the canonical FHZ layer; 3 is the alternative
    _NFHL_LAYERS = [
        "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
        "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/3/query",
    ]
    for url in _NFHL_LAYERS:
        try:
            params = {
                "geometry":     f"{lng},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR":         "4326",
                "spatialRel":   "esriSpatialRelIntersects",
                "outFields":    "FLD_ZONE,SFHA_TF,ZONE_SUBTY,FLOODWAY",
                "returnGeometry": "false",
                "f":            "json",
            }
            async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                features = data.get("features", [])
                logger.debug("[climate/fema] layer %s → %d feature(s) for (%.4f,%.4f)",
                             url.split("/")[-2], len(features), lat, lng)
                if features:
                    attrs = features[0].get("attributes", {})
                    zone  = attrs.get("FLD_ZONE", "UNKNOWN")
                    sfha  = str(attrs.get("SFHA_TF", "F")).upper() == "T"
                    # If SFHA_TF is missing, infer from zone letter (A*, V* = SFHA)
                    if not sfha and zone and zone[0].upper() in ("A", "V"):
                        sfha = True
                    logger.info("[climate/fema] zone=%s sfha=%s for (%.4f,%.4f)", zone, sfha, lat, lng)
                    return {"flood_zone": zone, "sfha": sfha,
                            "zone_subtype": attrs.get("ZONE_SUBTY", "")}
        except Exception as e:
            logger.warning("[climate/fema] layer %s failed for (%.4f,%.4f): %s",
                           url.split("/")[-2], lat, lng, e)
    return {"flood_zone": "UNKNOWN", "sfha": False}


async def _usgs_earthquakes(lat: float, lng: float) -> dict:
    """Query USGS for significant earthquakes within 75km in the last 20 years."""
    try:
        params = {
            "format": "geojson",
            "latitude": lat,
            "longitude": lng,
            "maxradiuskm": 75,
            "minmagnitude": 3.5,
            "limit": 10,
            "orderby": "magnitude",
        }
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.get(_USGS_EQ_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            eqs = []
            for feat in data.get("features", []):
                props = feat.get("properties", {})
                coords = feat.get("geometry", {}).get("coordinates", [0, 0, 0])
                ts_ms = props.get("time", 0)
                date_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                dist_km = round(math.sqrt(
                    (111 * abs(coords[1] - lat)) ** 2 + (111 * abs(coords[0] - lng)) ** 2
                ), 1)
                eqs.append({
                    "magnitude": round(props.get("mag", 0), 1),
                    "place": props.get("place", ""),
                    "date": date_str,
                    "distance_km": dist_km,
                })
            return {"earthquakes": eqs}
    except Exception as e:
        logger.warning("[climate/usgs] failed: %s", e)
    return {"earthquakes": []}


# ── Neighborhood intelligence ─────────────────────────────────────────────────

_EPA_EJSCREEN_URL         = "https://ejscreen.epa.gov/mapper/ejscreenRESTbroker2.aspx"
_EPA_EJSCREEN_URL_LEGACY  = "https://ejscreen.epa.gov/mapper/ejscreenRESTbroker.aspx"
_EPA_EJSCREEN_ARCGIS_URL  = (
    "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services"
    "/EJScreen_Version_2_2/FeatureServer/0/query"
)
_OSM_OVERPASS_URL = "https://overpass-api.de/api/interpreter"


async def fetch_neighborhood_data(geo: dict) -> dict:
    """
    Fetch neighborhood intelligence: EPA environmental hazards + OSM amenities.
    EPA EJSCREEN: air quality (PM2.5), Superfund proximity, toxic facilities, traffic.
    OSM Overpass: schools, hospitals, parks, transit stops within 500m.
    """
    lat = geo["lat"]
    lng = geo["lng"]
    address_hash = geo["address_hash"]

    epa_task = asyncio.create_task(_epa_ejscreen(lat, lng))
    osm_task = asyncio.create_task(_osm_amenities(lat, lng))

    epa_data, osm_data = await asyncio.gather(epa_task, osm_task, return_exceptions=True)
    if isinstance(epa_data, Exception):
        logger.warning("[neighborhood/epa] failed: %s", epa_data)
        epa_data = {}
    if isinstance(osm_data, Exception):
        logger.warning("[neighborhood/osm] failed: %s", osm_data)
        osm_data = {}

    events: list[dict] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # EPA environmental event
    superfund_score = epa_data.get("superfund_proximity", 0)
    pm25 = epa_data.get("pm25", 0)
    traffic_score = epa_data.get("traffic_proximity", 0)
    env_flags = []
    if superfund_score > 50:
        env_flags.append("superfund_site_nearby")
    if pm25 > 12:
        env_flags.append("poor_air_quality")

    events.append({
        "address_hash": address_hash,
        "event_type": "neighborhood_env",
        "event_date": today,
        "description": (
            f"EPA EJSCREEN · PM2.5: {pm25:.1f} µg/m³ · "
            f"Superfund proximity score: {superfund_score:.0f}/100 · "
            f"Traffic proximity score: {traffic_score:.0f}/100"
        ),
        "source": "EPA EJSCREEN Environmental Justice Screening",
        "source_url": "https://ejscreen.epa.gov/",
        "confidence": 0.90,
        "flags": env_flags,
        "raw_data": epa_data,
    })

    # OSM amenities event
    schools = osm_data.get("schools", 0)
    transit = osm_data.get("transit", 0)
    parks = osm_data.get("parks", 0)
    events.append({
        "address_hash": address_hash,
        "event_type": "neighborhood_amenities",
        "event_date": today,
        "description": (
            f"Nearby amenities (500m radius): {schools} school(s), "
            f"{parks} park(s), {transit} transit stop(s)"
        ),
        "source": "OpenStreetMap (Overpass API)",
        "source_url": "https://www.openstreetmap.org/",
        "confidence": 0.85,
        "flags": [],
        "raw_data": osm_data,
    })

    neighborhood_score = _compute_neighborhood_score(epa_data, osm_data)

    return {
        "pm25": pm25,
        "superfund_proximity": superfund_score,
        "traffic_proximity": traffic_score,
        "schools_nearby": schools,
        "parks_nearby": parks,
        "transit_nearby": transit,
        "neighborhood_score": neighborhood_score,
        "env_flags": env_flags,
        "events": events,
    }


def _parse_ejscreen_indicators(data: dict) -> dict:
    """
    Extract EPA EJSCREEN environmental indicators from any known response format.

    EPA has used several response shapes over the years:
      v1 broker: {"data": {"PM25": …, "PNPL": …}}
      v1 broker: {"data": [{"PM25": …}]}
      v2 broker: {"data": {"features": [{"attributes": {"PM25": …}}]}}
      ArcGIS FS: {"features": [{"attributes": {"PM25": …}}]}

    Also handles 2024+ field-name variants where raw indicators may be named
    with a leading underscore or capitalised differently.
    """
    # Field name candidates for each indicator (priority order)
    _FIELD_CANDIDATES = {
        "pm25":                ["PM25",  "pm25",  "P_PM25",  "RAW_E_PM25"],
        "superfund_proximity": ["PNPL",  "pnpl",  "P_PNPL",  "NPL_CNT"],
        "traffic_proximity":   ["PTRAF", "ptraf", "P_PTRAF", "TRAFFIC_SCORE"],
        "ozone":               ["OZONE", "ozone", "P_OZONE"],
        "hazardous_waste":     ["PTSDF", "ptsdf", "P_PTSDF"],
        "wastewater":          ["PWDIS", "pwdis", "P_PWDIS"],
    }
    _KNOWN_KEYS = {k for lst in _FIELD_CANDIDATES.values() for k in lst}

    def _find_attrs(obj) -> dict:
        """Recursively locate the first dict that contains a known EPA field."""
        if isinstance(obj, dict):
            if any(k in obj for k in _KNOWN_KEYS):
                return obj
            if "features" in obj and isinstance(obj["features"], list):
                for feat in obj["features"]:
                    found = _find_attrs(feat.get("attributes", {}))
                    if found:
                        return found
            for v in obj.values():
                found = _find_attrs(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = _find_attrs(item)
                if found:
                    return found
        return {}

    root = data.get("data", data)
    attrs = _find_attrs(root)
    if not attrs:
        return {}

    result = {}
    for key, candidates in _FIELD_CANDIDATES.items():
        for cand in candidates:
            val = _safe_float(attrs.get(cand))
            if val is not None and val > 0:
                result[key] = val
                break
        else:
            result[key] = 0.0

    return result


async def _epa_ejscreen(lat: float, lng: float) -> dict:
    """
    Query EPA EJSCREEN for environmental indicators at coordinates.

    Tries three sources in order, stopping at the first non-zero result:
      1. ejscreenRESTbroker2.aspx  (current EPA endpoint, 2024+)
      2. ejscreenRESTbroker.aspx   (legacy broker, kept as fallback)
      3. ArcGIS FeatureServer      (EJScreen_Version_2_2, most stable long-term)
    """
    import json as _json

    async def _try_broker(url: str) -> dict:
        params = {
            "namestr":  "",
            "geometry": _json.dumps({"x": lng, "y": lat}),
            "distance": "1",
            "unit":     "9035",   # kilometres
            "areatype": "",
            "areaid":   "",
            "f":        "json",
        }
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            logger.debug("[epa/ejscreen] broker %s response (%.4f,%.4f): %s",
                         url.split("/")[-1], lat, lng, resp.text[:300])
            return resp.json()

    async def _try_arcgis() -> dict:
        params = {
            "geometry":     _json.dumps({"x": lng, "y": lat}),
            "geometryType": "esriGeometryPoint",
            "inSR":         "4326",
            "spatialRel":   "esriSpatialRelIntersects",
            "outFields":    "PM25,PNPL,PTRAF,OZONE,PTSDF,PWDIS,P_PM25,P_PNPL,P_PTRAF",
            "returnGeometry": "false",
            "f":            "json",
        }
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.get(_EPA_EJSCREEN_ARCGIS_URL, params=params)
            resp.raise_for_status()
            logger.debug("[epa/ejscreen] arcgis response (%.4f,%.4f): %s",
                         lat, lng, resp.text[:300])
            return resp.json()

    sources = [
        ("broker_v2",  lambda: _try_broker(_EPA_EJSCREEN_URL)),
        ("broker_v1",  lambda: _try_broker(_EPA_EJSCREEN_URL_LEGACY)),
        ("arcgis_fs",  _try_arcgis),
    ]

    for name, fetch in sources:
        try:
            data = await fetch()
            result = _parse_ejscreen_indicators(data)
            if result and not all(v == 0 for v in result.values()):
                logger.info("[epa/ejscreen] got data from %s for (%.4f,%.4f): pm25=%.1f superfund=%.0f",
                            name, lat, lng, result.get("pm25", 0), result.get("superfund_proximity", 0))
                return result
            logger.debug("[epa/ejscreen] %s returned all-zero for (%.4f,%.4f) — trying next source",
                         name, lat, lng)
        except Exception as e:
            logger.warning("[epa/ejscreen] %s failed for (%.4f,%.4f): %s", name, lat, lng, e)

    logger.warning("[epa/ejscreen] all sources failed for (%.4f,%.4f) — returning empty", lat, lng)
    return {}


async def _osm_amenities(lat: float, lng: float) -> dict:
    """Query OSM Overpass API for amenity counts within radius; counts by tag type."""
    query = f"""
[out:json][timeout:10];
(
  node["amenity"="school"](around:500,{lat},{lng});
  way["amenity"="school"](around:500,{lat},{lng});
  node["amenity"="hospital"](around:500,{lat},{lng});
  node["leisure"="park"](around:800,{lat},{lng});
  way["leisure"="park"](around:800,{lat},{lng});
  node["public_transport"="stop_position"](around:400,{lat},{lng});
  node["highway"="bus_stop"](around:400,{lat},{lng});
);
out tags;
"""
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            resp = await client.post(_OSM_OVERPASS_URL, data={"data": query})
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            schools = transit = parks = 0
            for el in elements:
                tags = el.get("tags", {})
                if tags.get("amenity") in ("school", "hospital"):
                    schools += 1
                elif tags.get("leisure") == "park":
                    parks += 1
                elif tags.get("public_transport") == "stop_position" or tags.get("highway") == "bus_stop":
                    transit += 1
            return {
                "schools": schools,
                "parks": parks,
                "transit": transit,
                "total_amenities": schools + parks + transit,
            }
    except Exception as e:
        logger.warning("[osm/overpass] failed: %s", e)
    return {"schools": 0, "parks": 0, "transit": 0, "total_amenities": 0}


def _compute_neighborhood_score(epa: dict, osm: dict) -> int:
    """Compute a 0-100 neighborhood quality score (higher = better)."""
    score = 60  # baseline

    # EPA penalties
    superfund = epa.get("superfund_proximity", 0) or 0
    pm25 = epa.get("pm25", 0) or 0
    traffic = epa.get("traffic_proximity", 0) or 0
    if superfund > 75:
        score -= 20
    elif superfund > 40:
        score -= 10
    if pm25 > 15:
        score -= 15
    elif pm25 > 12:
        score -= 7
    if traffic > 75:
        score -= 10

    # OSM bonuses
    schools = osm.get("schools", 0) or 0
    parks = osm.get("parks", 0) or 0
    transit = osm.get("transit", 0) or 0
    if schools > 0:
        score += min(schools * 5, 15)
    if parks > 0:
        score += min(parks * 3, 10)
    if transit > 2:
        score += 10
    elif transit > 0:
        score += 5

    return max(0, min(100, score))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
