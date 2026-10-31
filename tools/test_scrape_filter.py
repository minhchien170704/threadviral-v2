"""Kiem tra scrape + smart filter tren bai viet Threads that."""
import asyncio
import sys

sys.path.insert(0, ".")

from tvcore.content import select_comments
from tvcore.scrape import scrape_threads_post

URL = "https://www.threads.net/@zuck"


async def main():
    scenes = await scrape_threads_post(URL, max_comments=12)
    print(f"Scape: {len(scenes)} scenes")
    root = scenes[0]
    print(f"Bai goc (@{root['author']}): {root['display_text'][:100]}")

    comments = [s["display_text"] for s in scenes[1:]]
    print(f"Binh luan: {len(comments)}")

    sel, stats = select_comments(comments, root_text=root["display_text"], max_n=7)
    print(f"Stats: {stats}")
    print("--- Duoc chon ---")
    for s in sel:
        print(f"  {s['score']*100:5.1f}  {s['text'][:70]}  {s['reasons']}")


asyncio.run(main())
