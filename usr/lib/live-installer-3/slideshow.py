#!/usr/bin/env python3

# Slideshow makes use of SimpleBrowser

from gi.repository import GLib
from os.path import join, isdir
import glob
import time
from threading import Thread

SLIDESHOW_DIR = "/usr/share/live-installer-3/slideshow"


class Slideshow(Thread):
    def __init__(self, webview, language, basedir=SLIDESHOW_DIR,
                 interval_seconds=30, loop_pages=True):
        Thread.__init__(self)
        self.browser = webview
        self.basedir = basedir
        self.interval = interval_seconds
        self.loop = loop_pages
        self.slideshow_pages = []
        self.daemon = True  # let the thread die with the parent
        
        # Save all html paths in list
        lang_dir = self.get_language_dir(language)
        for page in sorted(glob.iglob(join(lang_dir, '*.html'))):
            self.slideshow_pages.append(join(lang_dir, page))

    def run(self):
        if not self.slideshow_pages:
            return  # prevent busy-looping if no pages
        # Update webview in the main thread
        while True:
            for page in self.slideshow_pages:
                # Use GLib.idle_add to schedule an update of the browser object
                # If you do this directly, the objects won't refresh
                GLib.idle_add(self.browser.show_html, page)
                time.sleep(self.interval)
            if not self.loop:
                break

    def get_language_dir(self, lang):
        # First test if full locale directory exists, e.g. html/pt_BR,
        # otherwise perhaps at least the language is there, e.g. html/pt
        # and if that doesn't work, try html/pt_PT
        path = join(self.basedir, lang)
        if not isdir(path):
            base_lang = lang.split('_')[0].lower()
            path = join(self.basedir, base_lang)
            if not isdir(path):
                path = join(self.basedir, "{}_{}".format(base_lang, base_lang.upper()))
                if not isdir(path):
                    path = join(self.basedir, 'en')
        return path
