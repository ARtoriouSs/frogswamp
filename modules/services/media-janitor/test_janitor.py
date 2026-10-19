from unittest import mock

import janitor
from janitor import File, Item, LibFile, State, Torrent

NOW = 1_000_000_000.0
H = 3600
DL = "/srv/media/downloads/radarr/m.mkv"
LIB = "/srv/media/movies/M (2000)/m.mkv"


def video(path, inode):
    return File(path=path, inode=inode, is_video=True)


def torrent(files, hash="h1", category="radarr", done=True):
    return Torrent(hash=hash, name="Title", category=category, complete=done, files=files)


def movie(inode=1, age_h=1, path=LIB, ref=7, title="M"):
    return LibFile(app="radarr", path=path, inode=inode, added=NOW - age_h * H, title=title, ref=ref)


def item(path, inode, id=None, played=False, pos=0, user="u1"):
    return Item(user=user, id=id or path, path=path, inode=inode, played=played, position=pos)


def state(torrents=(), library=(), items=(), playing=(), queued=()):
    return State(torrents=list(torrents), library=list(library), items=list(items),
                 playing_ids=set(playing), queued_hashes=set(queued))


def imported(**kw):
    # a finished movie: download copy and library copy share inode 1, both seen by Jellyfin
    items = kw.pop("items", [item(DL, 1, id="dl"), item(LIB, 1, id="lib")])
    return state(torrents=[torrent([video(DL, 1)])], library=[movie(1)], items=items, **kw)


# ---------- releasing finished torrents ----------

def test_imported_torrent_released():
    p = janitor.plan(imported(), NOW)
    assert p.remove_torrents == ["h1"]


def test_downloading_torrent_kept():
    s = imported()
    s.torrents[0].complete = False
    assert janitor.plan(s, NOW).remove_torrents == []


def test_not_imported_torrent_kept():
    s = state(torrents=[torrent([video(DL, 1)])], items=[item(DL, 1)])
    assert janitor.plan(s, NOW).remove_torrents == []


def test_torrent_still_in_arr_queue_kept():
    # Radarr keeps a download in its queue until the whole import is done
    assert janitor.plan(imported(queued={"H1"}), NOW).remove_torrents == []


def test_torrent_playing_kept():
    p = janitor.plan(imported(playing={"dl"}), NOW)
    assert p.remove_torrents == [] and any("playing" in r for _, r in p.kept)


def test_library_copy_not_in_jellyfin_yet_kept():
    assert janitor.plan(imported(items=[item(DL, 1, id="dl")]), NOW).remove_torrents == []


def test_other_categories_ignored():
    s = imported()
    s.torrents[0].category = "manual"
    assert janitor.plan(s, NOW).remove_torrents == []


def test_unimported_sample_does_not_block_release():
    s = imported()
    s.torrents[0].files.append(video("/srv/media/downloads/radarr/sample.mkv", 9))
    assert janitor.plan(s, NOW).remove_torrents == ["h1"]


def test_watched_mark_moved_to_library_copy():
    s = imported(items=[item(DL, 1, id="dl", played=True), item(LIB, 1, id="lib")])
    assert janitor.plan(s, NOW).user_data == [("u1", "lib", True, 0)]


def test_resume_position_moved_to_library_copy():
    s = imported(items=[item(DL, 1, id="dl", pos=500), item(LIB, 1, id="lib")])
    assert janitor.plan(s, NOW).user_data == [("u1", "lib", False, 500)]


def test_library_mark_not_overwritten():
    s = imported(items=[item(DL, 1, id="dl", pos=500), item(LIB, 1, id="lib", played=True)])
    assert janitor.plan(s, NOW).user_data == []


def test_marks_moved_per_user():
    items = [item(DL, 1, id="dl", played=True, user="a"), item(LIB, 1, id="lib", user="a"),
             item(DL, 1, id="dl", user="b"), item(LIB, 1, id="lib", user="b")]
    assert janitor.plan(imported(items=items), NOW).user_data == [("a", "lib", True, 0)]


# ---------- deleting watched titles ----------

def test_unwatched_never_deleted():
    s = state(library=[movie(1, age_h=500)], items=[item(LIB, 1)])
    assert janitor.plan(s, NOW).delete == []


def test_watched_recent_kept():
    s = state(library=[movie(1, age_h=47)], items=[item(LIB, 1, played=True)])
    p = janitor.plan(s, NOW)
    assert p.delete == [] and any("48h" in r for _, r in p.kept)


def test_watched_old_deleted():
    lf = movie(1, age_h=49)
    s = state(library=[lf], items=[item(LIB, 1, played=True)])
    assert janitor.plan(s, NOW).delete == [lf]


def test_watched_by_any_user_counts():
    lf = movie(1, age_h=49)
    s = state(library=[lf], items=[item(LIB, 1, user="a"), item(LIB, 1, played=True, user="b")])
    assert janitor.plan(s, NOW).delete == [lf]


def test_watched_in_download_copy_counts():
    # watched while downloading, torrent not released yet: the inode is shared
    lf = movie(1, age_h=49)
    s = state(library=[lf], items=[item(DL, 1, played=True)])
    assert janitor.plan(s, NOW).delete == [lf]


def test_watched_playing_kept():
    s = state(library=[movie(1, age_h=49)], items=[item(LIB, 1, id="lib", played=True)], playing={"lib"})
    assert janitor.plan(s, NOW).delete == []


def test_watched_with_torrent_left_kept():
    # the torrent goes first, so its files are never left behind
    s = imported(items=[item(DL, 1, id="dl", played=True), item(LIB, 1, id="lib")], playing={"dl"})
    s.library[0].added = NOW - 49 * H
    assert janitor.plan(s, NOW).delete == []


def test_season_partly_watched():
    eps = [LibFile("sonarr", f"/srv/media/shows/S/e{i}.mkv", i, NOW - 60 * H, "S", (3, i)) for i in (1, 2, 3)]
    items = [item(e.path, e.inode, played=e.inode != 3) for e in eps]
    assert janitor.plan(state(library=eps, items=items), NOW).delete == eps[:2]


# ---------- stale ----------

def test_stale_unwatched_5_days():
    s = state(library=[movie(1, age_h=121), movie(2, age_h=200, path="/x", ref=8)], items=[])
    assert janitor.plan(s, NOW).stale == ["M"]
    assert janitor.plan(state(library=[movie(1, age_h=119)]), NOW).stale == []


def test_stale_ignores_watched():
    s = state(library=[movie(1, age_h=121)], items=[item(LIB, 1, played=True)])
    assert janitor.plan(s, NOW).stale == []


# ---------- I/O safety ----------

def test_collect_error_deletes_nothing():
    with mock.patch.object(janitor, "collect", side_effect=RuntimeError("jellyfin down")), \
         mock.patch.object(janitor, "apply") as apply:
        janitor.run({}, now=NOW, dry_run=False)
    apply.assert_not_called()


def test_dry_run_changes_nothing():
    p = janitor.Plan(delete=[movie(1)], remove_torrents=["h1"], user_data=[("u1", "lib", True, 0)])
    cfg = {"radarr": {"url": "http://r", "key_file": "k"}, "qbittorrent": {"url": "http://q"},
           "jellyfin": {"url": "http://j", "key_file": "k"}}
    with mock.patch.object(janitor, "http", return_value=[]) as http, \
         mock.patch.object(janitor, "read", return_value="x"), \
         mock.patch.object(janitor, "qbit_opener") as qbit:
        janitor.apply(cfg, p, dry_run=True)
    assert all(c.args[0] == "GET" for c in http.call_args_list)
    qbit.assert_not_called()


def test_marks_moved_before_torrent_removed():
    p = janitor.Plan(remove_torrents=["h1"], user_data=[("u1", "lib", True, 0)])
    cfg = {"qbittorrent": {"url": "http://q"}, "jellyfin": {"url": "http://j", "key_file": "k"}}
    with mock.patch.object(janitor, "http", return_value=None) as http, \
         mock.patch.object(janitor, "read", return_value="x"), mock.patch.object(janitor, "qbit_opener"):
        janitor.apply(cfg, p, dry_run=False)
    urls = [c.args[1] for c in http.call_args_list]
    assert "UserData" in urls[0] and urls[1].endswith("/torrents/delete")
