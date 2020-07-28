#!/usr/bin/env python3

# WebKit1 reference: https://webkitgtk.org/reference/webkitgtk/stable
# WebKit2 reference: https://webkitgtk.org/reference/webkit2gtk/stable

# Make sure you have WebKit2 2.22.x or higher installed.
# For Debian Stretch you need the backports packages:
# apt install -t stretch-backports gir1.2-javascriptcoregtk-4.0 gir1.2-webkit2-4.0 libjavascriptcoregtk-4.0-18 libwebkit2gtk-4.0-37  libwebkit2gtk-4.0-37-gtk2

# Make sure you have this JavaScript in your HTML when using WebKit2:
# <script>
# function get_checked_values(class_name) {
# var e = document.getElementsByClassName(class_name); var r = []; var c = 0;
# if (e.length == 0) { e = document.getElementsById(class_name); }
# for (var i = 0; i < e.length; i++) {
# if (e[i].checked) { r[c] = e[i].value; c++;}
# }return r;}
# </script>

WEBKIT2 = False

import gi
try:
    gi.require_version('WebKit', '3.0')
    from gi.repository import WebKit
except:
    gi.require_version('WebKit2', '4.0')
    from gi.repository import WebKit2 as WebKit
    WEBKIT2 = True
from gi.repository import GObject
from os.path import exists
import webbrowser
import re
import sys


class SimpleBrowser(WebKit.WebView):
    # Create custom signals
    __gsignals__ = {
        "js-finished" : (GObject.SignalFlags.RUN_LAST, GObject.TYPE_NONE, ()),
        "html-response-finished" : (GObject.SignalFlags.RUN_LAST, GObject.TYPE_NONE, ()),
        "html-load-finished" : (GObject.SignalFlags.RUN_LAST, GObject.TYPE_NONE, ())
    }
    
    def __init__(self):
        WebKit.WebView.__init__(self)
        
        # Check version
        if WEBKIT2:
            webkit_ver = WebKit.get_major_version(), WebKit.get_minor_version(), WebKit.get_micro_version()
            if webkit_ver[0] < 2 or \
               webkit_ver[1] < 22:
                   raise Exception('WebKit2 wrong version ({0}). Upgrade to version 2.22.x or higher: {}'.format('.'.join(map(str, webkit_ver))))
                   sys.exit()
        
        # Store JS output
        self.js_values = []
        # Store html response
        self.html_response = ''

        # WebKit2 Signals
        if WEBKIT2:
            self.connect('decide-policy', self.on_decide_policy)
            self.connect("load_changed", self.on_load_changed)
            self.connect('button-press-event', lambda w, e: e.button == 3)
        else:
            self.connect('new-window-policy-decision-requested', self.on_nav_request)
            self.connect('resource-load-finished', self.on_resource_load_finished)
            self.connect('button-press-event', lambda w, e: e.button == 3)
        
        # Settings
        s = self.get_settings()
        if WEBKIT2:
            s.set_property('allow_file_access_from_file_urls', True)
            s.set_property('enable-spatial-navigation', False)
            s.set_property('enable_javascript', True)
        else:
            s.set_property('enable-file-access-from-file-uris', True)
            s.set_property('enable-default-context-menu', False)

    def show_html(self, html_or_url):
        if exists(html_or_url):
            matchObj = re.search('^file:\/\/', html_or_url)
            if not matchObj:
                html_or_url = "file://{0}".format(html_or_url)
        matchObj = re.search('^[a-z]+:\/\/', html_or_url)
        if matchObj:
            if WEBKIT2:
                self.load_uri(html_or_url)
            else:
                self.open(html_or_url)
        else:
            if WEBKIT2:
                self.load_html(html_or_url)
            else:
                self.load_string(html_or_url, 'text/html', 'UTF-8', 'file://')
        self.show()

    def get_element_values(self, element_name):
        if WEBKIT2:
            self.js_run('get_checked_values("{0}")'.format(element_name))
        else:
            values = []
            doc = self.get_dom_document()
            # https://webkitgtk.org/reference/webkitdomgtk/stable/WebKitDOMDocument.html#webkit-dom-document-get-elements-by-name
            # https://webkitgtk.org/reference/webkitdomgtk/stable/WebKitDOMNodeList.html#webkit-dom-node-list-item
            elements = doc.get_elements_by_name(element_name)
            for i in range(elements.get_length()):
                # https://webkitgtk.org/reference/webkitdomgtk/stable/WebKitDOMHTMLInputElement.html
                child = elements.item(i)
                if child.get_checked():
                    values.append(child.get_value().strip())
            # Get return value in line with WebKit2
            self.js_values = values
            self.emit('js-finished')
        
    def js_run(self, function_name, js_return=True):
        if WEBKIT2:
            # JavaScript
            # https://webkitgtk.org/reference/webkit2gtk/stable/WebKitWebView.html#webkit-web-view-run-javascript
            run_js_finish = self._js_finish if js_return else None
            self.run_javascript(function_name, None, run_js_finish, None);
        else:
            raise Exception('WebKit2 method only. Use get_element_values.')
        
    def _js_finish(self, webview, result, user_data=None):
        if WEBKIT2:
            # https://webkitgtk.org/reference/webkit2gtk/stable/WebKitWebView.html#webkit-web-view-run-javascript-finish
            js_result = self.run_javascript_finish(result)
            if js_result is not None:
                # https://webkitgtk.org/reference/jsc-glib/stable/JSCValue.html
                # Couldn't handle anything but string :(
                # If returning the getElementsByClassName object itself: GLib.Error: WebKitJavascriptError:  (699)
                value = js_result.get_js_value().to_string()
                self.js_values = value.split(',')
                #print((self.js_values))
                self.emit('js-finished')
        else:
            raise Exception('WebKit2 method only. Use get_element_values.')
            
    def get_response_data(self):
        # Get html of loaded page
        if WEBKIT2:
            resource = self.get_main_resource()
            resource.get_data(None, self._get_response_data_finish, None)
        else:
            frame = self.get_main_frame()
            # Get return value in line with WebKit2
            self.html_response = frame.get_data_source().get_data()
            self.emit('html-response-finished')

    def  _get_response_data_finish(self, resource, result, user_data=None):
        if WEBKIT2:
            # Callback from get_response_data
            self.html_response = resource.get_data_finish(result).decode("utf-8")
            self.emit('html-response-finished')
        else:
            raise Exception('WebKit2 method only. Use get_response_data.')
            
    def on_decide_policy(self, webview, decision, decision_type):
        # WebKit2: User clicked on a <a href link: open uri in new tab or new default webview
        if (decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION):
            action = decision.get_navigation_action()
            action_type = action.get_navigation_type()
            if action_type == WebKit.NavigationType.LINK_CLICKED:
                decision.ignore()
                uri = action.get_request().get_uri()
                # Open link in default browser
                webbrowser.open_new_tab(uri)
        else:
            if decision is not None:
                decision.use()
                
    def on_nav_request(self, webview, frame, request, action, decision):
        # WebKit1: User clicked on a <a href link: open uri in new tab or new default browser
        reason = action.get_reason()
        if (reason == WebKit.WebNavigationReason.LINK_CLICKED):
            if decision is not None:
                decision.ignore()
                uri = request.get_uri()
                webbrowser.open_new_tab(uri)
        else:
            if decision is not None:
                decision.use()
                
    def on_load_changed(self, webview, event):
        # WebKit2: signal loading page finished
        if event == WebKit.LoadEvent.FINISHED:
            self.emit('html-load-finished')
        
    def on_resource_load_finished(self, webview, frame, resource, user_data=None):
        # WebKit1: signal loading page finished
        self.emit('html-load-finished')

