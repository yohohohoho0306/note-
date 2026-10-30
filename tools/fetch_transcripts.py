"""YouTube の動画の字幕と概要欄を取り、research/記録json/<動画ID>.json に1本ずつ保存する.

    python3 tools/fetch_transcripts.py 1vE36sjjHLo TO6p-QsR9A4      # 動画IDを並べる
    python3 tools/fetch_transcripts.py --gap 900 TO6p-QsR9A4 ...     # 1本ごとに15分あける
    python3 tools/fetch_transcripts.py --force 1vE36sjjHLo           # 保存済みでも取り直す

yt-dlp(pip install yt-dlp)を使う。ブラウザは使わない。接続先は www.youtube.com。
字幕は日本語の字幕(なければ自動生成の字幕)。1本ずつ、間をあけて取る。
確認画面(「ボットではないことを確認」)や 429 が出たら、回避はせずに待ってからやり直す。
字幕は著作物で、このリポジトリは公開なので、記録json は git に入れない(.gitignore 済み)。
分析は research/youtube/ に自分の言葉で書く。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "記録json"
CHUNK_SEC = 30  # 字幕をこの秒数ごとに1行にまとめる
BLOCK_WORDS = ("not a bot", "HTTP Error 429", "Too Many Requests")


def pick_subtitle(info: dict) -> tuple[str, str, str] | None:
    """(字幕の URL, 言語, 種類) を返す。手動の日本語字幕を優先し、なければ自動生成."""
    for key, kind in (("subtitles", "手動"), ("automatic_captions", "自動生成")):
        tracks = info.get(key) or {}
        for lang in ("ja", "ja-orig", "ja-JP"):
            for t in tracks.get(lang) or []:
                if t.get("ext") == "json3":
                    return t["url"], lang, kind
    return None


def to_lines(raw: bytes) -> list[str]:
    """json3 形式の字幕を、[mm:ss] 付きの行(CHUNK_SEC 秒ごと)にする."""
    lines, start, buf = [], None, []
    for ev in json.loads(raw).get("events") or []:
        text = "".join(s.get("utf8", "") for s in ev.get("segs") or []).replace("\n", " ").strip()
        if not text:
            continue
        t = ev.get("tStartMs", 0) // 1000
        if start is None:
            start = t
        elif t - start >= CHUNK_SEC:
            lines.append(f"[{start // 60:02d}:{start % 60:02d}] " + " ".join(buf))
            start, buf = t, []
        buf.append(text)
    if buf:
        lines.append(f"[{start // 60:02d}:{start % 60:02d}] " + " ".join(buf))
    return lines


def fetch(ydl, video_id: str) -> dict | None:
    """1本分の記録を作る。字幕がなければ None."""
    info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    sub = pick_subtitle(info)
    if not sub:
        return None
    url, lang, kind = sub
    time.sleep(10)  # 動画の情報と字幕の取得の間をあける
    raw = ydl.urlopen(url).read()
    d = info.get("upload_date") or ""
    return {
        "id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "upload_date": f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else None,
        "duration_sec": info.get("duration"),
        "view_count": info.get("view_count"),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": info.get("description"),
        "subtitle": {"lang": lang, "kind": kind},
        "transcript": to_lines(raw),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ids", nargs="+", help="動画ID(watch?v= のあとの11文字)")
    p.add_argument("--out", default=str(OUT), help="保存先のフォルダ")
    p.add_argument("--gap", type=int, default=60, help="1本ごとにあける秒数(既定 60)")
    p.add_argument("--wait", type=int, default=1200, help="確認画面が出たときに待つ秒数(既定 1200)")
    p.add_argument("--hours", type=float, default=6, help="この時間を過ぎたらやめる(既定 6)")
    p.add_argument("--force", action="store_true", help="保存済みでも取り直す")
    args = p.parse_args()

    import yt_dlp

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    end = time.time() + args.hours * 3600
    todo = list(args.ids)
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        while todo and time.time() < end:
            vid = todo[0]
            path = out / f"{vid}.json"
            if path.exists() and not args.force:
                print(f"SKIP {vid} 保存済み", flush=True)
                todo.pop(0)
                continue
            try:
                rec = fetch(ydl, vid)
            except Exception as e:  # noqa: BLE001  1本の失敗で止めない
                msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                if any(w in str(e) for w in BLOCK_WORDS):
                    print(f"WAIT {vid} 確認画面のため {args.wait // 60}分待ちます", flush=True)
                    time.sleep(args.wait)
                    continue
                print(f"FAIL {vid} {msg}", flush=True)
                todo.pop(0)
            else:
                if rec is None:
                    print(f"NOSUB {vid} 日本語の字幕なし", flush=True)
                else:
                    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                    print(f"OK {vid} {sum(len(s) for s in rec['transcript'])}字({rec['subtitle']['kind']})", flush=True)
                todo.pop(0)
            if todo:
                time.sleep(args.gap)
    print("DONE" if not todo else "TIMEOUT 残り: " + " ".join(todo), flush=True)
    return 0 if not todo else 1


if __name__ == "__main__":
    sys.exit(main())
