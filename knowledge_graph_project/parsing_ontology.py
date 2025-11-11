# -*- coding: utf-8 -*-
"""
Наполнение графа знаний «Гарри Поттер» (ru.fandom) с помощью rdflib.
Сохраняет результат в harrypotter_kg_ru.ttl
Определение подклассов персонажей по инфобоксу + категориям + тексту.
"""

import re
import time
import html
import urllib.parse
import requests
import logging
from bs4 import BeautifulSoup
from unidecode import unidecode
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, RDFS, OWL
from typing import Optional

# -----------------------------
# ЛОГИ
# -----------------------------
logging.basicConfig(
    level=logging.INFO,  # DEBUG для подробностей
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("hp-kg")

# -----------------------------
# ПАРАМЕТРЫ
# -----------------------------
BASE_IRI = "http://www.semanticweb.org/ekaterinakulesova/ontologies/2025/0/harry_potter#"
BASE = "https://harrypotter.fandom.com/ru/wiki/"

REQUEST_DELAY = 0.2
RELATION_DELAY = 0.05

OUT_FILE = "harrypotter_kg_ru.ttl"
CHECKPOINT_EVERY = 120
_save_counter = 0

# -----------------------------
# RDF граф
# -----------------------------
g = Graph()
HP = Namespace(BASE_IRI)
HPO = Namespace(BASE_IRI)
g.bind("hp", HP)
g.bind("hpo", HPO)
g.bind("rdfs", RDFS)
g.bind("owl", OWL)

def qn(term) -> str:
    try:
        return g.namespace_manager.normalizeUri(term)
    except Exception:
        return str(term)

# --- Классы ---
classes = {
    "Thing": HPO.Thing,
    "Artifact": HPO.Artifact,
    "Character": HPO.Character,
    "Human": HPO.Human,
    "Muggle": HPO.Muggle,
    "Squib": HPO.Squib,
    "Wizard": HPO.Wizard,
    "Magical_creature": HPO.Magical_creature,
    "Centaur": HPO.Centaur,
    "Ghost": HPO.Ghost,
    "Giant": HPO.Giant,
    "Giant_spider": HPO.Giant_spider,
    "House": HPO.House,
    "House_elf": HPO.House_elf,
    "Mermaid": HPO.Mermaid,
    "Event": HPO.Event,
    "Big": HPO.Big,
    "Small": HPO.Small,
    "Location": HPO.Location,
    "Organization": HPO.Organization,
    "Potion": HPO.Potion,
    "Role": HPO.Role,
    "Spell": HPO.Spell,
}
for c, p in [
    (classes["Artifact"], classes["Thing"]),
    (classes["Character"], classes["Thing"]),
    (classes["Human"], classes["Character"]),
    (classes["Muggle"], classes["Human"]),
    (classes["Squib"], classes["Human"]),
    (classes["Wizard"], classes["Human"]),
    (classes["Magical_creature"], classes["Character"]),
    (classes["Centaur"], classes["Magical_creature"]),
    (classes["Ghost"], classes["Magical_creature"]),
    (classes["Giant"], classes["Magical_creature"]),
    (classes["Giant_spider"], classes["Magical_creature"]),
    (classes["House_elf"], classes["Magical_creature"]),
    (classes["Mermaid"], classes["Magical_creature"]),
    (classes["Event"], classes["Thing"]),
    (classes["Big"], classes["Event"]),
    (classes["Small"], classes["Event"]),
    (classes["House"], classes["Thing"]),
    (classes["Location"], classes["Thing"]),
    (classes["Organization"], classes["Thing"]),
    (classes["Potion"], classes["Thing"]),
    (classes["Role"], classes["Thing"]),
    (classes["Spell"], classes["Thing"]),
]:
    g.add((c, RDF.type, OWL.Class))
    g.add((c, RDFS.subClassOf, p))

# --- Свойства ---
obj_props = {
    "activeAt": HPO.activeAt,
    "artifactInvolvedIn": HPO.artifactInvolvedIn,
    "friendWith": HPO.friendWith,
    "hasRole": HPO.hasRole,
    "memberOf": HPO.memberOf,
    "participatedIn": HPO.participatedIn,
    "relativeOf": HPO.relativeOf,
    "hasFather": HPO.hasFather,
    "hasMother": HPO.hasMother,
    "marriedWith": HPO.marriedWith,
    "romanceWith": HPO.romanceWith,
    "studiedAt": HPO.studiedAt,
    "takePartInEvent": HPO.takePartInEvent,
    "tookPlaceAt": HPO.tookPlaceAt,
}
for p in obj_props.values():
    g.add((p, RDF.type, OWL.ObjectProperty))

# -----------------------------
# HTTP session (ретраи)
# -----------------------------
session = requests.Session()
retry = Retry(
    total=4, backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"], raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)
session.mount("http://", adapter)

def http_get(url: str) -> BeautifulSoup | None:
    try:
        r = session.get(url, headers={"User-Agent": "hp-kg-populator/1.0"}, timeout=20)
        if r.status_code == 200:
            return BeautifulSoup(r.text, "html.parser")
        logger.warning("HTTP %s: %s", r.status_code, url)
    except requests.RequestException as e:
        logger.warning("Ошибка запроса %s: %s", url, e)
    return None

# -----------------------------
# Утилиты + чекпоинты
# -----------------------------
def save_checkpoint(force=False):
    global _save_counter
    if not force and _save_counter < CHECKPOINT_EVERY:
        return
    g.serialize(destination=OUT_FILE, format="turtle")
    logger.info("Сохранено в %s (триплетов: %s)", OUT_FILE, len(g))
    _save_counter = 0

def bump_counter(n=1):
    global _save_counter
    _save_counter += n
    save_checkpoint(False)

SKIP_TITLE_PATTERNS = [
    r"\(персонажи\)$", r"\(персонаж\)$", r"\(персонажи фильма\)$",
    r"^Список($|[ \t])", r"^Персонажи($|[ \t])", r"^Категория:",
]
def should_skip_title(title: str) -> bool:
    if not title:
        return True
    t = title.strip()
    for pat in SKIP_TITLE_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return True
    return False

def slugify(label: str) -> str:
    txt = html.unescape(label).strip()
    ascii_txt = unidecode(txt)
    ascii_txt = re.sub(r"[\s/]+", "_", ascii_txt)
    ascii_txt = re.sub(r"[^A-Za-z0-9_\-]", "", ascii_txt)
    return ascii_txt or "entity"

def hp_entity(s: str) -> URIRef:
    return HP[s]

def add_labeled_instance(uri: URIRef, label_ru: str, rdf_type: URIRef):
    already = (uri, RDF.type, None) in g
    g.add((uri, RDF.type, rdf_type))
    g.add((uri, RDFS.label, Literal(label_ru, lang="ru")))
    if not already:
        logger.info("%s ← %s (%s)", qn(rdf_type), label_ru, qn(uri))
        bump_counter()

def fandom_url(title_ru: str) -> str:
    return urllib.parse.urljoin(BASE, urllib.parse.quote(title_ru.replace(" ", "_")))

# --------- ИСПРАВЛЕНО: реальные категории из шапки страницы ----------
def parse_categories(soup: BeautifulSoup) -> set[str]:
    """
    Возвращает категории именно этой статьи (из шапки / page header).
    Не сканируем всю страницу, чтобы не ловить навигацию/шаблоны.
    """
    cats = set()
    for a in soup.select('.page-header__categories a.category[href^="/ru/wiki/Категория:"]'):
        t = (a.get("title") or a.get_text(strip=True) or "").strip()
        if t.startswith("Категория:"):
            cats.add(t.replace("Категория:", "").strip())
    # резервный блок категорий (если включен темой)
    for a in soup.select('#articleCategories a[href^="/ru/wiki/Категория:"]'):
        t = (a.get("title") or a.get_text(strip=True) or "").strip()
        if t.startswith("Категория:"):
            cats.add(t.replace("Категория:", "").strip())
    return cats

def parse_infobox(soup: BeautifulSoup) -> dict:
    data = {}
    box = soup.select_one(".portable-infobox")
    if not box:
        return data
    for row in box.select(".pi-data"):
        label = row.select_one(".pi-data-label")
        value = row.select_one(".pi-data-value")
        if not label or not value:
            continue
        key = label.get_text(separator=" ", strip=True)
        text = value.get_text(separator=" ", strip=True)
        links = []
        for a in value.select("a[href]"):
            href = a.get("href") or ""
            title = a.get("title") or a.get_text(strip=True)
            if href.startswith("/ru/wiki/") and title and "/Категория:" not in href:
                links.append(title)
        data[key] = {"text": text, "links": links}
    return data

def ensure_entity(title_ru: str, rdf_type: URIRef):
    uri = hp_entity(slugify(title_ru))
    add_labeled_instance(uri, title_ru, rdf_type)
    return uri

def link_by_titles(subject_uri: URIRef, prop: URIRef, titles: list[str], fallback_type: URIRef):
    for t in titles:
        if should_skip_title(t):
            continue
        obj = hp_entity(slugify(t))
        if (obj, RDF.type, None) not in g:
            add_labeled_instance(obj, t, fallback_type)
            ##time.sleep(RELATION_DELAY)
        g.add((subject_uri, prop, obj))


detect_type_cache: dict[str, Optional[URIRef]] = {}

# New helper: попытка определить тип сущности по её странице
def determine_type_for_title(title_ru: str) -> Optional[URIRef]:
    """
    Если в графе уже есть тип — возвращаем его.
    Иначе пробуем запросить страницу персонажа и вычислить тип через type_from_sources.
    Кешируем результаты в detect_type_cache, чтобы не делать лишних запросов.
    Возвращаем найденный класс или None, если не удалось определить.
    """
    if not title_ru:
        return None
    # кеш
    if title_ru in detect_type_cache:
        return detect_type_cache[title_ru]
    obj = hp_entity(slugify(title_ru))
    # если уже есть тип в графе — вернём первый найденный URIRef
    for _, _, t in g.triples((obj, RDF.type, None)):
        if isinstance(t, URIRef):
            detect_type_cache[title_ru] = t
            return t
        # если встретился не-URIRef — игнорируем и продолжаем

    # попробуем получить страницу
    url = fandom_url(title_ru)
    soup = http_get(url)
    ##time.sleep(REQUEST_DELAY)
    if not soup:
        detect_type_cache[title_ru] = None
        return None
    # если нет инфобокса — не считаем это персональной страницей
    if not soup.select_one(".portable-infobox"):
        detect_type_cache[title_ru] = None
        return None
    info = parse_infobox(soup)
    cats = parse_categories(soup)
    page_text = " ".join(soup.stripped_strings)
    rdf_type = type_from_sources(info, cats, page_text)
    detect_type_cache[title_ru] = rdf_type
    return rdf_type


# Новая функция: для супругов — не назначаем сразу fallback, а пытаемся проанализировать каждого по имени/ссылке
def link_people_analyze(subject_uri: URIRef, prop: URIRef, titles: list[str], fallback_type: URIRef):
    for t in titles:
        if should_skip_title(t):
            continue
        # попытаемся определить тип через страницу
        detected_type = determine_type_for_title(t)
        use_type = detected_type or fallback_type
        obj = hp_entity(slugify(t))
        if (obj, RDF.type, None) not in g:
            # создаём сущность с найденным типом (или fallback)
            add_labeled_instance(obj, t, use_type)
            ##time.sleep(RELATION_DELAY)
        g.add((subject_uri, prop, obj))


# -----------------------------
# 3) Маппинг полей инфобокса -> свойства/классы
# -----------------------------

FIELD_MAP = {
    "Дом": ("memberOf", classes["House"]),
    "Организация": ("memberOf", classes["Organization"]),
    "Принадлежность": ("memberOf", classes["Organization"]),

    "Место обучения": ("studiedAt", classes["Location"]),
    "Обучался в": ("studiedAt", classes["Location"]),
    "Школа": ("studiedAt", classes["Location"]),
    "Учился в": ("studiedAt", classes["Location"]),

    "Род занятий": ("hasRole", classes["Role"]),
    "Профессия": ("hasRole", classes["Role"]),
    "Должность": ("hasRole", classes["Role"]),
    "Специальность": ("hasRole", classes["Role"]),

    "Супруг": ("marriedWith", classes["Character"]),
    "Супруга": ("marriedWith", classes["Character"]),
    "Супруг(а)": ("marriedWith", classes["Character"]),
    "Отец": ("hasFather", classes["Character"]),
    "Мать": ("hasMother", classes["Character"]),
    "Друзья": ("friendWith", classes["Character"]),
    "Любовный интерес": ("romanceWith", classes["Character"]),
    "Романтические отношения": ("romanceWith", classes["Character"]),

    # подсказки типа — отдельно
    "Вид": ("type_hint", None),
    "Вид(ы)": ("type_hint", None),
    "Раса": ("type_hint", None),
    "Раса/вид": ("type_hint", None),
    "Принадлежность к виду": ("type_hint", None),
    "Пол": ("sex_hint", None),

    # важно: чтобы поле попало в info
    "Чистота крови": ("blood_status_hint", None),
}

CATEGORY_TO_CLASS = {
    # люди
    "Люди": classes["Human"],
    "Маги": classes["Wizard"],
    "Маги по алфавиту": classes["Wizard"],
    "Магглы": classes["Muggle"],
    "Сквибы": classes["Squib"],
    "Маглорождённые волшебники": classes["Wizard"],
    "Чистокровные волшебники": classes["Wizard"],
    "Полукровки": classes["Wizard"],
    # существа
    "Домовые эльфы": classes["House_elf"],
    "Привидения": classes["Ghost"],
    "Кентавры": classes["Centaur"],
    "Акромантулы": classes["Giant_spider"],
    "Великаны": classes["Giant"],
    "Русалки": classes["Mermaid"],
}

CREATURE_KEYWORDS = {
    "кентавр": classes["Centaur"],
    "привидение": classes["Ghost"],
    "гигант": classes["Giant"],
    "акромантул": classes["Giant_spider"],
    "домовой эльф": classes["House_elf"],
    "русалк": classes["Mermaid"],  # стем
}

# --- поиск по слову с границами ---
def has_word(txt: str, pattern: str) -> bool:
    try:
        return re.search(rf"(?iu)\b{pattern}\w*\b", txt) is not None
    except re.error:
        patt = re.escape(pattern)
        return re.search(rf"(?iu)\b{patt}\w*\b", txt) is not None

def choose_most_specific(types: list[URIRef]) -> URIRef:
    priority = [
        classes["Centaur"], classes["Ghost"], classes["Giant"], classes["Giant_spider"],
        classes["House_elf"], classes["Mermaid"],
        classes["Wizard"], classes["Muggle"], classes["Squib"],
        classes["Human"], classes["Character"],
    ]
    for cls in priority:
        if cls in types:
            return cls
    return classes["Character"]

def type_from_sources(info: dict, cats: set[str], page_text: str) -> URIRef:
    """
    Улучшенная классификация:
      1. если 'Вид' содержит 'человек' — решаем по 'Чистота крови';
      2. если нет 'Чистоты крови', пробуем контекст (дом, категории, текст);
      3. если есть 'Вид' существо — выбираем класс существа;
      4. иначе по категориям или контексту страницы;
      5. дефолт Human.
    """

    # --- вспомогательная функция для поля "Чистота крови" ---
    def classify_by_purity() -> Optional[URIRef]:
        purity = (info.get("Чистота крови", {}) or {}).get("text", "")
        purity = purity.lower()
        if not purity:
            return None
        if has_word(purity, "маглорожд"):
            return classes["Wizard"]
        if has_word(purity, "сквиб"):
            return classes["Squib"]
        if any(has_word(purity, s) for s in ["чистокровный", "полукров", "грязнокров"]):
            return classes["Wizard"]
        if has_word(purity, "магл") and not has_word(purity, "маглорожд"):
            return classes["Muggle"]
        return None

    # --- 1) Вид / Раса ---
    raw_kind = ""
    for k in ("Вид", "Вид(ы)", "Раса", "Раса/вид", "Принадлежность к виду"):
        if k in info:
            raw_kind = info[k]["text"].lower().strip()
            break

        # --- 2) существа по виду ---
    if raw_kind:
        for key, cls in CREATURE_KEYWORDS.items():
            if key in raw_kind:
                return cls
        if "волшебник" in raw_kind or "маг" in raw_kind:
            return classes["Wizard"]
        if "великан" in raw_kind:
            return classes["Giant"]
        if "привидение" in raw_kind or "призрак" in raw_kind:
            return classes["Ghost"]
        if "кентавр" in raw_kind:
            return classes["Centaur"]
        if "акромантул" in raw_kind:
            return classes["Giant_spider"]
        if "домовой" in raw_kind or "эльф" in raw_kind:
            return classes["House_elf"]
        if "русалк" in raw_kind:
            return classes["Mermaid"]

    # человек → смотрим чистоту крови
    if raw_kind and "челов" in raw_kind:
        by_purity = classify_by_purity()
        if by_purity:
            return by_purity
        # если чистота не указана, проверим контекст (дом, категории)
        if "Дом" in info:
            house = info["Дом"]["text"].lower()
            if any(h in house for h in ["гриффиндор", "слизерин", "когтевран", "пуффендуй"]):
                return classes["Wizard"]
        # категории про Хогвартс или магию → Wizard
        for c in cats:
            if any(x in c.lower() for x in ["хогвартс", "маг", "волшебник"]):
                return classes["Wizard"]
        # если упоминается обучение в Хогвартсе
        if "обучался" in page_text.lower() or "хогвартс" in page_text.lower():
            return classes["Wizard"]
        return classes["Human"]

    # --- 3) Категории ---
    for c in cats:
        if c in CATEGORY_TO_CLASS:
            return CATEGORY_TO_CLASS[c]
        if any(x in c.lower() for x in ["хогвартс", "маг", "волшебник"]):
            return classes["Wizard"]

    # --- 4) Контекст страницы ---
    txt = page_text.lower()
    if "хогвартс" in txt or "палочка" in txt or "чары" in txt or "волшебник" in txt:
        return classes["Wizard"]
    for key, cls in CREATURE_KEYWORDS.items():
        if has_word(txt, key):
            return cls

    # --- 5) Чистота крови (если вид не указан вовсе) ---
    by_purity = classify_by_purity()
    if by_purity:
        return by_purity

    # --- 6) дефолт ---
    return classes["Human"]

# -----------------------------
# 4) Скраперы
# -----------------------------

def scrape_character(title_ru: str):
    if should_skip_title(title_ru):
        return
    url = fandom_url(title_ru)
    soup = http_get(url)
    ##time.sleep(REQUEST_DELAY)
    if not soup:
        logger.warning("Пропуск (нет доступа): %s", title_ru)
        return

    # только реальные персональные страницы
    if not soup.select_one(".portable-infobox"):
        logger.debug("Пропуск (нет инфобокса): %s", title_ru)
        return

    info = parse_infobox(soup)
    cats = parse_categories(soup)  # реальные категории
    rdf_type = type_from_sources(info, cats, soup.get_text(separator=" ", strip=True))
    subj = ensure_entity(title_ru, rdf_type)

    # метаданные
    if "Пол" in info:
        g.add((subj, RDFS.comment, Literal(f"Пол: {info['Пол']['text']}", lang="ru")))

    # связи из инфобокса
    for key, val in info.items():
        if key not in FIELD_MAP:
            continue
        prop_key, fallback_cls = FIELD_MAP[key]
        if prop_key in ("type_hint", "sex_hint", "blood_status_hint") or prop_key not in obj_props:
            continue
        prop_uri = obj_props[prop_key]
        # для персональных связей (супруг/а, отец, мать, друзья, роман, родственники)
        # пытаемся анализировать тип связанного по его странице, а не ставить
        # сразу общий fallback-class
        PERSON_RELATIONS = {"marriedWith", "hasFather", "hasMother", "friendWith", "romanceWith", "relativeOf"}
        if prop_key in PERSON_RELATIONS:
            if val["links"]:
                link_people_analyze(subj, prop_uri, val["links"], fallback_cls)
            else:
                v = val["text"]
                if not v or should_skip_title(v):
                    continue
                detected = determine_type_for_title(v)
                use_type = detected or fallback_cls
                obj = ensure_entity(v, use_type)
                g.add((subj, prop_uri, obj))
            continue

        # прочие связи: прежняя логика
        if val["links"]:
            link_by_titles(subj, prop_uri, val["links"], fallback_cls)
        else:
            v = val["text"]
            if not v or should_skip_title(v):
                continue
            obj = ensure_entity(v, fallback_cls)
            g.add((subj, prop_uri, obj))

def scrape_single_page_as(label_ru: str, rdf_type: URIRef):
    if should_skip_title(label_ru):
        return
    ensure_entity(label_ru, rdf_type)

# Пагинация + фильтры
def iter_category_members(category_title_ru: str, cap: int | None = None):
    url = fandom_url("Категория:" + category_title_ru)
    seen, count = set(), 0
    while url:
        soup = http_get(url)
        ##time.sleep(REQUEST_DELAY)
        if not soup:
            break
        for a in soup.select("a.category-page__member-link"):
            href = a.get("href") or ""
            title = a.get("title") or a.get_text(strip=True)
            if not title or title in seen:
                continue
            if "/Категория:" in href or should_skip_title(title):
                continue
            seen.add(title)
            yield title
            count += 1
            if cap and count >= cap:
                return
        next_a = soup.select_one('a.category-page__pagination-next')
        url = next_a["href"] if (next_a and next_a.get("href")) else None
        if url and url.startswith("/"):
            url = urllib.parse.urljoin(BASE, url)

def scrape_category_characters(category_title_ru: str, cap: int, delay: float = 0.2):
    logger.info("Категория персонажей: %s (cap=%s)", category_title_ru, cap)
    for title in iter_category_members(category_title_ru, cap=cap):
        scrape_character(title)
        ##time.sleep(delay)

def scrape_category_entities(category_title_ru: str, rdf_type: URIRef, cap: int, delay: float = 0.1):
    logger.info("Категория сущностей: %s → %s (cap=%s)", category_title_ru, qn(rdf_type), cap)
    for title in iter_category_members(category_title_ru, cap=cap):
        ensure_entity(title, rdf_type)
        ##time.sleep(delay)

def scrape_category_list(category_title_ru: str, want_type: URIRef, cap: int):
    url = fandom_url("Категория:" + category_title_ru)
    soup = http_get(url)
    ##time.sleep(REQUEST_DELAY)
    if not soup:
        return
    items = []
    for a in soup.select("a.category-page__member-link"):
        href = a.get("href") or ""
        title = a.get("title") or a.get_text(strip=True)
        if not title or should_skip_title(title) or "/Категория:" in href:
            continue
        items.append(title)
    for title in items[:cap]:
        ensure_entity(title, want_type)
        ##time.sleep(RELATION_DELAY)

# -----------------------------
# Семена и списки категорий
# -----------------------------
CHAR_SEED = [
    "Гарри Поттер","Гермиона Грейнджер","Рон Уизли","Альбус Дамблдор","Северус Снегг",
    "Драко Малфой","Рубеус Хагрид","Минерва Макгонагалл","Сириус Блэк","Лорд Волан-де-Морт",
]
HOUSES = ["Гриффиндор", "Слизерин", "Когтевран", "Пуффендуй"]
ORGS = ["Орден Феникса", "Пожиратели смерти", "Министерство магии"]
LOCATIONS = ["Хогвартс", "Косой переулок", "Хогсмид", "Азкабан"]

PERSON_CATS = [
    "Персонажи", "Люди", "Маги", "Ученики Хогвартса", "Преподаватели Хогвартса",
    "Домовые эльфы", "Привидения", "Кентавры", "Акромантулы", "Великаны", "Русалки",
]

ENTITY_CATS = [
    ("Локации", classes["Location"]),
    ("Организации", classes["Organization"]),
    ("Артефакты", classes["Artifact"]),
    ("Должности", classes["Role"]),
]

# -----------------------------
# main
# -----------------------------
def main():
    # базовые узлы
    for h in HOUSES: scrape_single_page_as(h, classes["House"])
    for o in ORGS:   scrape_single_page_as(o, classes["Organization"])
    for l in LOCATIONS: scrape_single_page_as(l, classes["Location"])

    # семена персонажей
    for name in CHAR_SEED:
        scrape_character(name)
        # time.sleep(0.4)

    # массовые категории персонажей
    for cat in PERSON_CATS:
        scrape_category_characters(cat, cap=400, delay=0.2)

    # прочие сущности
    for cat, tp in ENTITY_CATS:
        scrape_category_entities(cat, tp, cap=300, delay=0.1)

    # заклинания и зелья
    scrape_category_list("Заклинания", classes["Spell"], cap=200)
    scrape_category_list("Зелья", classes["Potion"], cap=200)

    # финал
    save_checkpoint(force=True)
    logger.info("Готово. Триплетов в графе: %s", len(g))

if __name__ == "__main__":
    main()
