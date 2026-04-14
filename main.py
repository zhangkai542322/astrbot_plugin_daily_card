"""
astrbot_plugin_daily_card - LLM驱动的每日卡片图片生成插件
天气/日程/新闻/自定义/聚合，纯黑白400x300图片
全部通过 LLM 对话驱动
"""

import aiohttp
import json
import logging
import datetime
import os
import tempfile
from typing import Optional, List, Dict
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from astrbot.api.all import (
    Star, Context, register, AstrBotConfig,
    AstrMessageEvent, MessageEventResult
)
from astrbot.api.event import filter

logger = logging.getLogger("DailyCard")

# ============================================================
# 常量
# ============================================================
W, H = 400, 300
BLACK = 0
WHITE = 255
GRAY_LIGHT = 220
GRAY_MID = 160
GRAY_DARK = 80

WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

WMO_CODES = {
    0: ("晴", "*"), 1: ("基本晴", "~"), 2: ("多云", "o"), 3: ("阴", "#"),
    45: ("雾", "."), 48: ("雾凇", "."),
    51: ("小毛毛雨", "/"), 53: ("毛毛雨", "/"), 55: ("大毛毛雨", "//"),
    56: ("冻毛毛雨", "/!"), 57: ("冻雨", "/!"),
    61: ("小雨", "/"), 63: ("中雨", "//"), 65: ("大雨", "///"),
    66: ("冻小雨", "/!"), 67: ("冻大雨", "/!"),
    71: ("小雪", "*"), 73: ("中雪", "**"), 75: ("大雪", "***"), 77: ("雪粒", "*"),
    80: ("阵雨", "/~"), 81: ("中阵雨", "/~"), 82: ("大阵雨", "/~~"),
    85: ("小阵雪", "*~"), 86: ("大阵雪", "*~"),
    95: ("雷暴", "!"), 96: ("雷暴伴冰雹", "!!"), 99: ("强雷暴伴冰雹", "!!!"),
}

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

# ============================================================
# 字体系统（参考 talkative_king：打包字体 + 绝对路径加载）
# ============================================================
_FONT_CACHE: Dict[int, ImageFont.FreeTypeFont] = {}
_CUSTOM_FONT_PATH: str = ""  # 用户指定的字体文件名（在 font_dir 中查找）
_FONT_DIR: str = "/AstrBot/data/fonts"  # 字体查找目录

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_BUNDLED_FONT = os.path.join(_PLUGIN_DIR, "assets", "font.ttf")  # 插件自带字体


def _find_available_font() -> Optional[str]:
    """按优先级查找可用的中文字体"""
    # 1. 用户指定的字体文件名（在 font_dir 中查找）
    if _CUSTOM_FONT_PATH:
        if os.path.isabs(_CUSTOM_FONT_PATH) and os.path.exists(_CUSTOM_FONT_PATH):
            return _CUSTOM_FONT_PATH
        candidate = os.path.join(_FONT_DIR, _CUSTOM_FONT_PATH)
        if os.path.exists(candidate):
            return candidate

    # 2. font_dir 下的 font.ttf（AstrBot 社区通用约定）
    default_font = os.path.join(_FONT_DIR, "font.ttf")
    if os.path.exists(default_font):
        return default_font

    # 3. font_dir 下任意字体文件
    if os.path.isdir(_FONT_DIR):
        for f in sorted(os.listdir(_FONT_DIR)):
            if f.lower().endswith(('.ttf', '.ttc', '.otf')):
                return os.path.join(_FONT_DIR, f)

    # 4. 插件自带字体（打包字体，任何环境都可用）
    if os.path.exists(_BUNDLED_FONT):
        return _BUNDLED_FONT

    return None


def _resolve_font_path(path: str) -> Optional[str]:
    """解析字体路径，支持绝对路径和相对于 font_dir 的路径"""
    if not path:
        return None
    if os.path.isabs(path):
        return path if os.path.exists(path) else None
    candidate = os.path.join(_FONT_DIR, path)
    if os.path.exists(candidate):
        return candidate
    candidate = os.path.join(_PLUGIN_DIR, path)
    if os.path.exists(candidate):
        return candidate
    return None


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """获取指定大小的字体，带缓存"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]

    font_path = _find_available_font()
    if font_path:
        try:
            f = ImageFont.truetype(font_path, size)
            _FONT_CACHE[size] = f
            return f
        except Exception as e:
            logger.warning(f"加载字体失败 {font_path}: {e}")

    # 最终兜底
    try:
        f = ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


def _clear_font_cache():
    """清除字体缓存（切换字体后调用）"""
    _FONT_CACHE.clear()


def get_font_info() -> str:
    """返回当前字体使用情况"""
    lines = []
    lines.append(f"字体目录: {_FONT_DIR}")
    lines.append(f"  存在: {'✅' if os.path.isdir(_FONT_DIR) else '❌'}")

    if os.path.isdir(_FONT_DIR):
        font_files = [f for f in os.listdir(_FONT_DIR)
                      if f.lower().endswith(('.ttf', '.ttc', '.otf'))]
        if font_files:
            lines.append(f"  已有字体: {', '.join(font_files)}")
        else:
            lines.append(f"  ⚠️ 目录为空")

    if _CUSTOM_FONT_PATH:
        resolved = _resolve_font_path(_CUSTOM_FONT_PATH)
        lines.append(f"指定字体: {_CUSTOM_FONT_PATH}")
        lines.append(f"  解析: {resolved or '❌ 未找到'}")
    else:
        lines.append(f"指定字体: 未设置（自动查找）")

    lines.append(f"自带字体: {_BUNDLED_FONT}")
    lines.append(f"  存在: {'✅' if os.path.exists(_BUNDLED_FONT) else '❌'}")

    font_path = _find_available_font()
    lines.append(f"当前使用: {font_path or '❌ 无可用字体'}")

    return "\n".join(lines)

def _draw_weather_icon(dr, cx, cy, wmo, size=60):
    """在 (cx, cy) 中心绘制天气图标"""
    s = size // 2  # 半径基准
    BLACK_FIG = 0

    if wmo in (0, 1):  # 晴/基本晴 - 太阳
        r = s * 0.35
        dr.ellipse([cx-r, cy-r, cx+r, cy+r], fill=BLACK_FIG)
        import math
        for i in range(8):
            angle = math.radians(i * 45)
            x1 = cx + (r+4) * math.cos(angle)
            y1 = cy + (r+4) * math.sin(angle)
            x2 = cx + (r+s*0.25) * math.cos(angle)
            y2 = cy + (r+s*0.25) * math.sin(angle)
            dr.line([(x1,y1),(x2,y2)], fill=BLACK_FIG, width=2)

    elif wmo in (2, 3):  # 多云/阴 - 云
        dr.ellipse([cx-s*0.5, cy-s*0.2, cx+s*0.1, cy+s*0.4], fill=BLACK_FIG)
        dr.ellipse([cx-s*0.2, cy-s*0.5, cx+s*0.5, cy+s*0.1], fill=BLACK_FIG)
        dr.ellipse([cx-s*0.1, cy-s*0.35, cx+s*0.6, cy+s*0.25], fill=BLACK_FIG)
        dr.rectangle([cx-s*0.4, cy, cx+s*0.5, cy+s*0.3], fill=BLACK_FIG)

    elif wmo in (45, 48):  # 雾 - 横线
        for i in range(5):
            y = cy - s*0.5 + i * (s*0.25)
            x_start = cx - s*0.5 + (i%2)*s*0.15
            x_end = cx + s*0.5 - (i%2)*s*0.15
            dr.line([(x_start,y),(x_end,y)], fill=BLACK_FIG, width=2)

    elif wmo in (51,53,55,56,57,61,63,65,66,67,80,81,82):  # 各种雨
        # 云
        dr.ellipse([cx-s*0.5, cy-s*0.4, cx+s*0.1, cy+s*0.1], fill=BLACK_FIG)
        dr.ellipse([cx-s*0.2, cy-s*0.6, cx+s*0.5, cy-s*0.1], fill=BLACK_FIG)
        dr.ellipse([cx-s*0.1, cy-s*0.5, cx+s*0.6, cy], fill=BLACK_FIG)
        dr.rectangle([cx-s*0.4, cy-s*0.2, cx+s*0.5, cy], fill=BLACK_FIG)
        # 雨滴
        drops = 3 if wmo < 63 else 4
        for i in range(drops):
            dx = cx - s*0.3 + i * (s*0.6/max(drops-1,1))
            dy = cy + s*0.15
            dr.line([(dx, dy),(dx-s*0.05, dy+s*0.25)], fill=BLACK_FIG, width=2)

    elif wmo in (71,73,75,77,85,86):  # 雪
        # 云
        dr.ellipse([cx-s*0.5, cy-s*0.4, cx+s*0.1, cy+s*0.1], fill=BLACK_FIG)
        dr.ellipse([cx-s*0.2, cy-s*0.6, cx+s*0.5, cy-s*0.1], fill=BLACK_FIG)
        dr.ellipse([cx-s*0.1, cy-s*0.5, cx+s*0.6, cy], fill=BLACK_FIG)
        dr.rectangle([cx-s*0.4, cy-s*0.2, cx+s*0.5, cy], fill=BLACK_FIG)
        # 雪花 (小十字)
        for i in range(3):
            dx = cx - s*0.3 + i * s*0.3
            dy = cy + s*0.25
            dr.line([(dx-4,dy),(dx+4,dy)], fill=BLACK_FIG, width=1)
            dr.line([(dx,dy-4),(dx,dy+4)], fill=BLACK_FIG, width=1)

    elif wmo in (95,96,99):  # 雷暴
        # 云
        dr.ellipse([cx-s*0.5, cy-s*0.4, cx+s*0.1, cy+s*0.1], fill=BLACK_FIG)
        dr.ellipse([cx-s*0.2, cy-s*0.6, cx+s*0.5, cy-s*0.1], fill=BLACK_FIG)
        dr.ellipse([cx-s*0.1, cy-s*0.5, cx+s*0.6, cy], fill=BLACK_FIG)
        dr.rectangle([cx-s*0.4, cy-s*0.2, cx+s*0.5, cy], fill=BLACK_FIG)
        # 闪电
        points = [(cx, cy+s*0.05),(cx-s*0.1, cy+s*0.25),
                  (cx+s*0.05, cy+s*0.25),(cx-s*0.05, cy+s*0.45)]
        dr.line(points, fill=BLACK_FIG, width=2)

    else:  # 未知
        dr.ellipse([cx-s*0.3, cy-s*0.3, cx+s*0.3, cy+s*0.3],
                   outline=BLACK_FIG, width=2)


# ============================================================
# 工具函数
# ============================================================
def _dt(draw, xy, text, font, fill=BLACK, anchor=None):
    draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)

def _wind_dir(deg: float) -> str:
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    return dirs[round(deg / 45) % 8]

def _wrap_text(text, font, max_w, draw):
    lines, cur = [], ""
    for ch in text:
        test = cur + ch
        bb = draw.textbbox((0, 0), test, font=font)
        if bb[2] - bb[0] > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines or [""]

def _save(img, prefix="card"):
    tmp = tempfile.gettempdir()
    fn = f"{prefix}_{datetime.datetime.now().strftime('%H%M%S%f')}.png"
    p = os.path.join(tmp, fn)
    img.save(p, "PNG")
    return p


# ============================================================
# 天气 API
# ============================================================
async def _geocode(city: str) -> Optional[Dict]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(GEOCODING_URL, params={"name": city, "count": 1, "language": "zh"},
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    d = await r.json()
                    results = d.get("results", [])
                    if results:
                        rr = results[0]
                        return {"name": rr.get("name", city), "lat": rr["latitude"],
                                "lon": rr["longitude"], "admin1": rr.get("admin1", "")}
    except Exception as e:
        logger.error(f"Geocode error: {e}")
    return None

async def _fetch_weather(lat, lon, unit="celsius"):
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_sum,wind_speed_10m_max",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "timezone": "Asia/Shanghai",
        "temperature_unit": "fahrenheit" if unit == "fahrenheit" else "celsius",
        "forecast_days": 4,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(WEATHER_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        logger.error(f"Weather error: {e}")
    return None

def _parse_weather(raw, city_name):
    try:
        cur = raw.get("current", {})
        daily = raw.get("daily", {})
        hourly = raw.get("hourly", {})
        now = datetime.datetime.now()
        wmo = cur.get("weather_code", 0)
        desc, icon = WMO_CODES.get(wmo, ("未知", "?"))

        h_fc = []
        h_t = hourly.get("time", [])
        h_tmp = hourly.get("temperature_2m", [])
        h_cd = hourly.get("weather_code", [])
        h_pr = hourly.get("precipitation_probability", [])
        for i, t in enumerate(h_t):
            try:
                dt = datetime.datetime.fromisoformat(t)
                if dt >= now and len(h_fc) < 8:
                    hd, _ = WMO_CODES.get(h_cd[i] if i < len(h_cd) else 0, ("未知", "?"))
                    h_fc.append({"time": dt.strftime("%H:%M"), "temp": round(h_tmp[i]) if i < len(h_tmp) else 0,
                                 "desc": hd, "prob": h_pr[i] if i < len(h_pr) else 0})
            except Exception:
                continue

        d_fc = []
        d_dates = daily.get("time", [])
        d_max = daily.get("temperature_2m_max", [])
        d_min = daily.get("temperature_2m_min", [])
        d_codes = daily.get("weather_code", [])
        d_sr = daily.get("sunrise", [])
        d_ss = daily.get("sunset", [])
        d_precip = daily.get("precipitation_sum", [])
        d_wind = daily.get("wind_speed_10m_max", [])
        for i in range(min(len(d_dates), 4)):
            dd, _ = WMO_CODES.get(d_codes[i] if i < len(d_codes) else 0, ("未知", "?"))
            try:
                wk = WEEKDAYS_CN[datetime.datetime.fromisoformat(d_dates[i]).weekday()]
            except Exception:
                wk = ""
            d_fc.append({
                "date": d_dates[i] if i < len(d_dates) else "", "weekday": wk,
                "max": round(d_max[i]) if i < len(d_max) else 0,
                "min": round(d_min[i]) if i < len(d_min) else 0,
                "desc": dd,
                "sunrise": d_sr[i].split("T")[-1] if i < len(d_sr) else "",
                "sunset": d_ss[i].split("T")[-1] if i < len(d_ss) else "",
                "precip": round(d_precip[i], 1) if i < len(d_precip) else 0,
                "wind": round(d_wind[i], 1) if i < len(d_wind) else 0,
            })

        return {
            "city": city_name, "temp": round(cur.get("temperature_2m", 0)),
            "feels_like": round(cur.get("apparent_temperature", 0)),
            "humidity": cur.get("relative_humidity_2m", 0),
            "wind_speed": round(cur.get("wind_speed_10m", 0), 1),
            "wind_dir": cur.get("wind_direction_10m", 0),
            "precip": cur.get("precipitation", 0),
            "weather_code": wmo, "desc": desc, "icon": icon,
            "hourly": h_fc, "daily": d_fc,
            "updated": now.strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as e:
        logger.error(f"Parse error: {e}")
        return None


# ============================================================
# 图片渲染 - 天气 ×8
# ============================================================
def _r_classic(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(30); fb=_get_font(64); fm=_get_font(20); fs=_get_font(17)
    dr.rectangle([0,0,W,4],fill=BLACK); dr.rectangle([0,52,W,56],fill=BLACK)
    _dt(dr,(W//2,30),d["city"],ft,anchor="mm")
    _draw_weather_icon(dr, 80, 115, d.get("weather_code", 0), 70)
    bw=dr.textbbox((0,0),str(d['temp']),font=fb)[2]
    _dt(dr,(290,100),str(d['temp']),fb,anchor="mm")
    _dt(dr,(290,148),d["desc"],fm,anchor="mm")
    dr.line([(20,170),(W-20,170)],fill=BLACK,width=2)
    col = [W//6, W//2, 5*W//6]
    t1, t2, t3 = f"体感{d['feels_like']}°", f"湿度{d['humidity']}%", f"{_wind_dir(d['wind_dir'])}风"
    b1 = dr.textbbox((0,0), t1, font=fs); w1 = b1[2]-b1[0]
    b2 = dr.textbbox((0,0), t2, font=fs); w2 = b2[2]-b2[0]
    b3 = dr.textbbox((0,0), t3, font=fs); w3 = b3[2]-b3[0]
    _dt(dr,(col[0]-w1//2,176), t1, fs)
    _dt(dr,(col[1]-w2//2,176), t2, fs)
    _dt(dr,(col[2]-w3//2,176), t3, fs)
    t4, t5 = f"风速{d['wind_speed']}km/h", f"气压{d.get('pressure',1013)}hPa"
    b4 = dr.textbbox((0,0), t4, font=fs); w4 = b4[2]-b4[0]
    b5 = dr.textbbox((0,0), t5, font=fs); w5 = b5[2]-b5[0]
    _dt(dr,(col[0]-w4//2,203), t4, fs)
    _dt(dr,(col[2]-w5//2,203), t5, fs)
    dr.line([(20,230),(W-20,230)],fill=BLACK,width=2)
    days=d.get("daily",[])[1:4]; cw=(W-40)//max(len(days),1)
    for i,dd in enumerate(days):
        cx=20+cw*i+cw//2
        _dt(dr,(cx,245),dd["weekday"],fs,anchor="mm")
        _dt(dr,(cx,263),f"{dd['max']}/{dd['min']}",fs,anchor="mm")
        _dt(dr,(cx,283),dd["desc"],fs,anchor="mm")
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img

def _r_newspaper(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    fb=_get_font(15); ft=_get_font(26); fs=_get_font(20)
    fbig=_get_font(58); fsm=_get_font(17); fx=_get_font(16)
    dr.rectangle([0,0,W,48],fill=BLACK)
    _dt(dr,(W//2,14),"WEATHER BULLETIN",fb,fill=WHITE,anchor="mm")
    _dt(dr,(W//2,32),d.get("updated",""),fx,fill=WHITE,anchor="mm")
    dr.rectangle([0,48,W,76],fill=BLACK)
    _dt(dr,(W//2,55),f"{d['city']}",ft,fill=WHITE,anchor="mm")
    dr.rectangle([10,86,195,195])
    bw=dr.textbbox((0,0),str(d['temp']),font=fbig)[2]
    _dt(dr,(102,115),str(d['temp']),fbig,anchor="mm")
    _dt(dr,(102,158),d["desc"],fs,anchor="mm")
    _dt(dr,(102,180),f"体感{d['feels_like']}°",fsm,anchor="mm")
    dr.rectangle([205,86,W-10,195])
    _dt(dr,(300,102),"风力信息",fs,anchor="mm")
    center_x = 300
    text1 = f"风向:{_wind_dir(d['wind_dir'])}"
    bbox1 = dr.textbbox((0,0), text1, font=fsm)
    x1 = center_x - (bbox1[2]-bbox1[0]) / 2
    _dt(dr,(x1, 115), text1, fsm)
    text2 = f"风速:{d['wind_speed']}km/h"
    bbox2 = dr.textbbox((0,0), text2, font=fsm)
    x2 = center_x - (bbox2[2]-bbox2[0]) / 2
    _dt(dr,(x2, 133), text2, fsm)
    if d.get("daily"):
        dd=d["daily"][0]
        text3 = f"日出:{dd.get('sunrise','--')}"
        bbox3 = dr.textbbox((0,0), text3, font=fsm)
        x3 = center_x - (bbox3[2]-bbox3[0]) / 2
        _dt(dr,(x3, 151), text3, fsm)
        text4 = f"日落:{dd.get('sunset','--')}"
        bbox4 = dr.textbbox((0,0), text4, font=fsm)
        x4 = center_x - (bbox4[2]-bbox4[0]) / 2
        _dt(dr,(x4, 169), text4, fsm)
    dr.rectangle([0,203,W,220],fill=BLACK)
    _dt(dr,(W//2,212),"未 来 预 报",fsm,fill=WHITE,anchor="mm")
    days=d.get("daily",[])[1:4]; cw=(W-20)//max(len(days),1)
    for i,dd in enumerate(days):
        x=10+cw*i
        dr.rectangle([x,228,x+cw-6,290])
        _dt(dr,(x+cw//2-3,239),dd["weekday"],fsm,anchor="mm")
        _dt(dr,(x+cw//2-3,258),f"{dd['max']}/{dd['min']}",fsm,anchor="mm")
        _dt(dr,(x+cw//2-3,277),dd["desc"],fx,anchor="mm")
    return img

def _r_dashboard(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    fl=_get_font(16); fbig=_get_font(74); fc=_get_font(20); fsm=_get_font(16); fd=_get_font(20)
    dr.rectangle([0,0,W-1,H-1],outline=BLACK,width=2)
    dr.rectangle([0,0,W,30],fill=BLACK)
    _dt(dr,(W//2,15),d["city"],fc,fill=WHITE,anchor="mm")
    bw=dr.textbbox((0,0),str(d['temp']),font=fbig)[2]
    _dt(dr,(W//2,100),str(d['temp']),fbig,anchor="mm")
    _dt(dr,(W//2,148),d["desc"],fd,anchor="mm")
    for lbl,val,px,py in [("湿度",f"{d['humidity']}%",30,175),("体感",f"{d['feels_like']}°",220,175),
                           ("风速",f"{d['wind_speed']}km/h",30,225),("降水",f"{d['precip']}mm",220,225)]:
        dr.rectangle([px,py,px+150,py+40],outline=BLACK,width=1)
        _dt(dr,(px+75,py+12),lbl,fl,anchor="mm")
        _dt(dr,(px+75,py+30),val,fsm,anchor="mm")
    return img

def _r_minimal(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    fbig=_get_font(96); fsm=_get_font(16); fx=_get_font(15)
    _dt(dr,(W//2,H//2-20),str(d['temp']),fbig,anchor="mm")
    dr.line([(40,H-65),(W-40,H-65)],fill=BLACK)
    _dt(dr,(W//2,H-45),f"{d['city']} · {d['desc']}",fsm,anchor="mm")
    _dt(dr,(W//2,H-22),f"湿度{d['humidity']}% · {_wind_dir(d['wind_dir'])}风{d['wind_speed']}km/h",fx,anchor="mm")
    dr.line([(40,25),(W-40,25)],fill=BLACK)
    _dt(dr,(W//2,12),"WEATHER",fx,anchor="mm")
    return img

def _r_datapanel(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(26); fh=_get_font(19); fsm=_get_font(19); fss=_get_font(17)
    dr.rectangle([0,0,W,36],fill=BLACK)
    _dt(dr,(W//2,18),f"{d['city']} 天气数据面板",ft,fill=WHITE,anchor="mm")
    rows=[("当前温度",f"{d['temp']}°C","体感温度",f"{d['feels_like']}°C"),
          ("相对湿度",f"{d['humidity']}%","紫外指数",str(d.get("uv","未知"))),
          ("穿衣指数",d.get("clothing","未知"),"空气质量",str(d.get("aqi","未知"))),
          ("天气状况",d["desc"],"日出日落",f"{d.get('daily',[{}])[0].get('sunrise','--')} {d.get('daily',[{}])[0].get('sunset','--')}")]
    top_offset=5
    y=44+top_offset
    dr.rectangle([8,y,W-8,y+22],fill=BLACK)
    for j,h in enumerate(["指标","数值","指标","数值"]):
        _dt(dr,(8+(W-16)*(j+.5)//4,y+11),h,fh,fill=WHITE,anchor="mm")
    y+=24
    for i,row in enumerate(rows):
        dr.rectangle([8,y,W-8,y+28],outline=BLACK,width=1)
        dr.line([(W//2,y),(W//2,y+28)],fill=BLACK)
        for j,v in enumerate(row):
            font = fss if (i == 3 and j == 3) else fsm
            _dt(dr,(8+(W-16)*(j+.5)//4,y+14),v,font,anchor="mm")
        y+=30
    bottom_offset=5
    dr.rectangle([8,y+4+bottom_offset,W-8,y+24+bottom_offset],fill=BLACK)
    _dt(dr,(W//2,y+14+bottom_offset),"未来三日预报",fh,fill=WHITE,anchor="mm")
    y+=26+bottom_offset; days=d.get("daily",[])[1:4]; cw=(W-16)//max(len(days),1)
    for i,dd in enumerate(days):
        x=8+cw*i
        right=W-8 if i==len(days)-1 else x+cw-4
        dr.rectangle([x,y,right,y+72],outline=BLACK,width=1)
        _dt(dr,(x+cw//2-2,y+12),dd["weekday"],fsm,anchor="mm")
        _dt(dr,(x+cw//2-2,y+34),f"{dd['max']}/{dd['min']}",fsm,anchor="mm")
        _dt(dr,(x+cw//2-2,y+58),dd["desc"],fsm,anchor="mm")


    return img

def _r_timeline(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(20); fsm=_get_font(15); fx=_get_font(12); ftemp=_get_font(18)
    dr.rectangle([0,0,W,32],fill=BLACK)
    _dt(dr,(W//2,16),f"{d['city']} · 逐时预报",ft,fill=WHITE,anchor="mm")
    top_offset=20
    _dt(dr,(W//2,55+top_offset),f"{d['temp']}° | {d['desc']}",ftemp,anchor="mm")
    dr.line([(30,80+top_offset),(W-30,80+top_offset)],fill=BLACK,width=2)
    hours=d.get("hourly",[])[:8]
    if hours:
        sw=(W-60)//len(hours)
        for i,h in enumerate(hours):
            cx=30+sw*i+sw//2
            dr.ellipse([cx-4,76+top_offset,cx+4,84+top_offset],fill=BLACK)
            _dt(dr,(cx,98+top_offset),h["time"],fsm,anchor="mm")
            _dt(dr,(cx,113+top_offset),f"{h['temp']}°",fsm,anchor="mm")
    bottom_y=165
    y=bottom_y+20; days=d.get("daily",[])[1:4]; cw=(W-20)//max(len(days),1)
    for i,dd in enumerate(days):
        x=10+cw*i; right=W-10 if i==len(days)-1 else x+cw-8
        dr.rectangle([x,y,right,y+55],outline=BLACK,width=1)
        _dt(dr,(x+cw//2-4,y+12),dd["weekday"],fsm,anchor="mm")
        _dt(dr,(x+cw//2-4,y+30),f"{dd['max']}/{dd['min']}",fsm,anchor="mm")
        _dt(dr,(x+cw//2-4,y+45),dd["desc"],fx,anchor="mm")
    return img

def _r_postcard(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(22); fsm=_get_font(14); fx=_get_font(15)
    fx_date=_get_font(15)

    _dt(dr,(W//2,40),d["city"],ft,anchor="mm")
    _dt(dr,(W//2,95),str(d['temp']),_get_font(72),anchor="mm")
    _dt(dr,(W//2,160),d["desc"],fsm,anchor="mm")
    _dt(dr,(W//2,185),f"{d['daily'][0]['date']} {d['daily'][0]['weekday']}",fx_date,anchor="mm")
    dr.line([(40,205),(W-40,205)],fill=BLACK)

    right_offset = 15
    y=223
    for dd in d.get("daily",[])[1:4]:
        dr.rectangle([20,y,W-20,y+18],outline=BLACK,width=1)
        _dt(dr,(30+right_offset,y+10),f"{dd['weekday']}",fx,anchor="lm")
        _dt(dr,(280+right_offset,y+10),f"{dd['max']}°/{dd['min']}°",fx,anchor="lm")
        y+=22
    return img

def _r_terminal(d):
    img = Image.new("L", (W, H), BLACK); dr = ImageDraw.Draw(img)
    ft=_get_font(20); fsm=_get_font(20)
    dr.rectangle([2,2,W-3,26],fill=BLACK)
    _dt(dr,(W//2,14),"Terminal",fsm,fill=WHITE,anchor="mm")
    # Ubuntu风格按钮
    btn_y=14; btn_spacing=22; btn_start_x=W-60; r=8
    for i, btn_type in enumerate(["minimize","maximize","close"]):
        cx=btn_start_x+i*btn_spacing
        dr.ellipse([cx-r,btn_y-r,cx+r,btn_y+r],outline=WHITE,width=1)
        if btn_type=="close":
            dr.line([(cx-3,btn_y-3),(cx+3,btn_y+3)],fill=WHITE,width=1)
            dr.line([(cx-3,btn_y+3),(cx+3,btn_y-3)],fill=WHITE,width=1)
        elif btn_type=="minimize":
            dr.line([(cx-3,btn_y),(cx+3,btn_y)],fill=WHITE,width=1)
        elif btn_type=="maximize":
            dr.rectangle([cx-3,btn_y-3,cx+3,btn_y+3],outline=WHITE,width=1)
    lines=[f"$ weather --city {d['city']}",
           f"> 温度: {d['temp']}C (体感{d['feels_like']}C)",
           f"> 天气: {d['desc']} [{d['weather_code']}]",
           f"> 湿度: {d['humidity']}%",
           f"> 风速: {d['wind_speed']}km/h ({_wind_dir(d['wind_dir'])})",
           f"> 降水: {d['precip']}mm",
           f"$ forecast --days 3"]
    y=32
    for l in lines: _dt(dr,(8,y),l,ft,fill=WHITE); y+=24
    for dd in d.get("daily",[])[1:4]:
        _dt(dr,(8,y),f"> [{dd['weekday']}] {dd['desc']} {dd['max']}C/{dd['min']}C",fsm,fill=WHITE); y+=22
    _dt(dr,(8,H-30),"$",ft,fill=WHITE)
    return img


# ============================================================
# 图片渲染 - 日程 ×3
# ============================================================
def _r_schedule_grid(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(22); fsm=_get_font(20); fx=_get_font(20)
    dr.rectangle([0,0,W,36],fill=BLACK)
    _dt(dr,(W//2,18),d.get("title","今日日程"),ft,fill=WHITE,anchor="mm")
    _dt(dr,(W//2,50),f"{d.get('date','')} {d.get('weekday','')}",fsm,anchor="mm")
    y=66
    col_w = (W-20)//3
    col_x1 = 10
    col_x2 = col_x1 + 70  # 时间列固定70宽
    col_x3 = W - 10 - col_w  # 地点列保持原宽度
    dr.rectangle([10,y,W-10,y+22],fill=BLACK)
    _dt(dr,(col_x1+35,y+10),"时间",fsm,fill=WHITE,anchor="mm")
    _dt(dr,(col_x2+(col_x3-col_x2)//2,y+10),"事项",fsm,fill=WHITE,anchor="mm")
    _dt(dr,(col_x3+col_w//2,y+10),"地点",fsm,fill=WHITE,anchor="mm")
    y+=24
    dr.line([(col_x2,y),(col_x2,H-10)],fill=BLACK)
    dr.line([(col_x3,y),(col_x3,H-10)],fill=BLACK)
    slots=d.get("slots",[]); rh=(H-y-15)//max(len(slots[:8]),1)
    for i,sl in enumerate(slots[:8]):
        ry=y+rh*i
        # 只显示开始时间，不显示结尾时间
        time_str = sl.get("time","")
        start_time = time_str.split("-")[0] if time_str else ""
        _dt(dr,(col_x1+35,ry+rh//2-1),start_time,fx,anchor="mm")
        _dt(dr,(col_x2+(col_x3-col_x2)//2,ry+rh//2-1),sl.get("event",""),fsm,anchor="mm")
        loc=sl.get("location","")
        if loc: _dt(dr,(col_x3+col_w//2,ry+rh//2-1),loc,fx,anchor="mm")
        if i<len(slots[:8])-1: dr.line([(10,ry+rh),(W-10,ry+rh)],fill=BLACK)
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img

def _r_course_table(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(20); fsm=_get_font(18); fx=_get_font(16)
    dr.rectangle([0,0,W,32],fill=BLACK)
    _dt(dr,(W//2,16),d.get("title","今日课程"),ft,fill=WHITE,anchor="mm")
    y=40; cols=[10,55,200,265,340]
    col_w=[45,145,65,75,60]
    dr.rectangle([10,y,W-10,y+20],fill=BLACK)
    for j,h in enumerate(["节次","课程","时间","教室","教师"]):
        _dt(dr,(cols[j]+col_w[j]//2,y+10),h,fx,fill=WHITE,anchor="mm")
    y+=22; courses=d.get("courses",[]); rh=(H-y-10)//max(len(courses),1)
    for i,c in enumerate(courses[:8]):
        ry=y+rh*i
        _dt(dr,(cols[0]+col_w[0]//2,ry+rh//2),c.get("period",""),fsm,anchor="mm")
        _dt(dr,(cols[1]+col_w[1]//2,ry+rh//2),c.get("name",""),fsm,anchor="mm")
        t=c.get("time","")
        if t and "-" in t:
            s,e=t.split("-",1)
            _dt(dr,(cols[2]+col_w[2]//2,ry+rh//2-9),s,fx,anchor="mm")
            _dt(dr,(cols[2]+col_w[2]//2,ry+rh//2+9),e,fx,anchor="mm")
        else:
            _dt(dr,(cols[2]+col_w[2]//2,ry+rh//2),t,fx,anchor="mm")
        _dt(dr,(cols[3]+col_w[3]//2,ry+rh//2),c.get("room",""),fx,anchor="mm")
        _dt(dr,(cols[4]+col_w[4]//2,ry+rh//2),c.get("teacher",""),fx,anchor="mm")
        if i<len(courses[:8])-1: dr.line([(10,ry+rh),(W-10,ry+rh)],fill=BLACK)
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img



def _r_progress(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(20); fsm=_get_font(15); fx=_get_font(13); fbig=_get_font(48)
    dr.rectangle([0,0,W,36],fill=BLACK)
    _dt(dr,(W//2,18),d.get("title","进度概览"),ft,fill=WHITE,anchor="mm")
    total=d.get("total_progress",0)
    _dt(dr,(80,80),f"{total}%",fbig,anchor="mm")
    bx,by,bw,bh=140,60,220,20
    dr.rectangle([bx,by,bx+bw,by+bh],outline=BLACK,width=2)
    fw=int(bw*total/100)
    if fw>0: dr.rectangle([bx+2,by+2,bx+fw,by+bh-2],fill=BLACK)
    _dt(dr,(W//2,100),d.get("summary",""),fsm,anchor="mm")
    items=d.get("items",[]); y=120; ih=(H-y-10)//max(len(items[:6]),1)
    for i,item in enumerate(items[:6]):
        ry=y+ih*i
        _dt(dr,(15,ry+7),item.get("label",""),fsm)
        _dt(dr,(W-34,ry+7),item.get("detail",""),fx,anchor="rt")
        bx2,by2,bw2,bh2=15,ry+22,W-50,10
        dr.rectangle([bx2,by2,bx2+bw2,by2+bh2],outline=BLACK,width=1)
        pct=item.get("progress",0); fw2=int(bw2*pct/100)
        if fw2>0: dr.rectangle([bx2+1,by2+1,bx2+fw2,by2+bh2-1],fill=GRAY_DARK)
        _dt(dr,(bx2+bw2+5,by2-2),f"{pct} %",fx)
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img


# ============================================================
# 图片渲染 - 新闻 ×2
# ============================================================
def _r_headline(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(22); fsm=_get_font(16); fx=_get_font(14); fcat=_get_font(19)
    dr.rectangle([0,0,W,38],fill=BLACK)
    _dt(dr,(W//2,12),d.get("title","每日新闻"),ft,fill=WHITE,anchor="mm")
    _dt(dr,(W//2,30),f"{d.get('date','')} | {d.get('category','综合')}",fx,fill=WHITE,anchor="mm")
    items=d.get("items",[])
    if not items:
        _dt(dr,(W//2,H//2),"暂无新闻",fsm,anchor="mm"); return img
    top=items[0]
    _dt(dr,(15,42),top.get("headline","")[:28],fcat)
    sl=_wrap_text(top.get("summary","")[:80],fx,W-30,dr)
    for j,l in enumerate(sl[:2]): _dt(dr,(15,68+j*16),l,fx,fill=BLACK)
    dr.line([(15,103),(W-15,103)],fill=BLACK,width=2)
    y=108; rh=38  # 行高38，每行可容纳标题+摘要
    for i,item in enumerate(items[1:6]):
        ry=y+rh*i
        # 圆圈和数字垂直居中于该行
        dr.ellipse([15,ry+8,31,ry+24],outline=BLACK,width=1)  # 圆圈居中
        _dt(dr,(25,ry+16),str(i+1),fx,anchor="mm")  # 数字在圆圈中心，右移2px
        _dt(dr,(40,ry+2),item.get("headline","")[:22],fsm)  # 标题，上移3px
        _dt(dr,(40,ry+18),item.get("summary","")[:32],fx,fill=BLACK)  # 摘要，上移3px
        if i<len(items[1:6])-1: dr.line([(12,ry+rh),(W-12,ry+rh)],fill=BLACK,width=1)
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img

def _r_ticker(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    fsm=_get_font(13); fx=_get_font(11); ftag=_get_font(10)
    dr.rectangle([0,0,W,4],fill=BLACK); dr.rectangle([0,4,W,30],fill=BLACK)
    _dt(dr,(W//2,17),f"{d.get('title','NEWS')} | {d.get('date','')}",fsm,fill=WHITE,anchor="mm")
    items=d.get("items",[]); y=38; ih=min((H-48)//max(len(items[:8]),1),32)
    for i,item in enumerate(items[:8]):
        ry=y+ih*i; tag=item.get("tag","")
        if tag:
            tw=len(tag)*8+10
            dr.rectangle([12,ry+2,12+tw,ry+16],fill=BLACK)
            _dt(dr,(12+tw//2,ry+5),tag,ftag,fill=WHITE,anchor="mm")
            hx=18+tw
        else: hx=15
        _dt(dr,(hx,ry+3),item.get("headline","")[:36],fsm)
        if i<len(items[:8])-1: dr.line([(12,ry+ih),(W-12,ry+ih)],fill=GRAY_LIGHT)
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img


# ============================================================
# 图片渲染 - 自定义 ×4
# ============================================================
def _r_quote(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    fsm=_get_font(20); fx=_get_font(15)
    _dt(dr,(20,15),"\u300c",_get_font(60),fill=GRAY_LIGHT)
    _dt(dr,(W-55,H-80),"\u300d",_get_font(60),fill=GRAY_LIGHT)
    quote=d.get("quote",""); lines=_wrap_text(quote,fsm,W-80,dr)
    th=len(lines)*26; sy=(H-th)//2-10
    for i,l in enumerate(lines): _dt(dr,(W//2,sy+i*26),l,fsm,anchor="mm")
    author=d.get("author","")
    if author: _dt(dr,(W//2,sy+len(lines)*26+18),f"- {author}",fx,anchor="mm",fill=GRAY_DARK)
    _dt(dr,(W//2,H-18),d.get("date",""),fx,anchor="mm",fill=GRAY_MID)
    dr.rectangle([10,10,W-10,H-10],outline=GRAY_MID)
    return img

def _r_memo(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(20); fsm=_get_font(18); fx=_get_font(15); ftag=_get_font(12)
    dr.rectangle([0,0,W,36],fill=BLACK)
    dr.polygon([(0,36),(20,36),(0,52)],fill=BLACK)
    _dt(dr,(W//2,18),d.get("title","备忘"),ft,fill=WHITE,anchor="mm")
    _dt(dr,(W-15,48),d.get("date",""),fx,anchor="rt",fill=BLACK)
    content=d.get("content",""); lines=_wrap_text(content,fsm,W-40,dr); y=58
    for i,l in enumerate(lines[:12]):
        if y>H-40: _dt(dr,(20,y),"...",fsm,fill=BLACK); break
        _dt(dr,(20,y),l,fsm); y+=22
    tags=d.get("tags",[])
    if tags:
        # 双线分隔
        dr.line([(10,y+8),(W-10,y+8)],fill=BLACK)
        dr.line([(10,y+12),(W-10,y+12)],fill=BLACK)
        tx=20; ty=y+20
        for tag in tags[:5]:
            tw=len(tag)*7+14
            if tx+tw>W-15: break
            dr.rectangle([tx,ty,tx+tw,ty+18],outline=BLACK)
            _dt(dr,(tx+tw//2,ty+9),tag,ftag,anchor="mm")
            tx+=tw+6
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img



def _r_greeting(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    fbig=_get_font(62); fsm=_get_font(18); fx=_get_font(14)
    # 外框
    dr.rectangle([6,6,W-6,H-6],outline=BLACK,width=2)
    dr.rectangle([12,12,W-12,H-12],outline=BLACK)
    # 四角装饰
    for cx,cy in [(0,0),(W,0),(0,H),(W,H)]:
        dx=1 if cx==0 else -1; dy=1 if cy==0 else -1
        dr.line([(cx,cy+dy*24),(cx,cy),(cx+dx*24,cy)],fill=BLACK,width=2)
        dr.line([(cx+dx*8,cy+dy*8),(cx+dx*20,cy+dy*20)],fill=BLACK,width=1)
    # 顶部装饰线
    dr.line([(40,45),(W-40,45)],fill=BLACK,width=1)
    for x in range(60,W-60,20):
        dr.ellipse([x-2,43,x+2,47],fill=BLACK)
    # 祝福语
    _dt(dr,(W//2,100),d.get("greeting",""),fbig,anchor="mm")
    # 分隔装饰
    dr.line([(80,125),(W-80,125)],fill=BLACK,width=1)
    dr.line([(100,129),(W-100,129)],fill=BLACK,width=1)
    dr.ellipse([W//2-4,123,W//2+4,131],fill=BLACK)
    # 消息
    message=d.get("message",""); lines=_wrap_text(message,fsm,W-60,dr)
    for i,l in enumerate(lines[:5]): _dt(dr,(W//2,150+i*24),l,fsm,anchor="mm")
    # 底部装饰线
    dr.line([(40,H-50),(W-40,H-50)],fill=BLACK,width=1)
    for x in range(60,W-60,20):
        dr.ellipse([x-2,H-52,x+2,H-48],fill=BLACK)
    # 日期
    _dt(dr,(W//2,H-30),d.get("date",""),fx,anchor="mm",fill=BLACK)
    # 底部角花
    for x in range(30,W-30,40):
        dr.line([(x,H-15),(x+5,H-20)],fill=BLACK,width=1)
        dr.line([(x,H-15),(x-5,H-20)],fill=BLACK,width=1)
    return img


def _r_list(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(20); fsm=_get_font(16); fx=_get_font(16)
    dr.rectangle([0,0,W,36],fill=BLACK)
    _dt(dr,(W//2,18),d.get("title","列表"),ft,fill=WHITE,anchor="mm")
    _dt(dr,(W-15,50),d.get("date",""),fx,anchor="rt",fill=BLACK)
    items=d.get("items",[]); y=55
    for i,item in enumerate(items[:9]):
        ry=y+i*26
        dr.ellipse([15,ry+1,37,ry+23],outline=BLACK)
        _dt(dr,(26,ry+12),str(i+1),fx,anchor="mm")
        _dt(dr,(46,ry+12),str(item)[:36],fsm,anchor="lm")
    footer=d.get("footer","")
    if footer:
        dr.rectangle([0,H-28,W,H],fill=BLACK)
        _dt(dr,(W//2,H-14),footer[:40],fx,fill=WHITE,anchor="mm")
    else:
        dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img



# ============================================================
# 图片渲染 - 聚合 ×2
# ============================================================
def _r_daily_summary(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(20); fsm=_get_font(15); fx=_get_font(13); fbig=_get_font(44)
    dr.rectangle([0,0,W,32],fill=BLACK)
    _dt(dr,(W//2,16),f"每日总览 | {d.get('date','')}",ft,fill=WHITE,anchor="mm")
    y=40; weather=d.get("weather"); todos=d.get("todos")
    if weather:
        dr.rectangle([8,y,W//2-4,140],outline=BLACK)
        _dt(dr,(W//4,y+14),f"{weather.get('city','')} 天气",fsm,anchor="mm")
        _dt(dr,(W//4,y+45),str(weather.get('temp',0)),fbig,anchor="mm")
        _dt(dr,(W//4,y+80),weather.get("desc",""),fsm,anchor="mm")
    if todos:
        rx=W//2+4; dr.rectangle([rx,y,W-8,140],outline=BLACK)
        _dt(dr,(rx+(W//2-12)//2,y+14),"今日待办",fsm,anchor="mm")
        ty=y+21
        for i,t in enumerate(todos[:4]):
            mark="[x]" if t.get("done") else "[ ]"
            _dt(dr,(rx+8,ty),f"{mark} {t.get('text','')[:16]}",fx); ty+=20
    y=148; dr.line([(10,y+2),(W-10,y+2)],fill=BLACK); y+=10
    nb=d.get("news_brief","")
    if nb:
        _dt(dr,(15,y),"新闻速览:",fsm); y+=20
        for l in _wrap_text(nb[:120],fx,W-30,dr)[:3]:
            _dt(dr,(15,y),l,fx,fill=BLACK); y+=18
    q=d.get("quote","")
    if q:
        dr.line([(10,y+2),(W-10,y+2)],fill=BLACK); y+=20
        for l in _wrap_text(q,fsm,W-30,dr)[:2]:
            _dt(dr,(W//2,y),l,fsm,anchor="mm",fill=BLACK); y+=30
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img


def _r_split_panel(d):
    img = Image.new("L", (W, H), WHITE); dr = ImageDraw.Draw(img)
    ft=_get_font(18); fx=_get_font(15)
    dr.rectangle([0,0,W,28],fill=BLACK)
    _dt(dr,(W//2,14),d.get("date",""),ft,fill=WHITE,anchor="mm")
    panels=[d.get("left",{}), d.get("center",{}), d.get("right",{})]
    cw=(W-20)//3
    for i,p in enumerate(panels):
        x=10+cw*i
        dr.rectangle([x,34,x+cw-4,52],fill=BLACK)
        _dt(dr,(x+cw//2-2,43),p.get("title",""),fx,fill=WHITE,anchor="mm")
        y=58
        for l in p.get("lines",[])[:10]:
            if y>H-15: break
            _dt(dr,(x+4,y),str(l)[:16],fx); y+=18
        if i<2: dr.line([(x+cw-2,34),(x+cw-2,H-5)],fill=GRAY_LIGHT)
    dr.rectangle([0,H-4,W,H],fill=BLACK)
    return img


# ============================================================
# 模板注册表
# ============================================================
TEMPLATES = {
    # 天气
    "classic":    ("经典简约", "天气", _r_classic),
    "newspaper":  ("报纸风格", "天气", _r_newspaper),
    "dashboard":  ("仪表盘", "天气", _r_dashboard),
    "minimal":    ("极简主义", "天气", _r_minimal),
    "datapanel":  ("数据面板", "天气", _r_datapanel),
    "timeline":   ("时间轴", "天气", _r_timeline),
    "postcard":   ("明信片", "天气", _r_postcard),
    "terminal":   ("终端风格", "天气", _r_terminal),
    # 日程
    "schedule_grid":  ("日程表格", "日程", _r_schedule_grid),
    "course_table":   ("课程表", "日程", _r_course_table),
    "progress":       ("进度概览", "日程", _r_progress),
    # 新闻
    "headline":  ("头条摘要", "新闻", _r_headline),
    "ticker":    ("滚动条", "新闻", _r_ticker),
    # 自定义
    "quote":    ("名言金句", "自定义", _r_quote),
    "memo":     ("备忘录", "自定义", _r_memo),
    "greeting": ("贺卡问候", "自定义", _r_greeting),
    "list":     ("列表", "自定义", _r_list),
    # 聚合
    "daily_summary": ("每日总览", "聚合", _r_daily_summary),
    "split_panel":   ("分屏面板", "聚合", _r_split_panel),
}


# ============================================================
# 插件主类
# ============================================================
@register(
    "astrbot_plugin_daily_card",
    "daily-card",
    "LLM驱动的每日卡片图片生成插件 - 天气/日程/新闻/自定义/聚合，纯黑白400x300",
    "1.5.2",
    "https://github.com/zhangkai542322/astrbot_plugin_daily_card",
)
class DailyCardPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.default_city = config.get("default_city", "北京")
        self.weather_template = config.get("weather_template", "classic")
        self.todo_template = config.get("todo_template", "schedule_grid")
        self.news_template = config.get("news_template", "headline")
        self.custom_template = config.get("custom_template", "memo")
        self.combined_template = config.get("combined_template", "daily_summary")
        self.temp_unit = config.get("temp_unit", "celsius")

        # 初始化字体系统
        global _CUSTOM_FONT_PATH, _FONT_DIR
        _FONT_DIR = config.get("font_dir", "/AstrBot/data/fonts")
        _CUSTOM_FONT_PATH = config.get("font_filename", "")
        _clear_font_cache()

        font_path = _find_available_font()
        if font_path:
            logger.info(f"DailyCard 字体: {font_path}")
        else:
            logger.warning("DailyCard: 未找到中文字体。插件自带字体也不存在，请检查 assets/font.ttf")

    # ==========================
    # 命令
    # ==========================
    @filter.command_group("daily_card")
    def dc_group(self):
        pass

    @dc_group.command("templates")
    async def cmd_templates(self, event: AstrMessageEvent):
        """列出所有可用的卡片模板"""
        groups = {}
        for k, (name, cat, _) in TEMPLATES.items():
            groups.setdefault(cat, []).append((k, name))
        lines = ["可用卡片模板:"]
        for cat, items in groups.items():
            lines.append(f"\n[{cat}]")
            for k, n in items:
                lines.append(f"  {k:16s} - {n}")
        lines.append(f"\n共 {len(TEMPLATES)} 款模板")
        yield event.plain_result("\n".join(lines))

    @dc_group.command("font")
    async def cmd_font(self, event: AstrMessageEvent):
        """查看当前字体使用情况"""
        info = get_font_info()
        yield event.plain_result(f"字体信息:\n{info}")

    @dc_group.command("setfont")
    async def cmd_setfont(self, event: AstrMessageEvent, filename: str = ""):
        """设置字体文件名。用法: /daily_card setfont font.ttf
        字体应放在 /AstrBot/data/fonts/ 目录下"""
        global _CUSTOM_FONT_PATH
        if not filename:
            yield event.plain_result(
                "用法: /daily_card setfont <字体文件名>\n"
                f"字体目录: {_FONT_DIR}\n"
                "示例: /daily_card setfont NotoSansCJK-Regular.ttc\n\n"
                "或在 WebUI → 插件设置中修改 font_dir 和 font_filename"
            )
            return
        resolved = _resolve_font_path(filename)
        if not resolved:
            yield event.plain_result(
                f"找不到字体: {filename}\n\n"
                f"请确认文件已放到 {_FONT_DIR}/ 目录下\n"
                "支持格式: .ttf .ttc .otf"
            )
            return
        _CUSTOM_FONT_PATH = filename
        _clear_font_cache()
        yield event.plain_result(f"字体设置成功: {resolved}\n字体缓存已刷新，所有图片生成将使用此字体。")

    # ==========================
    # LLM 工具集
    # ==========================

    @filter.llm_tool(name="get_weather_card")
    async def tool_weather(self, event: AstrMessageEvent, city: str = "", template: str = "") -> MessageEventResult:
        '''生成天气图片卡片（支持中文）。触发词：查天气、天气怎么样、北京天气、明天天气。

用法：get_weather_card(city="北京")，不填城市用默认。直接返回图片。

Args:
            city(string): 城市名，不填用默认
            template(string): 可选: classic(经典), newspaper(报纸), dashboard(仪表盘), minimal(极简), datapanel(数据面板), timeline(时间轴), postcard(明信片), terminal(终端)
        '''
        city = city or self.default_city
        template = template or self.weather_template
        if template not in TEMPLATES:
            template = "classic"
        geo = await _geocode(city)
        if not geo:
            yield event.plain_result(f"找不到城市 '{city}'")
            return
        display = f"{geo['admin1']} {geo['name']}" if geo.get("admin1") else geo["name"]
        raw = await _fetch_weather(geo["lat"], geo["lon"], self.temp_unit)
        if not raw:
            yield event.plain_result(f"查询天气失败")
            return
        data = _parse_weather(raw, display)
        if not data:
            yield event.plain_result(f"解析天气数据失败")
            return
        _, _, renderer = TEMPLATES[template]
        img = renderer(data)
        path = _save(img, "weather")
        yield event.image_result(path)
        yield event.plain_result(f"天气卡片已生成。图片路径: {path}。可调用 push_image_to_device 推送到设备。")

    @filter.llm_tool(name="get_schedule_card")
    async def tool_schedule(self, event: AstrMessageEvent, data_json: str, template: str = "") -> MessageEventResult:
        '''生成日程/课程表/进度图片卡片（支持中文）。触发词：生成日程图、今日日程、课程表、进度图。

用法：get_schedule_card(data_json='{"title":"今日日程","date":"2026-04-13","weekday":"周二","slots":[{"time":"09:00","event":"开会","location":"会议室"}]}', template="schedule_grid")。直接返回图片。

Args:
            data_json(string): 
【schedule_grid 日程表格】title: 标题, date: 日期, weekday: 周几, slots: [{"time":"09:00-10:00","event":"开会","location":"会议室"}]
【course_table 课程表】title: 标题, courses: [{"period":"1","name":"数学","room":"A101","teacher":"张老师","time":"08:00-09:40"}]
【progress 进度概览】title: 标题, total_progress: 总进度(0-100), items: [{"label":"任务名","detail":"描述","progress":90}]
            template(string): 可选: schedule_grid(日程表格), course_table(课程表), progress(进度概览)
        '''
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            yield event.plain_result("数据格式错误，请传入有效JSON")
            return
        if template not in TEMPLATES:
            template = self.todo_template
        _, _, renderer = TEMPLATES[template]
        img = renderer(data)
        path = _save(img, "schedule")
        yield event.image_result(path)
        yield event.plain_result(f"日程卡片已生成。图片路径: {path}。可调用 push_image_to_device 推送到设备。")

    @filter.llm_tool(name="get_news_card")
    async def tool_news(self, event: AstrMessageEvent, data_json: str, template: str = "") -> MessageEventResult:
        '''生成新闻图片卡片（支持中文）。触发词：今日新闻、新闻摘要、生成新闻图。

用法：get_news_card(data_json='{"title":"每日新闻","date":"2026-04-13","items":[{"headline":"标题","summary":"摘要"}]}', template="headline")。直接返回图片。

Args:
            data_json(string): 
【headline 头条摘要】title: 标题, date: 日期, category: 分类, items: [{"headline":"标题","summary":"摘要"}]
【ticker 滚动条】title: 标题, date: 日期, items: [{"headline":"标题","tag":"标签"}]
            template(string): 可选: headline(头条摘要), ticker(滚动条)
        '''
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            yield event.plain_result("数据格式错误，请传入有效JSON")
            return
        if template not in TEMPLATES:
            template = self.news_template
        _, _, renderer = TEMPLATES[template]
        img = renderer(data)
        path = _save(img, "news")
        yield event.image_result(path)
        yield event.plain_result(f"新闻卡片已生成。图片路径: {path}。可调用 push_image_to_device 推送到设备。")

    @filter.llm_tool(name="get_custom_card")
    async def tool_custom(self, event: AstrMessageEvent, data_json: str, template: str = "") -> MessageEventResult:
        '''生成自定义图片卡片（支持中文）。触发词：生成贺卡、早安问候、名言警句、备忘录、生成列表图。

用法：get_custom_card(data_json='{"greeting":"早安","message":"新的一天加油"}', template="greeting")。直接返回图片。

Args:
            data_json(string): 
【quote 名言】quote: 名言内容, author: 作者, date: 日期
【memo 备忘录】title: 标题, content: 内容, tags: ["标签1"], date: 日期
【greeting 贺卡】greeting: 祝福语(必填), message: 详细消息, date: 日期
【list 列表】title: 标题, items: ["项目1","项目2"], date: 日期, footer: 底部备注
            template(string): 可选: quote(名言), memo(备忘录), greeting(贺卡), list(列表)
        '''
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            yield event.plain_result("数据格式错误，请传入有效JSON")
            return
        if template not in TEMPLATES:
            template = self.custom_template
        _, _, renderer = TEMPLATES[template]
        img = renderer(data)
        path = _save(img, "custom")
        yield event.image_result(path)
        yield event.plain_result(f"自定义卡片已生成。图片路径: {path}。可调用 push_image_to_device 推送到设备。")

    @filter.llm_tool(name="get_combined_card")
    async def tool_combined(self, event: AstrMessageEvent, data_json: str, template: str = "") -> MessageEventResult:
        '''生成聚合图片卡片（天气+日程合并等，支持中文）。触发词：生成汇总图、今日总览、天气+日程合并。

用法：get_combined_card(data_json='{"date":"2026-04-13","weather":{"city":"北京","temp":22,"desc":"晴"},"todos":[{"text":"写代码","done":false}]}')。直接返回图片。

Args:
            data_json(string): 
【daily_summary 每日总览】date: 日期, weather: {"city":"城市","temp":22,"desc":"描述"}, todos: [{"text":"任务","done":false}], news_brief: 新闻内容, quote: 名言
【split_panel 分屏面板】date: 日期, left/center/right: {"title":"标题","lines":["项目1","项目2"]}
            template(string): 可选: daily_summary(每日总览), split_panel(分屏面板)
        '''
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            yield event.plain_result("数据格式错误，请传入有效JSON")
            return
        if template not in TEMPLATES:
            template = self.combined_template
        _, _, renderer = TEMPLATES[template]
        img = renderer(data)
        path = _save(img, "combined")
        yield event.image_result(path)
        yield event.plain_result(f"聚合卡片已生成。图片路径: {path}。可调用 push_image_to_device 推送到设备。")

    @filter.llm_tool(name="list_card_templates")
    async def tool_list_templates(self, event: AstrMessageEvent, category: str = "") -> MessageEventResult:
        '''列出所有可用的卡片模板及其类别。

Args:
            category(string): 按类别过滤。可选: 天气, 日程, 新闻, 自定义, 聚合。不填则列出全部
        '''
        groups = {}
        for k, (name, cat, _) in TEMPLATES.items():
            groups.setdefault(cat, []).append(f"  {k}: {name}")
        lines = []
        if category and category in groups:
            lines.append(f"[{category}]")
            lines.extend(groups[category])
        else:
            for cat, items in groups.items():
                lines.append(f"[{cat}]")
                lines.extend(items)
                lines.append("")
        lines.append(f"共 {len(TEMPLATES)} 款模板")
        yield event.plain_result("\n".join(lines))
