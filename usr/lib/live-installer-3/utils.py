#!/usr/bin/env python3

import gi
gi.require_version('UDisks', '2.0')
from gi.repository import UDisks
import os
from os.path import exists, join
from distutils.version import LooseVersion, StrictVersion
import subprocess
from socket import timeout
from urllib.request import ProxyHandler, HTTPBasicAuthHandler, Request, \
                           build_opener, HTTPHandler, install_opener, urlopen
from urllib.error import URLError, HTTPError
from random import choice
import re
import threading
import fnmatch
import apt
import math
import csv
import operator


def shell_exec_popen(command, kwargs={}):
    print(("Executing: %s" % command))
    #return subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, **kwargs)
    return subprocess.Popen(command, shell=True, bufsize=0, stdout=subprocess.PIPE, universal_newlines=True, **kwargs)


def shell_exec(command, logger=None):
    if logger is not None:
        logger.write(command, "utils.shell_exec")
    else:
        print(("Executing: %s" % command))
    return subprocess.call(command, shell=True)


def getoutput(command, always_as_list=False, logger=None):
    #return shell_exec(command).stdout.read().strip()
    try:
        if logger is not None:
            logger.write(command, "utils.getoutput")
        else:
            print(("Executing: %s" % command))
        output = subprocess.check_output(command, shell=True).decode('utf-8').strip().split('\n')
        if logger is not None:
            for line in output:
                logger.write(line, "utils.getoutput")
    except Exception as detail:
        if logger is not None:
            logger.write(detail, "utils.getoutput")
        else:
            print((detail))
        # Even if an error occurs, don't crash here
        output = ['']
    if len(output) == 1 and not always_as_list:
        # Return first line as string
        output = output[0]
    return output


def chroot_exec(command, target, language=None):
    lang = ''
    command = command.replace('"', "'").strip()  # FIXME
    if language is not None:
        lang = "LANG=%s.UTF-8 " % language
    return shell_exec('{0}chroot {1}/ /bin/sh -c "{2}"'.format(lang, target, command))


def memoize(func):
    """ Caches expensive function calls.

    Use as:

        c = Cache(lambda arg: function_to_call_if_yet_uncached(arg))
        c('some_arg')  # returns evaluated result
        c('some_arg')  # returns *same* (non-evaluated) result

    or as a decorator:

        @memoize
        def some_expensive_function(args [, ...]):
            [...]

    See also: http://en.wikipedia.org/wiki/Memoization
    """
    class memodict(dict):
        def __call__(self, *args):
            return self[args]

        def __missing__(self, key):
            ret = self[key] = func(*key)
            return ret
    return memodict()


def linux_distribution(include_minor_version=False):
    # (ID, VERSION, NAME) 
    # ('"SolydXK"', '"10"', '"solydxk-10"')
    distro_info = {}
    with open("/etc/os-release") as f:
        reader = csv.reader(f, delimiter="=")
        for row in reader:
            if row:
                distro_info[row[0]] = row[1]
    if include_minor_version and exists("/etc/debian_version"):
        with open("/etc/debian_version") as f:
            debian_version = f.readline().strip()
        major_version = debian_version.split(".")[0]
        version_split = distro_info["VERSION"].split(" ", maxsplit=1)
        if version_split[0] == major_version:
            # Just major version shown, replace it with the full version
            distro_info["VERSION"] = " ".join([debian_version] + version_split[1:])
    return (distro_info["ID"], distro_info["VERSION"], distro_info["NAME"])


def get_config_dict(file, key_value=re.compile(r'^\s*(\w+)\s*=\s*["\']?(.*?)["\']?\s*(#.*)?$')):
    """Returns POSIX config file (key=value, no sections) as dict.
    Assumptions: no multiline values, no value contains '#'. """
    d = {}
    with open(file) as f:
        for line in f:
            try:
                key, value, _ = key_value.match(line).groups()
            except AttributeError:
                continue
            d[key] = value
    return d

# Check for backports
def has_backports():
    try:
        bp = getoutput("grep backports /etc/apt/sources.list | grep -v ^#")[0]
    except:
        bp = ''
    if bp.strip() == "":
        try:
            bp = getoutput("grep backports /etc/apt/sources.list.d/*.list | grep -v ^#")[0]
        except:
            bp = ''
    if bp.strip() != "":
        return True
    return False


def get_package_version(package, candidate=False):
    version = ''
    cmd = "env LANG=C bash -c 'apt-cache policy %s | grep \"Installed:\"'" % package
    if candidate:
        cmd = "env LANG=C bash -c 'apt-cache policy %s | grep \"Candidate:\"'" % package
    lst = getoutput(cmd, True)[0].strip().split(' ')
    if lst:
        version = lst[-1]
    return version
    
    
def get_process_pids(process_name, process_argument=None, fuzzy=True):
    if fuzzy:
        args = ''
        if process_argument is not None:
            args = "| grep '%s'" % process_argument
        cmd = "ps -ef | grep -v grep | grep '%s' %s | awk '{print $2}'" % (process_name, args)
        #print(cmd)
        pids = getoutput(cmd, True)
    else:
        pids = getoutput("pidof %s" % process_name)
    return pids
    
    
def get_memory_gib():
    mem_bytes = float(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES'))
    return math.ceil(mem_bytes/(1024.**3))


def is_process_running(process_name, process_argument=None, fuzzy=True):
    pids = get_process_pids(process_name, process_argument, fuzzy)
    if pids[0] != '':
        return True
    return False
    

def get_keyboard_layout():
    # Get keyboard layout (default to us)
    kb = getoutput("setxkbmap -query | grep ^layout | awk '{print $NF}'")
    if kb:
        return kb[0]
    return 'us'
    
    
# Compare two package version strings
def compare_package_versions(package_version_1, package_version_2, compare_loose=True):
    if compare_loose:
        try:
            if LooseVersion(package_version_1) < LooseVersion(package_version_2):
                return 'smaller'
            if LooseVersion(package_version_1) > LooseVersion(package_version_2):
                return 'larger'
            else:
                return 'equal'
        except:
            return ''
    else:
        try:
            if StrictVersion(package_version_1) < StrictVersion(package_version_2):
                return 'smaller'
            if StrictVersion(package_version_1) > StrictVersion(package_version_2):
                return 'larger'
            else:
                return 'equal'
        except:
            return ''


# Check for internet connection
def has_internet_connection(test_url=None):
    urls = []
    if test_url is not None:
        urls.append(test_url)
    if not urls:
        src_lst = '/etc/apt/sources.list'
        if exists(src_lst):
            with open(src_lst, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith('#'):
                        matchObj = re.search(r'http[s]{,1}://[a-z0-9\.]+', line)
                        if matchObj:
                            urls.append(matchObj.group(0))
    for url in urls:
        if get_value_from_url(url) is not None:
            return True
    return False


def get_value_from_url(url, timeout_secs=5, return_errors=False):
    try:
        # http://www.webuseragents.com/my-user-agent
        user_agents = [
            'Mozilla/5.0 (X11; Linux x86_64; rv:61.0) Gecko/20100101 Firefox/61.0',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/67.0.3396.87 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64; rv:52.0) Gecko/20100101 Firefox/52.0'
        ]

        # Create proxy handler
        proxy = ProxyHandler({})
        auth = HTTPBasicAuthHandler()
        opener = build_opener(proxy, auth, HTTPHandler)
        install_opener(opener)

        # Create a request object with given url
        req = Request(url)

        # Get a random user agent and add that to the request object
        ua = choice(user_agents)
        req.add_header('User-Agent', ua)

        # Get the output of the URL
        output = urlopen(req, timeout=timeout_secs)

        # Decode to text
        txt = output.read().decode('utf-8')
        
        # Return the text
        return txt
        
    except (HTTPError, URLError) as error:
        err = 'ERROR: could not connect to {}: {}'.format(url, error)
        if return_errors:
            return err
        else:
            print((err))
            return None
    except timeout:
        err = 'ERROR: socket timeout on: {}'.format(url)
        if return_errors:
            return err
        else:
            print((err))
            return None


# Check if running in VB
def in_virtualbox():
    vb = 'VirtualBox'
    dmiBIOSVersion = getoutput("grep '{}' /sys/devices/virtual/dmi/id/bios_version".format(vb))
    dmiSystemProduct = getoutput("grep '{}' /sys/devices/virtual/dmi/id/product_name".format(vb))
    dmiBoardProduct = getoutput("grep '{}' /sys/devices/virtual/dmi/id/board_name".format(vb))
    if vb not in dmiBIOSVersion and \
       vb not in dmiSystemProduct and \
       vb not in dmiBoardProduct:
        return False
    return True


# Check if is 64-bit system
def is_amd64():
    machine = getoutput("uname -m")
    if machine == "x86_64":
        return True
    return False
    
# Check if xfce is running
def is_xfce_running():
    xfce = getoutput('pidof xfce4-session')
    if xfce:
        return True
    return False


def get_boot_parameters():
    parms = []
    not_allowed = 'live,ram,single,ignore,config,components,memtest,iso,noprompt,noeject,noswap,BOOT_IMAGE,root,locales'.split(',')
    cmd = "cat /proc/cmdline"
    ret = getoutput(cmd).split(" ")
    for line in ret:
        if len(line) > 2:
            add = True
            for s in not_allowed:
                if s in line:
                    add = False
                    break
            if add:
                parms.append(line)
    return parms


def filter_text(widget, allowed_chars_regexp='0-9'):
    def filter(entry, *args):
        text = entry.get_text().strip().lower()
        entry.set_text(re.sub("[^%s]" % allowed_chars_regexp, '', text))
        #entry.set_text(''.join([i for i in text if i in allowed_characters_string]))
    widget.connect('changed', filter)


# Taken from: http://stackoverflow.com/questions/2532053/validate-a-hostname-string
def is_valid_hostname(hostname):
    if len(hostname) > 255:
        return False
    if hostname[-1] == ".":
        hostname = hostname[:-1]  # strip exactly one dot from the right, if present
    allowed = re.compile("(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)
    return all(allowed.match(x) for x in hostname.split("."))


def get_files_from_dir(directory, pattern=''):
    if pattern == '':
        pattern = '*'
    found_files = []
    if exists(directory):
        for root, dirs, files in os.walk(directory):
            for f in fnmatch.filter(files, pattern):
                found_files.append(join(root, f))
    return found_files


# Check if a package exists
def does_package_exist(packageName):
    try:
        cache = apt.Cache()
        cache[packageName]
        return True
    except:
        return False


# Check if a package is installed
def is_package_installed(packageName, alsoCheckVersion=False):
    isInstalled = False
    expr = '^i\s([a-z0-9\-_\.]+)\s+(.*)\s+(.*)'
    if not '*' in packageName:
        packageName = '^{}$'.format(packageName)
    try:
        # https://aptitude.alioth.debian.org/doc/en/ch02s05s01.html
        cmd = "aptitude search -F '%c %p %v %V' --disable-columns {} | grep ^i".format(packageName)
        pckList = getoutput(cmd, True)
        for line in pckList:
            matchObj = re.search(expr, line)
            if matchObj:
                if alsoCheckVersion:
                    if matchObj.group(2) == matchObj.group(3):
                        isInstalled = True
                        break
                else:
                    isInstalled = True
                    break
            if isInstalled:
                break
    except:
        pass
    return isInstalled
    
    
# Check for string in file
def has_string_in_file(searchString, filePath):
    if exists(filePath):
        with open(filePath) as f:
            for line in f:
                if re.search("{0}".format(searchString), line):
                    return True
    return False


# Check if the machine has a battery
def has_power_supply():
    out = getoutput("ls /sys/class/power_supply | grep BAT", True)
    if out[0] != "":
        return True
    return False


# Comment or uncomment a line with given pattern in a file
def comment_line(file_path, pattern, comment=True):
    if exists(file_path):
        pattern = pattern.replace("/", "\/")
        cmd = "sed -i -e ''/{p}/s/^#*/#/'' {f}".format(p=pattern, f=file_path)
        if not comment:
            cmd = "sed -i -e '/^#.*{p}/s/^#//' {f}".format(p=pattern, f=file_path)
        shell_exec(cmd)


# Convert string to number
def str_to_nr(stringnr, toInt=False):
    nr = 0
    # Might be a int or float: convert to str
    stringnr = str(stringnr).strip()
    try:
        if toInt:
            nr = int(stringnr)
        else:
            nr = float(stringnr)
    except ValueError:
        nr = 0
    return nr


# Find all files in path
def find_file(file_name, start_dir):
    ret = []
    if exists(start_dir):
        for root, dirs, files in os.walk(start_dir):
            if file_name in files:
                ret.append(join(root, file_name))
    return ret

    
def get_firefox_version():
    return str_to_nr(getoutput("firefox --version 2>/dev/null | egrep -o '[0-9]{2,}' || echo 0"))
    
    
def clean_html(html):
    clean_re = re.compile('<.*?>')
    return re.sub(clean_re, '', html)


def replace_pattern_in_file(pattern, replace_string, file, append_if_not_exists=True):
    if os.path.exists(file):
        cont = None
        p_obj = re.compile(pattern, re.MULTILINE)
        with open(file, 'r') as f:
            cont = f.read()
        if re.search(p_obj, cont):
            cont = re.sub(p_obj, replace_string, cont)
        else:
            if append_if_not_exists:
                cont = cont + "\n" + replace_string + "\n"
            else:
                cont = None
        if cont:
            with open(file, 'w') as f:
                f.write(cont)


# Get valid screen resolutions
def get_resolutions(minRes='', maxRes='', reverse_order=False, use_vesa=False):
    cmd = None
    resolutions = []
    default_res = ['640x480', '800x600', '1024x768', '1280x1024', '1600x1200']

    cmd = "xrandr | awk '{print $1}' | egrep '[0-9]+x[0-9]+$'"
    if use_vesa:
        vbeModes = '/sys/bus/platform/drivers/uvesafb/uvesafb.0/vbe_modes'
        if exists(vbeModes):
            cmd = "cat %s | cut -d'-' -f1" % vbeModes
        elif is_package_installed('hwinfo'):
            cmd = "hwinfo --framebuffer | awk '{print $3}' | egrep '[0-9]+x[0-9]+$' | uniq"        

    resolutions = getoutput(cmd, 5)
    if not resolutions[0]:
        resolutions = default_res

    # Remove any duplicates from the list
    resList = list(set(resolutions))

    avlRes = []
    avlResTmp = []
    minW = 0
    minH = 0
    maxW = 0
    maxH = 0

    # Split the minimum and maximum resolutions
    if 'x' in minRes:
        minResList = minRes.split('x')
        minW = str_to_nr(minResList[0], True)
        minH = str_to_nr(minResList[1], True)
    if 'x' in maxRes:
        maxResList = maxRes.split('x')
        maxW = str_to_nr(maxResList[0], True)
        maxH = str_to_nr(maxResList[1], True)

    # Fill the list with screen resolutions
    for line in resList:
        for item in line.split():
            itemChk = re.search(r'\d+x\d+', line)
            if itemChk:
                itemList = item.split('x')
                itemW = str_to_nr(itemList[0], True)
                itemH = str_to_nr(itemList[1], True)
                # Check if it can be added
                if itemW >= minW and itemH >= minH and (maxW == 0 or itemW <= maxW) and (maxH == 0 or itemH <= maxH):
                    #print(("Resolution added: %(res)s" % { "res": item }))
                    avlResTmp.append([itemW, itemH])

    # Sort the list and return as readable resolution strings
    avlResTmp.sort(key=operator.itemgetter(0), reverse=reverse_order)
    for res in avlResTmp:
        avlRes.append(str(res[0]) + 'x' + str(res[1]))
    return avlRes


def get_pen_drives():
    pen_drives = []
    client = UDisks.Client.new_sync(None)
    manager = client.get_object_manager()
    objects = manager.get_objects()

    for o in objects:
        block = o.get_block()
        if block is None:
            #print(("> block device is None"))
            continue

        device_path = block.get_cached_property('Device').get_bytestring().decode('utf-8')
        drive_path = device_path.rstrip('0123456789')
        total_size = (block.get_cached_property('Size').get_uint64() / 1024)
        if not exists(drive_path) or total_size == 0:
            continue

        drive_name = block.get_cached_property('Drive').get_string()
        drive_obj = manager.get_object(drive_name)
        if drive_obj is None:
            continue
        drive = drive_obj.get_drive()

        removable = drive.get_cached_property("Removable").get_boolean()
        connectionbus = drive.get_cached_property("ConnectionBus").get_string()

        #print(('========== Device Info of: %s ==========' % device_path))
        #print(('Drive path: %s' % drive_path))
        #print(('Drive name: %s' % drive_name))
        #print(('Total size: %s' % total_size))
        #print(('ConnectionBus: %s' % connectionbus))
        #print(('Removable: %s' % str(removable)))
        #print(('======================================='))

        # Check for usb mounted flash drives
        if connectionbus == 'usb' and \
           removable and \
           drive_path not in pen_drives:
            pen_drives.append(drive_path)

    print(("pen_drives = %s" % str(pen_drives)))
    return pen_drives


# Class to run commands in a thread and return the output in a queue
class ExecuteThreadedCommands(threading.Thread):

    def __init__(self, commandList, theQueue=None, returnOutput=False):
        super(ExecuteThreadedCommands, self).__init__()
        self.commands = commandList
        self.queue = theQueue
        self.returnOutput = returnOutput

    def run(self):
        if isinstance(self.commands, (list, tuple)):
            for cmd in self.commands:
                self.exec_cmd(cmd)
        else:
            self.exec_cmd(self.commands)

    def exec_cmd(self, cmd):
        if self.returnOutput:
            ret = getoutput(cmd)
        else:
            ret = shell_exec(cmd)
        if self.queue is not None:
            self.queue.put(ret)


# Run passed function in a thread
# Example usage
# def someOtherFunc(data, key):
#    print "someOtherFunc was called : data=%s; key=%s" % (str(data), str(key))
# t1 = FuncThread(someOtherFunc, [1,2], 6)
# t1.start()
# t1.join()
class FuncThread(threading.Thread):
    def __init__(self, target, *args):
        super(FuncThread, self).__init__()
        self._target = target
        self._args = args

    def run(self):
        self._target(*self._args)
