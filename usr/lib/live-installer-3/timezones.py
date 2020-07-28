#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

import math
import re
from utils import getoutput
from collections import defaultdict, namedtuple
from datetime import datetime, timedelta
from PIL import Image, ImageEnhance, ImageChops, ImageOps
from functools import reduce


TIMEZONE_RESOURCES = '/usr/share/live-installer-3/timezone/'
CC_IM = Image.open(TIMEZONE_RESOURCES + 'cc.png').convert('RGB')
BACK_IM = Image.open(TIMEZONE_RESOURCES + 'bg.png').convert('RGB')
BACK_ENHANCED_IM = reduce(lambda im, mod: mod[0](im).enhance(mod[1]),
                          ((ImageEnhance.Color, 2),
                           (ImageEnhance.Contrast, 1.3),
                           (ImageEnhance.Brightness, 0.7)), BACK_IM)
NIGHT_IM = Image.open(TIMEZONE_RESOURCES + 'night.png').convert('RGBA')
LIGHTS_IM = Image.open(TIMEZONE_RESOURCES + 'lights.png').convert('RGBA')
DOT_IM = Image.open(TIMEZONE_RESOURCES + 'dot.png').convert('RGBA')


def to_float(position, wholedigits):
    assert position and len(position) > 4 and wholedigits < 9
    return float(position[:wholedigits + 1] + '.' + position[wholedigits + 1:])

MAP_CENTER = (373, 263)  # pixel center of where equatorial line and 0th meridian cross on our bg map; WARNING: cc.png relies on this exactly!
MAP_SIZE = BACK_IM.size  # size of the map image
assert MAP_SIZE == (800, 409), 'MAP_CENTER (et al.?) calculations depend on this size'


def pixel_position(lat, lon):
    """Transform latlong pair into map pixel coordinates"""
    dx = MAP_SIZE[0] / 2 / 180
    dy = MAP_SIZE[1] / 2 / 90
    # formulae from http://en.wikipedia.org/wiki/Miller_cylindrical_projection
    x = MAP_CENTER[0] + dx * lon
    y = MAP_CENTER[1] - dy * math.degrees(5 / 4 * math.log(math.tan(math.pi / 4 + 2 / 5 * math.radians(lat))))
    return int(x), int(y)

TZ_SPLIT_COORDS = re.compile('([+-][0-9]+)([+-][0-9]+)')

timezones = []

Timezone = namedtuple('Timezone', 'name ccode x y'.split())


def build_timezones(_installer):
    global installer, time_label, time_label_box, timezone
    installer = _installer
    # Add the label displaying current time
    time_label = installer.go("label_time")
    time_label_box = time_label.get_parent()
    
    # DeprecationWarning: Gtk.Widget.modify_bg is deprecated:
    #time_label_box.modify_bg(Gtk.StateType.NORMAL, Gdk.color_parse('#000'))
    #time_label.modify_fg(Gtk.StateType.NORMAL, Gdk.color_parse('#fff'))
    
    # Use CSS instead:
    # In case of external css file:
    #css_provider.load_from_path("style.css")
    # In case of css for whole window:
    #Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    # Reference:
    # https://developer.gnome.org/gtk3/stable/GtkCssProvider.html
    # https://developer.gnome.org/gtk3/stable/GtkStyleContext.html
    # https://developer.gnome.org/gtk3/stable/GtkStyleProvider.html
    css = b""".timelabel {
    background-color: #000;
    color: #fff;
}"""
    css_provider = Gtk.CssProvider()
    css_provider.load_from_data(css)
    style_context = time_label_box.get_style_context()
    style_context.add_class("timelabel")
    style_context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    
    GLib.timeout_add(200, update_local_time_label)
    # Populate timezones model
    installer.go("image_timezones").set_from_file(TIMEZONE_RESOURCES + 'bg.png')

    def autovivified():
        return defaultdict(autovivified)

    hierarchy = autovivified()

    for line in getoutput("awk '/^[^#]/{ print $1,$2,$3 }' /usr/share/zoneinfo/zone.tab | sort -k3", always_as_list=True):
        ccode, coords, name = line.split()
        lat, lon = TZ_SPLIT_COORDS.search(coords).groups()
        x, y = pixel_position(to_float(lat, 2), to_float(lon, 3))
        if x < 0:
            x = MAP_SIZE[0] + x
        tup = Timezone(name, ccode, x, y)
        submenu = hierarchy
        parts = name.split('/')
        for i, part in enumerate(parts, 1):
            if i != len(parts):
                submenu = submenu[part]
            else:
                submenu[part] = tup
        timezones.append(tup)

    def _build_menu(d):
        menu = Gtk.Menu()
        for k in sorted(d):
            v = d[k]
            item = Gtk.MenuItem(label=k)
            item.show()
            if isinstance(v, dict):
                item.set_submenu(_build_menu(v))
            else:
                item.connect('activate', cb_menu_selected, v)
            menu.append(item)
        menu.show()
        return menu
    tz_menu = _build_menu(hierarchy)
    tz_menu.show()
    installer.go('button_timezones').connect('event', cb_button_timezoens, tz_menu)

adjust_time = timedelta(0)


def update_local_time_label():
    now = datetime.utcnow() + adjust_time
    time_label.set_label(now.strftime('%X'))
    return True


def cb_button_timezoens(button, event, menu):
    if event.type == Gdk.EventType.BUTTON_PRESS:
        menu.popup(None, None, None, None, event.button.button, event.time)
        return True
    return False


def cb_menu_selected(widget, timezone):
    select_timezone(timezone)


def cb_map_clicked(widget, event, model):
    x, y = event.x, event.y
    et_alloc = installer.go("event_timezones").get_allocation()
    #print((">> event w=%d, h=%d" % (et_alloc.width, et_alloc.height)))
    it_alloc = installer.go("image_timezones").get_allocation()
    #print((">> image w=%d, h=%d" % (it_alloc.width, it_alloc.height)))
    x = x - ((et_alloc.width - it_alloc.width) / 2)
    y = y - ((et_alloc.height - it_alloc.height) /2)
    if event.window != installer.go("event_timezones").get_window():
        dx, dy = event.window.get_position()
        x, y = x + dx, y + dy
    print(("Click: (%d, %d)" % (x, y)))
    closest_timezone = min(timezones, key=lambda tz: math.sqrt((x - tz.x)**2 + (y - tz.y)**2))
    select_timezone(closest_timezone)

# Timezone offsets color coded in cc.png
# If someone can make this more robust (maintainable), I buy you lunch!
TIMEZONE_COLORS = {
    "2b0000": "-11.0",
    "550000": "-10.0",
    "66ff05": "-9.5",
    "800000": "-9.0",
    "aa0000": "-8.0",
    "d40000": "-7.0",
    "ff0001": "-6.0",
    "66ff00": "-5.5",
    "ff2a2a": "-5.0",
    "c0ff00": "-4.5",
    "ff5555": "-4.0",
    "00ff00": "-3.5",
    "ff8080": "-3.0",
    "ffaaaa": "-2.0",
    "ffd5d5": "-1.0",
    "2b1100": "0.0",
    "552200": "1.0",
    "803300": "2.0",
    "aa4400": "3.0",
    "00ff66": "3.5",
    "d45500": "4.0",
    "00ccff": "4.5",
    "ff6600": "5.0",
    "0066ff": "5.5",
    "00ffcc": "5.75",
    "ff7f2a": "6.0",
    "cc00ff": "6.5",
    "ff9955": "7.0",
    "ffb380": "8.0",
    "ffccaa": "9.0",
    "aa0044": "9.5",
    "ffe6d5": "10.0",
    "d10255": "10.5",
    "d4aa00": "11.0",
    "fc0266": "11.5",
    "ffcc00": "12.0",
    "fd2c80": "12.75",
    "fc5598": "13.0",
}

ADJUST_HOURS_MINUTES = re.compile('([+-])([0-9][0-9])([0-9][0-9])')

IS_WINTER = datetime.now().timetuple().tm_yday not in list(range(80, 264))  # today is between Mar 20 and Sep 20


def select_timezone(tz):
    # Adjust time preview to current timezone (using `date` removes need for pytz package)
    offset = getoutput('TZ={} date +%z'.format(tz.name))
    tzadj = ADJUST_HOURS_MINUTES.search(offset).groups()
    global adjust_time
    adjust_time = timedelta(hours=int(tzadj[0] + tzadj[1]),
                            minutes=int(tzadj[0] + tzadj[2]))
    print(("Timezone: {tz.name} (UTC{offset}) ({tz.x}, {tz.y})".format(tz=tz, offset=offset)))

    def _get_image(overlay, x, y):
        """Superpose the picture of the timezone on the map"""
        def _get_x_offset():
            now = datetime.utcnow().timetuple()
            return - int((now.tm_hour * 60 + now.tm_min - 12 * 60) / (24 * 60) * MAP_SIZE[0])  # night is centered at UTC noon (12)

        im = BACK_IM.copy()
        if overlay:
            overlay_im = Image.open(TIMEZONE_RESOURCES + overlay)
            im.paste(BACK_ENHANCED_IM, overlay_im)
        night_im = ImageChops.offset(NIGHT_IM, _get_x_offset(), 0).crop(im.getbbox())
        if IS_WINTER:
            night_im = ImageOps.flip(night_im)

        # In Wheezy alpha_composite and tobytes are not implemented, yet
        try:
            im.paste(Image.alpha_composite(night_im, LIGHTS_IM), night_im)
        except:
            im.paste(Image.blend(night_im, LIGHTS_IM, 0.5), night_im)
        im.paste(DOT_IM, (int(x - DOT_IM.size[1] / 2), int(y - DOT_IM.size[0] / 2)), DOT_IM)
        try:
            data = im.tobytes()
            w, h = im.size
            data = GLib.Bytes.new(data)
            pb = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB,
                 False, 8, w, h, w * 3)
            return pb
        except:
            data = im.tostring()
            w, h = im.size
            pb = GdkPixbuf.Pixbuf.new_from_data(data, GdkPixbuf.Colorspace.RGB,
                 False, 8, w, h, w * 3)
            return pb

    try:
        hexcolor = '{:02x}{:02x}{:02x}'.format(*CC_IM.getpixel((tz.x, tz.y)))
        print(("Color: #%s" % hexcolor))
        overlay = 'timezone_{}.png'.format(TIMEZONE_COLORS[hexcolor])
        print(("Image: %s" % overlay))
    except (IndexError, KeyError):
        installer.go("image_timezones").set_from_pixbuf(_get_image(None, min(tz.x, MAP_SIZE[0]), min(tz.y, MAP_SIZE[1])))
    else:
        pb = _get_image(overlay, tz.x, tz.y)
        installer.go("image_timezones").set_from_pixbuf(pb)
    installer.setup.timezone = tz.name
    installer.go("button_timezones").set_label(tz.name)
    # Move the current time label to appropriate position
    alloc = time_label_box.get_allocation()
    x, y = tz.x, tz.y
    if x + alloc.width + 4 > MAP_SIZE[0]:
        x -= alloc.width
    if y + alloc.height + 4 > MAP_SIZE[1]:
        y -= alloc.height
    installer.go("fixed_timezones").move(time_label_box, x, y)
