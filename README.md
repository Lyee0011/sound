# sound

A Claude Code skill that gives AI-made videos studio-grade sound effects.

Agents can't hear. Left alone, they reach for a synthesizer and judge a sound by its math. This skill sends them to real sound libraries instead, and teaches them to choose the way a person would: by what the people who *did* hear the clips left behind (likes, downloads, ratings, the uploader's description).

给 AI 做的视频配音效的 skill：不用合成器，去真的音效库里找，按人耳留下的信号（点赞、下载、评分、描述）挑。

| Source | What you get | Ranked by |
|---|---|---|
| [Pixabay](https://pixabay.com/sound-effects/) | huge catalog, no credit needed | ♥ likes |
| [Freesound](https://freesound.org) | real-world foley, with the uploader's description | ↓ downloads, ★ rating (votes) |
| [Mixkit](https://mixkit.co/free-sound-effects/) | curated wav | names only |
| [Kenney](https://kenney.nl/assets/category:Audio) | UI and game packs, CC0 | names only |

## 基础版与满血版

本仓库公开的 **sound skill 为基础版**，但已经能大幅提升视频声音质感。

如果你希望获得 **满血版 sound skill**：

1. 点击 [B 站充电入口](https://member.bilibili.com/mall/upower-pay?mid=198345535&oid=198345535&otype=up&navhide=1&plat_id=435&share_from=h5&share_medium=iphone&share_plat=ios&share_session_id=F22B4227-2DAF-4EC0-A9C4-5E7DFA2E7462&share_source=WEIXIN&share_tag=s_i&timestamp=1791902276&unique_k=ASTZOLp&type=fullscreen)，选择 **30 元档位**完成充电。
2. 扫描下方二维码添加我的微信，并附上 **充电截图**。

充电后还可获得我自己研发的 **BGM skill**、**人声优化混音 skill**，并加入 **私域群**。

<img src="assets/wechat-qr.jpg" alt="浙大猫学长微信二维码，扫码添加好友并附上充电截图" width="320" />

## Install

Clone it into your project's skills folder:

```bash
git clone https://github.com/vibe-motion/sound .claude/skills/sound
```

Python 3, standard library only. Pixabay sits behind Cloudflare, so the first Pixabay search builds a small venv with `curl_cffi` in `~/.cache/sfx-sources/venv`.

## Use

Ask while you work on a video: "add a whoosh when the card flies in", "a soft click for each toggle". The agent searches, quotes the numbers, measures the clips, and downloads the ones that fit.

The script also works on its own:

```bash
S=.claude/skills/sound/scripts/sfx.py
python3 $S pixabay "whoosh"                     # ranked by ♥
python3 $S freesound "paper slide" --license cc0
python3 $S kenney interface-sounds
python3 $S get sfx/ px:118248 fs:198960         # download (prints the credit a CC BY clip needs)
```

## Licenses

Pixabay and Mixkit clips are free to use without credit under their own licenses. Kenney packs are CC0. Freesound is per clip: CC0 is free, CC BY needs a credit (`get` prints it), and BY-NC is not for commercial work (`--license cc0|by` filters).

## Notes

Freesound caps downloads at about 80 KB/s per connection and can time out on some networks. `references/local-setup.md` and `scripts/clash_freesound.py` are the author's Clash setup for routing freesound.org; adapt them or ignore them.
