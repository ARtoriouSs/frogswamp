from unittest import mock

import janitor
from janitor import File, Torrent

NOW = 1_000_000_000.0
H = 3600


def torrent(age_h, files, category="radarr", hash="h1", name="Title"):
    return Torrent(hash=hash, name=name, category=category, completion_on=NOW - age_h * H, files=files)


def video(path, inode):
    return File(path=path, inode=inode, is_video=True)


def test_unwatched_never_deleted():
    p = janitor.plan([torrent(100, [video("/d/m.mkv", 1)])], played_inodes=set(), now=NOW)
    assert p.delete_files == [] and p.remove_torrents == []


def test_watched_recent_kept():
    p = janitor.plan([torrent(47, [video("/d/m.mkv", 1)])], played_inodes={1}, now=NOW)
    assert p.delete_files == [] and any("48h" in reason for _, reason in p.kept)


def test_watched_old_movie_deleted():
    p = janitor.plan([torrent(49, [video("/d/m.mkv", 1)])], played_inodes={1}, now=NOW)
    assert p.delete_files == [("radarr", "/d/m.mkv")] and p.remove_torrents == ["h1"]


def test_played_only_in_downloading_item_counts():
    # the played inode was recorded on the download path; the library hardlink shares it
    p = janitor.plan([torrent(60, [video("/srv/media/downloads/radarr/m.mkv", 42)])], played_inodes={42}, now=NOW)
    assert p.delete_files == [("radarr", "/srv/media/downloads/radarr/m.mkv")]


def test_season_pack_partial():
    files = [video(f"/d/s/e{i}.mkv", i) for i in (1, 2, 3)]
    p = janitor.plan([torrent(60, files, category="sonarr")], played_inodes={1, 2}, now=NOW)
    assert p.delete_files == [("sonarr", "/d/s/e1.mkv"), ("sonarr", "/d/s/e2.mkv")]
    assert p.remove_torrents == []


def test_season_pack_complete():
    files = [video(f"/d/s/e{i}.mkv", i) for i in (1, 2, 3)]
    p = janitor.plan([torrent(60, files, category="sonarr")], played_inodes={1, 2, 3}, now=NOW)
    assert len(p.delete_files) == 3 and p.remove_torrents == ["h1"]


def test_incomplete_ignored():
    t = Torrent(hash="h1", name="T", category="radarr", completion_on=-1, files=[video("/d/m.mkv", 1)])
    p = janitor.plan([t], played_inodes={1}, now=NOW)
    assert p.delete_files == [] and p.remove_torrents == [] and p.stale == []


def test_stale_unwatched_5_days():
    assert janitor.plan([torrent(121, [video("/d/m.mkv", 1)])], set(), NOW).stale == ["Title"]
    assert janitor.plan([torrent(119, [video("/d/m.mkv", 1)])], set(), NOW).stale == []


def test_other_categories_ignored():
    p = janitor.plan([torrent(100, [video("/d/m.mkv", 1)], category="manual")], played_inodes={1}, now=NOW)
    assert p.delete_files == [] and p.stale == []


def test_collect_error_deletes_nothing():
    with mock.patch.object(janitor, "collect_torrents", return_value=[torrent(100, [video("/d/m.mkv", 1)])]), \
         mock.patch.object(janitor, "collect_played_inodes", side_effect=RuntimeError("jellyfin down")), \
         mock.patch.object(janitor, "apply") as apply:
        janitor.run({}, now=NOW, dry_run=False)
    apply.assert_not_called()


def test_dry_run_calls_no_delete():
    p = janitor.Plan(delete_files=[("radarr", "/d/m.mkv")], remove_torrents=["h1"], stale=[], kept=[])
    cfg = {"radarr": {"url": "http://r", "key_file": "k"}, "qbittorrent": {"url": "http://q"}}
    with mock.patch.object(janitor, "http", return_value=[]) as http, mock.patch.object(janitor, "read", return_value="x"):
        janitor.apply(cfg, p, dry_run=True)
    assert all(c.args[0] == "GET" for c in http.call_args_list)
