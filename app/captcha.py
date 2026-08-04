# -*- coding: utf-8 -*-
"""图形验证码：Pillow 生成 4 位字母数字图片 + 干扰线/点。

答案存入 session（大写、5 分钟过期、一次性消费、大小写不敏感）。
"""
import io
import random
import string
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from flask import session

CAPTCHA_LEN = 4
EXPIRE_SECONDS = 300  # 5 分钟

# 排除易混字符 0/O/1/I/L
_CHARS = "".join(c for c in (string.ascii_uppercase + string.digits)
                 if c not in "0OI1L")


def _rand_color(lo, hi):
    return tuple(random.randint(lo, hi) for _ in range(3))


def _load_font(size):
    """优先用系统 ttf，找不到回退 Pillow 默认字体。"""
    for candidate in ("arial.ttf", "arialbd.ttf", "DejaVuSans.ttf",
                      "msyh.ttc", "simsun.ttc", "segoeui.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def generate_captcha():
    """生成验证码。返回 (BytesIO PNG, 答案大写)；答案与过期时间写入 session。"""
    text = "".join(random.choices(_CHARS, k=CAPTCHA_LEN))
    width, height = 132, 44
    img = Image.new("RGB", (width, height), _rand_color(235, 255))
    draw = ImageDraw.Draw(img)

    font = _load_font(30)
    for i, ch in enumerate(text):
        x = 12 + i * 28 + random.randint(-3, 3)
        y = random.randint(-1, 7)
        draw.text((x, y), ch, font=font, fill=_rand_color(15, 110))

    # 干扰线
    for _ in range(4):
        draw.line(
            [(random.randint(0, width), random.randint(0, height)),
             (random.randint(0, width), random.randint(0, height))],
            fill=_rand_color(120, 200), width=1,
        )
    # 干扰点
    for _ in range(90):
        draw.point((random.randint(0, width - 1), random.randint(0, height - 1)),
                   fill=_rand_color(90, 200))

    img = img.filter(ImageFilter.SMOOTH)

    answer = text.upper()
    session["captcha"] = answer
    session["captcha_exp"] = int(time.time()) + EXPIRE_SECONDS

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf, answer


def verify_captcha(user_input):
    """校验并一次性消费（无论成败都清除 session），大小写不敏感。"""
    expected = session.pop("captcha", None)
    exp = session.pop("captcha_exp", 0)
    if not expected or int(time.time()) > exp:
        return False
    return (user_input or "").strip().upper() == expected
