"""Remove finished torrents and delete watched titles 48 h after they finished.

1. A torrent that finished and was imported by Radarr/Sonarr is removed with its
   download copy. The library copy is a hardlink, so the title stays. Watched
   marks and resume positions move from the download copy to the library copy.
2. A watched movie or episode is deleted 48 h after it was last played.
3. Unwatched titles are never deleted. After 5 days they're reported as stale.

Decisions live in plan(), a pure function. Everything else is thin I/O around
the qBittorrent, Jellyfin, Radarr, Sonarr and Home Assistant APIs. When in
doubt (missing data, API error, someone is watching) nothing is removed.
"""

import argparse
import glob
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    complete: bool
    files: list


@dataclass
class LibFile:
    app: str  # radarr | sonarr
    path: str
    inode: int
    added: float  # unix time of the import
    title: str
    ref: object  # radarr: movie id; sonarr: (series id, episode file id)


@dataclass
class Item:
    user: str
    id: str
    path: str
    inode: int
    played: bool
    position: int  # resume position in ticks
    last_played: float = None  # unix time, None if Jellyfin has no date


@dataclass
class State:
    torrents: list
    library: list
    items: list  # Jellyfin movies and episodes, one per user
    playing_ids: set  # Jellyfin item ids someone is watching now
    queued_hashes: set  # upper-case torrent hashes still in the Radarr/Sonarr queue


@dataclass
class Plan:
    remove_torrents: list = field(default_factory=list)  # hashes, removed with the download copy
    user_data: list = field(default_factory=list)  # (user, item id, played, position, last played) for library copies
    delete: list = field(default_factory=list)  # LibFile
    stale: list = field(default_factory=list)  # titles of unwatched old downloads
    kept: list = field(default_factory=list)  # (path, reason)


def plan(s, now, watched_after_h=48, stale_after_d=5):
    p = Plan()
    lib_inodes = {f.inode for f in s.library}
    playing = {i.inode for i in s.items if i.id in s.playing_ids}
    last_played = {}  # inode → latest play by anyone who watched it, None when no date
    for i in s.items:
        if i.played:
            prev = last_played.get(i.inode)
            last_played[i.inode] = i.last_played if prev is None else max(prev, i.last_played or prev)
    torrent_inodes = set()

    for t in s.torrents:
        if t.category not in CATEGORIES:
            continue
        torrent_inodes.update(f.inode for f in t.files)
        imported = [f for f in t.files if f.is_video and f.inode in lib_inodes]
        if not t.complete or not imported or t.hash.upper() in s.queued_hashes:
            continue
        if any(f.inode in playing for f in t.files):
            p.kept.append((t.name, "torrent is playing now"))
            continue
        moves, ready = [], True
        for f in imported:
            lib_items = {i.user: i for i in s.items if i.inode == f.inode and i.path != f.path}
            if not lib_items:
                ready = False  # Jellyfin hasn't seen the library copy yet
                break
            for d in (i for i in s.items if i.path == f.path and (i.played or i.position)):
                lib = lib_items.get(d.user)
                if lib is None:
                    ready = False
                elif not lib.played and not lib.position:
                    moves.append((d.user, lib.id, d.played, d.position, d.last_played))
        if not ready:
            p.kept.append((t.name, "library copy not in Jellyfin yet"))
            continue
        p.user_data.extend(moves)
        p.remove_torrents.append(t.hash)

    stale = []
    for f in s.library:
        if f.inode not in last_played:
            if now - f.added > stale_after_d * 86400 and f.title not in stale:
                stale.append(f.title)
            continue
        if now - (last_played[f.inode] or f.added) < watched_after_h * 3600:
            p.kept.append((f.path, f"watched less than {watched_after_h}h ago"))
        elif f.inode in playing:
            p.kept.append((f.path, "watched, but playing now"))
        elif f.inode in torrent_inodes:
            p.kept.append((f.path, "watched, waiting for its torrent to be removed"))
        else:
            p.delete.append(f)
    p.stale = stale
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


def timestamp(iso):
    return datetime.fromisoformat(iso).timestamp()


def qbit_opener(cfg):
    q = cfg["qbittorrent"]
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    http("POST", q["url"] + "/api/v2/auth/login",
         form={"username": q["username"], "password": read(q["password_file"])}, opener=opener)
    return opener


def arr(cfg, name, method, path, body=None):
    a = cfg[name]
    return http(method, a["url"] + path, {"X-Api-Key": read(a["key_file"])}, body)


def jellyfin(cfg, method, path, body=None):
    j = cfg["jellyfin"]
    return http(method, j["url"] + path, {"X-Emby-Token": read(j["key_file"])}, body)


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
            out.append(Torrent(t["hash"], t["name"], t["category"], (t.get("completion_on") or -1) > 0, files))
    return out


def collect_library(cfg):
    out = []
    for movie in arr(cfg, "radarr", "GET", "/movie") or []:
        mf = movie.get("movieFile")
        if mf and (ino := inode(mf["path"])) is not None:
            out.append(LibFile("radarr", mf["path"], ino, timestamp(mf["dateAdded"]), movie["title"], movie["id"]))
    for series in arr(cfg, "sonarr", "GET", "/series") or []:
        for ef in arr(cfg, "sonarr", "GET", f"/episodefile?seriesId={series['id']}") or []:
            if (ino := inode(ef["path"])) is not None:
                out.append(LibFile("sonarr", ef["path"], ino, timestamp(ef["dateAdded"]), series["title"],
                                   (series["id"], ef["id"])))
    return out


def collect_items(cfg):
    out = []
    for user in jellyfin(cfg, "GET", "/Users"):
        query = "Recursive=true&Fields=Path&IncludeItemTypes=Movie,Episode"
        for it in jellyfin(cfg, "GET", f"/Users/{user['Id']}/Items?{query}")["Items"]:
            ino = inode(it.get("Path") or "")
            if ino is not None:
                ud = it.get("UserData") or {}
                out.append(Item(user["Id"], it["Id"], it["Path"], ino, bool(ud.get("Played")),
                                int(ud.get("PlaybackPositionTicks") or 0),
                                timestamp(ud["LastPlayedDate"]) if ud.get("LastPlayedDate") else None))
    return out


def collect(cfg):
    return State(
        torrents=collect_torrents(cfg),
        library=collect_library(cfg),
        items=collect_items(cfg),
        playing_ids={s["NowPlayingItem"]["Id"] for s in jellyfin(cfg, "GET", "/Sessions") or []
                     if s.get("NowPlayingItem")},
        queued_hashes={r["downloadId"].upper() for name in CATEGORIES
                       for r in (arr(cfg, name, "GET", "/queue?pageSize=1000") or {}).get("records", [])
                       if r.get("downloadId")},
    )


def remove_subtitles(video_path, dry_run):
    stem = os.path.splitext(video_path)[0]
    for sub in glob.glob(glob.escape(stem) + ".*"):
        if sub.lower().endswith(SUBTITLES):
            log(f"{'would delete' if dry_run else 'deleting'} subtitle {sub}")
            if not dry_run:
                os.remove(sub)


def apply(cfg, p, dry_run):
    verb = "would" if dry_run else "will"
    # marks first: if this fails, the torrent stays and the next run tries again
    for user, item_id, played, position, last in p.user_data:
        log(f"{verb} copy watched state to Jellyfin item {item_id} (played={played}, position={position})")
        if not dry_run:
            body = {"Played": played, "PlaybackPositionTicks": position}
            if last is not None:
                body["LastPlayedDate"] = datetime.fromtimestamp(last, timezone.utc).isoformat()
            jellyfin(cfg, "POST", f"/UserItems/{item_id}/UserData?userId={user}", body)
    if p.remove_torrents:
        log(f"{verb} remove torrents {', '.join(p.remove_torrents)} with their download copies")
        if not dry_run:
            q = cfg["qbittorrent"]
            http("POST", q["url"] + "/api/v2/torrents/delete", opener=qbit_opener(cfg),
                 form={"hashes": "|".join(p.remove_torrents), "deleteFiles": "true"})
    for f in p.delete:
        log(f"{verb} delete {f.app} {f.title} ({f.path})")
        remove_subtitles(f.path, dry_run)
        if dry_run:
            continue
        if f.app == "radarr":
            arr(cfg, "radarr", "DELETE", f"/movie/{f.ref}?deleteFiles=true&addImportExclusion=false")
        else:
            series_id, file_id = f.ref
            eps = [e["id"] for e in arr(cfg, "sonarr", "GET", f"/episode?seriesId={series_id}")
                   if e.get("episodeFileId") == file_id]
            arr(cfg, "sonarr", "PUT", "/episode/monitor", {"episodeIds": eps, "monitored": False})
            arr(cfg, "sonarr", "DELETE", f"/episodefile/{file_id}")


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
        state = collect(cfg)
    except Exception as e:
        log(f"could not collect state, deleting nothing: {e}")
        return
    p = plan(state, now, cfg.get("watched_after_h", 48), cfg.get("stale_after_d", 5))
    log(f"{'dry run: ' if dry_run else ''}{len(p.remove_torrents)} torrents to remove, "
        f"{len(p.delete)} files to delete, {len(p.stale)} stale, {len(p.kept)} kept")
    for path, reason in p.kept:
        log(f"keeping {path}: {reason}")
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
