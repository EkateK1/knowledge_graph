# -*- coding: utf-8 -*-
"""
Наполнение онтологии Protégé данными с русской Harry Potter Wiki (fandom, /ru/)

Зависимости:
    pip install rdflib requests beautifulsoup4 lxml
"""

import re
import time
import random
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from collections import Counter

import requests
from bs4 import BeautifulSoup
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS

# ----------------------- НАСТРОЙКИ -----------------------

ONTOLOGY_PATH = "/Users/ekaterinakulesova/PycharmProjects/knowledge_graph_project/Untitled.ttl"
ONTOLOGY_FORMAT = "turtle"

BASE_IRI = "http://www.semanticweb.org/ekaterinakulesova/ontologies/2025/0/harry_potter#"

HEADERS = {"User-Agent": "OntologyFillerBot/1.0 (educational use)"}
SLEEP_RANGE = (0.6, 1.2)
MAX_PAGES = 250

SEED_PAGES = [
    # персонажи
    "https://harrypotter.fandom.com/ru/wiki/Гарри_Поттер",
    "https://harrypotter.fandom.com/ru/wiki/Гермиона_Грейнджер",
    "https://harrypotter.fandom.com/ru/wiki/Рон_Уизли",
    "https://harrypotter.fandom.com/ru/wiki/Альбус_Дамблдор",
    "https://harrypotter.fandom.com/ru/wiki/Северус_Снегг",
    "https://harrypotter.fandom.com/ru/wiki/Драко_Малфой",
    "https://harrypotter.fandom.com/ru/wiki/Минерва_Макгонагалл",
    # дома / организация / школа
    "https://harrypotter.fandom.com/ru/wiki/Гриффиндор",
    "https://harrypotter.fandom.com/ru/wiki/Слизерин",
    "https://harrypotter.fandom.com/ru/wiki/Когтевран",
    "https://harrypotter.fandom.com/ru/wiki/Пуффендуй",
    "https://harrypotter.fandom.com/ru/wiki/Хогвартсская_школа_чародейства_и_волшебства",
    # списки (для разлёта по ссылкам)
    "https://harrypotter.fandom.com/ru/wiki/Список_заклинаний",
    "https://harrypotter.fandom.com/ru/wiki/Категория:Персонажи",
    "https://harrypotter.fandom.com/ru/wiki/Категория:Места",
    "https://harrypotter.fandom.com/ru/wiki/Категория:Магические_предметы",
]

# --------------------- ИНИЦИАЛИЗАЦИЯ RDF ---------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

g = Graph()
logging.info(f"Parsing ontology from {ONTOLOGY_PATH} (format={ONTOLOGY_FORMAT})")
g.parse(ONTOLOGY_PATH, format=ONTOLOGY_FORMAT)

EX = Namespace(BASE_IRI)
g.bind("ex", EX)

CLASSES = {
    "Artifact": EX.Artifact,
    "Character": EX.Character,
    "Human": EX.Human,
    "Magical_creature": EX.Magical_creature,
    "Event": EX.Event,
    "Big": EX.Big,
    "Small": EX.Small,
    "House": EX.House,
    "Location": EX.Location,
    "Organization": EX.Organization,
    "Potion": EX.Potion,
    "Role": EX.Role,
    "Spell": EX.Spell,
}

OP = {
    "activeAt": EX.activeAt,
    "artifactInvolvedIn": EX.artifactInvolvedIn,
    "friendWith": EX.friendWith,
    "hasRole": EX.hasRole,
    "memberOf": EX.memberOf,
    "participatedIn": EX.participatedIn,
    "relativeOf": EX.relativeOf,
    "hasFather": EX.hasFather,
    "hasMother": EX.hasMother,
    "marriedWith": EX.marriedWith,
    "romanceWith": EX.romanceWith,
    "studiedAt": EX.studiedAt,
    "takePartInEvent": EX.takePartInEvent,
    "tookPlaceAt": EX.tookPlaceAt,
}

# -------------------------- ФИЛЬТРЫ/ХЕЛПЕРЫ --------------------------

# RU/EN месяцы (для фильтра дат в заголовке)
RU_MONTHS = {
    "января","февраля","марта","апреля","мая","июня","июля","августа",
    "сентября","октября","ноября","декабря",
    "январь","февраль","март","апрель","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"
}
EN_MONTHS = {
    "january","february","march","april","may","june","july","august",
    "september","october","november","december"
}

MEDIA_TITLE_TOKENS = {
    # ru
    "(фильм)", "(саундтрек)", "(видеоигра)", "(телесериал)", "(книга)",
    # en (на всякий)
    "(film)", "(soundtrack)", "(video game)", "(tv series)", "(novel)", "(book)"
}
MEDIA_LEAD_HINTS = {
    "роман","книга","фильм","картина","кинофильм","саундтрек","эпизод","серия","сериал",
    "видеоигра","video game","novel","book","film","movie","soundtrack","episode","tv series"
}

# подсказки для локаций (без «школы», чтобы не ловить книги)
LOCATION_HINTS = {
    "деревня","город","улица","переулок","лес","озеро","река","замок","церковь","магазин",
    "паб","кладбище","остров","гора","район","округ","боро","парковая","парк","квартал","площадь",
    "village","town","city","street","alley","forest","lake","river","castle","church","shop","pub",
    "cemetery","island","mountain","borough","district","park","square"
}

def is_year_title(t: str) -> bool:
    t = t.strip().lower().replace("-", "-")  # возможный узкий дефис
    return bool(re.fullmatch(r"\d{3,4}(-е)?(\s*г(г|\.)?)?", t))  # 1991, 1880-е, 1991 гг.

def is_date_title(t: str) -> bool:
    parts = t.lower().split()
    return len(parts) == 2 and parts[0].isdigit() and (parts[1] in RU_MONTHS or parts[1] in EN_MONTHS)

def is_media_title(t: str) -> bool:
    tl = t.lower()
    return any(tok in tl for tok in MEDIA_TITLE_TOKENS)

def lead_text(soup: BeautifulSoup) -> str:
    p = soup.select_one("#mw-content-text > div.mw-parser-output > p")
    return (p.get_text(" ", strip=True) if p else "").lower()

def page_categories(soup: BeautifulSoup) -> List[str]:
    cats = []
    for a in soup.select('#mw-content-text a[href^="/ru/wiki/Категория:"]'):
        txt = a.get_text(" ", strip=True)
        if txt:
            cats.append(txt.lower())
    return cats

# -------------------------- УТИЛЫ --------------------------

def slug(name: str) -> str:
    """
    Делаем «мягкий» слаг: оставляем кириллицу (чтобы URI были на русском),
    убираем лишние символы, пробелы → подчёркивания.
    """
    s = name
    s = re.sub(r"[^\w\s\-А-Яа-яЁё]", "", s, flags=re.UNICODE).strip().replace(" ", "_")
    return re.sub(r"_+", "_", s)

def I(name: str) -> URIRef:
    return EX[slug(name)]

def add_label(ind: URIRef, label: str, lang: Optional[str] = "ru"):
    g.add((ind, RDFS.label, Literal(label, lang=lang)))

def add_link(s: URIRef, p: URIRef, o: URIRef):
    g.add((s, p, o))

def ensure_individual(name: str, class_local: Optional[str], label: Optional[str] = None) -> URIRef:
    uri = I(name)
    if class_local and class_local in CLASSES:
        g.add((uri, RDF.type, CLASSES[class_local]))
    if label:
        add_label(uri, label, "ru")  # русская метка
    return uri

# один Session + lxml быстрее
SESSION = requests.Session()

def get_page(url: str) -> Optional[BeautifulSoup]:
    try:
        r = SESSION.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            logging.warning("HTTP %s for %s", r.status_code, url)
            return None
        time.sleep(random.uniform(*SLEEP_RANGE))
        return BeautifulSoup(r.text, "lxml")
    except Exception as e:
        logging.warning("Request failed: %s", e)
        return None

# ---------------------- ПАРСИНГ -----------------------

# алиасы полей инфобокса (ru/en) -> «каноническое» имя
ALIASES = {
    "species": {"вид","species"},
    "house": {"факультет","дом","house"},
    "affiliation": {"принадлежность","организация","аффилиация","affiliation","organisation","organization"},
    "school": {"школа","образование","учёба","учеба","school","education"},
    "father": {"отец","father"},
    "mother": {"мать","mother"},
    "spouse": {"супруг","супруга","муж","жена","spouse"},
    "partner": {"партнёр","партнер","partner","romances"},
    "friends": {"друзья","friends"},
    "family": {"семья","family"},
    "residence": {"место жительства","проживание","место проживания","residence"},
    "location": {"расположение","местонахождение","локация","location"},
    "incantation": {"инкантация","заклинание","incantation"},
    "spell_type": {"тип заклинания","spell type","spell_type"},
    "manufacturer": {"производитель","manufacturer"},
    "owner": {"владелец","owner"},
    "material": {"материал","material"},
    "made": {"сделан","изготовлен","made"},
}

def key_to_canonical(k: str) -> Optional[str]:
    k = k.strip().lower()
    for canonical, variants in ALIASES.items():
        if k in variants:
            return canonical
    return None

def parse_infobox(soup: BeautifulSoup) -> Dict[str, List[str]]:
    """
    Берём либо data-source, либо подпись поля .pi-data-label,
    приводим к каноническому имени по ALIASES.
    """
    data: Dict[str, List[str]] = {}

    # строки с явным data-source
    for row in soup.select('.portable-infobox .pi-item'):
        src = row.get('data-source')
        key = None
        if src:
            key = key_to_canonical(src)
        else:
            label = row.select_one(".pi-data-label")
            if label:
                key = key_to_canonical(label.get_text(" ", strip=True))

        if not key:
            continue

        # значения — ссылки или текст
        vals = [a.get_text(" ", strip=True) for a in row.select("a") if a.get_text(strip=True)]
        if not vals:
            txt = row.get_text(" ", strip=True)
            if ":" in txt:
                txt = txt.split(":", 1)[1].strip()
            vals = [x.strip() for x in re.split(r",|;|/| и | and ", txt) if x.strip()]
        if vals:
            data.setdefault(key, [])
            for v in vals:
                if v not in data[key]:
                    data[key].append(v)
    return data

def classify(infobox: Dict[str, List[str]], title: str, lead: str, cats: List[str]) -> Optional[str]:
    t = title.strip()

    # отбрасываем даты/годы/медиа по заголовку
    if is_year_title(t) or is_date_title(t) or is_media_title(t):
        return None

    # отбрасываем медиа по лиду/категориям
    if any(h in lead for h in MEDIA_LEAD_HINTS):
        return None
    if any(re.search(r"(книг|роман|фильм|саундтрек|видеоигр|novel|book|film|movie|soundtrack|video game)", c) for c in cats):
        return None

    # дома
    if t in {"Гриффиндор","Слизерин","Когтевран","Пуффендуй"}:
        return "House"

    # заклинание
    if "spell_type" in infobox or "incantation" in infobox:
        return "Spell"

    # человек/существо
    if "species" in infobox:
        s = " ".join(infobox["species"]).lower()
        if any(w in s for w in ["человек","маг","волшебник","ведьма","human","witch","wizard"]):
            return "Human"
        return "Magical_creature"

    # артефакт
    if any(k in infobox for k in ["manufacturer","owner","made","material"]):
        return "Artifact"

    # локация (если нет признаков медиа)
    if ("location" in infobox and "species" not in infobox and "owner" not in infobox) or \
       any(w in lead for w in LOCATION_HINTS) or \
       any("места" in c or "locations" in c for c in cats):
        return "Location"

    # организация
    if "affiliation" in infobox and "species" not in infobox:
        return "Organization"
    if any(w in lead for w in ["организация","общество","орден","клуб","organisation","organization","society","order","group"]) or \
       any("организации" in c or "organizations" in c for c in cats):
        return "Organization"

    return None  # нет уверенности

def map_properties(subj: URIRef, info: Dict[str, List[str]]):
    def link(vals, prop, cls=None):
        if not vals:
            return
        for v in vals:
            obj = ensure_individual(v, cls, v)  # русские имена/метки
            add_link(subj, OP[prop], obj)

    link(info.get("house"), "memberOf", "House")
    link(info.get("affiliation"), "memberOf", "Organization")
    link(info.get("school"), "studiedAt", "Organization")
    link(info.get("father"), "hasFather", "Human")
    link(info.get("mother"), "hasMother", "Human")
    link(info.get("spouse"), "marriedWith", "Human")
    link(info.get("partner"), "romanceWith", "Human")
    link(info.get("residence") or info.get("location"), "activeAt", "Location")
    link(info.get("friends"), "friendWith", "Human")
    link(info.get("family"), "relativeOf")

def parse_page(url: str) -> Optional[Tuple[str, URIRef, List[str]]]:
    soup = get_page(url)
    if not soup:
        return None

    title_el = soup.select_one("#firstHeading")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)

    if is_year_title(title) or is_date_title(title) or is_media_title(title):
        return None

    info = parse_infobox(soup)
    lead = lead_text(soup)
    cats = page_categories(soup)

    cls = classify(info, title, lead, cats)
    if not cls:
        return None

    subj = ensure_individual(title, cls, title)
    map_properties(subj, info)
    logging.info("Added: %s (%s)", title, cls)

    # ссылки (не берём namespace-ссылки)
    links = []
    for a in soup.select("#mw-content-text a[href^='/ru/wiki/']"):
        href = a.get("href")
        if not href:
            continue
        if re.match(r"^/ru/wiki/[^/]+:", href):  # Категория:, Файл:, Обсуждение:, и т.п.
            continue
        full = "https://harrypotter.fandom.com" + href
        links.append(full)

    # dedup + ограничение
    seen = set()
    dedup = []
    for l in links:
        if l not in seen:
            seen.add(l)
            dedup.append(l)
            if len(dedup) >= 15:
                break

    return title, subj, dedup

# ----------------------- КРАУЛЕР -----------------------

def crawl(seed_urls, max_pages=200):
    queue = list(seed_urls)
    seen = set()
    pages = 0
    while queue and pages < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        res = parse_page(url)
        if not res:
            continue
        _, _, new_links = res
        for nl in new_links:
            if nl not in seen and nl not in queue:
                queue.append(nl)
        pages += 1
    logging.info("Crawled %d pages, queue left: %d", pages, len(queue))

# ----------------------- ОСНОВНОЙ КОД -----------------------

if __name__ == "__main__":
    logging.info("Start crawling (RU)…")
    crawl(SEED_PAGES, MAX_PAGES)

    out_file = Path(ONTOLOGY_PATH).with_name("ontology_filled_ru.ttl")
    g.serialize(out_file, format="turtle")
    logging.info(f"Saved filled ontology to {out_file}")

    # Статистика по классам
    cnt = Counter(o.split("#")[-1] for _, _, o in g.triples((None, RDF.type, None))
                  if isinstance(o, URIRef) and o.startswith(BASE_IRI))
    logging.info("By classes: %s", dict(cnt))
    logging.info(f"Total triples: {len(g)}")
