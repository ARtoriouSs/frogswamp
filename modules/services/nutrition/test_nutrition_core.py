import datetime as dt
import zoneinfo

import nutrition_core as nc

TZ = zoneinfo.ZoneInfo("Europe/Warsaw")


def text(kind, value):
    return {"type": kind, kind: [{"type": "text", "plain_text": value}]}


def page(page_id="p1", product="Кофе", eaten="2026-11-14T12:59:00.000+01:00", meal="Перекус", kcal=35, protein=1.8, fat=1.6, carbs=2.4, time_zone=None, water=None):
    props = {
        "Product": text("title", product),
        "Eaten": {"type": "date", "date": None if eaten is None else {"start": eaten, "end": None, "time_zone": time_zone}},
        "Meal": {"type": "select", "select": None if meal is None else {"name": meal}},
        "Amount": text("rich_text", "250 (250 ml)"),
        "Kcal": {"type": "number", "number": kcal},
        "Protein": {"type": "number", "number": protein},
        "Fat": {"type": "number", "number": fat},
        "Carbs": {"type": "number", "number": carbs},
        "Comment": text("rich_text", "оценка"),
        "Water": {"type": "number", "number": water},
    }
    return {"object": "page", "id": page_id, "in_trash": False, "properties": props}


def day_page(date, full):
    return {"object": "page", "id": date, "properties": {"Date": text("title", date), "Full": {"type": "checkbox", "checkbox": full}}}


def meal(page_id="p1", kcal=35.0, eaten=dt.datetime(2026, 11, 14, 12, 59, tzinfo=TZ), **kwargs):
    values = dict(product="Кофе", meal="Перекус", protein=1.8, fat=1.6, carbs=2.4)
    values.update(kwargs)
    return nc.Meal(id=page_id, eaten=eaten, kcal=kcal, **values)


def test_parse_meal_full_row():
    meals, skipped = nc.parse_meals([page()], TZ)
    assert skipped == 0
    assert meals == [
        nc.Meal(
            id="p1",
            product="Кофе",
            eaten=dt.datetime(2026, 11, 14, 12, 59, tzinfo=dt.timezone(dt.timedelta(hours=1))),
            meal="Перекус",
            kcal=35.0,
            protein=1.8,
            fat=1.6,
            carbs=2.4,
        )
    ]
    assert meals[0].eaten.tzinfo is not None


def test_parse_skips_incomplete_rows():
    meals, skipped = nc.parse_meals([page("a", kcal=None), page("b", eaten=None), page("c", fat=None)], TZ)
    assert skipped == 2
    assert [m.id for m in meals] == ["c"]
    assert meals[0].fat == 0.0


def test_parse_naive_time_uses_given_zone():
    meals, _ = nc.parse_meals([page(eaten="2026-11-14T12:59:00.000", time_zone="Europe/Warsaw"), page("d", eaten="2026-11-14")], TZ)
    assert meals[0].eaten == dt.datetime(2026, 11, 14, 12, 59, tzinfo=TZ)
    # A date without time counts as noon, so it stays on its day
    assert meals[1].eaten == dt.datetime(2026, 11, 14, 12, 0, tzinfo=TZ)


def test_parse_day_flags():
    flags = nc.parse_day_flags([day_page("2026-11-13", True), day_page("2026-10-27", False), day_page("not a date", True)])
    assert flags == {dt.date(2026, 11, 13): True, dt.date(2026, 10, 27): False}


def test_days_are_drawn_unless_unticked():
    meals, _ = nc.parse_meals([page("a", eaten="2026-10-27T12:00:00+01:00"), page("b", eaten="2026-11-18T12:00:00+01:00"), page("c", eaten="2026-11-20T12:00:00+01:00")], TZ)
    days = nc.daily_totals(meals, {dt.date(2026, 10, 27): False}, TZ)
    assert [(d.day, d.full) for d in days] == [(dt.date(2026, 10, 27), False), (dt.date(2026, 11, 18), True), (dt.date(2026, 11, 20), True)]


def test_day_uses_local_time_zone():
    meals, _ = nc.parse_meals([page("a", eaten="2026-11-17T10:30:00+01:00"), page("b", eaten="2026-11-17T09:30:00Z")], TZ)
    days = nc.daily_totals(meals, {}, TZ)
    assert [d.day for d in days] == [dt.date(2026, 11, 17)]
    assert days[0].kcal == 70


def test_meals_before_four_count_for_previous_day():
    meals, _ = nc.parse_meals([page("a", eaten="2026-11-17T03:59:00+01:00"), page("b", eaten="2026-11-17T02:30:00Z"), page("c", eaten="2026-11-17T04:00:00+01:00")], TZ)
    days = nc.daily_totals(meals, {}, TZ)
    assert [(d.day, d.kcal) for d in days] == [(dt.date(2026, 11, 16), 70), (dt.date(2026, 11, 17), 35)]
    assert nc.meal_day(dt.datetime(2026, 11, 17, 3, 59, tzinfo=TZ), TZ) == dt.date(2026, 11, 16)


def test_daily_totals_matches_old_sheet():
    pages = [
        page("1", eaten="2026-11-13T19:17:00+01:00", kcal=225, protein=2, fat=0, carbs=20),
        page("2", eaten="2026-11-13T19:17:00+01:00", kcal=1320, protein=42, fat=85, carbs=96),
        page("3", eaten="2026-11-14T12:31:00+01:00", kcal=300, protein=19, fat=21, carbs=6),
        page("4", eaten="2026-11-14T12:31:00+01:00", kcal=1740, protein=62.6, fat=83.4, carbs=179.8),
    ]
    meals, _ = nc.parse_meals(pages, TZ)
    days = nc.daily_totals(meals, {}, TZ)
    assert [(d.day, d.kcal, d.full) for d in days] == [(dt.date(2026, 11, 13), 1545, True), (dt.date(2026, 11, 14), 2040, True)]
    assert (days[1].fat, days[1].protein, days[1].carbs) == (104.4, 81.6, 185.8)


def test_csv_format():
    days = [
        nc.DayTotal(day=dt.date(2026, 11, 13), kcal=1545.0, fat=85.0, protein=44.0, carbs=116.0, full=True),
        nc.DayTotal(day=dt.date(2026, 11, 14), kcal=2040.0, fat=104.4, protein=81.6, carbs=185.8, full=False),
    ]
    assert nc.to_csv(days) == (
        "date,calories,fats,proteins,carbohydrates,full?,water\n"
        "2026-11-13,1545,85,44,116,TRUE,0\n"
        "2026-11-14,2040,104.4,81.6,185.8,FALSE,0\n"
    )


def test_meals_csv_in_time_order_with_their_day():
    pages = [
        page("1", eaten="2026-11-15T02:00:00+01:00", kcal=100),
        page("2", eaten="2026-11-14T08:00:00+01:00", kcal=200, protein=10, fat=5, carbs=20),
        page("3", eaten="2026-11-14T13:00:00+01:00", kcal=300),
        page("4", eaten="2026-11-15T12:00:00+01:00", kcal=400),
    ]
    meals, _ = nc.parse_meals(pages, TZ)
    assert nc.meals_csv(meals, TZ).splitlines() == [
        "time,calories,fats,proteins,carbohydrates,day,water",
        "2026-11-14T08:00:00+01:00,200,5,10,20,2026-11-14,0",
        "2026-11-14T13:00:00+01:00,300,1.6,1.8,2.4,2026-11-14,0",
        "2026-11-15T02:00:00+01:00,100,1.6,1.8,2.4,2026-11-14,0",
        "2026-11-15T12:00:00+01:00,400,1.6,1.8,2.4,2026-11-15,0",
    ]


def test_csv_rounds_float_noise():
    days = [nc.DayTotal(day=dt.date(2026, 11, 13), kcal=0.1 + 0.2, fat=1.25, protein=0, carbs=2.04, full=False)]
    assert nc.to_csv(days).splitlines()[1] == "2026-11-13,0.3,1.2,0,2,FALSE,0"


def test_plan_new_meal_created():
    plan = nc.plan_google([meal()], {}, notion_ok=True)
    assert plan == nc.GooglePlan(create=[meal()], delete=[], skipped_mass_delete=False)


def test_plan_removed_meal_deleted():
    synced = {"gone": {"name": "users/me/x/1", "hash": "h"}}
    plan = nc.plan_google([meal()], synced, notion_ok=True)
    assert plan.delete == ["users/me/x/1"]


def test_plan_unchanged_meal_untouched():
    synced = {"p1": {"name": "users/me/x/1", "hash": nc.meal_hash(meal())}}
    plan = nc.plan_google([meal()], synced, notion_ok=True)
    assert plan == nc.GooglePlan(create=[], delete=[], skipped_mass_delete=False)


def test_plan_edit_is_delete_and_create():
    synced = {"p1": {"name": "users/me/x/1", "hash": nc.meal_hash(meal())}}
    edited = meal(kcal=50.0)
    plan = nc.plan_google([edited], synced, notion_ok=True)
    assert plan.delete == ["users/me/x/1"]
    assert plan.create == [edited]


def test_meal_hash_ignores_nothing_google_gets():
    base = nc.meal_hash(meal())
    for changed in [meal(kcal=1.0), meal(product="Чай"), meal(meal="Ужин"), meal(protein=9.0), meal(fat=9.0), meal(carbs=9.0), meal(eaten=dt.datetime(2026, 11, 14, 13, 0, tzinfo=TZ))]:
        assert nc.meal_hash(changed) != base


def test_plan_skips_mass_delete():
    synced = {str(i): {"name": f"users/me/x/{i}", "hash": "h"} for i in range(4)}
    plan = nc.plan_google([], synced, notion_ok=True)
    assert plan.delete == []
    assert plan.skipped_mass_delete
    assert nc.plan_google([meal()], synced, notion_ok=False) == nc.GooglePlan(create=[], delete=[], skipped_mass_delete=False)


def test_plan_allows_small_deletes_when_empty():
    synced = {str(i): {"name": f"users/me/x/{i}", "hash": "h"} for i in range(3)}
    plan = nc.plan_google([], synced, notion_ok=True)
    assert len(plan.delete) == 3
    assert not plan.skipped_mass_delete


def test_meal_types():
    assert nc.MEAL_TYPES == {"Завтрак": "BREAKFAST", "Обед": "LUNCH", "Ужин": "DINNER", "Перекус": "SNACK"}
    assert nc.google_meal_type("Полдник") == "MEAL_TYPE_UNSPECIFIED"


def test_plan_waits_for_unfinished_operation():
    synced = {"p1": {"operation": "operations/op1", "hash": "old"}}
    plan = nc.plan_google([meal(kcal=99.0)], synced, notion_ok=True)
    assert plan == nc.GooglePlan(create=[], delete=[], skipped_mass_delete=False)
    assert nc.plan_google([], synced, notion_ok=True).delete == []


def test_water_is_summed_per_day_and_written_to_csv():
    pages = [page("a", eaten="2026-11-14T08:00:00+01:00", water=250), page("b", product="Вода", eaten="2026-11-14T10:00:00+01:00", kcal=0, protein=0, fat=0, carbs=0, meal=None, water=500), page("c", eaten="2026-11-14T12:00:00+01:00")]
    meals, skipped = nc.parse_meals(pages, TZ)
    assert skipped == 0
    assert [m.water for m in meals] == [250.0, 500.0, 0.0]
    days = nc.daily_totals(meals, {}, TZ)
    assert days[0].water == 750
    assert nc.to_csv(days).splitlines() == ["date,calories,fats,proteins,carbohydrates,full?,water", "2026-11-14,70,3.2,3.6,4.8,TRUE,750"]
    assert nc.meals_csv(meals, TZ).splitlines()[0] == "time,calories,fats,proteins,carbohydrates,day,water"
    assert nc.meals_csv(meals, TZ).splitlines()[2] == "2026-11-14T10:00:00+01:00,0,0,0,0,2026-11-14,500"


def test_water_only_rows_are_not_sent_to_google():
    water = meal("w", kcal=0.0, protein=0.0, fat=0.0, carbs=0.0, product="Вода", water=500.0)
    coffee = meal("c", water=250.0)
    plan = nc.plan_google([water, coffee], {}, notion_ok=True)
    assert plan.create == [coffee]


def test_water_does_not_change_the_meal_hash():
    assert nc.meal_hash(meal(water=300.0)) == nc.meal_hash(meal())
