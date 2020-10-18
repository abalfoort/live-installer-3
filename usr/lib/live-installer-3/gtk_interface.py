#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

import os
import PIL
from os.path import join, abspath, dirname, basename, exists, getctime
from textwrap import TextWrapper
from queue import Queue
from glob import glob
import installer
from slideshow import Slideshow
import timezones
from dialogs import MessageDialog, QuestionDialog, ErrorDialog, \
                    WarningDialog, SelectImageDialog
from utils import getoutput, get_config_dict, shell_exec, filter_text, \
                  is_valid_hostname, ExecuteThreadedCommands, \
                  has_internet_connection, memoize, is_xfce_running, get_memory_gib
import partitioning
from widgets import PictureChooserButton
from simplebrowser import SimpleBrowser
from splash import Splash
import math
import requests
import GeoIP

# i18n: http://docs.python.org/3/library/gettext.html
# Actual language set in on_treeview_language_list_cursor_changed
import gettext


LOADING_ANIMATION = '/usr/share/live-installer-3/loading.gif'
CONFIG_FILE = '/etc/live/live-installer-3.conf'
TMP_FACE = '/tmp/live-installer-3-face.png'


class WizardPage():
    def __init__(self, help_text, icon):
        self.help_text = help_text
        self.icon = icon


class InstallerWindow():
    # Cancelable timeout for keyboard preview generation, which is
    # quite expensive, so avoid drawing it if only scrolling through
    # the keyboard layout list
    kbd_preview_generation = -1

    def __init__(self, fullscreen=False, nosplash=False, nopartitioncheck=False):
        # Set base dirs
        self.scriptName = basename(__file__)
        self.scriptDir = join(abspath(dirname(__file__)), "../")
        self.mediaDir = join(self.scriptDir, '../share/live-installer-3')
        
        #Build the Setup object (where we put all our choices)
        self.fullscreen = fullscreen
        self.nosplash = nosplash
        self.nopartitioncheck = nopartitioncheck
        self.splash = None
        self.setup = installer.Setup()

        # Set distribution name and version
        for f, n, v in (('/etc/os-release', 'PRETTY_NAME', 'VERSION'),
                        ('/etc/lsb-release', 'DISTRIB_DESCRIPTION', 'DISTRIB_RELEASE'),
                        (CONFIG_FILE, 'DISTRIBUTION_NAME', 'DISTRIBUTION_VERSION')):
            try:
                config = get_config_dict(f)
                name, version = config[n], config[v]
            except (IOError, KeyError):
                continue
            else:
                break
        else:
            name, version = 'Unknown GNU/Linux', '1.0'
        self.setup.distribution_name, self.setup.distribution_version = name, version
        config = get_config_dict('/etc/os-release')
        self.setup.distribution_id = config.get('ID', 'distro')
        
        # Show splash screen while loading
        self.title = gettext.dgettext('live-installer-3', "{} Installer")
        self.title = self.title.format(self.setup.distribution_name)
        if not self.nosplash:
            b_img = join(self.mediaDir, 'images/splash-bgk.png')
            f_clr = '#243e4b'
            if is_xfce_running():
                b_img = join(self.mediaDir, 'images/splash-bgx.png')
                f_clr = '#502800'
            self.splash = Splash(title=self.title, font='Roboto Slab 18', font_weight='bold', font_color=f_clr, background_image=b_img)
            self.splash.start()

        # should be set earl
        self.live_capture = "/tmp/live-installer-3-capture.jpeg"
        self.queue = Queue(-1)
        self.threads = {}
        self.done = False
        self.fail = False
        self.should_pulse = False

        # Main window objects
        self.builder = Gtk.Builder()
        self.builder.add_from_file(join(self.mediaDir, 'live-installer-3.glade'))
        self.go = self.builder.get_object
        self.window = self.go("main_window")
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_resizable(True)
        self.go("notebook1").set_show_tabs(False)

        self.treeview_language_list = self.go("treeview_language_list")
        col = Gtk.TreeViewColumn("", Gtk.CellRendererPixbuf(), pixbuf=2)
        self.treeview_language_list.append_column(col)
        ren = Gtk.CellRendererText()
        self.langcol_language = Gtk.TreeViewColumn("Language", ren, text=0)
        self.langcol_language.set_sort_column_id(0)
        self.treeview_language_list.append_column(self.langcol_language)
        self.langcol_country = Gtk.TreeViewColumn("Country", ren, text=1)
        self.langcol_country.set_sort_column_id(1)
        self.treeview_language_list.append_column(self.langcol_country)
        
        # build timezones
        self.timezones_model = timezones.build_timezones(self)
        
        # build the language list (timezones.build_timezones must have run before this)
        self.build_lang_list()

        # build user info page
        if exists(self.setup.face):
            shell_exec("convert %s -resize x96 %s" % (self.setup.face, TMP_FACE))

        pic_box = self.go("pic_box")
        self.setup.face_button = PictureChooserButton(num_cols=4, button_picture_size=96, menu_pictures_size=64)
        
        # DeprecationWarning: Gtk.Button.set_alignment is deprecated
        #self.setup.face_button.set_alignment(0.0, 0.5)
        
        self.setup.face_photo_menuitem = Gtk.MenuItem()
        self.setup.face_photo_menuitem.connect('activate', self._on_face_take_picture_button_clicked)

        self.setup.face_browse_menuitem = Gtk.MenuItem()
        self.setup.face_browse_menuitem.connect('activate', self._on_face_browse_menuitem_activated)

        faces_added = False
        face_dirs = ["/usr/share/pixmaps/faces"]
        for face_dir in face_dirs:
            if exists(face_dir):
                pictures = sorted(os.listdir(face_dir))
                for picture in pictures:
                    path = join(face_dir, picture)
                    if path != self.setup.face:
                        faces_added = True
                        self.setup.face_button.add_picture(path, self._on_face_menuitem_activated)

        if faces_added:
            self.setup.face_button.add_picture(self.setup.face, self._on_face_menuitem_activated)
            self.setup.face_button.add_separator()

        # if no /dev/video*, we don't have a webcam
        webcam_detected = bool(len(glob('/dev/video*')))

        if webcam_detected:
            self.setup.face_button.add_menuitem(self.setup.face_photo_menuitem)
        self.setup.face_button.add_menuitem(self.setup.face_browse_menuitem)

        if exists(TMP_FACE):
            self.setup.face_button.set_picture_from_file(TMP_FACE)

        pic_box.pack_start(self.setup.face_button, True, False, 6)

        # Initiate disks treeview
        for i in (partitioning.IDX_PART_PATH,
                  partitioning.IDX_PART_GRUB,
                  partitioning.IDX_PART_TYPE,
                  partitioning.IDX_PART_LABEL,
                  partitioning.IDX_PART_MOUNT_AS,
                  partitioning.IDX_PART_FORMAT_AS,
                  partitioning.IDX_PART_ENCRYPT,
                  partitioning.IDX_PART_SIZE,
                  partitioning.IDX_PART_FREE_SPACE):

            if i == partitioning.IDX_PART_GRUB:
                # Render a toggle box for grub
                rend = Gtk.CellRendererToggle()
                # Set specific properties of the renderer
                rend.connect('toggled', self.grub_chk_on_toggle, i)
                col = Gtk.TreeViewColumn("grub", rend, active=i)
            elif i == partitioning.IDX_PART_ENCRYPT:
                # Render a toggle box for encryption
                rend = Gtk.CellRendererToggle()
                # Set specific properties of the renderer
                rend.connect('toggled', self.encrypt_chk_on_toggle, i)
                col = Gtk.TreeViewColumn("encrypt", rend, active=i)
            else:
                col = Gtk.TreeViewColumn("", Gtk.CellRendererText(), markup=i)  # real title is set in i18n()

            self.go("treeview_disks").append_column(col)

        # Filter text input and limit input length
        filter_text(self.go("entry_username"), 'a-z0-9-_')
        self.go("entry_username").set_max_length(32)
        # https://en.wikipedia.org/wiki/Hostname#Restrictions_on_valid_host_names
        filter_text(self.go("entry_hostname"), 'a-z0-9-.')
        self.go("entry_hostname").set_max_length(255)

        # Set the hostname
        self.setup.hostname = self.setup.distribution_id
        self.go("entry_hostname").set_text(self.setup.hostname)

        # kb models
        cell = Gtk.CellRendererText()
        self.go("combobox_kb_model").pack_start(cell, True)
        self.go("combobox_kb_model").add_attribute(cell, 'text', 0)

        # kb layouts
        ren = Gtk.CellRendererText()
        self.kbcol_layout = Gtk.TreeViewColumn("Layout", ren, text=0)
        self.go("treeview_layouts").append_column(self.kbcol_layout)
        ren = Gtk.CellRendererText()
        self.kbcol_variant = Gtk.TreeViewColumn("Variant", ren, text=0)
        self.go("treeview_variants").append_column(self.kbcol_variant)
        # Build the keyboard list
        self.build_kb_lists()

        # 'about to install' aka overview
        ren = Gtk.CellRendererText()
        self.overview_col = Gtk.TreeViewColumn("Overview", ren, markup=0)
        self.go("treeview_overview").append_column(self.overview_col)

        # apply to the header
        self.title_box = self.go("title_eventbox")
        self.title_box.set_border_width(6)
        self.help_label = self.go("help_label")
        
        # DeprecationWarning: Gtk.Widget.modify_bg is deprecated
        #bgColor = Gdk.color_parse('#585858')
        #self.title_box.modify_bg(Gtk.StateType.NORMAL, bgColor)
        #fgColor = Gdk.color_parse('#FFFFFF')
        #self.help_label.modify_fg(Gtk.StateType.NORMAL, fgColor)
        
        # Use CSS instead:
        # In case of external css file:
        #css_provider.load_from_path("style.css")
        # In case of css for whole window:
        #Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # Reference:
        # https://developer.gnome.org/gtk3/stable/GtkCssProvider.html
        # https://developer.gnome.org/gtk3/stable/GtkStyleContext.html
        # https://developer.gnome.org/gtk3/stable/GtkStyleProvider.html
        css = b""".titlebox {
    background-color: #585858;
}
    .helplabel {
    color: #fff;
}"""
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css)
        style_context_tb = self.title_box.get_style_context()
        style_context_tb.add_class("titlebox")
        style_context_tb.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        style_context_hl = self.help_label.get_style_context()
        style_context_hl.add_class("helplabel")
        style_context_hl.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        # Partitions popup menu variables
        self.partition = None
        self.enc_passphrase = ''
        self.release = ''
        self.home = ''
        self.efi = ''
        self.efi_partition_type = ''
        self.menu = Gtk.Menu()

        # Connect signals
        self.builder.connect_signals(self)

        # Start translating here
        self.on_treeview_language_list_cursor_changed(self.treeview_language_list)

        # make sure we're on the right page (no pun.)
        self.activate_page(0)

        # Configure slideshow webview
        html = '<html><body style="background-color:#E6E6E6;"></body></html>'
        self.slideshow_browser = SimpleBrowser()
        self.slideshow_browser.show_html(html)
        self.go("box_slideshow").pack_start(self.slideshow_browser, True, True, 0)

        # Configure disks webview
        self.partitions_browser = SimpleBrowser()
        self.go("box_partitions_browser").pack_start(self.partitions_browser, True, True, 0)

        # Show window
        self.window.show_all()
        
        # Workaround for window adding unneeded space at the bottom
        self.window.resize(600, 400)

        # Build the partitions
        partitioning.build_partitions(self)

        # Hide home partition encryption when not in OEM setup
        if self.setup.oem_setup \
           and self.setup.home_partition != '' \
           and not self.setup.home_partition is None:
            self.go("frm_home_encryption_2").set_sensitive(False)
        else:
            self.go("frm_home_encryption_1").hide()
            self.go("frm_home_encryption_2").hide()

        # Let the user connect to the internet
        if not has_internet_connection():
            if self.splash is not None:
                self.splash.destroy()
            msg = _("Please, click on the network manager's system tray icon to connect to the internet before you continue.\n\n"
                    "You can still install %s without an internet connection.\n"
                    "Without an internet connection your system will not be upgraded and some packages cannot be localized." % self.setup.distribution_name)
            WarningDialog(_("No internet connection"), msg)

            # If we now have an internet connection we can check the user's position
            if has_internet_connection():
                self.build_lang_list()

        if self.fullscreen:
            self.window.fullscreen()
            
        # Destroy splash screen
        if self.splash is not None:
            self.splash.destroy()

    # ===================================================================
    # Main window signals
    # ===================================================================
    
    #def on_main_window_window_state_event(self, widget, event):
    #    self.fullscreen = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)

    def on_main_window_key_press_event(self, widget, event):
        try:
            keyname = Gdk.keyval_name(event.keyval)
            #print(keyname)
            if (keyname == 'Escape' and self.fullscreen) or \
               (keyname == 'f' and self.fullscreen):
                # Change window size to normal when Escape key has been hit
                self.window.unfullscreen()
                self.window.unmaximize()
                self.fullscreen = False
            elif keyname == 'f' and not self.fullscreen:
                self.window.fullscreen()
                self.fullscreen = True
        except:
            pass

    def on_button_quit_clicked(self, widget):
        self.on_main_window_delete_event(widget)

    def on_main_window_delete_event(self, widget, event=None):
        if QuestionDialog(_("Quit?"), _("Are you sure you want to quit the installer?")):
            Gtk.main_quit()
            return False
        else:
            return True

    def on_button_next_clicked(self, widget):
        self.wizard_cb(False)

    def on_button_back_clicked(self, widget):
        self.wizard_cb(True)

    # ===================================================================
    # Main window timezones signals
    # ===================================================================

    def on_event_timezones_button_release_event(self, widget, event=None):
        timezones.cb_map_clicked(widget, event, self.timezones_model)

    # ===================================================================
    # Main window language signals
    # ===================================================================

    def on_treeview_language_list_cursor_changed(self, treeview, data=None):
        ''' Called whenever someone updates the language '''
        model = treeview.get_model()
        active = treeview.get_selection().get_selected_rows()
        if len(active) < 2:
            return
        try:
            active = active[1][0]
            if active is None:
                return
            row = model[active]
            self.setup.language = row[-1]
            print(("Set user language: {}".format(self.setup.language)))
        except:
            print(("ERROR - Cannot parse selected row: {}.".format(active)))
            return

        try:
            # Try e.g. zh_CN, zh, or fallback to hardcoded English
            lang = gettext.translation(domain='live-installer-3',
                                       languages=[self.setup.language,
                                                  self.setup.language.split('_')[0]],
                                       fallback=True)
            # The install method will cause all the _() calls to return the translated strings globally into the built-in namespace.
            lang.install()
            _ = lang.gettext
            self.i18n()
        except IOError:
            print(("ERROR - Cannot install selected language {}. Falling back to EN.".format(self.setup.language)))

        # Check if the system can be localized in the selected language
        if self.setup.language != "en_US":
            if not exists("/lib/live/mount/medium/pool") \
            and not has_internet_connection():
                # Cannot localize system
                msg = _("Cannot download and install additional locale packages: no internet connection\n"
                        "Configuration will still be set to your selected language.")
                WarningDialog(_("No internet connection"), msg)

    def on_checkbutton_autologin_toggled(self, checkbox, data=None):
        self.setup.autologin = checkbox.get_active()

    def on_checkbutton_home_encryption_toggled(self, checkbox, data=None):
        if checkbox.get_active():
            self.go("frm_home_encryption_2").set_sensitive(True)
            self.go("entry_encpass1").grab_focus()
        else:
            self.go("frm_home_encryption_2").set_sensitive(False)
            self.go("entry_encpass1").set_text('')
            self.go("entry_encpass2").set_text('')


    # ===================================================================
    # Main window keyboard signals
    # ===================================================================

    def on_combobox_kb_model_changed(self, combobox):
        ''' Called whenever someone updates the keyboard model '''
        model = combobox.get_model()
        active = combobox.get_active()
        (self.setup.keyboard_model_description,
         self.setup.keyboard_model) = model[active]
        print(("Keyboard = {} ({})".format(self.setup.keyboard_model, self.setup.keyboard_model_description)))
        shell_exec('setxkbmap -model ' + self.setup.keyboard_model)

    def on_treeview_layouts_cursor_changed(self, treeview):
        ''' Called whenever someone updates the keyboard layout '''
        model, active = treeview.get_selection().get_selected_rows()
        if not active:
            return
        (self.setup.keyboard_layout_description,
         self.setup.keyboard_layout) = model[active[0]]
        # Set the correct variant list model ...
        model = self.layout_variants[self.setup.keyboard_layout]
        self.go("treeview_variants").set_model(model)
        # ... and select the first variant (standard)
        self.go("treeview_variants").set_cursor(0)

    def on_treeview_variants_cursor_changed(self, treeview):
        ''' Called whenever someone updates the keyboard layout or variant '''
        model, active = treeview.get_selection().get_selected_rows()
        if not active: return
        (self.setup.keyboard_variant_description,
         self.setup.keyboard_variant) = model[active[0]]
        if self.setup.keyboard_variant:
            shell_exec('setxkbmap -variant ' + self.setup.keyboard_variant)
        else:
            shell_exec('setxkbmap -layout ' + self.setup.keyboard_layout)
        # Set preview image
        self.go("image_keyboard").set_from_file(LOADING_ANIMATION)
        self.kbd_preview_generation = GLib.timeout_add(500, self._generate_keyboard_layout_preview)

    # ===================================================================
    # Main window user info signals
    # ===================================================================

    def on_entry_your_name_text_notify(self, entry, prop):
        self.setup.real_name = entry.props.text
        text = entry.props.text.strip().lower().replace(" ", "-")
        self.setup.username = text
        self.go("entry_username").set_text(text)

    def on_entry_username_text_notify(self, entry, prop):
        self.setup.username = entry.props.text

    def on_entry_hostname_text_notify(self, entry, prop):
        self.setup.hostname = entry.props.text

    def on_entry_userpass1_changed(self, widget):
        self.assign_password()

    def on_entry_userpass2_changed(self, widget):
        self.assign_password()

    def on_entry_encpass1_changed(self, widget):
        self.assign_enc_password()

    def on_entry_encpass2_changed(self, widget):
        self.assign_enc_password()

    # ===================================================================
    # Main window partitions signals
    # ===================================================================

    def on_treeview_disks_button_release_event(self, widget, event):
        partitioning.edit_partition_dialog()

    def on_treeview_disks_selection_changed(self, widget, data=None):
        selection = self.go("treeview_disks").get_selection()
        partitioning.update_html_preview(selection)

    def on_button_edit_partitions_clicked(self, widget):
        partitioning.manually_edit_partitions()

    def on_button_refresh_clicked(self, widget):
        partitioning.build_partitions(self)

    def on_button_custommount_clicked(self, widget):
        self.activate_page(self.PAGE_CUSTOMWARNING)

    # ===================================================================
    # Main window functions
    # ===================================================================

    def wrap_text(self, text, width=None):
        # Gtk.Label doesn't wrap when used in Glade file
        # Even programatically add labels to box or grid after initiating the window does not wrap the text
        # This function uses TextWrapper to insert a line feed (\n) after x characters on an empty space
        # Reference: https://docs.python.org/3.2/library/textwrap.html

        wrapper = TextWrapper()
        wrapper.width = width
        wrapper.break_long_words = False
        wrapper.break_on_hyphens = False
        return wrapper.fill(text)

    def i18n(self):
        # Window title
        if __debug__:
            self.window.set_title('{} (debug)'.format(self.title))
        else:
            if self.setup.oem_setup:
                self.window.set_title((_("{} OEM Setup").format(self.setup.distribution_name)))
            else:
                self.window.set_title(self.title)

        # Wizard pages
        (self.PAGE_LANGUAGE,
         self.PAGE_TIMEZONE,
         self.PAGE_KEYBOARD,
         self.PAGE_USER,
         self.PAGE_PARTITIONS,
         self.PAGE_CUSTOMWARNING,
         self.PAGE_OVERVIEW,
         self.PAGE_INSTALL,
         self.PAGE_CUSTOMPAUSED) = list(range(9))

        self.wizard_pages = list(range(9))
        self.wizard_pages[self.PAGE_LANGUAGE] = WizardPage(_("Language"), "locales.svg")
        self.wizard_pages[self.PAGE_TIMEZONE] = WizardPage(_("Timezone"), "time.svg")
        self.wizard_pages[self.PAGE_KEYBOARD] = WizardPage(_("Keyboard layout"), "keyboard.svg")
        self.wizard_pages[self.PAGE_USER] = WizardPage(_("User info"), "user.svg")
        self.wizard_pages[self.PAGE_PARTITIONS] = WizardPage(_("Partitioning"), "hdd.svg")
        self.wizard_pages[self.PAGE_CUSTOMWARNING] = WizardPage(_("Please make sure you wish to manage partitions manually"), "hdd.svg")
        self.wizard_pages[self.PAGE_OVERVIEW] = WizardPage(_("Summary"), "summary.svg")
        self.wizard_pages[self.PAGE_INSTALL] = WizardPage(_("Installing {}...").format(self.setup.distribution_name), "install.svg")
        self.wizard_pages[self.PAGE_CUSTOMPAUSED] = WizardPage(_("Installation is paused: please finish the custom installation"), "install.svg")

        # Help text for the language page
        help_text = _(self.wizard_pages[self.PAGE_LANGUAGE].help_text)
        self.go("help_label").set_markup("<big><b>%s</b></big>" % help_text)

        # Navigation buttons
        self.go("button_quit").set_label(_('Quit'))
        self.go("button_back").set_label(_('Back'))
        self.go("button_next").set_label(_('Forward'))

        # Language treeview columns
        self.langcol_language.set_title(_("Language"))
        self.langcol_country.set_title(_("Country"))

        # timezones
        self.go("label_timezones").set_label(_("Selected timezone:"))
        self.go("button_timezones").set_label(_('Select timezone'))

        # partitions
        self.go("button_refresh").set_label(_('Refresh'))
        self.go("button_custommount").set_label(_("_Expert mode"))
        self.go("button_edit_partitions").set_label(_("_Edit partitions"))
        # Columns
        for col, title in zip(self.go("treeview_disks").get_columns(),
                              (_("Device"),
                               _("Grub"),
                               _("Type"),
                               _("Label"),
                               _("Mount point"),
                               _("Format as"),
                               _("Encrypt"),
                               _("Size"),
                               _("Free space"))):
            col.set_title(title)

        # Navigation buttons
        self.go("button_quit").set_label(_('Quit'))
        self.go("button_back").set_label(_('Back'))
        self.go("button_next").set_label(_('Forward'))

        # keyboard page
        self.kbcol_layout.set_title(_("Layout"))
        self.kbcol_variant.set_title(_("Variant"))
        self.go("label_test_kb").set_label(_("Use this box to test your keyboard layout."))
        self.go("label_kb_model").set_label(_("Model"))

        # about you
        self.setup.face_button.set_tooltip_text(_("Click to change your picture"))
        self.setup.face_photo_menuitem.set_label(_("Take a photo..."))
        self.setup.face_browse_menuitem.set_label(_("Browse for more pictures..."))
        self.go("label_your_name").set_markup("<b>%s</b>" % _("Your full name"))
        self.go("label_your_name_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This will be shown in the About Me application."))
        self.go("label_username").set_markup("<b>%s</b>" % _("Your username"))
        self.go("label_username_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This is the name you will use to log in to your computer."))
        self.go("label_choose_pass").set_markup("<b>%s</b>" % _("Your password"))
        self.go("label_pass_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("Please enter your password twice to ensure it is correct."))

        self.go("label_hostname").set_markup("<b>%s</b>" % _("Hostname"))
        self.go("label_hostname_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % _("This hostname will be the computer's name on the network."))
        self.go("label_autologin").set_markup("<b>%s</b>" % _("Automatic login"))
        text = self.wrap_text(_("If enabled, the login screen is skipped when the system starts, and you are signed into your desktop session automatically."), 50)
        self.go("label_autologin_help").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % text)
        self.go("checkbutton_autologin").set_label(_("Log in automatically on system boot"))

        self.go("label_home_encryption").set_markup("<b>%s</b>" % _("Encryption"))
        self.go("checkbutton_home_encryption").set_label(_("Encrypt home partition"))
        text = self.wrap_text(_("If enabled, the home partition will be encrypted."), 50)
        self.go("label_home_encryption_help1").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % text)
        self.go("label_home_encryption_pwd").set_markup("<b>%s</b>" % _("Encryption password"))
        text = self.wrap_text(_("WARNING: when you loose your encryption password, you won't be able to recover your data! "
                                "During boot you will be asked for your password to unlock the home partition. "
                                "This is not necessarily the same password as your user login password."), 100)
        self.go("label_home_encryption_help2").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % text)

        self.go("face_label").set_markup("<b>%s</b>" % _("Your picture"))
        text = self.wrap_text(_("This picture represents your user account. It is used in the login screen and a few other places."), 50)
        self.go("face_description").set_markup("<span fgcolor='#3C3C3C'><sub><i>%s</i></sub></span>" % text)

        # custom install warning
        text = self.wrap_text(_("You have selected to manage your partitions manually, this feature is for ADVANCED USERS ONLY."), 100)
        self.go("label_custom_install_directions_1").set_label(text)
        text = self.wrap_text(_("Before continuing, please mount your target filesystem(s) at %s." % self.setup.target_dir), 100)
        self.go("label_custom_install_directions_2").set_label(text)
        text = self.wrap_text(_("Do NOT mount virtual devices such as /dev, /proc, /sys, etc on %s/." % self.setup.target_dir), 100)
        self.go("label_custom_install_directions_3").set_label(text)
        text = self.wrap_text(_("During the install, you will be given time to chroot into %s and install any packages that will be needed to boot your new system." % self.setup.target_dir), 100)
        self.go("label_custom_install_directions_4").set_label(text)
        text = self.wrap_text(_("During the install, you will be required to write your own /etc/fstab."), 100)
        self.go("label_custom_install_directions_5").set_label(text)
        text = self.wrap_text(_("If you aren't sure what any of this means, please go back and deselect manual partition management."), 100)
        self.go("label_custom_install_directions_6").set_label(text)

        # custom install installation paused directions
        text = self.wrap_text(_("Please do the following and then click Forward to finish installation:"), 100)
        self.go("label_custom_install_paused_1").set_label(text)
        text = self.wrap_text(_("Create {0}/etc/fstab for the filesystems as they will be mounted in your new system, matching those currently mounted at {0} (without using the {0} prefix in the mount paths themselves).".format(self.setup.target_dir)), 100)
        self.go("label_custom_install_paused_2").set_label(text)
        text = self.wrap_text(_("Install any packages that may be needed for first boot (mdadm, cryptsetup, dmraid, etc) by calling \"sudo chroot %s\" followed by the relevant apt-get/aptitude installations." % self.setup.target_dir), 100)
        self.go("label_custom_install_paused_3").set_label(text)
        text = self.wrap_text(_("Note that in order for update-initramfs to work properly in some cases (such as dm-crypt), you may need to have drives currently mounted using the same block device name as they appear in %s/etc/fstab." % self.setup.target_dir), 100)
        self.go("label_custom_install_paused_4").set_label(text)
        text = self.wrap_text(_("Double-check that your {0}/etc/fstab is correct, matches what your new system will have at first boot, and matches what is currently mounted at {0}.".format(self.setup.target_dir)), 100)
        self.go("label_custom_install_paused_5").set_label(text)

        # Overview treeview column
        self.overview_col.set_title(_("Overview"))

        # install page
        # self.go("label_install_progress").set_markup("<i>%s</i>" % _("Calculating file indexes ..."))

    def activate_page(self, index):
        help_text = _(self.wizard_pages[index].help_text)
        self.go("help_label").set_markup("<big><b>%s</b></big>" % help_text)

        # Resize icons to 48x48 whatever fysical size they have
        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(join(self.mediaDir, "images/%s" % self.wizard_pages[index].icon), 48, 48)
        self.go("help_icon").set_from_pixbuf(pb)

        self.go("notebook1").set_current_page(index)

        # TODO: move other page-depended actions from the wizard_cb into here below
        if index == self.PAGE_PARTITIONS:
            self.setup.skip_mount = False
        if index == self.PAGE_CUSTOMWARNING:
            self.setup.skip_mount = True

    def wizard_cb(self, goback):
        ''' wizard buttons '''
        sel = self.go("notebook1").get_current_page()
        self.go("button_next").set_label(_('Forward'))
        self.go("button_back").set_sensitive(True)

        # check each page for errors
        if(not goback):
            if(sel == self.PAGE_LANGUAGE):
                lang_country_code = self.setup.language.split('_')[-1]
                for value in (self.cur_timezone,      # timezone guessed from IP
                              self.cur_country_code,  # otherwise pick country from IP
                              lang_country_code):     # otherwise use country from language selection
                    if not value:
                        continue
                    tz_found = False
                    for row in timezones.timezones:
                        if value in row:
                            timezones.select_timezone(row)
                            tz_found = True
                            break
                    if tz_found:
                        break
                self.setup.print_setup("gtk_interfaces.languages")
                self.activate_page(self.PAGE_TIMEZONE)
            elif (sel == self.PAGE_TIMEZONE):
                if ("_" in self.setup.language):
                    country_code = self.setup.language.split("_")[1]
                else:
                    country_code = self.setup.language
                treeview = self.go("treeview_layouts")
                model = treeview.get_model()
                itr = model.get_iter_first()
                while itr is not None:
                    iter_country_code = model.get_value(itr, 1)
                    if iter_country_code.lower() == country_code.lower():
                        column = treeview.get_column(0)
                        path = model.get_path(itr)
                        treeview.set_cursor(path, column)
                        treeview.scroll_to_cell(path, column)
                        break
                    itr = model.iter_next(itr)
                self.setup.print_setup("gtk_interfaces.timezone")
                self.activate_page(self.PAGE_KEYBOARD)
            elif(sel == self.PAGE_KEYBOARD):
                self.setup.print_setup("gtk_interfaces.keyboard")
                self.activate_page(self.PAGE_USER)
                self.go("entry_your_name").grab_focus()
            elif(sel == self.PAGE_USER):
                # TODO: why can't we do this _init_?
                # Placed here or else overview is not shown.
                try:
                    model = self.go("treeview_disks").get_model()
                    html = model.get_html(self._selected_disk)
                    self.partitions_browser.show_html(html)
                except:
                    # No partitions were found
                    pass
                
                # Check user info
                errorFound = False
                errorMessage = ""

                if(self.setup.real_name is None or self.setup.real_name == ""):
                    errorFound = True
                    errorMessage = _("Please provide your full name.")
                elif(self.setup.username is None or self.setup.username == ""):
                    errorFound = True
                    errorMessage = _("Please provide a username.")
                elif self.setup.username[-4:] == "-oem" and self.setup.oem_setup:
                    errorFound = True
                    errorMessage = _("Please provide a username without -oem.")
                elif(self.setup.password1 is None or self.setup.password1 == ""):
                    errorFound = True
                    errorMessage = _("Please provide a password for your user account.")
                elif(self.setup.password1 != self.setup.password2):
                    errorFound = True
                    errorMessage = _("Your passwords do not match.")
                elif(self.setup.hostname is None or self.setup.hostname == ""):
                    errorFound = True
                    errorMessage = _("Please provide a hostname.")
                elif self.go("checkbutton_home_encryption").get_active():
                    if(self.setup.oem_home_encryption_pwd1 is None or self.setup.oem_home_encryption_pwd1 == ""):
                        errorFound = True
                        errorMessage = _("Please provide an encryption password.")
                    elif(self.setup.oem_home_encryption_pwd1 != self.setup.oem_home_encryption_pwd2):
                        errorFound = True
                        errorMessage = _("Your encryption passwords do not match.")
                else:
                    if self.setup.username[0:1].isdigit():
                        errorFound = True
                        errorMessage = _("Your username cannot start with a digit.")
                    if not is_valid_hostname(self.setup.hostname):
                        errorFound = True
                        errorMessage = _("The hostname is incorrect:\n"
                                         "not more than 63 characters between periods\n"
                                         "and not more than 255 characters total.")

                if (errorFound):
                    WarningDialog(_("Error"), errorMessage)
                else:
                    if self.setup.oem_setup:
                        # OEM Setup home partition encryption
                        #print((">> Checkbutton encrypt home = {}".format(self.go("checkbutton_home_encryption").get_active())))
                        if self.go("checkbutton_home_encryption").get_active():
                            for partition in self.setup.partitions:
                                #print((">> partition.mount_as= %s" % partition.mount_as))
                                if partition.mount_as == partitioning.HOME_MOUNT_POINT:
                                    partition.encrypt = True
                                    partition.format_as = partition.type
                                    partition.enc_passphrase = self.go("entry_encpass1").get_text().strip()
                                    #print((">> Encrypt %s with pwd %s and format with %s" % (partition.path, partition.enc_passphrase, partition.format_as)))

                        self.setup.print_setup("gtk_interfaces.user_info")
                        self.activate_page(self.PAGE_OVERVIEW)
                        self.show_overview()
                        self.go("treeview_overview").expand_all()
                        self.go("button_next").set_label(_("Apply"))
                        self.go("img_forward").hide()
                    else:
                        # Set OEM user to always autologin
                        if self.setup.username[-4:] == "-oem":
                            self.setup.autologin = True

                        self.setup.print_setup("gtk_interfaces.user_info")
                        self.activate_page(self.PAGE_PARTITIONS)
                        #partitioning.build_partitions(self)
            elif(sel == self.PAGE_PARTITIONS):
                model = self.go("treeview_disks").get_model()

                # Check for root partition
                found_root_partition = False
                for partition in self.setup.partitions:
                    if(partition.mount_as == partitioning.ROOT_MOUNT_POINT):
                        found_root_partition = True
                        if partition.format_as is None or partition.format_as == "":
                            ErrorDialog(_("Installation Tool"),
                                        _("Please indicate a filesystem to format the root (%s) partition with before proceeding.") % partitioning.ROOT_MOUNT_POINT)
                            return
                if not found_root_partition:
                    ErrorDialog(_("Installation Tool"),
                                _("<b>Please select a root (%s) partition.</b>") % partitioning.ROOT_MOUNT_POINT,
                                _("A root partition is needed to install the system.\n\n"
                                  " - Mount point: /\n"
                                  " - Recommended size: {0} GiB\n"
                                  " - Minimum size: {1} GiB\n"
                                  " - Recommended filesystem format: ext4\n".format(self.setup.rec_root_size_gb, self.setup.min_root_size_gb)))
                    return

                # Check if we need an EFI or boot partition
                found_efi = False
                found_boot = False
                needs_efi = self.setup.gptonefi
                needs_boot = False
                # Set EFI minimum size to 100 for backwards compatibility
                efi_min_size = 100
                boot_min_size = self.setup.min_boot_size_mb
                if self.nopartitioncheck:
                    efi_min_size = 0
                    boot_min_size = 0
                part_name = 'EFI'
                for partition in self.setup.partitions:
                    # Check if we need a /boot partition
                    if partition.mount_as == partitioning.ROOT_MOUNT_POINT and partition.format_as == 'f2fs':
                        needs_boot = True
                    # Check if this a /boot or /boot/efi partition
                    if not found_boot and partition.mount_as == partitioning.BOOT_MOUNT_POINT:
                        part_name = 'boot'
                        if int(float(partition.partition.getLength('MiB'))) < boot_min_size:
                            ErrorDialog(_("Installation Tool"), _("The {part_name} partition is too small.\nIt must be at least {boot_min_size} MiB.".format(part_name=part_name, boot_min_size=boot_min_size)))
                            return
                        if (partition.format_as and 'ext' not in partition.format_as) or \
                           (not partition.format_as and 'ext' not in partition.type):
                            # Force /boot to be ext2/3/4
                            needs_boot = True
                            found_boot = False
                        else:
                            found_boot = True
                    if not found_efi and partition.mount_as == partitioning.EFI_MOUNT_POINT:
                        part_name = 'EFI'
                        if int(float(partition.partition.getLength('MiB'))) < efi_min_size:
                            ErrorDialog(_("Installation Tool"), _("The {part_name} partition is too small.\nIt must be at least {efi_min_size} MiB.".format(part_name=part_name, efi_min_size=self.setup.min_efi_size_mb)))
                            return
                        if (partition.format_as and 'fat' not in partition.format_as) or \
                           (not partition.format_as and 'fat' not in partition.type):
                            # Force /boot/efi to be vfat
                            needs_efi = True
                            found_efi = False
                        else:
                            found_efi = True
                    
                    # Check swap partition
                    if partition.mount_as == partitioning.SWAP_MOUNT_POINT:
                        # Get total memory
                        mem_gib = get_memory_gib()
                        
                        # Calculate minimum, recommended (incl. hibernation) and maximum swap according to:
                        # https://help.ubuntu.com/community/SwapFaq#How_much_swap_do_I_need.3F
                        if mem_gib < 1:
                            min_swap_gib = mem_gib
                            req_swap_gib = mem_gib * 2
                            max_swap_gib = mem_gib * 2
                        else:
                            min_swap_gib = round(math.sqrt(mem_gib))
                            req_swap_gib = mem_gib + min_swap_gib
                            max_swap_gib = mem_gib * 2

                        # Check swap partition to be at least min_swap_gib
                        if int(float(partition.partition.getLength('GiB'))) < min_swap_gib :
                            ErrorDialog(_("Installation Tool"), _("The swap partition does not have the correct size:\n"
                                                                  "Minimum size: {0}GiB\n"
                                                                  "Recommended size: {1}GiB\n"
                                                                  "Maximum size: {2}GiB".format(min_swap_gib, req_swap_gib, max_swap_gib)))
                            return
                        # Swap cannot not be anything but type swap
                        if (partition.format_as and 'swap' not in partition.format_as) or \
                           (not partition.format_as and 'swap' not in partition.type):
                            MessageDialog(_("Installation Tool"),
                                         _("Swap partition {0} is not correctly formatted.\n"
                                         "Live Installer will format the partition to swap.").format(partition.path))
                            partition.format_as = 'swap'

                # Show message if needed
                part_name = ''
                part_mount = ''
                part_format = ''
                min_size = ''
                efi_win = ''
                if needs_boot and not found_boot:
                    part_name = 'boot'
                    part_mount = partitioning.BOOT_MOUNT_POINT
                    part_format = 'ext2/3/4'
                    min_size = "%s MiB" % str(boot_min_size)
                if needs_efi and not found_efi:
                    devider = '' if not part_name else ' + '
                    part_name += "%sEFI" % devider
                    part_mount += "%s%s" % (devider, partitioning.EFI_MOUNT_POINT)
                    part_format += "%svfat (fat32)" % devider
                    min_size += "%s%s MiB" % (devider, str(efi_min_size))
                    efi_win = "\n\n%s" % _("To ensure compatibility with Windows we recommend\n"
                              "you use the first partition of the disk as the EFI partition.")
    
                if self.nopartitioncheck:
                    min_size = '--'

                if part_name != '':
                    ErrorDialog(_("Installation Tool"),
                                _("<b>Please select a %s partition.</b>" % part_name),
                                "%s%s\n" % (_("A {part_name} partition is needed with the following requirements:\n\n"
                                  " - Mount point: {part_mount}\n"
                                  " - Size: at least {min_size}\n"
                                  " - Format: {part_format}".format(part_name=part_name, part_mount=part_mount, min_size=min_size, part_format=part_format)), efi_win))
                    return

                # Check for boot flag: https://en.wikipedia.org/wiki/Boot_flag
                bootFlagFoundPath = None
                bootMountPath = None
                bootRootPath = None
                efi_found = False
                # Loop through all partitions first
                for partition in self.setup.partitions:
                    # Check if boot flag already set
                    if 'boot' in partition.flags:
                        bootFlagFoundPath = partition.path
                    # Is this partition mounted as /boot or /boot/efi
                    if partitioning.BOOT_MOUNT_POINT in partition.mount_as:
                        if not efi_found:
                            bootMountPath = partition.path
                        if "efi" in partition.mount_as:
                            efi_found = True
                    # or as root
                    elif partition.mount_as == partitioning.ROOT_MOUNT_POINT:
                        bootRootPath = partition.path

                # Is this partition mounted as /boot or /boot/efi AND hasn't got a boot flag: set boot flag
                if bootMountPath is not None and bootFlagFoundPath != bootMountPath:
                    self.setup.boot_flag_partition = bootMountPath
                # If no boot flag was found on the system AND a partition is mounted as root: set boot flag
                elif bootFlagFoundPath is None and bootRootPath is not None:
                    self.setup.boot_flag_partition = bootRootPath

                self.setup.print_setup("gtk_interfaces.partitions")
                self.activate_page(self.PAGE_OVERVIEW)
                self.show_overview()
                self.go("treeview_overview").expand_all()
                self.go("button_next").set_label(_("Apply"))
                self.go("img_forward").hide()

            elif(sel == self.PAGE_CUSTOMWARNING):
                self.setup.print_setup("gtk_interfaces.custom_warning")
                self.activate_page(self.PAGE_OVERVIEW)
                self.show_overview()
                self.go("treeview_overview").expand_all()
                self.go("button_next").set_label(_("Apply"))
                self.go("img_forward").hide()
            elif(sel == self.PAGE_OVERVIEW):
                self.setup.print_setup("gtk_interfaces.overview")
                self.activate_page(self.PAGE_INSTALL)
                # do install
                self.go("button_next").set_sensitive(False)
                self.go("img_forward").show()
                self.go("button_back").set_sensitive(False)
                self.go("button_quit").set_sensitive(False)
                # Now it's time to load the slide show
                Slideshow(self.slideshow_browser, self.setup.language).start()
                # Create installer thread
                name = "installer"
                t = installer.InstallerEngine(self.queue, self.setup)
                self.threads[name] = t
                #t.daemon = True
                t.start()
                self.queue.join()
                GLib.timeout_add(100, self.check_thread, name)
            elif(sel == self.PAGE_CUSTOMPAUSED):
                self.setup.print_setup("gtk_interfaces.custom_paused")
                self.activate_page(self.PAGE_INSTALL)
                self.go("button_next").hide()
                if self.threads["installer"].is_alive():
                    self.threads["installer"].unpause()
        else:
            self.go("button_back").set_sensitive(True)
            if(sel == self.PAGE_OVERVIEW):
                if (self.setup.skip_mount):
                    self.activate_page(self.PAGE_CUSTOMWARNING)
                else:
                    if self.setup.oem_setup:
                        self.activate_page(self.PAGE_USER)
                    else:
                        self.activate_page(self.PAGE_PARTITIONS)
            elif(sel == self.PAGE_CUSTOMWARNING):
                self.activate_page(self.PAGE_PARTITIONS)
            elif(sel == self.PAGE_PARTITIONS):
                self.activate_page(self.PAGE_USER)
            elif(sel == self.PAGE_USER):
                self.activate_page(self.PAGE_KEYBOARD)
            elif(sel == self.PAGE_KEYBOARD):
                self.activate_page(self.PAGE_TIMEZONE)
            elif(sel == self.PAGE_TIMEZONE):
                self.activate_page(self.PAGE_LANGUAGE)

    def check_thread(self, name):
        if self.threads[name].is_alive():
            #self.set_progress()
            if not self.queue.empty():
                ret = self.queue.get()
                #print((">> Queue returns: {}".format(ret)))
                self.queue.task_done()
                self.show_installation_info(ret)
            return True

        # Thread is done
        #print((">> Installation thread is done"))
        critical_error_happened = False
        if not self.queue.empty():
            ret = self.queue.get()
            if ret[0] == installer.ERROR:
                critical_error_happened = True
            #print((">> Queue returns: {}".format(ret)))
            self.queue.task_done()
            self.show_installation_info(ret)
        del self.threads[name]

        if not critical_error_happened:
            if self.setup.oem_setup:
                # Remove EOM setup files
                fls = ["/etc/xdg/autostart/oem-setup.desktop",
                       "/etc/sudoers.d/oem-no-pwd",
                       "/root/filesystem.packages-remove"]
                for f in fls:
                    if exists(f):
                        os.remove(f)

                # Remove OEM user after reboot
                rm_oem_user = "/usr/sbin/remove-oem-user"
                systemd_service = "/lib/systemd/system/remove-oem-user.service"
                with open(rm_oem_user, "w") as scr:
                    cont = "#!/bin/bash\n" \
                           "deluser --remove-home %s\n" \
                           "systemctl disable remove-oem-user\n" \
                           "rm %s 2>/dev/null\n" \
                           "rm %s 2>/dev/null\n" % (self.setup.logged_user, systemd_service, rm_oem_user)
                    scr.write(cont)
                os.system("chmod +x %s" % rm_oem_user)

                with open(systemd_service, "w") as srv:
                    cont = "[Unit]\n" \
                           "Description=Remove OEM user\n\n" \
                           "[Service]\n" \
                           "ExecStart=%s\n" \
                           "Type=oneshot\n" \
                           "User=root\n\n" \
                           "[Install]\n" \
                           "WantedBy=multi-user.target" % rm_oem_user
                    srv.write(cont)
                shell_exec("systemctl enable remove-oem-user")

                # Done: reboot
                MessageDialog(_("Setup finished"),
                              _("Setup is complete. The system will now reboot."))
                answer = True

            else:
                # Done: ask to reboot
                answer = QuestionDialog(_("Installation finished"),
                                        _("Installation is now complete. Do you want to restart your computer to use the new system?"))
            if answer:
                # Reboot
                shell_exec('reboot')
            else:
                # Just quit the application
                Gtk.main_quit()
                return False
        return False

    def show_installation_info(self, list_info):
        if len(list_info) > 0:
            if list_info[0] == installer.ERROR:
                print(("ERROR: %s" % list_info[2]))
                ErrorDialog(list_info[1], list_info[2])
            elif list_info[0] == installer.WARNING:
                print(("WARNING: %s" % list_info[2]))
                WarningDialog(list_info[1], list_info[2])
            elif list_info[0] == installer.PAUSE:
                print(("PAUSE: %s" % list_info[2]))
                self.activate_page(self.PAGE_CUSTOMPAUSED)
                self.go("button_next").show()
                MessageDialog(list_info[1], list_info[2])
                self.go("button_next").set_sensitive(True)

                try:
                    # Show fstab and a chrooted terminal
                    # TODO: xdg-open opens LO in SolydK, even after changing mime association with xdg-mime for root
                    app = getoutput("which kate")
                    if app == "":
                        app = "xdg-open"
                    ExecuteThreadedCommands("sudo -H %s %s/etc/fstab &" % (app, self.setup.target_dir)).start()
                except Exception as detail:
                    print((">> Cannot open /etc/fstab for editing: {}".format(detail)))

                try:
                    ExecuteThreadedCommands("sudo -H x-terminal-emulator --hold -e \"chroot %s\" &" % self.setup.target_dir).start()
                except Exception as detail:
                    print((">> Cannot open a chrooted terminal: {}".format(detail)))

            elif list_info[0] == installer.UPDATE:
                #print(("UPDATE: %s" % list_info[6]))
                self.update_progress(list_info[1], list_info[2], list_info[3], list_info[4], list_info[5], list_info[6])

    def show_overview(self):
        bold = lambda str: '<b>' + str + '</b>'
        model = Gtk.TreeStore(str)
        self.go("treeview_overview").set_model(model)
        top = model.append(None, (_("Localization"),))
        model.append(top, (_("Language: ") + bold(self.setup.language),))
        model.append(top, (_("Timezone: ") + bold(self.setup.timezone),))
        model.append(top, (_("Keyboard layout: ") +
                           "<b>%s - %s %s</b>" % (self.setup.keyboard_model_description, self.setup.keyboard_layout_description,
                                                  '(%s)' % self.setup.keyboard_variant_description if self.setup.keyboard_variant_description else ''),))
        top = model.append(None, (_("User settings"),))
        model.append(top, (_("Real name: ") + bold(self.setup.real_name),))
        model.append(top, (_("Username: ") + bold(self.setup.username),))
        model.append(top, (_("Automatic login: ") + bold(_("enabled") if self.setup.autologin else _("disabled")),))
        top = model.append(None, (_("System settings"),))
        model.append(top, (_("Hostname: ") + bold(self.setup.hostname),))

        if not self.setup.oem_setup:
            top = model.append(None, (_("Filesystem operations"),))
            model.append(top, (bold(_("Install Grub on {}").format(self.setup.grub_device)) if self.setup.grub_device else _("Do not install Grub"),))

            if self.setup.skip_mount:
                model.append(top, (bold(_("Use already-mounted %s." % self.setup.target_dir)),))
                return
            for p in self.setup.partitions:
                if p.mount_as:
                    mount = p.mount_as
                    label = ''
                    if p.label != '':
                        label = " ({})".format(p.label)
                    if self.setup.boot_flag_partition == p.path:
                        mount += " ({})".format(_("set boot flag"))
                    model.append(top, (bold(_("Mount {}{} as {}").format(p.path, label, mount)),))

        for p in self.setup.partitions:
            if p.encrypt:
                if self.setup.oem_setup:
                    top = model.append(None, (_("Filesystem operations"),))
                    model.append(top, (bold(_("Encrypt {}").format(p.path)),))
                else:
                    model.append(top, (bold(_("Encrypt {} and format as {}").format(p.path, p.format_as)),))
            elif p.format_as:
                model.append(top, (bold(_("Format {} as {}").format(p.path, p.format_as)),))

    def update_progress(self, fail=False, done=False, pulse=False, total=0, current=0, message=""):
        # Needed to use idle_add to update the UI in python3
        GLib.idle_add(self.threaded_update_progress, fail, done, pulse, total, current, message)

    def threaded_update_progress(self, fail=False, done=False, pulse=False, total=0, current=0, message=""):
        #print "%d/%d: %s" % (current, total, message)
        if(pulse):
            self.go("label_install_progress").set_label(message)
            self.do_progress_pulse(message)
            return
        if(done):
            # cool, finished :D
            self.should_pulse = False
            self.done = done
            self.go("progressbar").set_fraction(1)
            self.go("label_install_progress").set_label(message)
            return
        self.should_pulse = False
        _total = float(total)
        _current = float(current)
        if _current > _total:
            _current = _total
        pct = 0
        if _total > 0:
            pct = float(_current / _total)
        #szPct = int(pct)
        # thread block
        self.go("progressbar").set_fraction(pct)
        self.go("label_install_progress").set_label(message)

    def do_progress_pulse(self, message):
        def pbar_pulse():
            if(not self.should_pulse):
                return False
            self.go("progressbar").pulse()
            return self.should_pulse
        if(not self.should_pulse):
            self.should_pulse = True
            GLib.timeout_add(100, pbar_pulse)
        else:
            # asssume we're "pulsing" already
            self.should_pulse = True
            pbar_pulse()

    # ===================================================================
    # Main window language functions
    # ===================================================================

    def build_lang_list(self):
        # Get system locale, country code (default is 'US') and time zone
        import locale, re
        cur_locale = locale.getdefaultlocale()[0]
        cur_country_code = 'US'
        if '_' in cur_locale:
            cur_country_code = re.split('_|@', cur_locale)[1]
        cur_timezone = getoutput("cat /etc/timezone")
        if not cur_timezone:
            cur_timezone = 'Europe/Amsterdam'
            
        # Try to find out where we're located when running the default US ISO
        if cur_country_code == 'US':
            try:
                # Use IP APIs from configuration file
                addr = None
                for ip_url in self.setup.my_ip.split(','):
                    try:
                        # Test IP4
                        addr = re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', requests.get(ip_url).text).group()
                        ip_ver = 4
                    except:
                        # Test IP6
                        addr = re.search(r'([0-9a-fA-F][0-9a-fA-F]{0,3}:){3,7}:([0-9a-fA-F][0-9a-fA-F]{0,3})', requests.get(ip_url).text).group()
                        ip_ver = 6
                    if addr: break

                # Get country code and time zone
                try:
                    if ip_ver == 6:
                        # Need to load the database manually for IP6
                        db = getoutput("dpkg -S GeoIPv6.dat | awk '{print $2}'")
                        geoip = GeoIP.open(db, GeoIP.GEOIP_STANDARD)
                        cur_country_code = geoip.country_code_by_addr_v6(addr)
                    else:
                        geoip = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)
                        cur_country_code = geoip.country_code_by_addr(addr)
                    
                    # Loop through the timezones
                    for tz in timezones.timezones:
                        if tz.ccode == cur_country_code:
                            cur_timezone = tz.name
                            break
                            
                except ValueError:
                    print(('Warning: address/netmask is invalid: %s' % addr))
            except:
                pass #best effort, we get here if we're not connected to the Internet

        self.cur_country_code = cur_country_code
        self.cur_timezone = cur_timezone

        #Load countries into memory
        countries = {}
        for line in getoutput("isoquery --iso 3166-1 | cut -f1,4-"):
            ccode, cname = line.strip().split(None, 1)
            countries[ccode] = cname

        #Load languages into memory
        languages = {}
        for line in getoutput("isoquery --iso 639-3"):
            c1, c2, c3, c4, c5, language = line.strip().split('\t')
            languages[c4 or c5 or c1] = language

        # Construct language selection model
        set_iter = None
        model = self.treeview_language_list.get_model()
        if model is not None:
            # Select iter in existing model corresponding to current language
            itr = model.get_iter_first()
            while itr is not None:
                locale = model.get_value(itr, 3)
                #print((">> locale = %s" % locale))
                ccode = ''
                if '_' in locale:
                    lang, ccode = locale.split('_')
                else:
                    lang = locale
                if (ccode == self.cur_country_code and
                   (not set_iter or
                    set_iter and lang == 'en' or  # prefer English, or
                    set_iter and lang == ccode.lower())):  # fuzzy: lang matching ccode (fr_FR, de_DE, es_ES, ...)
                    # Found
                    #print((">> Found lang = %s, ccode = %s" % (lang, ccode)))
                    set_iter = itr
                    break
                itr = model.iter_next(itr)
        else:
            model = Gtk.ListStore(str, str, GdkPixbuf.Pixbuf, str)
            flag_path = lambda ccode: self.mediaDir + '/flags/16/' + ccode.lower() + '.png'
            flag = memoize(lambda ccode: GdkPixbuf.Pixbuf.new_from_file(flag_path(ccode)))
            for locale in getoutput("awk -F'[@ \.]' '/UTF-8/{ print $1 }' /usr/share/i18n/SUPPORTED | uniq"):
                locale = locale.strip()
                try:
                    if '_' in locale:
                        lang, ccode = locale.split('_')
                        language, country = languages[lang], countries[ccode]
                    else:
                        lang = locale
                        language = languages[lang]
                        country = ''
                except:
                    print(("Error adding locale '%s'" % locale))
                    continue
                pixbuf = flag(ccode) if not lang in 'eo ia' else flag('_' + lang)
                itr = model.append((language, country, pixbuf, locale))
                if (ccode == self.cur_country_code and
                    (not set_iter or
                     set_iter and lang == 'en' or  # prefer English, or
                     set_iter and lang == ccode.lower())):  # fuzzy: lang matching ccode (fr_FR, de_DE, es_ES, ...)
                    set_iter = itr

            # Sort by Country, then by Language
            model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
            model.set_sort_column_id(1, Gtk.SortType.ASCENDING)

        # Set the model and pre-select the correct language
        self.treeview_language_list.set_model(model)
        if set_iter:
            path = model.get_path(set_iter)
            self.treeview_language_list.set_cursor(path)
            self.treeview_language_list.scroll_to_cell(path)

    # ===================================================================
    # Main window keyboard functions
    # ===================================================================

    def build_kb_lists(self):
        ''' Do some xml kung-fu and load the keyboard stuffs '''
        # Determine the layouts in use
        (keyboard_geom, self.setup.keyboard_layout) = getoutput("setxkbmap -query | awk -F\"(,|[ ]+)\" '/^(model:|layout:)/ {print $2}'")
        # Set default keyboard model if it is not set
        if keyboard_geom == "None":
            keyboard_geom = "pc105"
            shell_exec('setxkbmap -model ' + keyboard_geom)

        # Build the models
        from collections import defaultdict

        def _ListStore_factory():
            model = Gtk.ListStore(str, str)
            model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
            return model

        models = _ListStore_factory()
        layouts = _ListStore_factory()
        variants = defaultdict(_ListStore_factory)
        try:
            import xml.etree.cElementTree as ET
        except ImportError:
            import xml.etree.ElementTree as ET

        xml = ET.parse('/usr/share/X11/xkb/rules/xorg.xml')
        for node in xml.iterfind('.//modelList/model/configItem'):
            name, desc = node.find('name').text, node.find('description').text
            iterator = models.append((desc, name))
            if name == keyboard_geom:
                set_keyboard_model = iterator
                set_keyboard_description = desc
        for node in xml.iterfind('.//layoutList/layout'):
            name, desc = node.find('configItem/name').text, node.find('configItem/description').text
            variants[name].append((desc, None))
            for variant in node.iterfind('variantList/variant/configItem'):
                var_name, var_desc = variant.find('name').text, variant.find('description').text
                var_desc = var_desc if var_desc.startswith(desc) else '{} - {}'.format(desc, var_desc)
                variants[name].append((var_desc, var_name))
            iterator = layouts.append((desc, name))
            if name == self.setup.keyboard_layout:
                set_keyboard_layout = iterator
        # Set the models
        self.go("combobox_kb_model").set_model(models)
        self.go("treeview_layouts").set_model(layouts)
        self.layout_variants = variants
        # Preselect currently active keyboard info
        try:
            self.go("combobox_kb_model").set_active_iter(set_keyboard_model)
            self.setup.keyboard_model_description = set_keyboard_description
        except NameError:
            pass  # set_keyboard_model not set
        try:
            treeview = self.go("treeview_layouts")
            path = layouts.get_path(set_keyboard_layout)
            treeview.set_cursor(path)
            treeview.scroll_to_cell(path)
        except NameError:
            pass  # set_keyboard_layout not set

    def _generate_keyboard_layout_preview(self):
        filename = "/tmp/live-install-keyboard-layout.png"
        shell_exec("python3 /usr/lib/live-installer-3/generate_keyboard_layout.py %s %s %s" % (self.setup.keyboard_layout, self.setup.keyboard_variant, filename))
        self.go("image_keyboard").set_from_file(filename)
        return False

    # ===================================================================
    # Main window partitions functions
    # ===================================================================

    def encrypt_chk_on_toggle(self, cell, path, colNr):
        # Select row first before showing dialog
        # or else the dialog will show the currently selected row
        self.go("treeview_disks").set_cursor(path)
        partitioning.edit_partition_dialog()

    def grub_chk_on_toggle(self, cell, path, colNr):
        if path is not None:
            model = self.go("treeview_disks").get_model()
            if model is not None:
                itr = model.get_iter(path)
                toggled = model[itr][colNr]

                if toggled:
                    answer = QuestionDialog(_("Grub install"),
                                            _("You chose to NOT install Grub on your system.\n"
                                              "Without a bootloader like Grub your system might not boot.\n\n"
                                              "Are you sure you want to continue?"))
                    if not answer:
                        return True

                    # Make sure none are selected
                    loopItr = model.get_iter_first()
                    while loopItr is not None:
                        model[loopItr][colNr] = False

                        # Check children
                        childItr = model.iter_children(loopItr)
                        while childItr is not None:
                            model[childItr][colNr] = False
                            childItr = model.iter_next(childItr)

                        # Get next iter
                        loopItr = model.iter_next(loopItr)

                    # Set grub_device to none
                    self.setup.grub_device = None
                else:
                    # Warn when selecting a partition instead of a disk
                    if ':' in model.get_string_from_iter(itr):
                        answer = QuestionDialog(_("Grub install"),
                                                _("You chose to install Grub on a partition.\n"
                                                  "It is recommended to install Grub on a disk instead of a partition.\n\n"
                                                  "Only continue if you are certain that you have another bootloader already installed.\n\n"
                                                  "Are you sure you want to continue?"))
                        if not answer:
                            return True

                    # Deselect all other devices first
                    loopItr = model.get_iter_first()
                    while loopItr is not None:
                        model[loopItr][colNr] = False

                        # Check children
                        childItr = model.iter_children(loopItr)
                        while childItr is not None:
                            model[childItr][colNr] = False
                            childItr = model.iter_next(childItr)

                        # Get next iter
                        loopItr = model.iter_next(loopItr)

                    model[itr][colNr] = True
                    # Set grub_device to selected device
                    device = model.get_value(itr, partitioning.IDX_PART_PATH).split(' ')[0]
                    print(("Selected grub_device: {}".format(device)))
                    self.setup.grub_device = device

    ## ===================================================================
    ## Main window user info functions
    ## ===================================================================

    def assign_password(self):
        ''' Someone typed into the entry '''
        self.setup.password1 = self.go("entry_userpass1").get_text()
        self.setup.password2 = self.go("entry_userpass2").get_text()
        if(self.setup.password1 == "" and self.setup.password2 == ""):
            self.go("image_mismatch").hide()
        else:
            self.go("image_mismatch").show()
        if(self.setup.password1 != self.setup.password2):
            self.go("image_mismatch").set_from_icon_name('dialog-no', Gtk.IconSize.BUTTON)
        else:
            self.go("image_mismatch").set_from_icon_name('dialog-ok', Gtk.IconSize.BUTTON)

    def assign_enc_password(self):
        ''' Someone typed into the entry '''
        self.setup.oem_home_encryption_pwd1 = self.go("entry_encpass1").get_text()
        self.setup.oem_home_encryption_pwd2 = self.go("entry_encpass2").get_text()
        if(self.setup.oem_home_encryption_pwd1 == "" and self.setup.oem_home_encryption_pwd2 == ""):
            self.go("image_enc_mismatch").hide()
        else:
            self.go("image_enc_mismatch").show()
        if(self.setup.oem_home_encryption_pwd1 != self.setup.oem_home_encryption_pwd2):
            self.go("image_enc_mismatch").set_from_icon_name('dialog-no', Gtk.IconSize.BUTTON)
        else:
            self.go("image_enc_mismatch").set_from_icon_name('dialog-ok', Gtk.IconSize.BUTTON)

    def _on_face_browse_menuitem_activated(self, menuitem):
        title = _("Select image")
        dialog = SelectImageDialog(title)
        path = dialog.show()
        if path is not None:
            try:
                image = PIL.Image.open(path)
                width, height = image.size
                if width > height:
                    new_width = height
                    new_height = height
                elif height > width:
                    new_width = width
                    new_height = width
                else:
                    new_width = width
                    new_height = height
                left = (width - new_width) // 2
                top = (height - new_height) // 2
                right = (width + new_width) // 2
                bottom = (height + new_height) // 2
                image = image.crop((left, top, right, bottom))
                image.thumbnail((96, 96), PIL.Image.ANTIALIAS)
                image.save(TMP_FACE, "png")
                self.setup.face_button.set_picture_from_file(TMP_FACE)
            except Exception as detail:
                ErrorDialog(title,
                            "{}\n\n{}".format(_("Unable to convert the image."), detail))

    def _on_face_menuitem_activated(self, path):
        if exists(path):
            shell_exec("cp %s TMP_FACE" % (path, TMP_FACE))
            print(path)
            return True

    def _on_face_take_picture_button_clicked(self, menuitem):
        # streamer/fswebcam takes 8 pictures
        if exists('/usr/bin/streamer'):
            cmd = 'streamer -s 800x600 -j 90 -t 8 -o /tmp/live-installer-3-face00.jpeg'
        elif exists('/usr/bin/fswebcam'):
            cmd = 'fswebcam -r 800x600 --jpeg 90 -F 8 /tmp/live-installer-3-face00.jpeg'
        if 0 != os.system(cmd):
            return  # Error, no webcam
        
        # Get the latest jpeg
        files = glob('/tmp/live-installer-3-face*.jpeg')
        files.sort(key=getctime)
        jpeg = files[-1]
        
        # Convert and resize
        if exists(jpeg):
            os.system('convert {0} -crop 600x600+100+0 -resize 96x96 {1}'.format(jpeg, TMP_FACE))
            self.setup.face_button.set_picture_from_file(TMP_FACE)
