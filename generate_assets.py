#!/usr/bin/env python3
"""
ショート動画用の共通グラフィック素材（透過PNG）と音声素材（BGM・SE）を生成するスクリプト。
生成先: assets/
※ assets/custom_bgm.mp3 等を置けば後からフリーBGMへ差し替え可能です。
"""

from __future__ import annotations

import math
from pathlib import Path
import random
import struct
import wave
from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)


def make_frame_overlay():
    """1080x1920 透過枠オーバーレイ（グラデーションヘッダー・丸角ネオン枠・フッター）"""
    W, H = 1080, 1920
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1) Top header gradient (y=0..255)
    for y in range(255):
        t = y / 254.0
        r = int(16 + 26 * t)
        g = int(14 + 12 * t)
        b = int(38 + 42 * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))
    # Bright neon accent bar under header
    for x in range(W):
        t = x / (W - 1)
        r = int(255 * (1 - t) + 255 * t)
        g = int(220 * (1 - t) + 60 * t)
        b = int(40 * (1 - t) + 140 * t)
        draw.line([(x, 250), (x, 258)], fill=(r, g, b, 255))

    # 2) Side dark pillars / clean rounded mask around slide (268..852) and face (872..1704)
    # Draw dark matte frame around the two viewing windows so edges look polished
    mask = Image.new("L", (W, H), 255)
    md = ImageDraw.Draw(mask)
    # Cut out rounded windows (transparent inside)
    md.rounded_rectangle([18, 266, W - 18, 850], radius=28, fill=0)
    md.rounded_rectangle([18, 870, W - 18, 1700], radius=28, fill=0)
    # Also keep top header & bottom footer opaque
    matte = Image.new("RGBA", (W, H), (14, 16, 28, 255))
    matte.putalpha(mask)

    # Composite matte behind header gradient (only below y=258)
    matte_crop = matte.crop((0, 258, W, H))
    img.paste(matte_crop, (0, 258), matte_crop)

    # Bottom footer subtle gradient (y=1702..1920)
    for y in range(1702, H):
        t = (y - 1702) / (H - 1702)
        r = int(18 - 6 * t)
        g = int(20 - 8 * t)
        b = int(36 - 10 * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # 3) Glowing rounded borders around both windows
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle([18, 266, W - 18, 850], radius=28, outline=(90, 220, 255, 180), width=8)
    gd.rounded_rectangle([18, 870, W - 18, 1700], radius=28, outline=(255, 70, 150, 210), width=10)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=6))
    img = Image.alpha_composite(img, glow)

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([18, 266, W - 18, 850], radius=28, outline=(160, 240, 255, 255), width=4)
    draw.rounded_rectangle([18, 870, W - 18, 1700], radius=28, outline=(255, 110, 180, 255), width=5)

    img.save(ASSETS / "frame_overlay.png")


def make_speedlines():
    """マイルドな極細・外周のみの半透明集中線 透過PNG（顔アップ枠 1080x835 向け）"""
    W, H = 1080, 835
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = W * 0.50, H * 0.48
    rng = random.Random(42)

    for i in range(64):
        ang = (2 * math.pi * i / 64.0) + rng.uniform(-0.025, 0.025)
        r_in = rng.uniform(375, 455)  # keep face center completely clean
        r_out = 760
        spread = rng.uniform(0.003, 0.0075)  # delicate thin strokes
        p0 = (cx + r_in * math.cos(ang), cy + r_in * math.sin(ang))
        p1 = (cx + r_out * math.cos(ang - spread), cy + r_out * math.sin(ang - spread))
        p2 = (cx + r_out * math.cos(ang + spread), cy + r_out * math.sin(ang + spread))
        alpha = rng.randint(45, 85)
        draw.polygon([p0, p1, p2], fill=(255, 255, 255, alpha))

    img = img.filter(ImageFilter.GaussianBlur(radius=1.0))
    img.save(ASSETS / "speedlines.png")


def make_red_vignette():
    """オチ・ツッコミ用の赤い周辺フラッシュ枠 透過PNG（1080x835）"""
    W, H = 1080, 835
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for k in range(32):
        alpha = int(170 * ((32 - k) / 32.0) ** 1.8)
        draw.rounded_rectangle([k * 2, k * 2, W - 1 - k * 2, H - 1 - k * 2], radius=max(4, 28 - k), outline=(255, 30, 70, alpha), width=3)
    img = img.filter(ImageFilter.GaussianBlur(radius=4))
    img.save(ASSETS / "red_vignette.png")


def write_wav(path: Path, samples_l: list[float], samples_r: list[float] | None = None, sr: int = 48000):
    if samples_r is None:
        samples_r = samples_l
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for l, r in zip(samples_l, samples_r):
            il = max(-32767, min(32767, int(l * 32767)))
            ir = max(-32767, min(32767, int(r * 32767)))
            frames += struct.pack("<hh", il, ir)
        wf.writeframes(bytes(frames))


def make_sfx():
    sr = 48000
    # 1) Crisp pop/marimba ding (scene transition)
    dur = 0.16
    n = int(sr * dur)
    pop = []
    for i in range(n):
        t = i / sr
        env = math.exp(-24 * t)
        f = 880.0 if t < 0.045 else 1318.5  # A5 -> E6 upbeat two-tone blip
        val = env * (0.45 * math.sin(2 * math.pi * f * t) + 0.18 * math.sin(2 * math.pi * f * 2 * t))
        pop.append(val)
    write_wav(ASSETS / "sfx_pop.wav", pop)

    # 2) Snappy whoosh (transition swipe)
    dur = 0.18
    n = int(sr * dur)
    rng = random.Random(7)
    whoosh = []
    prev = 0.0
    for i in range(n):
        t = i / sr
        env = math.sin(math.pi * t / dur) ** 1.5
        # Simple low-pass filtered noise sweep
        raw = rng.uniform(-1, 1)
        alpha = 0.08 + 0.35 * env
        prev = prev + alpha * (raw - prev)
        whoosh.append(0.32 * env * prev)
    write_wav(ASSETS / "sfx_whoosh.wav", whoosh)

    # 3) Punchy comedy impact / bass drop (punchline!)
    dur = 0.30
    n = int(sr * dur)
    imp = []
    rng = random.Random(99)
    for i in range(n):
        t = i / sr
        # Fast pitch drop 210Hz -> 55Hz + brief woodblock transient
        freq = 55.0 + 155.0 * math.exp(-18 * t)
        sub = 0.65 * math.exp(-9.5 * t) * math.sin(2 * math.pi * freq * t)
        click = 0.35 * math.exp(-90 * t) * math.sin(2 * math.pi * 1200 * t)
        noise = 0.18 * math.exp(-45 * t) * rng.uniform(-1, 1)
        imp.append(max(-0.95, min(0.95, sub + click + noise)))
    write_wav(ASSETS / "sfx_impact.wav", imp)


def make_comedic_bgm():
    """軽快でポップなピッツィカート／マリンバ風フリーBGMループ（48kHz, 約48秒）"""
    sr = 48000
    bpm = 128.0
    step_sec = 60.0 / bpm / 2.0  # 8th note (~0.234s)
    # Playful 4-bar progression: C -> Am -> F -> G7 (freqs in Hz)
    # Bass notes per bar + bouncy off-beat plucks
    chords = [
        (130.81, [261.63, 329.63, 392.00, 523.25]),  # C
        (110.00, [220.00, 261.63, 329.63, 440.00]),  # Am
        (87.31,  [174.61, 220.00, 261.63, 349.23]),  # F
        (98.00,  [196.00, 246.94, 293.66, 392.00]),  # G
    ]
    melody_pattern = [
        (0, 2), (2, 3), (3, 1), (5, 2), (6, 3),
        (8, 2), (10, 1), (12, 3), (14, 2),
        (16, 1), (18, 2), (19, 3), (21, 2), (22, 1),
        (24, 2), (26, 3), (28, 1), (30, 2),
    ]
    total_bars = 24  # ~45s
    total_steps = total_bars * 8
    total_n = int(total_steps * step_sec * sr)
    buf_l = [0.0] * total_n
    buf_r = [0.0] * total_n

    def add_pluck(start_i: int, freq: float, amp: float, decay: float, pan: float = 0.0):
        ln = int(0.32 * sr)
        gl = amp * (1.0 - max(0.0, pan))
        gr = amp * (1.0 + min(0.0, pan))
        for k in range(ln):
            idx = start_i + k
            if idx >= total_n:
                break
            t = k / sr
            env = math.exp(-decay * t) * (1.0 - math.exp(-260 * t))
            sig = env * (0.75 * math.sin(2 * math.pi * freq * t) + 0.20 * math.sin(2 * math.pi * freq * 2 * t) + 0.05 * math.sin(2 * math.pi * freq * 3 * t))
            buf_l[idx] += sig * gl
            buf_r[idx] += sig * gr

    for bar in range(total_bars):
        bass_f, notes = chords[bar % 4]
        for s in range(8):
            gstep = bar * 8 + s
            si = int(gstep * step_sec * sr)
            # Soft bouncy bass on beats 1 and 3 (steps 0, 4)
            if s in (0, 4):
                add_pluck(si, bass_f, 0.16, 11.0, 0.0)
            # Light off-beat chord pizzicato on steps 2, 6
            if s in (2, 6):
                add_pluck(si, notes[1], 0.08, 18.0, -0.25)
                add_pluck(si, notes[2], 0.08, 18.0, 0.25)
        # Playful marimba melody notes
        bar_in_phrase = bar % 4
        for mstep, nidx in melody_pattern:
            if mstep // 8 == bar_in_phrase:
                gstep = bar * 8 + (mstep % 8)
                si = int(gstep * step_sec * sr)
                add_pluck(si, notes[nidx] * 2.0, 0.09, 16.0, 0.15 if (mstep % 2) else -0.15)

    write_wav(ASSETS / "bgm_comedic.wav", buf_l, buf_r)


if __name__ == "__main__":
    make_frame_overlay()
    make_speedlines()
    make_red_vignette()
    make_sfx()
    make_comedic_bgm()
    print("Generated all v3 assets in", ASSETS)
