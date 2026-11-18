# Nutrition tracking

You send Claude a photo or a description of what you ate. Claude estimates the calories and macros and writes one Notion row per food item. The `nutrition-sync` service on the NUC reads Notion every 5 minutes and sends the meals on:

```
phone → Claude project → Notion "Nutrition log" ─┐
                         Notion "Nutrition days" ─┤ read-only, every 5 min
                                                  ▼
                                    nutrition-sync (NUC, outbound only)
                                     ├─ /local/nutrition/daily.csv → Wellness charts
                                     ├─ /local/nutrition/meals.csv → Wellness Day charts (today's meals with times)
                                     ├─ 127.0.0.1:8131/status      → HA sensors and alerts
                                     └─ Google Health nutrition log
```

Nothing on the NUC is open to the internet. Claude writes to Notion through the free Notion connector, so no paid Sheets integration is needed.

Files:
- `nutrition_core.py`: pure logic (Notion rows → meals → daily totals, CSV, what to change in Google)
- `nutrition_sync.py`: the service (Notion and Google calls, `/status`, state in `/var/lib/nutrition-sync/google.json`)
- `google_auth.py`: the one-time Google login, run on the laptop with `just nutrition-google-auth`
- `claude-project-instructions.md`: the text for the Claude project

## Setup from scratch

### 1. Notion

1. Make two databases on one page (now Personal → Health → Nutrition Log):
   - **Nutrition log**: `Product` (title), `Eaten` (date with time), `Meal` (select: `Завтрак`, `Обед`, `Ужин`, `Перекус`), `Amount` (text), `Kcal`, `Protein`, `Fat`, `Carbs` (numbers), `Comment` (text)
   - **Nutrition days**: `Date` (title, `YYYY-MM-DD`), `Full` (checkbox). Every day is drawn on the charts, today too; a row with `Full` unchecked keeps an incomplete day off them
2. Notion → Settings → Connections → Develop or manage integrations → **New integration**: name `frogswamp-nutrition`, type Internal, capabilities **Read content** only. Copy the token.
3. On the page with both databases: ••• → Connections → add `frogswamp-nutrition`.
4. Find each database's data source id: open the database → ••• → Manage data sources → ••• → Copy data source ID, or ask Claude with the Notion connector for the `collection://…` id (the part after `collection://`).

### 2. Google Cloud and the Google Health login

1. https://console.cloud.google.com → new project (ours is `frogswamp`).
2. Enable the API: https://console.developers.google.com/apis/library/health.googleapis.com (search in the Library often doesn't find it).
3. Google Auth Platform:
   - **Branding**: app name, support email, homepage `https://artoriouss.github.io/frogswamp/`, privacy policy `https://artoriouss.github.io/frogswamp/privacy.html` (both from `docs/` on master through GitHub Pages), authorized domain `artoriouss.github.io`.
   - **Data access**: add the scope `https://www.googleapis.com/auth/googlehealth.nutrition.writeonly`. It can only add, change and delete the entries this app made, and can't read anything.
   - **Audience**: External, then **Publish app**. "Verification required" can be ignored: an unverified app works for up to 100 users, after a "Google hasn't verified this app" screen (Advanced → Go to …). In Testing mode the login would expire every 7 days.
   - **Clients**: create a client of type **Desktop app**, named e.g. `nutrition-sync`. Leave "Use this client for an AI-powered agent" off.
4. On the laptop: `just nutrition-google-auth --test-write`. Enter the client id and secret, log in with the Google account whose food log should get the meals, click through the warning, Allow. It creates a 1 kcal "Spike test" entry, waits while you check the Fitbit / Google Health app, deletes it and prints the refresh token as the last line. Without `--test-write` it only prints the token.

### 3. Secrets

`just secrets`, under `nutrition:`:

| Key | Value |
|---|---|
| `notion_token` | the Notion integration token |
| `notion_meals_source` | data source id of Nutrition log |
| `notion_days_source` | data source id of Nutrition days |
| `google_client_id` | Desktop client id |
| `google_client_secret` | Desktop client secret |
| `google_refresh_token` | last line of `just nutrition-google-auth` |

Then `just deploy`. Check on the server: `curl -s 127.0.0.1:8131/status` shows a fresh `last_read`, `google.ok: true`, `pending: 0`. Without Google, set `frogswamp.nutrition.google.enable = false` and leave the Google keys out.

### 4. The Claude project

1. claude.ai → Projects → new project, e.g. "Calories".
2. Turn on the **Notion** connector for it (Settings → Connectors, then allow it in the project).
3. Paste `claude-project-instructions.md` as the project instructions. It names the two data sources, the exact property values, and how to correct, remove and mark a day incomplete.
4. Use it from the phone app. Example prompts:
   - *(photo)* "Обед"
   - "Кофе с молоком, 250 мл, в 9:30"
   - "Пицца была 3 куска, не 2" (Claude updates the same row, never adds a second one)
   - "Удали булочку"
   - "Вчера я не всё записал" (marks yesterday incomplete, so it stays off the charts)

A day runs from 04:00 to 04:00, so a snack at 01:00 counts for the evening before. Today shows on the charts as you log it; there is no "day done" message.

Rows can also be fixed by hand in the Notion app; the NUC picks them up within 5 minutes.

## How Google gets the meals

- Each Notion row becomes one Google nutrition-log entry (name, kcal, fat, carbs, protein, meal type, time with the Warsaw offset). `google.json` keeps which entry belongs to which row.
- A changed row: Google can't edit these entries, so the old one is deleted and a new one sent.
- A deleted row: its entry is deleted.
- If Notion can't be read, or returns nothing while more than 3 meals are synced, nothing in Google is deleted.
- If Google refuses one meal, the others still go and HA alerts with Google's error code.

## Daily goals and alerts

Google Health has no API for its nutrition goals, so the goals live in HA: the "Daily goals" card on the Wellness tab (`input_number.nutrition_goal_*`, 0 = no goal). The cards above it show today against each goal with a bar. Over 115 % HA sends "over the goal"; over 150 % it says something is most likely logged wrong. Each fires once when the value crosses the line, and the day starts at 04:00.

## When something breaks

| HA alert | Cause | Fix |
|---|---|---|
| Nutrition sync is old | Notion not read for 1 h: token removed, integration disconnected from the page, or the service is down | `just ssh journalctl -u nutrition-sync` |
| Google Health sync, "login is gone" | Refresh token revoked or expired | `just nutrition-google-auth`, put the new token in `just secrets`, `just deploy` |
| Google Health sync, "refused a meal" | Google rejected one row (e.g. a strange value) | Find the page id in the journal, fix the row in Notion |
