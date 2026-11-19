"""Pure logic of nutrition-sync: Notion rows to meals, daily totals, the CSV for HA, and what to change in Google Health"""
import dataclasses
import datetime as dt
import hashlib
import json
import zoneinfo

MEAL_TYPES = {"Завтрак": "BREAKFAST", "Обед": "LUNCH", "Ужин": "DINNER", "Перекус": "SNACK"}
# An empty Notion answer with more synced meals than this looks like a broken read, not a cleanup
MASS_DELETE_LIMIT = 3
# A late snack after midnight still belongs to the evening before
DAY_START = dt.timedelta(hours=4)


@dataclasses.dataclass(frozen=True)
class Meal:
    id: str
    product: str
    eaten: dt.datetime
    meal: str
    kcal: float
    protein: float
    fat: float
    carbs: float
    water: float = 0.0


@dataclasses.dataclass(frozen=True)
class DayTotal:
    day: dt.date
    kcal: float
    fat: float
    protein: float
    carbs: float
    full: bool
    water: float = 0.0


@dataclasses.dataclass(frozen=True)
class GooglePlan:
    create: list
    delete: list
    skipped_mass_delete: bool


def google_meal_type(meal):
    return MEAL_TYPES.get(meal, "MEAL_TYPE_UNSPECIFIED")


def _plain(prop):
    if not prop:
        return ""
    return "".join(part.get("plain_text", "") for part in prop.get(prop.get("type"), None) or [])


def _number(prop):
    value = (prop or {}).get("number")
    return None if value is None else float(value)


def _eaten(prop, tz):
    date = (prop or {}).get("date")
    if not date or not date.get("start"):
        return None
    start = date["start"]
    if len(start) == 10:
        # A date without time counts as noon, so it stays on its day
        return dt.datetime.combine(dt.date.fromisoformat(start), dt.time(12), tzinfo=tz)
    eaten = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    if eaten.tzinfo is None:
        zone = date.get("time_zone")
        eaten = eaten.replace(tzinfo=zoneinfo.ZoneInfo(zone) if zone else tz)
    return eaten


def parse_meals(pages, tz):
    """Meals from Notion query results, and how many rows were skipped for missing Kcal or Eaten"""
    meals, skipped = [], 0
    for page in pages:
        props = page.get("properties", {})
        try:
            kcal = _number(props.get("Kcal"))
            eaten = _eaten(props.get("Eaten"), tz)
        except (ValueError, zoneinfo.ZoneInfoNotFoundError):
            kcal = eaten = None
        if kcal is None or eaten is None:
            skipped += 1
            continue
        meals.append(
            Meal(
                id=page["id"],
                product=_plain(props.get("Product")),
                eaten=eaten,
                meal=((props.get("Meal") or {}).get("select") or {}).get("name", ""),
                kcal=kcal,
                protein=_number(props.get("Protein")) or 0.0,
                fat=_number(props.get("Fat")) or 0.0,
                carbs=_number(props.get("Carbs")) or 0.0,
                water=_number(props.get("Water")) or 0.0,
            )
        )
    return meals, skipped


def parse_day_flags(pages):
    """The Full checkbox of each day that has a row in Nutrition days"""
    flags = {}
    for page in pages:
        props = page.get("properties", {})
        try:
            day = dt.date.fromisoformat(_plain(props.get("Date")).strip())
        except ValueError:
            continue
        flags[day] = bool((props.get("Full") or {}).get("checkbox"))
    return flags


def meal_day(moment, tz):
    return (moment.astimezone(tz) - DAY_START).date()


def daily_totals(meals, day_flags, tz):
    """Every day is drawn on the charts, today too, unless its Full box in Notion is unticked"""
    sums = {}
    for meal in meals:
        day = meal_day(meal.eaten, tz)
        total = sums.setdefault(day, [0.0, 0.0, 0.0, 0.0, 0.0])
        for i, value in enumerate((meal.kcal, meal.fat, meal.protein, meal.carbs, meal.water)):
            total[i] += value
    return [
        DayTotal(day, *(round(value, 1) for value in sums[day][:4]), full=day_flags.get(day, True), water=round(sums[day][4]))
        for day in sorted(sums)
    ]


def _number_text(value):
    return f"{round(value, 1):g}"


def to_csv(days):
    lines = ["date,calories,fats,proteins,carbohydrates,full?,water"]
    for d in days:
        numbers = ",".join(_number_text(v) for v in (d.kcal, d.fat, d.protein, d.carbs))
        lines.append(f"{d.day.isoformat()},{numbers},{'TRUE' if d.full else 'FALSE'},{_number_text(d.water)}")
    return "\n".join(lines) + "\n"


def meals_csv(meals, tz):
    """Every meal with its time and its day (after the 04:00 rule), for the Day charts. Same numbers as daily.csv"""
    lines = ["time,calories,fats,proteins,carbohydrates,day,water"]
    for meal in sorted(meals, key=lambda m: m.eaten):
        numbers = ",".join(_number_text(v) for v in (meal.kcal, meal.fat, meal.protein, meal.carbs))
        lines.append(f"{meal.eaten.astimezone(tz).isoformat()},{numbers},{meal_day(meal.eaten, tz).isoformat()},{_number_text(meal.water)}")
    return "\n".join(lines) + "\n"


def water_only(meal):
    return meal.water > 0 and meal.kcal == 0


def meal_hash(meal):
    fields = [meal.product, meal.eaten.isoformat(), meal.meal, meal.kcal, meal.protein, meal.fat, meal.carbs]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def plan_google(meals, synced, notion_ok):
    """What to create and delete in Google so it matches Notion. An edited meal is deleted and created again"""
    if not notion_ok:
        return GooglePlan(create=[], delete=[], skipped_mass_delete=False)
    # Water isn't food, Google would show it as an empty meal
    meals = [meal for meal in meals if not water_only(meal)]
    by_id = {meal.id: meal for meal in meals}
    create, delete = [], []
    for meal in meals:
        known = synced.get(meal.id)
        if known is not None and "name" not in known:
            # Google is still making it; changing it now could leave a duplicate
            continue
        if known is None:
            create.append(meal)
        elif known["hash"] != meal_hash(meal):
            delete.append(known["name"])
            create.append(meal)
    gone = [known["name"] for page_id, known in synced.items() if page_id not in by_id and "name" in known]
    if not meals and len(gone) > MASS_DELETE_LIMIT:
        return GooglePlan(create=create, delete=delete, skipped_mass_delete=True)
    return GooglePlan(create=create, delete=delete + gone, skipped_mass_delete=False)


# Rows the voice assistant writes (nutrition_sync.py /log). The limits catch a misheard or made-up number
DAY_LIMIT = 40
DELETE_LIMIT = 5
DELETE_WINDOW = dt.timedelta(hours=1)
UPDATE_LIMIT = 20
LIMITS = {"kcal": 3000, "protein": 300, "fat": 300, "carbs": 300, "water": 2000}
NUMBER_PROPS = {"kcal": "Kcal", "protein": "Protein", "fat": "Fat", "carbs": "Carbs", "water": "Water"}


class LogError(ValueError):
    """A reason the assistant can say back"""


@dataclasses.dataclass(frozen=True)
class LogEntry:
    product: str
    kcal: float
    protein: float
    fat: float
    carbs: float
    water: float
    meal: str
    amount: str
    eaten: dt.datetime
    note: str


def _log_number(body, key):
    value = body.get(key)
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LogError(f"{key} must be a number") from None
    if not 0 <= number <= LIMITS[key]:
        raise LogError(f"{key} must be 0 to {LIMITS[key]}")
    return number


def _log_text(body, key, longest, required=False):
    text = str(body.get(key) or "").strip()
    if required and not text:
        raise LogError(f"{key} is required")
    if len(text) > longest:
        raise LogError(f"{key} is longer than {longest} characters")
    return text


def _log_meal(body):
    meal = _log_text(body, "meal", 20)
    if meal and meal not in MEAL_TYPES:
        raise LogError(f"meal must be one of {', '.join(MEAL_TYPES)} or empty")
    return meal


def _log_eaten(body, now, tz):
    if not body.get("eaten"):
        return now
    try:
        eaten = dt.datetime.fromisoformat(str(body["eaten"]))
    except ValueError:
        raise LogError("eaten must be an ISO time") from None
    if eaten.tzinfo is None:
        eaten = eaten.replace(tzinfo=tz)
    if not now - dt.timedelta(hours=24) <= eaten <= now + dt.timedelta(minutes=5):
        raise LogError("eaten must be within the last 24 hours")
    return eaten


def parse_log_request(body, now, tz):
    numbers = {key: _log_number(body, key) for key in NUMBER_PROPS}
    if numbers["kcal"] == 0 and numbers["water"] == 0:
        raise LogError("kcal or water is required")
    return LogEntry(
        product=_log_text(body, "product", 100, required=True),
        meal=_log_meal(body),
        amount=_log_text(body, "amount", 50),
        eaten=_log_eaten(body, now, tz),
        note=_log_text(body, "note", 200),
        **numbers,
    )


def _rich(text):
    return [{"text": {"content": text}}] if text else []


def notion_properties(entry, tz):
    props = {
        "Product": {"title": _rich(entry.product)},
        "Eaten": {"date": {"start": entry.eaten.astimezone(tz).isoformat()}},
        "Amount": {"rich_text": _rich(entry.amount)},
        "Comment": {"rich_text": _rich("голосом, оценка" + (f"; {entry.note}" if entry.note else ""))},
    }
    for key, name in NUMBER_PROPS.items():
        props[name] = {"number": round(getattr(entry, key), 1)}
    if entry.meal:
        props["Meal"] = {"select": {"name": entry.meal}}
    return props


def log_update_properties(body, now, tz):
    """Only the fields given, with the same limits as a new row"""
    props = {}
    for key, name in NUMBER_PROPS.items():
        if key in body:
            props[name] = {"number": round(_log_number(body, key), 1)}
    if "product" in body:
        props["Product"] = {"title": _rich(_log_text(body, "product", 100, required=True))}
    if "amount" in body:
        props["Amount"] = {"rich_text": _rich(_log_text(body, "amount", 50))}
    if "meal" in body:
        meal = _log_meal(body)
        props["Meal"] = {"select": {"name": meal} if meal else None}
    if body.get("eaten"):
        props["Eaten"] = {"date": {"start": _log_eaten(body, now, tz).astimezone(tz).isoformat()}}
    if not props:
        raise LogError("nothing to change")
    return props


def delete_refusal(ids, known_ids, recent_deletes, now):
    """Why a removal is refused, or None. One row per call and a few per hour, so "remove everything" can't happen"""
    if len(ids) != 1:
        return "one entry per call"
    if ids[0] not in known_ids:
        return "unknown entry"
    if sum(1 for moment in recent_deletes if now - moment < DELETE_WINDOW) >= DELETE_LIMIT:
        return f"too many removals, at most {DELETE_LIMIT} per hour"
    return None
