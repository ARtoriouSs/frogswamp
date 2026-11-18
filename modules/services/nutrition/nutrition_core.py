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


@dataclasses.dataclass(frozen=True)
class DayTotal:
    day: dt.date
    kcal: float
    fat: float
    protein: float
    carbs: float
    full: bool


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
        total = sums.setdefault(day, [0.0, 0.0, 0.0, 0.0])
        for i, value in enumerate((meal.kcal, meal.fat, meal.protein, meal.carbs)):
            total[i] += value
    return [
        DayTotal(day, *(round(value, 1) for value in sums[day]), full=day_flags.get(day, True))
        for day in sorted(sums)
    ]


def _number_text(value):
    return f"{round(value, 1):g}"


def to_csv(days):
    lines = ["date,calories,fats,proteins,carbohydrates,full?"]
    for d in days:
        numbers = ",".join(_number_text(v) for v in (d.kcal, d.fat, d.protein, d.carbs))
        lines.append(f"{d.day.isoformat()},{numbers},{'TRUE' if d.full else 'FALSE'}")
    return "\n".join(lines) + "\n"


def meals_csv(meals, tz):
    """Every meal with its time and its day (after the 04:00 rule), for the Day charts. Same numbers as daily.csv"""
    lines = ["time,calories,fats,proteins,carbohydrates,day"]
    for meal in sorted(meals, key=lambda m: m.eaten):
        numbers = ",".join(_number_text(v) for v in (meal.kcal, meal.fat, meal.protein, meal.carbs))
        lines.append(f"{meal.eaten.astimezone(tz).isoformat()},{numbers},{meal_day(meal.eaten, tz).isoformat()}")
    return "\n".join(lines) + "\n"


def meal_hash(meal):
    fields = [meal.product, meal.eaten.isoformat(), meal.meal, meal.kcal, meal.protein, meal.fat, meal.carbs]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def plan_google(meals, synced, notion_ok):
    """What to create and delete in Google so it matches Notion. An edited meal is deleted and created again"""
    if not notion_ok:
        return GooglePlan(create=[], delete=[], skipped_mass_delete=False)
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
