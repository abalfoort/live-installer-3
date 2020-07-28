#!/usr/bin/env python3

import re
from os.path import join, exists, dirname, realpath
from utils import chroot_exec, shell_exec, \
                  does_package_exist, is_package_installed, \
                  find_file

# i18n: http://docs.python.org/3/library/gettext.html
import gettext
from gettext import gettext as _
gettext.textdomain('live-installer-3')


class Localize():
    def __init__(self, setup, target_dir):
        self.setup = setup
        self.language = setup.language
        self.username = setup.username
        self.target_dir = target_dir
        self.locale = self.language.lower().split("_")
        self.scriptDir = dirname(realpath(__file__))

    def set_progress_hook(self, progresshook):
        ''' Set a callback to be called on progress updates '''
        ''' i.e. def my_callback(progress_type, message, current_progress, total) '''
        ''' Where progress_type is any off PROGRESS_START, PROGRESS_UPDATE, PROGRESS_COMPLETE, PROGRESS_ERROR '''
        self.update_progress = progresshook

    def start(self):
        self.applications()
        self.languageSpecific()

    def languageSpecific(self):
        localizeConf = join(self.scriptDir, "localize/%s" % self.language)
        if exists(localizeConf):
            with open(localizeConf, 'r') as f:
                packages = f.read()
            if packages:
                try:
                    print((" --> Localizing for %s" % self.language))
                    self.update_progress(message=_("Install additional localized packages"))
                    self.exec_cmd("apt-get install %s" % packages)
                except Exception as detail:
                    msg = "ERROR: %s" % detail
                    print(msg)
                    self.update_progress(message=msg)

    def applications(self):
        apt_action = 'install'
        if self.language == "en_US":
            apt_action = 'purge'
        spellchecker = False
        # Localize KDE
        if is_package_installed("kde-runtime"):
            print(" --> Localizing KDE")
            self.update_progress(message=_("Localizing KDE"))
            package = self.get_localized_package("kde-l10n")
            if package != "":
                self.exec_cmd("apt-get %s %s" % (apt_action, package))

        # Localize LibreOffice
        if is_package_installed("libreoffice"):
            print(" --> Localizing LibreOffice")
            self.update_progress(message=_("Localizing LibreOffice"))
            package = self.get_localized_package("libreoffice-l10n")
            if package != "":
                self.exec_cmd("apt-get %s %s" % (apt_action, package))
            package = self.get_localized_package("libreoffice-help")
            if package != "":
                self.exec_cmd("apt-get %s %s" % (apt_action, package))
            if not spellchecker:
                package = self.get_localized_package("hunspell")
                if package == "":
                    package = self.get_localized_package("myspell")
                if package != "":
                    spellchecker = True
                    self.exec_cmd("apt-get %s %s" % (apt_action, package))

        # Localize AbiWord
        if is_package_installed("abiword"):
            print(" --> Localizing AbiWord")
            self.update_progress(message=_("Localizing AbiWord"))
            package = self.get_localized_package("aspell")
            if package != "":
                self.exec_cmd("apt-get %s %s" % (apt_action, package))

        # Localize Firefox
        ff = "firefox"
        esr = ""
        isESR = is_package_installed("firefox-esr")
        if isESR:
            ff = "firefox-esr"
            esr = "esr-"
        if isESR or is_package_installed("firefox"):
            print(" --> Localizing %s" % ff)
            self.update_progress(message=_("Localizing Firefox"))
            package = self.get_localized_package("firefox-%sl10n" % esr)
            if package != "":
                self.exec_cmd("apt-get %s %s" % (apt_action, package))
            if not spellchecker:
                package = self.get_localized_package("hunspell")
                if package == "":
                    package = self.get_localized_package("myspell")
                if package != "":
                    spellchecker = True
                    self.exec_cmd("apt-get %s %s" % (apt_action, package))

        # Localize Thunderbird
        if is_package_installed("thunderbird"):
            print(" --> Localizing Thunderbird")
            self.update_progress(message=_("Localizing Thunderbird"))
            package = self.get_localized_package("thunderbird-l10n")
            if package != "":
                self.exec_cmd("apt-get %s %s" % (apt_action, package))
            # lightning-l10n has been integrated into thunderbird-l10n
            #if is_package_installed("lightning"):
            #    print(" --> Localizing Lightning")
            #    package = self.get_localized_package("lightning-l10n")
            #    if package != "":
            #        self.exec_cmd("apt-get %s %s" % (apt_action, package))
            if not spellchecker:
                package = self.get_localized_package("hunspell")
                if package == "":
                    package = self.get_localized_package("myspell")
                if package != "":
                    spellchecker = True
                    self.exec_cmd("apt-get %s %s" % (apt_action, package))
                    
        # Localize Mozilla parameters in distribution.ini
        dict_lan = self.language.replace('_', '-')
        moz_lan = '' if self.language == 'en_US' else self.language
        inis = ["%s/usr/lib/firefox-esr/distribution/distribution.ini" % self.target_dir,
                "%s/usr/lib/firefox/distribution/distribution.ini" % self.target_dir,
                "%s/usr/lib/thunderbird/distribution/distribution.ini" % self.target_dir]

        for ini in inis:
            if exists(ini):
                with open(ini, 'r') as f:
                    text = f.read()
                    
                mozLine = "intl.locale.requested=\"%s\"" % moz_lan
                text = self.searchAndReplace(text, "^intl\.locale\.requested.*", mozLine, mozLine)
                
                mozLine = "spellchecker.dictionary=\"%s\"" % dict_lan
                text = self.searchAndReplace(text, "^spellchecker\.dictionary.*", mozLine, mozLine)

                with open(ini, 'w') as f:
                    f.write(text)
                        
        # Set Mozilla parameters in prefs.js
        moz_dirs = ["%s/home/%s/.mozilla/firefox" % (self.target_dir, self.username),
                    "%s/home/%s/.thunderbird" % (self.target_dir, self.username)]
        for moz_dir in moz_dirs:
            prefs_list = find_file('prefs.js', moz_dir)
            for prefs in prefs_list:
                if exists(prefs):
                    with open(prefs, 'r') as f:
                        text = f.read()

                    mozLine = "user_pref(\"intl.locale.requested\", \"%s\");" % moz_lan
                    text = self.searchAndReplace(text, "^user_pref.*intl\.locale\.requested.*", mozLine, mozLine)
                    
                    mozLine = "user_pref(\"spellchecker.dictionary\", \"%s\");" % dict_lan
                    text = self.searchAndReplace(text, "^user_pref.*spellchecker\.dictionary.*", mozLine, mozLine)

                    with open(prefs, 'w') as f:
                        f.write(text)
 
    def get_localized_package(self, package):
        lan = "".join(self.locale)
        pck = "{}-{}".format(package, lan)
        if not does_package_exist(pck):
            lan = "-".join(self.locale)
            pck = "{}-{}".format(package, lan)
            if not does_package_exist(pck):
                lan = self.locale[0]
                pck = "{}-{}".format(package, lan)
                if not does_package_exist(pck):
                    pck = ''
        return pck

    def searchAndReplace(self, text, regexpSearch, replaceWithString, appendString=None):
        matchObj = re.search(regexpSearch, text, re.MULTILINE)
        if matchObj:
            # We need the flags= or else the index of re.MULTILINE is passed
            text = re.sub(regexpSearch, replaceWithString, text, flags=re.MULTILINE)
        else:
            if appendString is not None:
                text += "\n%s\n" % appendString
        return text

    def exec_cmd(self, command):
        apt = False
        if command[0:3] == 'apt':
            apt = True
            # Add apt options in first space of the command
            cmd_arr = command.split(" ", 1)
            command = cmd_arr[0] + ' ' + self.setup.apt_options + ' ' + cmd_arr[1]
        if apt or command[0:4] == 'dpkg':
            # Add debian frontend
            command = self.setup.debian_frontend + ' ' + command
        if self.setup.oem_setup:
            shell_exec(command)
        else:
            chroot_exec(command, self.setup.target_dir)
