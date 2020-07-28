#!/usr/bin/env python3

# Make sure the right Gtk version is loaded
import gi
gi.require_version('Gtk', '3.0')

from gi.repository import Gtk, GLib, GdkPixbuf
from os.path import exists


DIALOG_TYPES = {
    Gtk.MessageType.INFO: 'MessageDialog',
    Gtk.MessageType.ERROR: 'ErrorDialog',
    Gtk.MessageType.WARNING: 'WarningDialog',
    Gtk.MessageType.QUESTION: 'QuestionDialog',
}


# Show message dialog
# Usage:
# MessageDialog(_("My Title"), "Your message here")
# Use safe=False when calling from a thread
class Dialog(Gtk.MessageDialog):
    def __init__(self, message_type, buttons, title, text, text2=None, parent=None, safe=True, icon=None):
        parent = parent or next((w for w in Gtk.Window.list_toplevels() if w.get_title()), None)
        Gtk.MessageDialog.__init__(self, 
                                   parent=None, 
                                   modal=True, 
                                   destroy_with_parent=True, 
                                   message_type=message_type, 
                                   buttons=buttons,
                                   text=text)
        self.set_position(Gtk.WindowPosition.CENTER)
        if parent is not None:
            self.set_icon(parent.get_icon())
        elif icon is not None:
            if exists(icon):
                self.set_icon_from_file(icon)
            else:
                self.set_icon_name(icon)
        self.set_title(title)
        self.set_markup(text)
        self.desc = text[:30] + ' ...' if len(text) > 30 else text
        self.dialog_type = DIALOG_TYPES[message_type]
        if text2: self.format_secondary_markup(text2)
        self.safe = safe
        if not safe:
            self.connect('response', self._handle_clicked)

    def _handle_clicked(self, *args):
        self.destroy()

    def show(self):
        if self.safe:
            return self._do_show_dialog()
        else:
            return GLib.timeout_add(0, self._do_show_dialog)

    def _do_show_dialog(self):
        """ Show the dialog.
            Returns True if user response was confirmatory.
        """
        #print(('Showing {0.dialog_type} ({0.desc})'.format(self)))
        try: return self.run() in (Gtk.ResponseType.YES, Gtk.ResponseType.APPLY,
                                   Gtk.ResponseType.OK, Gtk.ResponseType.ACCEPT)
        finally:
            if self.safe:
                self.destroy()
            else:
                return False


def MessageDialog(*args):
    return Dialog(Gtk.MessageType.INFO, Gtk.ButtonsType.OK, *args).show()


def QuestionDialog(*args):
    return Dialog(Gtk.MessageType.QUESTION, Gtk.ButtonsType.YES_NO, *args).show()


def WarningDialog(*args):
    return Dialog(Gtk.MessageType.WARNING, Gtk.ButtonsType.OK, *args).show()


def ErrorDialog(*args):
    return Dialog(Gtk.MessageType.ERROR, Gtk.ButtonsType.OK, *args).show()


# Create a custom question dialog
# Usage:
# dialog = CustomQuestionDialog(_("My Title"), myCustomObject, 600, 450, parentWindow))
#    if (dialog.show()):
# CustomQuestionDialog can NOT be called from a working thread, only from main (UI) thread
class CustomQuestionDialog(Gtk.Dialog):
    def __init__(self, title, myObject, width=500, height=300, parent=None):
        self.title = title
        self.myObject = myObject
        self.parent = parent
        self.width = width
        self.height = height

    def show(self):
        dialog = Gtk.Dialog(title=self.title, 
                            parent=self.parent, 
                            modal=True, 
                            destroy_with_parent=True)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.set_default_size(self.width, self.height)
        if self.parent is not None:
            dialog.set_icon(self.parent.get_icon())

        buttonbox = dialog.get_action_area()
        buttons = buttonbox.get_children()
        dialog.set_focus(buttons[0])

        dialog.vbox.pack_start(self.myObject, True, True, 0)
        dialog.show_all()

        answer = dialog.run()
        if answer == Gtk.ResponseType.OK:
            return_value = True
        else:
            return_value = False
        dialog.destroy()
        return return_value


# You can pass a Gtk.FileFilter object.
# Use add_mime_type, and add_pattern.
# Get the mime type of a file: $ mimetype [file]
# e.g.: $ mimetype solydx32_201311.iso
#         solydx32_201311.iso: application/x-cd-image
class SelectFileDialog(object):
    def __init__(self, title, start_directory=None, parent=None, gtkFileFilter=None):
        self.title = title
        self.start_directory = start_directory
        self.parent = parent
        self.gtkFileFilter = gtkFileFilter
        self.isImages = False
        if gtkFileFilter is not None:
            if gtkFileFilter.get_name() == "Images":
                self.isImages = True

    def show(self):
        filePath = None
        image = Gtk.Image()

        # Image preview function
        def image_preview_cb(dialog):
            filename = dialog.get_preview_filename()
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(filename, 128, 128)
                image.set_from_pixbuf(pixbuf)
                valid_preview = True
            except:
                valid_preview = False
            dialog.set_preview_widget_active(valid_preview)

        dialog = Gtk.FileChooserDialog(title=self.title, 
                                       parent=self.parent, 
                                       action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        if self.start_directory is not None:
            dialog.set_current_folder(self.start_directory)
        if self.gtkFileFilter is not None:
            dialog.add_filter(self.gtkFileFilter)

        if self.isImages:
            # Add a preview widget:
            dialog.set_preview_widget(image)
            dialog.connect("update-preview", image_preview_cb)

        answer = dialog.run()
        if answer == Gtk.ResponseType.OK:
            filePath = dialog.get_filename()
        dialog.destroy()
        return filePath


class SelectImageDialog(object):
    def __init__(self, title, start_directory=None, parent=None):
        self.title = title
        self.start_directory = start_directory
        self.parent = parent

    def show(self):
        fleFilter = Gtk.FileFilter()
        fleFilter.set_name("Images")
        fleFilter.add_mime_type("image/png")
        fleFilter.add_mime_type("image/jpeg")
        fleFilter.add_mime_type("image/gif")
        fleFilter.add_pattern("*.png")
        fleFilter.add_pattern("*.jpg")
        fleFilter.add_pattern("*.gif")
        fleFilter.add_pattern("*.tif")
        fleFilter.add_pattern("*.xpm")
        fdg = SelectFileDialog(self.title, self.start_directory, self.parent, fleFilter)
        return fdg.show()


class SelectDirectoryDialog(object):
    def __init__(self, title, start_directory=None, parent=None):
        self.title = title
        self.start_directory = start_directory
        self.parent = parent

    def show(self):
        directory = None
        dialog = Gtk.FileChooserDialog(title=self.title, 
                                       parent=self.parent, 
                                       action=Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        if self.start_directory is not None:
            dialog.set_current_folder(self.start_directory)
        answer = dialog.run()
        if answer == Gtk.ResponseType.OK:
            directory = dialog.get_filename()
        dialog.destroy()
        return directory


class InputDialog(Gtk.MessageDialog):
    def __init__(self, title, text, text2=None, parent=None, default_value='', is_password=False):
        parent = parent or next((w for w in Gtk.Window.list_toplevels() if w.get_title()), None)

        Gtk.MessageDialog.__init__(self, parent=parent, 
                                   modal=True, 
                                   destroy_with_parent=True, 
                                   message_type=Gtk.MessageType.QUESTION, 
                                   buttons=Gtk.ButtonsType.OK,
                                   text=text)
        self.set_position(Gtk.WindowPosition.CENTER)
        if parent is not None:
            self.set_icon(parent.get_icon())
        self.set_title(title)
        self.set_markup(text)
        if text2: self.format_secondary_markup(text2)

        # Add entry field
        entry = Gtk.Entry()
        if is_password:
            entry.set_visibility(False)
        entry.set_text(default_value)
        entry.connect("activate",
                      lambda ent, dlg, resp: dlg.response(resp),
                      self, Gtk.ResponseType.OK)
        self.vbox.pack_end(entry, True, True, 0)
        self.vbox.show_all()
        self.entry = entry

        self.set_default_response(Gtk.ResponseType.OK)

    def set_value(self, text):
        self.entry.set_text(text)

    def show(self):
        try:
            result = self.run()
            if result == Gtk.ResponseType.OK:
                return self.entry.get_text().strip()
            else:
                return ''
        except Exception as detail:
            print((">> InputDialog error: {}".format(detail)))
            return ''
        finally:
            self.destroy()
