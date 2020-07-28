#!/usr/bin/env python3

"""
Handle package installation/removal and show progress in window.

Important: run this script with root privileges!

Arguments: see apt_handler -h
"""

import gi
gi.require_version('Gtk', '3.0')

import subprocess
from threading import Event, Thread
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
from os.path import exists
from pathlib import Path
import argparse


class AptHandler(Gtk.Window):
    def __init__(self, packages=[], remove=False, simulate=False, title='', font_color='', background_color='', background_image=''):
        Gtk.Window.__init__(self)
        # Check if the update already finished before
        self.flag = '/tmp/apt_update_done'

        # Set width and height
        self.width = 450
        self.height = 150

        # Create event to use when thread is done
        self.check_done_event = Event()

        # Arguments
        self.packages = packages
        self.apt_action = 'install' if not remove else 'purge'
        self.simulate = '' if not simulate else '-s'
        title = '{0} {1}'.format(self.apt_action.capitalize(), packages[0]) if not title else title
        font_color = self.prep_hex_color(font_color)
        self.font_color_markup = 'color="#{}"'.format(font_color) if font_color else ''
        background_image = background_image

        # Window settings
        #self.set_border_width(10)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.SPLASHSCREEN)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", self.quit)

        # Create overlay with a background image
        overlay = Gtk.Overlay()
        self.add(overlay)
        if exists(background_image):
            print(('set background_image {}'.format(background_image)))
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(background_image)
            pixbuf = pixbuf.scale_simple(self.width, self.height, GdkPixbuf.InterpType.BILINEAR)
            bg = Gtk.Image.new_from_pixbuf(pixbuf)
            overlay.add(bg)
        else:
            if background_color:
                print(('set background_color {}'.format(background_color)))
                # Set background color
                self.override_background_color(Gtk.StateType.NORMAL, 
                                               self.hex_to_rgba(background_color, True))
            self.set_default_size(self.width, self.height)

        # Box
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        # Title label
        lbl_title = Gtk.Label()
        lbl_title.set_markup('<span {} weight="bold">{}</span>'.format(self.font_color_markup, title))
        box.pack_start(lbl_title, True, True, 0)
        # Message label
        self.lbl_msg = Gtk.Label()
        self.lbl_msg.set_xalign(0)
        box.pack_start(self.lbl_msg, True, True, 0)
        # Progressbar
        self.progressbar = Gtk.ProgressBar()
        box.pack_start(self.progressbar, True, True, 0)
        overlay.add_overlay(box)
        # Show the window
        self.show_all()

        # Start thread to check for connection changes
        Thread(target=self.run_apt).start()

    def run_apt(self):
        """
        Execute apt command and show apt progress in message label
        """
        # Make the progressbar pulse
        GLib.timeout_add(50, self.pulse, None)

        # Update apt cache first
        if not exists(self.flag):
            # Touch file that update was done
            # And can be skipped the next time it is started this session
            Path(self.flag).touch()
            self.run_command('apt-get update')

        # Now install each package separately
        for package in self.packages:
            cmd = 'apt-get {0} {1} -y --allow-downgrades --allow-remove-essential --allow-change-held-packages {2}'.format(self.apt_action, self.simulate, package)
            self.run_command(cmd)
        # Send done event
        self.check_done_event.set()
        
    def run_command(self, command):
        print(("Run command: {}".format(command)))
        p = subprocess.Popen(command, shell=True, bufsize=0, stdout=subprocess.PIPE, universal_newlines=True)
        for line in p.stdout:
            # Strip the line, also from null spaces (strip() only strips white spaces)
            line = line.strip().strip('\0')
            line = line[:50] + '...' if len(line) > 50 else line
            self.lbl_msg.set_markup('<span {}>{}</span>'.format(self.font_color_markup, line))
            print(line)
        p.stdout.close()
        return_code = p.wait()
        if return_code:
            print("Command '{cmd}' returned non-zero exit status: {code}".format(cmd=command, code=return_code))

    def pulse(self, user_data):
        """
        Make progressbar pulse
        """
        if not self.check_done_event.is_set():
            self.progressbar.pulse()
            # Return True so that it continues to get called
            return True
        # Quit the window
        self.quit()

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

    def quit(self, widget=None):
        Gtk.main_quit()

# Handle arguments
# https://docs.python.org/3/library/argparse.html
parser = argparse.ArgumentParser(description='AptHandler Help')
parser.add_argument('--packages', nargs='+', help='String with packages.')
parser.add_argument('-r', '--remove', action="store_true", help='Removes packages (installs if not provided).')
parser.add_argument('-s', '--simulate', action="store_true", help='Simulate installation/removal of the packages.')
parser.add_argument('--title', nargs='*', help='Window title.')
parser.add_argument('--font_color', nargs='*', help='Font color (hex).')
parser.add_argument('--background_color', nargs='*', help='Window background color (hex).')
parser.add_argument('--background_image', nargs='*', help='Window background image path.')
args, extra = parser.parse_known_args()
if args.packages:
    # Show window if arguments were passed
    remove = True if args.remove else False
    simulate = True if args.simulate else False
    title = args.title[0] if args.title else ''
    font_color = args.font_color[0] if args.font_color else ''
    background_color = args.background_color[0] if args.background_color else ''
    background_image = args.background_image[0] if args.background_image else ''
    AptHandler(packages=args.packages,
               remove=remove,
               simulate=simulate,
               title=title,
               font_color=font_color,
               background_color=background_color,
               background_image=background_image)
    Gtk.main()
