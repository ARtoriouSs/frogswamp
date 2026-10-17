"""Delete watched downloads 48 h after they finished; report unwatched ones.

Decisions live in plan(), a pure function. Everything else is thin I/O around
the qBittorrent, Jellyfin, Radarr, Sonarr and Home Assistant APIs. When in
doubt (missing data, API error) nothing is deleted.
"""

import argparse
import glob
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar

CATEGORIES = ("radarr", "sonarr")
VIDEO = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".webm", ".mov")
SUBTITLES = (".srt", ".ass", ".ssa", ".sub", ".vtt", ".idx")


@dataclass
class File:
    path: str
    inode: int
    is_video: bool


@dataclass
class Torrent:
    hash: str
    name: str
    category: str
    completion_on: float  # unix time, <= 0 while still downloading
    files: list


@dataclass
class Plan:
    delete_files: list = field(default_factory=list)  # (category, path)
    remove_torrents: list = field(default_factory=list)  # torrent hashes
    stale: list = field(default_factory=list)  # names of unwatched old downloads
    kept: list = field(default_factory=list)  # (path, reason)


def plan(torrents, played_inodes, now, watched_after_h=48, stale_after_d=5):
    p = Plan()
    for t in torrents:
        if t.category not in CATEGORIES or t.completion_on <= 0:
            continue
        age = now - t.completion_on
        videos = [f for f in t.files if f.is_video]
        watched = [f for f in videos if f.inode in played_inodes]
        unwatched = [f for f in videos if f.inode not in played_inodes]
        if unwatched and age > stale_after_d * 86400:
            p.stale.append(t.name)
        for f in unwatched:
            p.kept.append((f.path, "not watched"))
        if not watched:
            continue
        if age < watched_after_h * 3600:
            p.kept.extend((f.path, f"watched, finished less than {watched_after_h}h ago") for f in watched)
            continue
        p.delete_files.extend((t.category, f.path) for f in watched)
        if not unwatched:
            p.remove_torrents.append(t.hash)
    return p


# ---------- I/O ----------

def log(msg):
    print(msg, flush=True)


def read(path):
    with open(path) as f:
        return f.read().strip()


def http(method, url, headers=None, body=None, form=None, opener=None):
    data, headers = None, dict(headers or {})
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with (opener or urllib.request.build_opener()).open(req, timeout=60) as resp:
        raw = resp.read()
    return json.loads(raw) if raw.strip() else None


def inode(path):
    try:
        return os.stat(path).st_ino
    except OSError:
        return None


def qbit_opener(cfg):
    q = cfg["qbittorrent"]
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    http("POST", q["url"] + "/api/v2/auth/login",
         form={"username": q["username"], "password": read(q["password_file"])}, opener=opener)
    return opener


def collect_torrents(cfg):
    q, opener, out = cfg["qbittorrent"], qbit_opener(cfg), []
    for cat in CATEGORIES:
        for t in http("GET", f"{q['url']}/api/v2/torrents/info?category={cat}", opener=opener) or []:
            files = []
            for f in http("GET", f"{q['url']}/api/v2/torrents/files?hash={t['hash']}", opener=opener) or []:
                path = os.path.join(t["save_path"], f["name"])
                ino = inode(path)
                if ino is not None:
                    files.append(File(path, ino, path.lower().endswith(VIDEO)))
            out.append(Torrent(t["hash"], t["name"], t["category"], t.get("completion_on") or -1, files))
    return out


def collect_played_inodes(cfg):
    j = cfg["jellyfin"]
    headers = {"X-Emby-Token": read(j["key_file"])}
    played = set()
    for user in http("GET", j["url"] + "/Users", headers):
        query = "Recursive=true&IsPlayed=true&Fields=Path&IncludeItemTypes=Movie,Episode"
        for item in http("GET", f"{j['url']}/Users/{user['Id']}/Items?{query}", headers)["Items"]:
            ino = inode(item.get("Path") or "")
            if ino is not None:
                played.add(ino)
    return played


def arr(cfg, name, method, path, body=None):
    a = cfg[name]
    return http(method, a["url"] + path, {"X-Api-Key": read(a["key_file"])}, body)


def remove_subtitles(video_path, dry_run):
    stem = os.path.splitext(video_path)[0]
    for sub in glob.glob(glob.escape(stem) + ".*"):
        if sub.lower().endswith(SUBTITLES):
            log(f"{'would delete' if dry_run else 'deleting'} subtitle {sub}")
            if not dry_run:
                os.remove(sub)


def apply(cfg, p, dry_run):
    verb = "would delete" if dry_run else "deleting"
    targets = {ino for _, path in p.delete_files if (ino := inode(path)) is not None}
    if any(c == "radarr" for c, _ in p.delete_files):
        for movie in arr(cfg, "radarr", "GET", "/movie") or []:
            mf = movie.get("movieFile")
            if mf and inode(mf["path"]) in targets:
                log(f"{verb} movie {movie['title']} ({mf['path']})")
                remove_subtitles(mf["path"], dry_run)
                if not dry_run:
                    arr(cfg, "radarr", "DELETE", f"/movie/{movie['id']}?deleteFiles=true&addImportExclusion=false")
    if any(c == "sonarr" for c, _ in p.delete_files):
        for series in arr(cfg, "sonarr", "GET", "/series") or []:
            for ef in arr(cfg, "sonarr", "GET", f"/episodefile?seriesId={series['id']}") or []:
                if inode(ef["path"]) not in targets:
                    continue
                log(f"{verb} episode file {ef['path']}")
                remove_subtitles(ef["path"], dry_run)
                if not dry_run:
                    eps = [e["id"] for e in arr(cfg, "sonarr", "GET", f"/episode?seriesId={series['id']}")
                           if e.get("episodeFileId") == ef["id"]]
                    arr(cfg, "sonarr", "PUT", "/episode/monitor", {"episodeIds": eps, "monitored": False})
                    arr(cfg, "sonarr", "DELETE", f"/episodefile/{ef['id']}")
    for _, path in p.delete_files:
        remove_subtitles(path, dry_run)
    if p.remove_torrents:
        log(f"{verb} torrents {', '.join(p.remove_torrents)} with their files")
        if not dry_run:
            q = cfg["qbittorrent"]
            http("POST", q["url"] + "/api/v2/torrents/delete", opener=qbit_opener(cfg),
                 form={"hashes": "|".join(p.remove_torrents), "deleteFiles": "true"})


def publish_stale(cfg, p):
    ha = cfg["home_assistant"]
    http("POST", ha["url"] + "/api/states/sensor.stale_downloads",
         {"Authorization": "Bearer " + read(ha["token_file"])},
         {"state": len(p.stale), "attributes": {"titles": p.stale, "friendly_name": "Stale downloads",
                                                 "icon": "mdi:download-off", "unit_of_measurement": "downloads"}})


def enforce_one_season(cfg, dry_run):
    for series in arr(cfg, "sonarr", "GET", "/series") or []:
        if series.get("monitorNewItems", "none") != "none":
            log(f"{'would set' if dry_run else 'setting'} monitor new seasons = none for {series['title']}")
            if not dry_run:
                series["monitorNewItems"] = "none"
                arr(cfg, "sonarr", "PUT", f"/series/{series['id']}", series)


def run(cfg, now, dry_run):
    try:
        torrents = collect_torrents(cfg)
        played = collect_played_inodes(cfg)
    except Exception as e:
        log(f"could not collect state, deleting nothing: {e}")
        return
    p = plan(torrents, played, now, cfg.get("watched_after_h", 48), cfg.get("stale_after_d", 5))
    log(f"{'dry run: ' if dry_run else ''}{len(p.delete_files)} files to delete, "
        f"{len(p.remove_torrents)} torrents to remove, {len(p.stale)} stale, {len(p.kept)} kept")
    for step, fn, args in (("apply", apply, (cfg, p, dry_run)), ("stale sensor", publish_stale, (cfg, p)),
                           ("one season", enforce_one_season, (cfg, dry_run))):
        try:
            fn(*args)
        except Exception as e:
            log(f"FAILED {step}: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(json.load(open(args.config)), time.time(), args.dry_run)


if __name__ == "__main__":
    main()
