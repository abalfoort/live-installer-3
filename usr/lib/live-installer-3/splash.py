#!/usr/bin/env python3

# ====================================================================
# Class to show a splash screen while application is loading
# ====================================================================
# Initiate the splash screen:
# from splash import Splash
# splash = Splash(title='Splash Screen')
#     Other arguments: width, height, font, font_weight, font_color, background_color, background_image.
#     width and height are ignored when background_image is used.
#     background_color is ignored when background_image is provided.
#     font can be the font name with size: font='Roboto Slab 18', or only the size in which it will use the system default font.
#     font_weight can be ultralight, light, normal, bold, ultrabold, heavy, or a numeric weight.
#
# Start the splash screen:
# splash.start()
#
# When done:
# splash.destroy()
#
# Note: you can hide and show the splash screen when needed:
# splash.hide()
# splash.show()
# ====================================================================

# Make sure the right Gtk version is loaded
import gi
gi.require_version('Gtk', '3.0')

# from gi.repository import Gtk, GdkPixbuf, GObject, Pango, Gdk
from gi.repository import Gtk, Gdk
from threading import Thread
from os.path import exists
import argparse
import time


class Splash(Thread):
    def __init__(self, title='', width=400, height=250, font='', font_weight='', font_color='', background_color='', background_image='', with_args=False):
        Thread.__init__(self)
        self.title = title
        self.width = width
        self.height = height
        self.font = font
        self.font_weight = font_weight
        self.font_color = self.prep_hex_color(font_color)
        self.background_image = background_image
        self.with_args = with_args

        # Window settings
        self.window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.window.set_type_hint(Gdk.WindowTypeHint.SPLASHSCREEN)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_title(self.title)

        # Create overlay with a background image
        overlay = Gtk.Overlay()
        self.window.add(overlay)
        if exists(self.background_image):
            # Window will adjust to image size automatically
            bg = Gtk.Image.new_from_file(self.background_image)
            overlay.add(bg)
        else:
            # Set window dimensions
            self.window.set_default_size(self.width, self.height)
            if background_color:
                # Set background color
                self.window.override_background_color(Gtk.StateType.NORMAL, 
                                                      self.hex_to_rgba(background_color, True))

        # Add box with label
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(self.height / 3)
        box.set_margin_start(20)
        box.set_margin_end(20)
        # Add the box to a new overlay in the existing overlay
        overlay.add_overlay(box)
        lbl_title = Gtk.Label()
        lbl_title.set_line_wrap(True)
        # Markup format: https://developer.gnome.org/pango/stable/PangoMarkupFormat.html
        font_markup = 'color="#{}"'.format(self.font_color)  if self.font_color else ''
        weight_markup = 'weight="{}"'.format(self.font_weight) if self.font_weight else 'normal'
        lbl_title.set_markup('<span font="{}" {} {}>{}</span>'.format(self.font, 
                                                                      font_markup, 
                                                                      weight_markup, 
                                                                      self.title))
        box.pack_start(lbl_title, False, True, 0)

    def run(self):
        # Show the splash screen
        self.window.show_all()
        if self.with_args: Gtk.main()

        # Without this ugly one-liner, the window won't show
        while Gtk.events_pending(): Gtk.main_iteration()
        

    def prep_hex_color(self, hex_color):
        if not hex_color: return ''
        hex_color = hex_color.strip('#')
        # Fill up with last character until length is 6 characters
        if len(hex_color) < 6:
            hex_color = hex_color.ljust(6, hex_color[-1])
        # Add alpha channel if it's not there
        hex_color = hex_color.ljust(8, 'f')
        return hex_color

    def hex_to_rgba(self, hex_color, as_gdk_rgba=False):
        if not hex_color: return ''
        hex_color = self.prep_hex_color(hex_color)
        # Create a list with rgba values from hex_color
        rgba = list(int(hex_color[i : i + 2], 16) for i in (0, 2 ,4, 6))
        if as_gdk_rgba:
            # Change values to float between 0 and 1 for Gdk.RGBA
            for i, val in enumerate(rgba):
                if val > 0:
                    rgba[i] = 1 / (255 / val)
            # Return the Gdk.RGBA object
            return Gdk.RGBA(rgba[0], rgba[1], rgba[2], rgba[3])
        return rgba

    def show(self):
        self.window.show()

    def hide(self):
        self.window.hide()

    def destroy(self):
        while Gtk.events_pending(): Gtk.main_iteration()
        self.window.destroy()

# Handle arguments
# https://docs.python.org/3/library/argparse.html
parser = argparse.ArgumentParser()
parser = argparse.ArgumentParser(description='Splash Help')
parser.add_argument('--title', nargs='*', help='Window title.')
parser.add_argument('--font_color', nargs='*', help='Font color (hex).')
parser.add_argument('--background_color', nargs='*', help='Window background color (hex).')
parser.add_argument('--background_image', nargs='*', help='Path to background image.')
parser.add_argument('--timeout', metavar='N', type=int, nargs='+', help='Seconds to show the window.')
args, extra = parser.parse_known_args()
if args.title and args.timeout:
    # Show window if arguments were passed
    timeout = 5 if args.timeout[0] < 5 else args.timeout[0]
    title = args.title[0] if args.title else ''
    font_color = args.font_color[0] if args.font_color else ''
    background_color = args.background_color[0] if args.background_color else ''
    background_image = args.background_image[0] if args.background_image else ''
    splash = Splash(title=title,
                    font_color=font_color,
                    background_color=background_color,
                    background_image=background_image,
                    with_args=True)
    splash.start()
    time.sleep(timeout)
    Gtk.main_quit()
