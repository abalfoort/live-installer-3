#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

import os
import re
from collections import defaultdict
from utils import getoutput, shell_exec, get_pen_drives, str_to_nr, \
                  get_memory_gib
from dialogs import QuestionDialog, ErrorDialog, InputDialog
from encryption import is_encrypted, is_connected, connect_block_device, \
                       get_status, get_filesystem
from os.path import join, abspath, dirname, basename, exists
from combobox import ComboBoxHandler
import math

# https://github.com/dcantrell/pyparted
import parted

# Needed for atof
import locale
locale.setlocale(locale.LC_ALL, '')

# i18n: http://docs.python.org/3/library/gettext.html
import gettext
from gettext import gettext as _
gettext.textdomain('live-installer-3')


(IDX_PART_PATH,
 IDX_PART_GRUB,
 IDX_PART_TYPE,
 IDX_PART_LABEL,
 IDX_PART_MOUNT_AS,
 IDX_PART_FORMAT_AS,
 IDX_PART_ENCRYPT,
 IDX_PART_ENC_PASSPHRASE,
 IDX_PART_SIZE,
 IDX_PART_FREE_SPACE,
 IDX_PART_DISK_TYPE,
 IDX_PART_OBJECT,
 IDX_PART_DISK) = list(range(13))

TMP_MOUNTPOINT = '/tmp/live-installer-3'
RESOURCE_DIR = '/usr/share/live-installer-3/'

EFI_MOUNT_POINT = '/boot/efi'
SWAP_MOUNT_POINT = 'swap'
ROOT_MOUNT_POINT = '/'
HOME_MOUNT_POINT = '/home'
BOOT_MOUNT_POINT = '/boot'
SRV_MOUNT_POINT = '/srv'
TMP_MOUNT_POINT = '/tmp'
VAR_MOUNT_POINT = '/var'

HOME_LABEL = 'HOME'
EFI_LABEL = 'EFI'
BOOT_LABEL = 'BOOT'
SWAP_LABEL = 'SWAP'


with open(RESOURCE_DIR + 'disk-partitions.html') as f:
    DISK_TEMPLATE = f.read()
    # cut out the single partition (skeleton) block
    PARTITION_TEMPLATE = re.search('CUT_HERE([\s\S]+?)CUT_HERE', DISK_TEMPLATE, re.MULTILINE).group(1)
    # delete the skeleton from original
    DISK_TEMPLATE = DISK_TEMPLATE.replace(PARTITION_TEMPLATE, '')
    # duplicate all { or } in original CSS so they don't get interpreted as part of string formatting
    DISK_TEMPLATE = re.sub('<style>[\s\S]+?</style>', lambda match: match.group().replace('{', '{{').replace('}', '}}'), DISK_TEMPLATE)


# ===================================================================
# Global functions
# ===================================================================

def is_efi_supported():
    # Are we running under with efi ?
    shell_exec("modprobe efivars >/dev/null 2>&1")
    return exists("/proc/efi") or exists("/sys/firmware/efi")


def path_exists(*args):
    return exists(join(*args))


def build_partitions(_installer):
    global installer
    installer = _installer
    installer.window.set_sensitive(False)
    installer.setup.grub_device = None
    partition_setup = PartitionSetup()
    if installer.setup.disks:
        installer._selected_disk = installer.setup.disks[0][0]
        html = partition_setup.get_html(installer._selected_disk)
        #print(("\n\nINIT HTML FOR {}:\n{}\n\n".format(str(installer._selected_disk), html)))
        installer.partitions_browser.show_html(html)
    installer.go("treeview_disks").set_model(partition_setup)
    installer.go("treeview_disks").expand_all()
    installer.window.set_sensitive(True)

def update_html_preview(selection):
    model, row = selection.get_selected()
    try: disk = model[row][IDX_PART_DISK]
    except (TypeError, IndexError): return # no disk is selected or no disk available
    if disk != installer._selected_disk:
        installer._selected_disk = disk
        html = model.get_html(disk)
        #print(("\n\nUPDATE HTML FOR {}:\n{}\n\n".format(str(installer._selected_disk), html)))
        installer.partitions_browser.show_html(html)


def edit_partition_dialog(widget=None):
    ''' assign the partition ... '''
    model, iter = installer.go("treeview_disks").get_selection().get_selected()
    if not iter: return
    row = model[iter]
    partition = row[IDX_PART_OBJECT]
    if not partition: return
    #partition_type = model.get_value(iter, IDX_PART_TYPE)
    #partition_mount_as = model.get_value(iter, IDX_PART_MOUNT_AS)
    if (partition.partition.type == parted.PARTITION_EXTENDED or
        partition.partition.number == -1):
        #or ("swap" in partition_type and "swap" in partition_mount_as)):
        return

    PartitionDialog(partition,
                    row[IDX_PART_MOUNT_AS],
                    row[IDX_PART_FORMAT_AS])


def assign_mount_point(partition, mount_point, filesystem, encrypt=False, enc_passphrase='', label=''):
    # Assign it in the treeview
    model = installer.go("treeview_disks").get_model()
    for disk in model:
        for part in disk.iterchildren():
            if partition == part[IDX_PART_OBJECT]:
                if mount_point == ROOT_MOUNT_POINT and \
                   partition.size_mb < installer.setup.min_root_size_gb * 1024:
                    # Root partition too small: show warning and exit
                    msg = _("Cannot assign {0} as root partition: a minimum size of {1} GiB is required.".format(partition.path, installer.setup.min_root_size_gb))
                    ErrorDialog(_("Root size"), msg)
                    return True

                # Check the given filesystem
                filesystem = get_safe_fs(partition, mount_point, filesystem)

                if 'luks' in part[IDX_PART_TYPE]:
                    if not encrypt and filesystem != '':
                        # Formatting a LUKS partition to something else: use the source device as path
                        part[IDX_PART_PATH] = partition.enc_status['device']
                    else:
                        # Set path to mapped drive for existing encrypted drive
                        part[IDX_PART_PATH] = partition.enc_status['active']
                elif partition.enc_status['active'] != '':
                    part[IDX_PART_PATH] = partition.enc_status['active']

                part[IDX_PART_MOUNT_AS] = mount_point
                part[IDX_PART_FORMAT_AS] = filesystem
                part[IDX_PART_ENCRYPT] = encrypt
                part[IDX_PART_ENC_PASSPHRASE] = enc_passphrase
                part[IDX_PART_LABEL] = label
            elif mount_point == part[IDX_PART_MOUNT_AS]:
                part[IDX_PART_MOUNT_AS] = ''
                part[IDX_PART_FORMAT_AS] = ''
                part[IDX_PART_ENCRYPT] = False

    # Assign it in our setup
    for part in installer.setup.partitions:
        # Loop partitions to get partition info
        if part == partition:
            if part.type == 'luks':
                if not encrypt and filesystem != '':
                    # Formatting a LUKS partition to something else: use the source device as path
                    part.path = partition.enc_status['device']
                    print(("Set luks path %s to device" % part.path))
                else:
                    # Set path to mapped drive for existing encrypted drive
                    part.path = partition.enc_status['active']
                    print(("Set luks path %s to active" % part.path))
            elif partition.enc_status['active'] != '':
                part.path = partition.enc_status['active']
                print(("Set path %s to active" % part.path))

            # Save the new values
            part.mount_as, part.format_as, part.encrypt, part.enc_passphrase, part.label = mount_point, filesystem, encrypt, enc_passphrase, label
        elif part.mount_as == mount_point:
            print(("Reset data on %s" % part.path))
            part.mount_as, part.format_as, part.encrypt = '', '', False


def get_safe_fs(partition, mount_point, filesystem):
    if filesystem == '':
        for part in installer.setup.partitions:
            if part == partition:
                if part.type == 'unknown':
                    filesystem = 'swap' if mount_point == SWAP_MOUNT_POINT else 'ext4'
                    break
    return filesystem


def get_release_name(mount_point=''):
    # Get the name of the live OS
    try:
        lsb_release = "%s/etc/lsb-release" % mount_point
        if path_exists(lsb_release):
            name = getoutput(". \"%s\"; echo $DISTRIB_DESCRIPTION" % lsb_release)
        if name == '':
            os_release = "%susr/lib/os-release" % mount_point
            if path_exists(os_release):
                name = getoutput(". \"%s\"; echo PRETTY_NAME" % os_release)
        return name.strip()
    except:
        return ''


def manually_edit_partitions():
    """ Edit only known disks in gparted, selected one first """
    model, itr = installer.go("treeview_disks").get_selection().get_selected()
    preferred = model[itr][-1] if itr else ''  # prefer disk currently selected and show it first in gparted
    disks = ' '.join(sorted((disk for disk, desc, sdd, detachable in installer.setup.disks), key=lambda disk: disk != preferred))
    shell_exec("umount -f %s" % disks)  # umount disks (if possible) so gparted works out-of-the-box
    shell_exec('gparted %s &' % disks)


def has_grub(path):
    cmd = "dd bs=512 count=1 if=%s 2>/dev/null | strings" % path
    out = ' '.join(getoutput(cmd)).upper()
    if "GRUB" in out:
        print(("Grub installed on %s" % path))
        return True
    return False


def to_human_readable(bytes):
    symbols = ('K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y')
    prefix = {}
    for i, s in enumerate(symbols):
        prefix[s] = 1 << (i + 1) * 10
    for s in reversed(symbols):
        if bytes >= prefix[s]:
            value = float(bytes) / prefix[s]
            return "{:.2f} {}iB".format(value, s)
    return "{}B".format(bytes)


def get_partition_label(path):
    cmd = "lsblk -n -o LABEL %s | grep -v \"^$\"" % path
    try:
        return getoutput(cmd).strip()
    except:
        return ''


def get_device_size(device):
    return str_to_nr(getoutput("lsblk -n -b -o SIZE %s" % device))


def get_partition_path_from_string(partition_string):
    if '/' in partition_string:
        if path_exists(partition_string):
            return partition_string
        return ''
    cmd = "blkid | grep %s | cut -d':' -f 1" % partition_string
    try:
        return getoutput(cmd).strip()
    except:
        return ''


def do_mount(device, mountpoint=None, filesystem=None, options=None):
    mount_options = ''
    if options:
        mount_options = options if options[:1] == '-' else '-o {}'.format(options)
    filesystem = '-t ' + filesystem if filesystem else ''
    if mountpoint:
        if get_mount_point(device, mountpoint) == '':
            cmd = "mount {mount_options} {filesystem} {device} {mountpoint}".format(**locals())
            shell_exec(cmd)
    else:
        cmd = "udisksctl mount --no-user-interaction {0} -b {1}".format(filesystem, device)
        shell_exec(cmd)


def do_unmount(mountpoint, force=False):
    if get_mount_point('', mountpoint) != '':
        force_str = '-f' if force else ''
        device = get_device_from_mountpoint(mountpoint)
        if device:
            cmd = "udisksctl unmount --no-user-interaction {0} -b {1}".format(force_str, device)
            shell_exec(cmd)
        # If not a device or mount still exists use umount (needs root permission)
        if get_mount_point('', mountpoint) != '':
            cmd = "umount {force_str} {mountpoint}".format(**locals())
            shell_exec(cmd)


def get_device_from_mountpoint(mountpoint):
    # Get the device path from the mount point
    ret = getoutput("grep ' %s ' /proc/mounts | awk '{print $1}'" % mountpoint)
    return ret if ret[:5] == '/dev/' else ''


def get_mount_point(device, mountpoint=None):
    if device[:5] == '/dev/':
        cmd = "grep %s /proc/mounts | awk '{print $2}'" % device
        output = getoutput(command=cmd, always_as_list=True)
        if mountpoint is not None:
            for mp in output:
                if mp == mountpoint: return mp
        else:
            return output[0]
    elif mountpoint is not None:
        # Just check if mountpoint exists
        ret = getoutput("grep ' {0} ' /proc/mounts".format(mountpoint))
        if ret:
            return mountpoint
    return ''


def is_ssd(path):
    if 'mmcblk' in path:
        return True
    dev = basename(path)[0:3]
    rotational = getoutput("cat /sys/block/%s/queue/rotational 2>/dev/null" % dev)
    if rotational == "0":
        return True
    return False


def is_detachable(path):
    #print(("is_detachable %s" % path))
    pen_drives = get_pen_drives()
    #udisks_detachable = getoutput("env LANG=C udisks --show-info %s | grep detachable | awk '{print $2}'" % path)
    #if udisks_detachable == "1":
    if path in pen_drives:
        return True
    return False


# ===================================================================
# Partition classes
# ===================================================================

class PartitionSetup(Gtk.TreeStore):
    def __init__(self):
        super(PartitionSetup, self).__init__(str,  # path
                                             bool,  # grub
                                             str,  # type (fs)
                                             str,  # volume label
                                             str,  # mount point
                                             str,  # format to
                                             bool,  # encrypt
                                             str,  # encryption passphrase
                                             str,  # size
                                             str,  # free space
                                             str,  # disk type (msdos or gpt)
                                             object,  # partition object
                                             str)  # disk device path
        installer.setup.partitions = []
        installer.setup.partition_setup = self
        self.html_disks, self.html_chunks = {}, defaultdict(list)
        self.full_disk_format_runonce = False

        def _get_attached_disks():
            disks = []
            exclude_devices = ['/dev/sr0', '/dev/sr1', '/dev/cdrom', '/dev/dvd', '/dev/fd0', '/dev/mmcblk0boot0', '/dev/mmcblk0boot1', '/dev/mmcblk0rpmb']
            live_device = getoutput("findmnt -n -o source /lib/live/mount/findiso 2>/dev/null || :").split('\n')[0]
            live_device = re.sub('[0-9]+$', '', live_device) # remove partition numbers if any
            if live_device is not None and live_device.startswith('/dev/'):
                exclude_devices.append(live_device)
                print("Excluding %s (detected as the live device)" % live_device)
            lsblk = getoutput('LC_ALL=en_US.UTF-8 lsblk -rindo TYPE,NAME,RM,SIZE,MODEL | sort -k3,2')
            for line in lsblk:
                try:
                    elements = line.strip().split(" ", 4)
                    if elements[0] == 'loop':
                        continue
                    if len(elements) < 4:
                        print(("Can't parse blkid output: %s" % elements))
                        continue
                    elif len(elements) < 5:
                        print(("Can't find model in blkid output: %s" % elements))
                        type, device, removable, size, model = elements[0], elements[1], elements[2], elements[3], elements[1]
                    else:
                        type, device, removable, size, model = elements
                    device = "/dev/" + device
                    if type == "disk" and device not in exclude_devices:
                        # convert size to manufacturer's size for show, e.g. in GB, not GiB!
                        size = str(int(float(size[:-1]) * (1024/1000)**'BkMGTPEZY'.index(size[-1]))) + size[-1]
                        model = model.replace("\\x20", " ")
                        description = '%s (%sB)' % (model.strip(), size)
                        if int(removable):
                            description = _('Removable:') + ' ' + description

                        # Is this device a SSD or pen drive?
                        ssd = is_ssd(device)
                        print(("device = %s" % str(device)))
                        detachable = is_detachable(device)
                        print(("detachable = %s" % str(detachable)))
                        disks.append((device, description, ssd, detachable))
                except:
                    pass
            return disks

        installer.setup.gptonefi = is_efi_supported()
        installer.setup.disks = _get_attached_disks()
        print(('Disks: {0}'.format(installer.setup.disks)))
        already_done_full_disk_format = False
        primary_partition_found = False

        for disk_path, disk_description, ssd, detachable in installer.setup.disks:
            try:
                # VMWare returns non-existing /dev/fd0
                disk_device = parted.getDevice(disk_path)
            except:
                # Try next disk
                continue

            try:
                disk = parted.Disk(disk_device)
                if not primary_partition_found:
                    if len(disk.getPrimaryPartitions()) == 0:
                        if len(disk.getExtendedPartitions()):
                            raise ValueError('No logical partitions found')
                    else:
                        primary_partition_found = True
            except Exception:
                if installer.splash is not None:
                    installer.splash.destroy()
                dialog = QuestionDialog(_("Installation Tool"),
                                        _("No partition table was found on the hard drive: %s.\n\n"
                                          "Do you want the installer to create a set of partitions for you?\n\n"
                                          "Note: This will ERASE ALL DATA present on this disk.") % disk_description,
                                        None,
                                        installer.window)
                if not dialog:
                    continue  # the user said No, skip this disk
                if not already_done_full_disk_format:
                    assign_mount_format = self.full_disk_format(disk_device)
                    if not assign_mount_format:
                        # Could not format the disk
                        return None
                    already_done_full_disk_format = True
                #else:
                #    self.full_disk_format(disk_device) # Format but don't assign mount points
                disk = parted.Disk(disk_device)

            partitions = []
            parted_partitions = list(disk.getFreeSpacePartitions()) + \
                                list(disk.getPrimaryPartitions()) + \
                                list(disk.getLogicalPartitions()) + \
                                list(disk.getRaidPartitions()) + \
                                list(disk.getLVMPartitions())
            for partition in parted_partitions:
                if partition.getLength('MiB') > 5:
                    partitions.append(Partition(partition))

            try: # assign mount_as and format_as if disk was just auto-formatted
                for partition, (mount_as, format_as) in zip(partitions, assign_mount_format):
                    partition.mount_as = mount_as
                    partition.format_as = format_as
                del assign_mount_format
            except NameError:
                pass

            # Needed to fix the 1% minimum Partition.size_percent
            sum_size_percent = sum(p.size_percent for p in partitions) + .5  # .5 for good measure

            for partition in partitions:
                # Save disk information
                partition.disk_type = disk.type
                partition.disk_path = disk_path
                partition.disk_description = disk_description

                # Check partition for Grub
                if installer.setup.grub_device is None:
                    if has_grub(partition.path):
                        partition.grub = True
                        installer.setup.grub_device = partition.path

                partition.size_percent = round(partition.size_percent / sum_size_percent * 100, 1)
                installer.setup.partitions.append(partition)
                
            # Create html info per disk
            self.html_disks[disk_path] = DISK_TEMPLATE.format(PARTITIONS_HTML=''.join(PARTITION_TEMPLATE.format(p) for p in partitions))
        
        # Reset disk_path
        disk_path = ''
        disk_iter = None
        # Loop through all available partitions and get additional information
        for partition in installer.setup.partitions:
            # Set root and home partition information if not multi-boot
            if installer.setup.root_partition:
                if partition.path == installer.setup.root_partition:
                    # Only set mount point for root if it's big enough
                    if partition.size_mb >= installer.setup.min_root_size_gb * 1024:
                        partition.mount_as = ROOT_MOUNT_POINT
                    if not installer.setup.oem_setup:
                        # Check if current fs is allowed
                        partition.format_as = partition.type if partition.type in installer.setup.available_system_fs else 'ext4'
                elif partition.path == installer.setup.home_partition:
                    partition.mount_as = HOME_MOUNT_POINT
                    # Check if current fs is allowed
                    if partition.type not in installer.setup.available_system_fs:
                        partition.format_as = 'ext4'
                    if not partition.label: partition.label = HOME_LABEL
            # Set efi and boot partition information
            if installer.setup.efi_partition:
                if partition.path == installer.setup.efi_partition:
                    partition.mount_as = EFI_MOUNT_POINT
                    if not partition.label: partition.label = EFI_LABEL
            if installer.setup.boot_partition:
                if partition.path == installer.setup.boot_partition:
                    partition.mount_as = BOOT_MOUNT_POINT
                    # Check if current fs is allowed
                    if partition.type not in installer.setup.available_system_fs:
                        partition.format_as = 'ext4'
                    if not partition.label: partition.label = BOOT_LABEL
            # Set swap partition information
            if partition.type == 'swap':
                partition.mount_as = SWAP_MOUNT_POINT
                if not partition.label: partition.label = SWAP_LABEL
            
            # Create html per disk
            if not disk_path or disk_path == partition.disk_path:
                if not disk_iter:
                    # Create a Gtk.TreeIter
                    disk_iter = self.append(None, ("%s (%s)" % (partition.disk_path, partition.disk_description), False, '', '', '', '', False, '', '', '', '', None, partition.disk_path))
            else:
                # Create a new Gtk.TreeIter
                disk_iter = self.append(None, ("%s (%s)" % (partition.disk_path, partition.disk_description), False, '', '', '', '', False, '', '', '', '', None, partition.disk_path))
            
            # Save the current disk_path
            disk_path = partition.disk_path
            
            # Add the Gtk.TreeIter
            self.append(disk_iter, (partition.name,
                                        partition.grub,
                                        "<span foreground=\"%s\">%s</span>" % (partition.color, partition.type),
                                        partition.label,
                                        partition.mount_as,
                                        partition.format_as,
                                        partition.encrypt,
                                        partition.enc_passphrase,
                                        partition.size,
                                        partition.free_space,
                                        partition.disk_type,
                                        partition,
                                        partition.disk_path))

        # If no Grub was found on any partition, check disks now
        # There might be an installed Grub from another distribution
        if installer.setup.grub_device is None \
            and len(self) > 0:
            i = 0
            for disk_path, disk_description, ssd, detachable in installer.setup.disks:
                # Check if grub is installed on this disk
                if has_grub(disk_path):
                    installer.setup.grub_device = disk_path
                    try:
                        # In very rare cases you get an exception: best effort
                        self[i][1] = True
                    except:
                        pass
                    break
                i += 1

        # If no Grub was found anywhere: select first disk
        if installer.setup.grub_device is None \
            and len(installer.setup.disks) > 0 \
            and len(self) > 0:
            installer.setup.grub_device = installer.setup.disks[0][0]
            self[0][1] = True

    def get_html(self, disk):
        try:
            return self.html_disks[disk]
        except:
            return ''

    def full_disk_format(self, device):
        # Create a default partition set up
        disk_label = ('gpt' if device.getLength('B') > 2 ** 32 * .9 * device.sectorSize  # size of disk > ~2TB
                               or installer.setup.gptonefi
                            else 'msdos')

        # Calculate recommended (incl. hibernation) swap size:
        # https://help.ubuntu.com/community/SwapFaq#How_much_swap_do_I_need.3F
        mem_gib = get_memory_gib()
        if mem_gib < 1:
            req_swap_gib = mem_gib * 2
        else:
            req_swap_gib = mem_gib + round(math.sqrt(mem_gib))

        # Set sizes
        req_device_size_mb = (installer.setup.rec_root_size_gb + req_swap_gib) * 1024
        min_home_size_mb = installer.setup.min_home_size_gb * 1024
        device_size_mb = device.getLength('MiB')
        efi_size_mb = installer.setup.min_efi_size_mb if installer.setup.gptonefi else 0
        swap_size_mb = req_swap_gib * 1024 if device_size_mb >= req_device_size_mb else 0
        if swap_size_mb == 0:
            req_device_size_mb = installer.setup.rec_root_size_gb * 1024
        if req_device_size_mb + min_home_size_mb > device_size_mb:
            min_home_size_mb = 0
        if efi_size_mb == 0 and swap_size_mb == 0 and min_home_size_mb == 0:
            root_size_mb = device_size_mb
        else:
            root_size_mb = installer.setup.rec_root_size_gb * 1024 if device_size_mb >= req_device_size_mb else installer.setup.min_root_size_gb * 1024

        # Check if sufficient space on drive
        size_needed_mb = efi_size_mb + swap_size_mb + root_size_mb + min_home_size_mb
        print(("Full disk format: efi_size_mb={0} swap_size_mb={1} root_size_mb={2} min_home_size_mb={3} req_device_size_mb={4} device_size_mb={5}".format(efi_size_mb, swap_size_mb, root_size_mb, min_home_size_mb, req_device_size_mb, device_size_mb)))
        if size_needed_mb > device_size_mb:
            # Device too small: show warning and exit
            min_round = int(math.ceil(installer.setup.min_root_size_gb / 10.0)) * 10
            msg = _("Cannot partition {0}: at least {1} GiB is needed.".format(device.path, min_round))
            ErrorDialog(_("Drive size"), msg)
            return None

        # Can we create a separate home partition and swap?
        separate_home_partition = False if min_home_size_mb == 0 else device_size_mb > size_needed_mb
        create_swap = True if swap_size_mb > 0 else False

        mkpart = (
            # (condition, mount_as, format_as, mkfs command, size_mb, label)
            # EFI
            (installer.setup.gptonefi, EFI_MOUNT_POINT, 'vfat', 'mkfs.vfat {0} -F 32 -n "{1}"', efi_size_mb, EFI_LABEL),
            # swap 
            (create_swap, SWAP_MOUNT_POINT, 'swap', 'mkswap {0} -L "{1}"', swap_size_mb, SWAP_LABEL),
            # root
            (True, ROOT_MOUNT_POINT, 'ext4', 'mkfs.ext4 -F {0} -L "{1}"', root_size_mb if separate_home_partition else 0, get_release_name()),
            # home
            (separate_home_partition, HOME_MOUNT_POINT, 'ext4', 'mkfs.ext4 -F {0} -L "{1}"', 0, HOME_LABEL),
        )
        run_parted = lambda cmd: os.system("parted --script --align optimal %s %s ; sync" % (device.path, cmd))
        run_parted('mklabel ' + disk_label)
        
        # Use MiB instead of MB
        # https://www.gnu.org/software/parted/manual/parted.html
        start_mb = 1
        partition_number = 0
        for partition in mkpart:
            if partition[0]:
                partition_number = partition_number + 1
                mkfs = partition[3]
                size_mb = partition[4]
                label = partition[5]
                end = "%sMiB" % str(start_mb + size_mb) if size_mb > 0 else '100%'
                mkpart_cmd = "mkpart primary %dMiB %s" % (start_mb, end)
                #print((">> %d | %s" % (size_mb, mkpart_cmd)))
                run_parted(mkpart_cmd)
                mkfs = mkfs.format("%s%d" % (device.path, partition_number), label)
                #print((">> mkfs = %s" % mkfs))
                os.system(mkfs)
                start_mb += size_mb
        if installer.setup.gptonefi:
            run_parted('set 1 boot on')
        elif not self.full_disk_format_runonce:
            # Set the boot flag for the root partition
            root_partition_nr = 2 if create_swap else 1
            run_parted('set {0} boot on'.format(root_partition_nr))
        # Save that the first drive has been configured
        self.full_disk_format_runonce = True
        return ((i[1], i[2]) for i in mkpart if i[0])


class Partition(object):
    def __init__(self, partition):
        assert partition.type not in (parted.PARTITION_METADATA, parted.PARTITION_EXTENDED)

        self.format_as = ''
        self.mount_as = ''
        self.label = ''
        self.encrypt = False
        self.enc_passphrase = ''
        # Initiate encryption status dictionary (even for non-encrypted partitions)
        self.enc_status = get_status(partition.path)
        self.grub = False
        self.disk_type = ''
        self.disk_path = ''
        self.disk_description = ''

        self.partition = partition
        self.path = partition.path
        self.length = partition.getLength()
        self.size_percent = max(1, round(80*self.length/partition.disk.device.getLength(), 1))
        self.size_mb = int(partition.getLength('MiB'))
        self.size_b = int(partition.getLength('B'))
        self.size = to_human_readable(self.size_b)
        try:
            # This will crash on USB sticks
            self.flags = partition.getFlagsAsString().split(', ')
        except:
            self.flags = []

        # if not normal partition with /dev/sdXN path, set its name to '' and discard it from model
        self.name = self.path if partition.number != -1 else ''

        encrypted = is_encrypted(self.path)
        try:
            if encrypted:
                self.type = 'luks'
            elif partition.fileSystem is not None:
                self.type = partition.fileSystem.type
            else:
                self.type = get_filesystem(partition.path)
            for fs in ('swap', 'hfs', 'ufs'):  # normalize fs variations (parted.filesystem.fileSystemType.keys())
                if fs in self.type:
                    self.type = fs
            self.style = self.type
        except AttributeError:  # non-formatted partitions
            self.type = {
                parted.PARTITION_LVM: 'LVM',
                parted.PARTITION_SWAP: 'swap',
                parted.PARTITION_RAID: 'RAID',  # Empty space on Extended partition is recognized as this
                parted.PARTITION_PALO: 'PALO',
                parted.PARTITION_PREP: 'PReP',
                parted.PARTITION_LOGICAL: _('Logical partition'),
                parted.PARTITION_EXTENDED: _('Extended partition'),
                parted.PARTITION_FREESPACE: _('Free space'),
                parted.PARTITION_HPSERVICE: 'HP Service',
                parted.PARTITION_MSFT_RESERVED: 'MSFT Reserved',
            }.get(partition.type, 'unknown')
            self.style = {
                parted.PARTITION_SWAP: 'swap',
                parted.PARTITION_FREESPACE: 'freespace',
            }.get(partition.type, '')

        # identify partition's label and used space
        mount_point = ''
        part_label = ''
        try:
            if encrypted:
                if installer.splash is not None:
                    installer.splash.destroy()
                cnt = 0
                title = _("Encryption password")
                while not is_connected(self):
                    cnt += 1
                    # Ask for password for encrypted partition and save it in enc_password
                    pwd = InputDialog(title=title,
                                      text="%s\n\n%s" % (_("Password for the encrypted partition ({0}/3):").format(cnt), self.path),
                                      is_password=True)
                    enc_passphrase = pwd.show()
                    self.enc_passphrase = enc_passphrase
                    mapped_drv = connect_block_device(self)
                    if cnt > 2:
                        break
                if is_connected(self):
                    self.enc_status = get_status(mapped_drv)
                    self.path = mapped_drv
                    self.name = mapped_drv
                    part_label = get_partition_label(mapped_drv)
                    self.mount_device(mapped_drv)
                else:
                    ErrorDialog(title, "{0} {1}".format(_("Failed to connect the encrypted partition:"), self.path))
            else:
                part_label = get_partition_label(self.path)
                if not "swap" in self.type:
                    self.mount_device(self.path)

            # Get size, free space and free percent
            mount_point = get_mount_point(self.path)
            statvfs = os.statvfs(mount_point)
            self.free_space = to_human_readable(statvfs.f_frsize * statvfs.f_bfree)
            self.used_percent = (self.size_b-(statvfs.f_frsize * statvfs.f_bfree))*(100/self.size_b)

        except Exception as detail:
            print(("WARNING: Partition %s or type %s failed to mount (%s)" % (self.path, self.type, detail)))
            self.os_fs_info, self.label, self.free_space, self.used_percent = ': '+self.type, part_label, '', 0
        else:
            # Had to rewrite label: multiple user errors
            if not part_label:
                if path_exists(mount_point, 'etc/'):
                    try:
                        self.label = get_release_name(mount_point)
                        if not self.label:
                            self.label = getoutput('uname -s')
                    except:
                        self.label = 'Unix'
                elif path_exists(mount_point, 'Windows/servicing/Version'):
                    try:
                        self.label = 'Windows ' + {
                            '10.0':'10',
                            '6.3':'8.1',
                            '6.2':'8',
                            '6.1':'7',
                            '6.0':'Vista',
                            '5.2':'XP Pro x64',
                            '5.1':'XP',
                            '5.0':'2000',
                            '4.9':'ME',
                            '4.1':'98',
                            '4.0':'95',
                        }.get(getoutput("ls %s/Windows/servicing/Version" % mount_point, True)[-1][:4].rstrip('.'), '')
                    except:
                        self.label = 'Windows'
                elif path_exists(mount_point, 'Boot/BCD'):
                    self.label = 'Windows recovery'
                elif path_exists(mount_point, 'Windows/System32'):
                    self.label = 'Windows'
                elif path_exists(mount_point, 'System/Library/CoreServices/SystemVersion.plist'):
                    self.label = 'Mac OS X'
            else:
                self.label = part_label
            # Used in disk-partitions.html
            self.os_fs_info = ': {0.label} ({0.type}; {0.size}; {0.free_space})'.format(self) if self.label else ': ' + self.type

        self.html_name = self.name.split('/')[-1]
        self.html_description = self.label
        if (self.size_percent < 10 and len(self.label) > 5):
            self.html_description = "%s..." % self.label[0:5]
        if (self.size_percent < 5):
            #Not enough space, don't write the name
            self.html_name = ""
            self.html_description = ""

        self.color = {
            # colors approximately from gparted
            'btrfs': '#ff9955',
            'exfat': '#47872a',
            'ext2':  '#2582a0',
            'ext3':  '#2582a0',
            'ext4':  '#21619e',
            'f2fs':  '#df421e',
            'fat16': '#47872a',
            'fat32': '#47872a',
            'hfs':   '#636363',
            'hfsplus':  '#c0a39e',
            'jfs':   '#636363',
            'luks':  '#3E3B4D',
            'LVM2_member':  '#b39169',
            'nilfs2': '#826647',
            'ntfs':  '#66a6a8',
            'reiser4': '#636363',
            'reiserfs': '#636363',
            'swap':  '#be3a37',
            'ufs':   '#636363',
            'xfs':   '#636363',
            'zfs':   '#636363',
            parted.PARTITION_EXTENDED: '#a9a9a9',
        }.get(self.type, '#a9a9a9')
        
        # Find out root and home partitions
        fstab = join(mount_point, 'etc/fstab')
        if exists(fstab):
            if installer.setup.oem_setup:
                # OEM setup: check if this partition is mounted as root
                if get_mount_point(self.path) == ROOT_MOUNT_POINT:
                    installer.setup.root_partition = self.path
            else:
                # Save this partition as a partition to mount as root
                if installer.setup.root_partition is None:
                    installer.setup.root_partition = self.path
                else:
                    # Empty the root, home and partition variables to show that this
                    # is a multi-boot system and no root or home partition may be pre-mounted
                    installer.setup.root_partition = ''
                    installer.setup.home_partition = ''
            # Get partition information from fstab
            if installer.setup.root_partition:
                fstab_cont = ''
                with open(fstab, 'r') as f:
                    fstab_cont = f.read()
                obj = re.search("([a-zA-Z0-9-/]+)\s+%s\s" % HOME_MOUNT_POINT, fstab_cont)
                if obj: installer.setup.home_partition = get_partition_path_from_string(obj.group(1))
                obj = re.search("([a-zA-Z0-9-/]+)\s+%s\s" % BOOT_MOUNT_POINT, fstab_cont)
                if obj: installer.setup.boot_partition = get_partition_path_from_string(obj.group(1))
                obj = re.search("([a-zA-Z0-9-/]+)\s+%s\s" % EFI_MOUNT_POINT, fstab_cont)
                if obj: installer.setup.efi_partition = get_partition_path_from_string(obj.group(1))
        
        # Unmount if temporary mount point
        if TMP_MOUNTPOINT in mount_point:
            shell_exec("umount -f %s" % self.path)

    def mount_device(self, device_path):
        # Check if mounted
        mount_point = get_mount_point(device_path)
        if mount_point == '':
            mount_point = '{0}/{1}'.format(TMP_MOUNTPOINT, basename(device_path))
            #print((">>> mount %s ro on %s" % (device_path, mount_point)))
            shell_exec('mkdir -p {0}; mount --read-only {1} {0}'.format(mount_point, device_path))
        return mount_point

    def print_partition(self):
        print("Device: %s (%s), format as: %s, mount as: %s, encrypt: %s" % (self.path, self.label, self.format_as, self.mount_as, self.encrypt))


class PartitionDialog(object):
    def __init__(self, partition, mount_as, format_as):
        self.partition = partition

        # Load window and widgets
        self.scriptName = basename(__file__)
        self.scriptDir = abspath(dirname(__file__))
        self.mediaDir = join(self.scriptDir, '../../share/live-installer-3')
        self.builder = Gtk.Builder()
        self.builder.add_from_file(join(self.mediaDir, 'live-installer-3-dialog.glade'))

        # Main window objects
        self.go = self.builder.get_object
        self.window = self.go("dialog")
        self.window.set_transient_for(installer.window)
        self.window.set_destroy_with_parent(True)
        self.window.set_modal(True)
        self.window.set_title(_("Edit partition"))
        self.loading = True
        self.txt_label = self.go("txt_label")
        self.cmb_mount_point = self.go("combobox_mount_point")
        self.cmb_mount_point_handler = ComboBoxHandler(self.cmb_mount_point)
        self.cmb_use_as = self.go("combobox_use_as")
        self.cmb_use_as_handler = ComboBoxHandler(self.cmb_use_as)

        # Translations
        self.go("label_partition").set_markup("<b>%s</b>" % _("Device"))
        self.go("label_use_as").set_markup(_("Format as"))
        self.go("label_mount_point").set_markup(_("Mount point"))
        self.go("label_label").set_markup(_("Label (optional)"))
        self.go("chk_encryption").set_label(_("Encrypt partition"))
        self.go("label_encryption_pwd").set_label(_("Password"))

        # Show the selected partition path
        self.go("label_partition_value").set_label(self.partition.path)

        # Encryption
        self.go("chk_encryption").set_active(partition.encrypt)
        if partition.encrypt:
            self.go("frm_partition_encryption").set_sensitive(True)
            self.go("entry_encpass1").set_text(partition.enc_passphrase)
            self.go("entry_encpass2").set_text(partition.enc_passphrase)
        else:
            self.go("frm_partition_encryption").set_sensitive(False)
            self.go("entry_encpass1").set_text('')
            self.go("entry_encpass2").set_text('')

        # Label
        label_len = 16
        if "fat" in partition.type or (partition.format_as and "fat" in partition.format_as):
            label_len = 11
        self.txt_label.set_max_length(label_len)
        self.txt_label.set_text(partition.label)

        # Build list of pre-provided mountpoints
        mounts = ['']
        mounts.append(ROOT_MOUNT_POINT)
        mounts.append(HOME_MOUNT_POINT)
        mounts.append(BOOT_MOUNT_POINT)
        mounts.append(EFI_MOUNT_POINT)
        mounts.append(SRV_MOUNT_POINT)
        mounts.append(TMP_MOUNT_POINT)
        mounts.append(VAR_MOUNT_POINT)
        mounts.append(SWAP_MOUNT_POINT)
        self.cmb_mount_point_handler.fillComboBox(mounts)
        
        # Select mount if given
        self.cmb_mount_point_handler.selectValue(mount_as)

        # Enable/disable objects in dialog
        self.on_combobox_mount_point_changed(self.cmb_mount_point)

        # Connect builder signals and show window
        self.builder.connect_signals(self)
        self.window.show_all()

        self.loading = False

    def on_combobox_mount_point_changed(self, widget):
        mount_as = self.cmb_mount_point_handler.getValue()
        self.cmb_use_as.set_sensitive(True)
        if mount_as == BOOT_MOUNT_POINT:
            ext_fs = ['ext2', 'ext3', 'ext4']
            if self.partition.type in ext_fs:
                filesystems = [''] + ext_fs
                select_fs = self.partition.type
            else:
                filesystems = ext_fs
                select_fs = 'ext4'
            self.cmb_use_as_handler.fillComboBox(filesystems, select_fs)
            self.txt_label.set_text(BOOT_LABEL)
            #self.disable_encryption()
        elif mount_as == EFI_MOUNT_POINT:
            fat_fs = ['fat', 'vfat']
            if 'fat' in self.partition.type:
                filesystems = [''] + fat_fs
                select_fs = self.partition.type
            else:
                filesystems = fat_fs
                select_fs = 'vfat'
            self.cmb_use_as_handler.fillComboBox(filesystems, select_fs)
            self.txt_label.set_text(EFI_LABEL)
            self.disable_encryption()
        elif mount_as == SWAP_MOUNT_POINT:
            self.cmb_use_as_handler.fillComboBox(['', 'swap'], '')
            #self.txt_label.set_text(SWAP_LABEL)
            #self.txt_label.set_sensitive(False)
            #self.cmb_use_as.set_sensitive(False)
        elif mount_as == ROOT_MOUNT_POINT:
            if self.partition.type in installer.setup.available_system_fs:
                select_fs = self.partition.type
            else:
                select_fs = 'ext4'
            self.cmb_use_as_handler.fillComboBox(installer.setup.available_system_fs, select_fs)
            self.txt_label.set_text(get_release_name())
        elif mount_as == HOME_MOUNT_POINT or \
             mount_as == SRV_MOUNT_POINT or \
             mount_as == TMP_MOUNT_POINT or \
             mount_as == VAR_MOUNT_POINT:
            if self.partition.type in installer.setup.available_system_fs:
                filesystems = [''] + installer.setup.available_system_fs
                select_fs = ''
            else:
                filesystems = installer.setup.available_system_fs
                select_fs = 'ext4'
            self.cmb_use_as_handler.fillComboBox(filesystems, select_fs)
            label = HOME_LABEL if mount_as == HOME_MOUNT_POINT else ''
            self.txt_label.set_text(label)
        elif mount_as == '':
            self.cmb_use_as_handler.fillComboBox([''])
            self.txt_label.set_text('')
        else:
            self.cmb_use_as_handler.fillComboBox([''] + installer.setup.available_fs)
            self.txt_label.set_text('')

    def on_combobox_use_as_changed(self, widget):
        format_as = self.cmb_use_as_handler.getValue()
        if format_as:
            label_len = 11 if 'fat' in format_as else 16
            self.txt_label.set_max_length(label_len)

    def on_button_cancel_clicked(self, widget):
        # Close window without saving
        self.window.hide()
        
    def disable_encryption(self):
        self.go("chk_encryption").set_active(False)
        self.go("chk_encryption").set_sensitive(False)
        self.go("frm_partition_encryption").set_sensitive(False)
        self.go("entry_encpass1").set_text('')
        self.go("entry_encpass2").set_text('')

    def on_chk_encryption_toggled(self, widget):
        if self.loading: return
        if widget.get_active():
            # Show warning message
            mount_as = self.cmb_mount_point_handler.getValue()
            if mount_as == ROOT_MOUNT_POINT and not installer.setup.gptonefi:
                encrypt = QuestionDialog(_("Encryption"),
                                         _("You chose to encrypt the root partition.\n\n"
                                           "You will need to mount {0} on a separate non-encrypted partition (500 MiB).\n"
                                           "Without a non-encrypted {0} partition your system will be unbootable.\n\n"
                                           "Encryption will erase all data from {1}\n\n"
                                           "Are you sure you want to continue?").format(BOOT_MOUNT_POINT, self.partition.path))
            else:
                encrypt = True
                if 'swap' not in mount_as:
                    encrypt = QuestionDialog(_("Encryption"),
                                             _("Encryption will erase all data from {}\n\n"
                                               "Are you sure you want to continue?").format(self.partition.path))
            if encrypt:
                format_as = self.cmb_use_as_handler.getValue()
                if not format_as:
                    mount_as = self.cmb_mount_point_handler.getValue()
                    if 'swap' in mount_as:
                        self.cmb_use_as_handler.selectValue('swap')
                    else:
                        self.cmb_use_as_handler.selectValue('ext4')
                self.go("frm_partition_encryption").set_sensitive(True)
                self.go("entry_encpass1").set_text(self.partition.enc_passphrase)
                self.go("entry_encpass2").set_text(self.partition.enc_passphrase)
                self.go("entry_encpass1").grab_focus()
            else:
                widget.set_active(False)
                self.go("frm_partition_encryption").set_sensitive(False)
                self.go("entry_encpass1").set_text("")
                self.go("entry_encpass2").set_text("")
        else:
            self.go("frm_partition_encryption").set_sensitive(False)
            self.go("entry_encpass1").set_text("")
            self.go("entry_encpass2").set_text("")

    def on_entry_encpass1_changed(self, widget):
        self.assign_enc_password()

    def on_entry_encpass2_changed(self, widget):
        self.assign_enc_password()

    def assign_enc_password(self):
        encryption_pwd1 = self.go("entry_encpass1").get_text()
        encryption_pwd2 = self.go("entry_encpass2").get_text()
        if(encryption_pwd1 == "" and encryption_pwd2 == ""):
            self.go("image_enc_mismatch").hide()
        else:
            self.go("image_enc_mismatch").show()
        if(encryption_pwd1 != encryption_pwd2):
            self.go("image_enc_mismatch").set_from_icon_name('dialog-no', Gtk.IconSize.BUTTON)
        else:
            self.go("image_enc_mismatch").set_from_icon_name('dialog-ok', Gtk.IconSize.BUTTON)

    def on_button_ok_clicked(self, widget):
        # Collect data
        format_as = self.cmb_use_as_handler.getValue()
        mount_as = self.cmb_mount_point_handler.getValue()
        encrypt = self.go("chk_encryption").get_active()
        enc_passphrase1 = self.go("entry_encpass1").get_text().strip()
        enc_passphrase2 = self.go("entry_encpass2").get_text().strip()
        label = self.txt_label.get_text().strip()

        # Check user input
        if encrypt:
            errorFound = False
            if enc_passphrase1 == "":
                errorFound = True
                errorMessage = _("Please provide an encryption password.")
            elif enc_passphrase1 != enc_passphrase2:
                errorFound = True
                errorMessage = _("Your encryption passwords do not match.")
            elif not format_as:
                errorFound = True
                errorMessage = "{} {}".format(_("You need to choose a format type\n"
                               "for your encrypted partition (default: ext4):"), self.partition.path)
                self.cmb_use_as_handler.selectValue('ext4')
            if not mount_as:
                errorFound = True
                errorMessage = "{} {}".format(_("You need to choose a mount point for partition:"), self.partition.path)

            if errorFound:
                ErrorDialog(_("Encryption"), errorMessage)
                return True
        else:
            # For good measure
            enc_passphrase1 = ''

        # Save the settings and close the window
        assign_mount_point(self.partition, mount_as, format_as, encrypt, enc_passphrase1, label)
        self.window.hide()

    def on_dialog_delete_event(self, widget, data=None):
        self.window.hide()
        return True
