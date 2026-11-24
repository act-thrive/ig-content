#!/usr/bin/env python3
"""Instagram投稿素材のビルド。

使い方:
  python3 tools/build.py content/2026-11-26.json [content/...json ...]

各JSONは {"posts": [...]} 形式。posts の各要素:
  カルーセル: {"id","at","type":"carousel","slides":[...],"caption"}
  リール    : {"id","at","type":"reel","scenes":[...],"caption"}
  at は "2026-11-26T08:00:00+09:00" のような日時（この時刻以降に投稿される）

出力:
  media/<id>/01.jpg ... （カルーセル 1080x1350）
  media/<id>/reel.mp4, cover.jpg （リール 1080x1920, H.264 + 無音AAC）
  manifest.json に予定を追加・更新（同じidは上書き）
"""
import html, json, os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND = "AIで仕事をラクにする｜非エンジニアの実践ノート"
HANDLE = "@556bear_556"

BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Noto Sans CJK JP','Noto Sans JP',sans-serif;background:#F6F3EC;color:#1E2A3A;overflow:hidden}
.bar{position:absolute;left:0;top:0;width:100%;height:22px;background:#E8743B}
.brand{position:absolute;left:96px;bottom:70px;font-size:26px;color:#7A8594;letter-spacing:.04em}
.page{position:absolute;right:96px;bottom:70px;font-size:26px;color:#7A8594}
.kicker{display:inline-block;background:#1E2A3A;color:#fff;font-size:40px;font-weight:700;padding:14px 32px;border-radius:999px}
.title{font-weight:900;line-height:1.3;white-space:pre-line;display:block;width:100%}
.title em,.head em{font-style:normal;color:#E8743B}
.sub{font-size:44px;color:#4A5668;font-weight:500;white-space:pre-line}
.no{font-size:120px;font-weight:900;color:#E8743B;line-height:1}
.head{font-size:72px;font-weight:900;line-height:1.35;white-space:pre-line;display:block;width:100%}
.rule{width:120px;height:10px;background:#1E2A3A;margin:56px 0 60px}
.lines p{font-size:46px;line-height:1.75;font-weight:500}
.box{border-radius:28px;padding:40px 48px;margin-top:40px}
.box .lab{font-size:36px;font-weight:900;margin-bottom:14px}
.box .txt{font-size:46px;line-height:1.6;font-weight:700;white-space:pre-line}
.bad{background:#E9E5DC}.bad .lab{color:#8A8F98}.bad .txt{color:#6B7280}
.good{background:#1E2A3A;color:#fff}.good .lab{color:#F2A47B}
.follow{display:inline-block;background:#E8743B;color:#fff;font-size:44px;font-weight:900;padding:22px 48px;border-radius:999px}
"""

CAROUSEL_CSS = BASE_CSS + """
body{width:1080px;height:1350px}
.wrap{position:relative;width:100%;height:100%;padding:110px 96px}
.kicker{margin-top:150px}
.title{font-size:104px;margin-top:70px}
.sub{margin-top:60px}
.no{margin-top:60px}
.head{margin-top:36px}
.cta{display:flex;flex-direction:column;justify-content:center;height:100%;padding-bottom:120px}
.cta .head{font-size:88px}
.cta .lines p{font-size:44px;color:#4A5668}
.cta .follow{margin-top:70px;align-self:flex-start}
"""

REEL_CSS = BASE_CSS + """
body{width:1080px;height:1920px}
.wrap{position:relative;width:100%;height:100%;padding:260px 96px 300px;display:flex;flex-direction:column;justify-content:center}
.kicker{align-self:flex-start;margin-bottom:60px}
.title{font-size:112px}
.sub{margin-top:60px}
.no{margin-bottom:30px}
.head{font-size:88px}
.lines p{font-size:54px}
.follow{margin-top:80px;align-self:flex-start}
.brand{bottom:200px}.page{bottom:200px}
"""


FIT_JS = """
() => { for (const el of document.querySelectorAll('.title,.head,.box .txt')) {
  el.style.whiteSpace = 'pre'; let fs = parseFloat(getComputedStyle(el).fontSize);
  const max = el.parentElement.clientWidth - (el.classList.contains('txt') ? 0 : 0);
  while (el.scrollWidth > el.clientWidth + 1 && fs > 30) { fs -= 2; el.style.fontSize = fs + 'px'; }
} }
"""


def e(s):
    return html.escape(str(s))


def em(s):
    """「**強調**」を色付きにする"""
    out, parts = "", str(s).split("**")
    for i, p in enumerate(parts):
        out += f"<em>{e(p)}</em>" if i % 2 else e(p)
    return out


def slide_inner(s):
    t = s.get("type", "body")
    if t == "cover":
        return (f'<div class="kicker">{e(s["kicker"])}</div><div class="title">{em(s["title"])}</div>'
                f'<div class="sub">{e(s.get("sub", ""))}</div>')
    if t == "compare":
        return (f'<div class="no">{e(s.get("no", ""))}</div><div class="head">{em(s["head"])}</div>'
                f'<div class="box bad"><div class="lab">✕ {e(s.get("bad_label", "よくある例"))}</div><div class="txt">{e(s["bad"])}</div></div>'
                f'<div class="box good"><div class="lab">◯ {e(s.get("good_label", "おすすめ"))}</div><div class="txt">{e(s["good"])}</div></div>')
    if t == "cta":
        return (f'<div class="cta"><div class="head">{em(s["head"])}</div><div class="rule"></div><div class="lines">'
                + "".join(f"<p>{e(l)}</p>" for l in s.get("lines", []))
                + f'</div><div class="follow">{HANDLE} をフォロー</div></div>')
    return (f'<div class="no">{e(s.get("no", ""))}</div><div class="head">{em(s["head"])}</div><div class="rule"></div>'
            '<div class="lines">' + "".join(f"<p>{e(l)}</p>" for l in s.get("lines", [])) + "</div>")


def page_html(css, inner, i, n):
    return (f'<html><head><meta charset="utf-8"><style>{css}</style></head><body><div class="bar"></div>'
            f'<div class="wrap">{inner}</div><div class="brand">{e(BRAND)}</div><div class="page">{i}/{n}</div></body></html>')


def reel_inner(s):
    t = s.get("type", "point")
    if t == "hook":
        return (f'<div class="kicker">{e(s.get("kicker", "AI活用"))}</div><div class="title">{em(s["text"])}</div>'
                f'<div class="sub">{e(s.get("sub", ""))}</div>')
    if t == "cta":
        return (f'<div class="head">{em(s["text"])}</div><div class="rule"></div><div class="lines">'
                + "".join(f"<p>{e(l)}</p>" for l in s.get("lines", []))
                + f'</div><div class="follow">{HANDLE} をフォロー</div>')
    return (f'<div class="no">{e(s.get("no", ""))}</div><div class="head">{em(s["text"])}</div><div class="rule"></div>'
            '<div class="lines">' + "".join(f"<p>{e(l)}</p>" for l in s.get("lines", [])) + "</div>")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(" ".join(cmd[:3]) + "...\n" + r.stderr[-1500:])


def make_reel(page, post, outdir):
    scenes = post["scenes"]
    n = len(scenes)
    durs = [float(s.get("sec", 3.0)) for s in scenes]
    with tempfile.TemporaryDirectory() as td:
        pngs = []
        for i, s in enumerate(scenes, 1):
            page.set_viewport_size({"width": 1080, "height": 1920})
            page.set_content(page_html(REEL_CSS, reel_inner(s), i, n))
            page.evaluate(FIT_JS)
            page.wait_for_timeout(150)
            p = os.path.join(td, f"s{i:02d}.png")
            page.screenshot(path=p)
            pngs.append(p)
        # 表紙画像（1枚目）
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", pngs[0], "-q:v", "3", os.path.join(outdir, "cover.jpg")])
        fade = 0.4
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for p, d in zip(pngs, durs):
            cmd += ["-loop", "1", "-t", f"{d + fade:.2f}", "-i", p]
        total = sum(durs) + fade
        cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        # 各シーンに軽いズームをかけ、クロスフェードでつなぐ
        fl = []
        for i, d in enumerate(durs):
            frames = int((d + fade) * 30)
            fl.append(f"[{i}:v]scale=1188:2112,zoompan=z='min(zoom+0.0006,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                      f":d={frames}:s=1080x1920:fps=30,format=yuv420p,setsar=1[v{i}]")
        last, offset = "v0", 0.0
        for i in range(1, n):
            offset += durs[i - 1]
            fl.append(f"[{last}][v{i}]xfade=transition=fade:duration={fade}:offset={offset:.2f}[x{i}]")
            last = f"x{i}"
        cmd += ["-filter_complex", ";".join(fl), "-map", f"[{last}]", "-map", f"{n}:a",
                "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "30",
                "-b:v", "2500k", "-maxrate", "4M", "-bufsize", "6M",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-shortest", "-movflags", "+faststart",
                os.path.join(outdir, "reel.mp4")]
        run(cmd)
    return ["reel.mp4", "cover.jpg"]


def make_carousel(page, post, outdir):
    slides = post["slides"]
    n = len(slides)
    if not 2 <= n <= 10:
        raise ValueError(f"{post['id']}: スライドは2〜10枚にしてください（{n}枚）")
    files = []
    page.set_viewport_size({"width": 1080, "height": 1350})
    for i, s in enumerate(slides, 1):
        page.set_content(page_html(CAROUSEL_CSS, slide_inner(s), i, n))
        page.evaluate(FIT_JS)
        page.wait_for_timeout(150)
        png = os.path.join(outdir, f"{i:02d}.png")
        page.screenshot(path=png)
        jpg = os.path.join(outdir, f"{i:02d}.jpg")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", png, "-q:v", "3", jpg])
        os.remove(png)
        files.append(f"{i:02d}.jpg")
    return files


def main(paths):
    from playwright.sync_api import sync_playwright
    mpath = os.path.join(ROOT, "manifest.json")
    manifest = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {"items": []}
    items = {it["id"]: it for it in manifest["items"]}
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        for path in paths:
            for post in json.load(open(path, encoding="utf-8"))["posts"]:
                if len(post.get("caption", "")) > 2200:
                    raise ValueError(f"{post['id']}: キャプションが2200字を超えています")
                outdir = os.path.join(ROOT, "media", post["id"])
                os.makedirs(outdir, exist_ok=True)
                for f in os.listdir(outdir):
                    os.remove(os.path.join(outdir, f))
                if post["type"] == "reel":
                    files = make_reel(page, post, outdir)
                else:
                    files = make_carousel(page, post, outdir)
                items[post["id"]] = {"id": post["id"], "at": post["at"], "type": post["type"],
                                     "files": [f"media/{post['id']}/{f}" for f in files],
                                     "caption": post["caption"]}
                print("built", post["id"], post["type"], len(files))
        b.close()
    manifest["items"] = sorted(items.values(), key=lambda x: x["at"])
    json.dump(manifest, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("manifest items:", len(manifest["items"]))


if __name__ == "__main__":
    main(sys.argv[1:])
