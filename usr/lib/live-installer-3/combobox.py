#!/usr/bin/env python3

# Make sure the right Gtk version is loaded
import gi
gi.require_version('Gtk', '3.0')

from gi.repository import Gtk, GObject


class ComboBoxHandler(GObject.GObject):

    def __init__(self, combobox):
        GObject.GObject.__init__(self)
        self.combobox = combobox

    # Clear treeview
    def clearComboBox(self):
        liststore = self.combobox.get_model()
        if liststore is not None:
            liststore.clear()
            self.combobox.set_model(liststore)
        self.setValue("")

    def fillComboBox(self, dataList, select_value=None):
        self.clearComboBox()
        liststore = self.combobox.get_model()
        if liststore is None:
            if not self.combobox.get_has_entry():
                cell = Gtk.CellRendererText()
                self.combobox.pack_start(cell, True)
                self.combobox.add_attribute(cell, "text", 0)
        if self.combobox.get_has_entry():
            entry = self.combobox.get_child()
            entry.set_can_focus(True)

        # Dirty but need to dynamically create a list store
        multiCols = self.isListOfLists(dataList)
        dynListStore = 'Gtk.ListStore('
        if multiCols:
            for i in range(len(dataList[0])):
                dynListStore += 'str, '
        else:
            dynListStore += 'str'
        dynListStore += ')'
        msg = "Create list store eval string: %(eval)s" % { "eval": dynListStore }
        print(msg)
        liststore = eval(dynListStore)

        # Add the data
        for i in range(len(dataList)):
            dynListStoreAppend = 'liststore.append(['
            if multiCols:
                for j in range(len(dataList[i])):
                    val = str(dataList[i][j])
                    # Make sure it's a single line
                    val = '"' + val.replace('\n', ' ').replace('\r', '') + '"'
                    dynListStoreAppend += '%s, ' % val
            else:
                val = dataList[i]
                val = '"' + val.replace('\n', ' ').replace('\r', '') + '"'
                dynListStoreAppend += val
            dynListStoreAppend += '])'
            msg = "Add data to list store: %(data)s" % { "data": dynListStoreAppend }
            print(msg)
            eval(dynListStoreAppend)

        self.combobox.set_model(liststore)
        if select_value is not None:
            self.selectValue(select_value)

    def selectValue(self, value, valueColNr=0):
        i = 0
        activeIndex = -1
        liststore = self.combobox.get_model()
        for data in liststore:
            if data[valueColNr] == value:
                activeIndex = i
                break
            i += 1
        if self.combobox.get_has_entry():
            self.combobox.set_entry_text_column(valueColNr)
            if activeIndex < 0:
                self.setValue(value)
        else:
            if activeIndex < 0:
                activeIndex = 0
        self.combobox.set_active(activeIndex)

    def setValue(self, value):
        if self.combobox.get_has_entry():
            entry = self.combobox.get_child()
            entry.set_text(value)

    def getValue(self):
        value = None
        if self.combobox.get_has_entry():
            entry = self.combobox.get_child()
            value = entry.get_text().strip()
        else:
            model = self.combobox.get_model()
            active_iter = self.combobox.get_active_iter()
            if active_iter is not None:
                value = model.get_value(active_iter, 0)
        return value

    # Check if parameter is a list containing lists
    def isListOfLists(self, lst):
        return len(lst) == len([x for x in lst if isinstance(x, list)])

# Register the class
GObject.type_register(ComboBoxHandler)
