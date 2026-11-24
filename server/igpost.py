#!/usr/bin/env python3
"""Instagram自動投稿（予定表方式：カルーセル＋リール、1日複数回）

GitHub（act-thrive/ig-content）の manifest.json に書かれた予定のうち、
時刻が来たものを投稿します。cronで1時間ごとに実行してください。

使い方:
  python3 igpost.py                  時刻が来た投稿を行う（cron用）
  python3 igpost.py --list           これからの予定を表示（投稿しない）
  python3 igpost.py --dry-run ID     指定IDを投稿直前まで確認（投稿しない）
  python3 igpost.py --now ID         指定IDを今すぐ投稿
"""
import configparser, datetime, json, os, shutil, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://graph.facebook.com/v25.0"
JST = datetime.timezone(datetime.timedelta(hours=9))
MAX_LATE_HOURS = 12      # 予定時刻からこれ以上遅れたものは投稿しない
MAX_PER_RUN = 2          # 1回の実行で投稿する最大数
KEEP_DAYS = 14           # 投稿済み素材をサーバーに残す日数


def log(msg):
    line = f"[{datetime.datetime.now(JST):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(os.path.join(HERE, "igpost.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_cfg():
    cp = configparser.ConfigParser()
    cp.read(os.path.join(HERE, "config.ini"), encoding="utf-8")
    return cp["instagram"]


def call(method, path, token, **params):
    params["access_token"] = token
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    req = urllib.request.Request(url + "?" + data.decode()) if method == "GET" else urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"APIエラー {e.code}: {e.read().decode(errors='replace')}") from None


def notify(cfg, text):
    tok, uid = cfg.get("line_token", "").strip(), cfg.get("line_user_id", "").strip()
    if not tok or "ここに" in tok or not uid:
        return
    body = json.dumps({"to": uid, "messages": [{"type": "text", "text": text[:4900]}]}).encode()
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        log(f"LINE通知に失敗しました: {e}")


def short_error(e):
    msg = str(e)
    try:
        err = json.loads(msg.split(": ", 1)[1])["error"]
        return err.get("error_user_msg") or err.get("error_user_title") or err.get("message") or msg
    except Exception:
        return msg[:300]


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "igpost/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def manifest_url(cfg):
    return cfg.get("manifest_url", "https://raw.githubusercontent.com/act-thrive/ig-content/main/manifest.json").strip()


def load_manifest(cfg):
    url = manifest_url(cfg)
    data = fetch(url + ("&" if "?" in url else "?") + "t=" + str(int(time.time())))
    return json.loads(data.decode("utf-8"))["items"]


def media_paths(cfg):
    d = cfg.get("media_dir", os.path.join(os.path.dirname(HERE), "public_html", "ig-media")).strip()
    base = cfg.get("media_base_url", "https://ai-work-note.com/ig-media").strip().rstrip("/")
    return d, base


def stage_files(cfg, item):
    """GitHubから素材を取得し、サーバーの公開フォルダに置いて公開URLを返す"""
    raw_base = manifest_url(cfg).rsplit("/", 1)[0]
    mdir, mbase = media_paths(cfg)
    dest = os.path.join(mdir, item["id"])
    os.makedirs(dest, exist_ok=True)
    urls = {}
    for rel in item["files"]:
        name = os.path.basename(rel)
        path = os.path.join(dest, name)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            data = fetch(f"{raw_base}/{urllib.parse.quote(rel)}", timeout=300)
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
        urls[name] = f"{mbase}/{urllib.parse.quote(item['id'])}/{urllib.parse.quote(name)}"
    # 公開URLで取得できるか確認
    for name, u in urls.items():
        req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": "igpost/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            ctype = r.headers.get("Content-Type", "")
            if r.status != 200 or not (ctype.startswith("image/") or ctype.startswith("video/")):
                raise RuntimeError(f"素材の公開URLが正しくありません: {u}（{r.status} {ctype}）")
    return [urls[os.path.basename(rel)] for rel in item["files"]]


def wait_ready(cid, token, tries=60, sleep=10):
    for _ in range(tries):
        res = call("GET", cid, token, fields="status_code,status")
        st = res.get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"メディアの準備に失敗しました（{st} {res.get('status', '')}）")
        time.sleep(sleep)
    raise RuntimeError("メディアの準備がタイムアウトしました")


def publish(cfg, item, dry=False):
    token, ig = cfg["token"], cfg["ig_user_id"]
    urls = stage_files(cfg, item)
    if item["type"] == "reel":
        video = next(u for u in urls if u.endswith(".mp4"))
        cover = next((u for u in urls if u.endswith(".jpg")), None)
        params = dict(media_type="REELS", video_url=video, caption=item["caption"], share_to_feed="true")
        if cover:
            params["cover_url"] = cover
        cid = call("POST", f"{ig}/media", token, **params)["id"]
        wait_ready(cid, token)
    else:
        children = []
        for u in urls:
            children.append(call("POST", f"{ig}/media", token, image_url=u, is_carousel_item="true")["id"])
        for c in children:
            wait_ready(c, token, tries=30, sleep=5)
        cid = call("POST", f"{ig}/media", token, media_type="CAROUSEL", children=",".join(children),
                   caption=item["caption"])["id"]
        wait_ready(cid, token, tries=30, sleep=5)
    if dry:
        return "テストOK（投稿の準備まで成功・投稿はしていません）"
    return call("POST", f"{ig}/media_publish", token, creation_id=cid)["id"]


def read_set(name):
    p = os.path.join(HERE, name)
    return set(open(p, encoding="utf-8").read().split()) if os.path.exists(p) else set()


def add_line(name, text):
    with open(os.path.join(HERE, name), "a", encoding="utf-8") as f:
        f.write(text + "\n")


def cleanup(cfg, items):
    mdir, _ = media_paths(cfg)
    if not os.path.isdir(mdir):
        return
    limit = datetime.datetime.now(JST) - datetime.timedelta(days=KEEP_DAYS)
    ats = {it["id"]: datetime.datetime.fromisoformat(it["at"]) for it in items}
    for name in os.listdir(mdir):
        at = ats.get(name)
        if at is None or at < limit:
            p = os.path.join(mdir, name)
            if os.path.isdir(p) and os.path.getmtime(p) < limit.timestamp():
                shutil.rmtree(p, ignore_errors=True)


def low_stock_alert(cfg, items, now):
    future = [it for it in items if datetime.datetime.fromisoformat(it["at"]) > now]
    days_left = 0
    if future:
        days_left = (datetime.datetime.fromisoformat(future[-1]["at"]) - now).total_seconds() / 86400
    if days_left < 2:
        stamp = now.strftime("%Y-%m-%d")
        if stamp not in read_set("alert_days.txt"):
            add_line("alert_days.txt", stamp)
            notify(cfg, f"⚠️ Instagram：予約済みの投稿が残り{len(future)}件（約{max(days_left, 0):.1f}日分）です。"
                        "自動作成がうまく動いていない可能性があるので、Claudeに確認を頼んでください。")


def main():
    cfg = load_cfg()
    if not cfg.get("token") or "ここに" in cfg.get("token"):
        log("config.ini にトークンが入っていません")
        sys.exit(1)
    items = load_manifest(cfg)
    now = datetime.datetime.now(JST)
    posted = read_set("posted_ids.txt")

    if "--list" in sys.argv:
        for it in items:
            mark = "済" if it["id"] in posted else "  "
            print(mark, it["at"], it["type"], it["id"], len(it["files"]))
        return
    for flag in ("--dry-run", "--now"):
        if flag in sys.argv:
            iid = sys.argv[sys.argv.index(flag) + 1]
            item = next(it for it in items if it["id"] == iid)
            try:
                res = publish(cfg, item, dry=(flag == "--dry-run"))
                if flag == "--now":
                    add_line("posted_ids.txt", iid)
                log(f"{iid}: {res}")
                notify(cfg, f"🧪 Instagram {iid}（{item['type']}）：{res}" if flag == "--dry-run" else f"✅ Instagram：{iid} を投稿しました。")
            except Exception as e:
                log(f"{iid} でエラー: {e}")
                notify(cfg, f"❌ Instagram {iid} でエラー：{short_error(e)}")
            return

    fail_counts = {}
    p = os.path.join(HERE, "failed_ids.txt")
    if os.path.exists(p):
        for l in open(p, encoding="utf-8").read().split():
            fail_counts[l] = fail_counts.get(l, 0) + 1

    done = 0
    for it in items:
        if done >= MAX_PER_RUN:
            break
        at = datetime.datetime.fromisoformat(it["at"])
        if it["id"] in posted or at > now or now - at > datetime.timedelta(hours=MAX_LATE_HOURS):
            continue
        if fail_counts.get(it["id"], 0) >= 3:
            continue
        try:
            mid = publish(cfg, it)
            add_line("posted_ids.txt", it["id"])
            log(f"{it['id']}（{it['type']}）を投稿しました（media_id={mid}）")
            done += 1
        except Exception as e:
            add_line("failed_ids.txt", it["id"])
            n = fail_counts.get(it["id"], 0) + 1
            log(f"{it['id']} の投稿に失敗（{n}回目）: {e}")
            if n == 3:
                notify(cfg, f"❌ Instagram：{it['id']}（{'リール' if it['type'] == 'reel' else '投稿'}）が3回続けて投稿できませんでした。"
                            f"\n原因：{short_error(e)}\nClaudeに確認を頼んでください。")
    low_stock_alert(cfg, items, now)
    cleanup(cfg, items)


if __name__ == "__main__":
    main()
