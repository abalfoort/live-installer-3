#!/usr/bin/env python3

# Optionally skip all mouting/partitioning for advanced users with custom setups (raid/dmcrypt/etc)
# Make sure the user knows that they need to:
#  * Mount their target directory structure at /target
#  * NOT mount /target/dev, /target/dev/shm, /target/dev/pts, /target/proc, and /target/sys
#  * Manually create /target/etc/fstab after init_install has completed and before finish_install is called
#  * Install cryptsetup/dmraid/mdadm/etc in target environment (using chroot) between init_install and finish_install
#  * Make sure target is mounted using the same block device as is used in /target/etc/fstab (eg if you change the name of a dm-crypt device between now and /target/etc/fstab, update-initramfs will likely fail)

from utils import shell_exec, shell_exec_popen, getoutput, chroot_exec, \
                  get_config_dict, has_internet_connection, in_virtualbox, \
                  get_boot_parameters, get_files_from_dir, is_package_installed, \
                  linux_distribution, replace_pattern_in_file, comment_line
from localize import Localize
from encryption import clear_partition, encrypt_partition, write_crypttab, \
                       create_keyfile
import partitioning
import os
from os.path import exists, isdir, basename, join, dirname, sep
import time
import threading
from logger import Logger
import shutil

# i18n: http://docs.python.org/3/library/gettext.html
import gettext
from gettext import gettext as _
from ntpath import dirname
gettext.textdomain('live-installer-3')

CONFIG_FILE = '/etc/live/live-installer-3.conf'

(ERROR,
WARNING,
UPDATE,
PAUSE
) = list(range(4))


class InstallerEngine(threading.Thread):
    def __init__(self, theQueue, setup):
        super(InstallerEngine, self).__init__()
        # Save current dir
        self.scriptDir = dirname(os.path.realpath(__file__))
        # Create a pause event
        self.pause_event = threading.Event()
        # Save the parameters
        self.queue = theQueue
        self.setup = setup
        # Set other configuration
        self.boot_parms = get_boot_parameters()
        self.medium_dir = '/lib/live/mount/medium'
        if not exists(join(self.medium_dir, 'live')):
            self.medium_dir = '/run/live/medium'
        self.media = join(self.medium_dir, 'live/filesystem.squashfs')
        if not exists(self.media):
            self.media = '/dev/loop0'
        self.media_type = 'squashfs'
        self.critical_error_happened = False
        self.our_total = 0
        self.our_current = 0
        self.ssd = False
        self.detachable = False
        self.ssd_partition = ""
        self.sorted_partitions = []
        self.installBroadcom = False

        # Log
        if self.setup.oem_setup:
            self.setup.log.write("--------------------->>> OEM User Setup <<<---------------------", "InstallerEngine.init")
        else:
            self.setup.log.write("------------------->>> Live Installation <<<-------------------", "InstallerEngine.init")

        manual_partitions = []
        if self.setup.skip_mount and not self.setup.oem_setup:
            # Create partition objects with the information of the manually mounted partitions
            manual_mounts = getoutput("cat /proc/mounts | grep -Ev '%s/dev|%s/sys|%s/proc' | grep %s" % (self.setup.target_dir, self.setup.target_dir, self.setup.target_dir, self.setup.target_dir), always_as_list=True, logger=self.setup.log)
            if manual_mounts[0] != '':
                try:
                    for manual_mount in manual_mounts:
                        device_info = manual_mount.split(" ")
                        if device_info:
                            # 0 = partition path, 1 = mount_as, 2 = fs type
                            path = device_info[0]
                            self.setup.log.write("Find manual mount {} in known partitions".format(path), "InstallerEngine.init")
                            mount_as = device_info[1].replace(self.setup.target_dir, "/").replace("//", "/")
                            efi_found = False
                            for partition in self.setup.partitions:
                                if partition.path == path:
                                    self.setup.log.write("Manual mount {} found will be mounted as {} with type {}".format(path, mount_as, partition.type), "InstallerEngine.init")
                                    partition.mount_as = mount_as
                                    if partitioning.BOOT_MOUNT_POINT in partition.mount_as:
                                        if not efi_found:
                                            self.setup.boot_partition = partition.path
                                        if "efi" in partition.mount_as:
                                            efi_found = True
                                    manual_partitions.append(partition)
                except:
                    self.setup.log.write("Could not handle manual mounts - exiting", "InstallerEngine.init", "exception")
                    return
            else:
                self.setup.log.write("Nothing mounted on %s - exiting" % self.setup.target_dir, "InstallerEngine.init", "error")
                return

        # Sort the partitions (needed for right order of mounting/unmounting
        mounts = []
        partitions = self.setup.partitions
        if manual_partitions:
            self.setup.log.write("Use manually mounted partitions", "InstallerEngine.init")
            partitions = manual_partitions
        for partition in partitions:
            # Get and sort mount partition information
            if partition.mount_as:
                mounts.append(partition.mount_as)
        # Sort mounts
        mounts.sort()
        for mount in mounts:
            for partition in partitions:
                if mount == partition.mount_as:
                    self.setup.log.write("Add mount {} to sorted partitions".format(mount), "InstallerEngine.init")
                    self.sorted_partitions.append(partition)

        # Check if mounted root is ssd of detachable
        for partition in self.sorted_partitions:
            if partition.mount_as == partitioning.ROOT_MOUNT_POINT:
                path = partition.path
                if "/dev/mapper" in path:
                    path = partition.enc_status['device']
                # Check if we need to treat this disk as an SSD
                for disk in self.setup.disks:
                    self.setup.log.write("Check disk {} for ssd".format(disk), "InstallerEngine.init")
                    if disk[0] in path:
                        self.ssd = disk[2]
                        self.detachable = disk[3]
                        self.ssd_partition = partition.path
                        self.setup.log.write("disk={}, ssd={}, detachable={}".format(disk[0], self.ssd, self.detachable), "InstallerEngine.init")
                        break
                break

    def pause(self):
        self.pause_event.set()

    def unpause(self):
        self.pause_event.clear()

    def run(self):
        self.critical_error_happened = False
        do_try_finish_install = True
        try:
            self.init_install()
        except Exception as detail1:
            self.setup.log.write(detail1, "InstallerEngine.run")
            do_try_finish_install = False
            self.show_dialog(ERROR,
                           _("Installation error"),
                           str(detail1))

        if self.critical_error_happened:
            do_try_finish_install = False

        if do_try_finish_install:
            if self.setup.skip_mount:
                self.pause()
                self.setup.log.write("Thread has been paused", "InstallerEngine.run")
                msg = "%s\n\n%s\n%s" % (_("Installation is now paused. Please read the instructions on the page carefully before clicking Forward to finish the installation."),
                                        _("Verify that fstab is correct (use blkid to check the UUIDs)."),
                                        _("A chrooted terminal and fstab will be opened after you close this message."))
                self.show_dialog(PAUSE,
                               _("Installation paused"),
                               msg)
                while self.pause_event.is_set():
                    time.sleep(1)
                self.setup.log.write("Thread has been unpaused from main thread", "InstallerEngine.run")

            try:
                self.finish_install()
            except Exception as detail1:
                self.setup.log.write(detail1, "InstallerEngine.run")
                self.show_dialog(ERROR,
                               _("Installation error"),
                               str(detail1))

    def update_progress(self, fail=False, done=False, pulse=False, message="", log=True):
        self.our_current += 1
        if log:
            self.setup.log.write("%s (%s of %s)" % (message, self.our_current, self.our_total), "InstallerEngine.update_progress", "info")
        info_list = [UPDATE, fail, done, pulse, self.our_total, self.our_current, message]
        self.queue.put(info_list)

    def show_dialog(self, dialog_type=ERROR, title="", message=""):
        if dialog_type == ERROR:
            self.critical_error_happened = True
        self.queue.put([dialog_type, title, message])

    def step_boot_prepare(self):
        for partition in self.sorted_partitions:
            # Set boot flag for partition
            if self.setup.boot_flag_partition is not None:
                bootDisk = self.setup.boot_flag_partition[0:-1]
                partDisk = partition.path[0:-1]
                if 'mmcblk' in bootDisk:
                    bootDisk = self.setup.boot_flag_partition[0:-2]
                    partDisk = partition.path[0:-2]
                if self.setup.boot_flag_partition == partition.path:
                    partNr = self.setup.boot_flag_partition[-1]
                    self.setup.log.write("Set boot flag on disk {}, partition {}".format(bootDisk, partNr), "InstallerEngine.step_boot_prepare")
                    cmd = "parted --script --align optimal {} set {} boot on; sync".format(bootDisk, partNr)
                    self.local_exec(cmd)
                    if self.setup.gptonefi:
                        self.setup.log.write("Set esp flag on disk {}, partition {}".format(bootDisk, partNr), "InstallerEngine.step_boot_prepare")
                        cmd = "parted --script --align optimal {} set {} esp on; sync".format(bootDisk, partNr)
                        self.local_exec(cmd)
                elif bootDisk != partDisk:
                    if 'boot' in partition.flags:
                        partNr = partition.path[-1]
                        self.setup.log.write("Remove boot flag from disk {}, partition {}".format(partDisk, partNr), "InstallerEngine.step_boot_prepare")
                        cmd = "parted --script --align optimal {} set {} boot off; sync".format(partDisk, partNr)
                        self.local_exec(cmd)

    def step_format_partitions(self):
        for partition in self.sorted_partitions:
            # Save new label
            newLabel = partition.label[0:16].strip()
            
            if partition.format_as is not None and partition.format_as != "":
                
                
                # Set counters
                self.our_total = 4
                self.our_current = 0

                # Encrypt partition when needed
                if partition.encrypt:
                    msg = _("Encrypting %(partition)s ..." % {'partition':partition.enc_status["device"]})
                    self.update_progress(pulse=True, message=msg)
                    # Make sure the partition is not mounted
                    partitioning.do_unmount(partition.path, True)
                    # Clear and encrypt the partition
                    #clear_partition(partition)
                    if partition.mount_as == partitioning.ROOT_MOUNT_POINT or \
                       partition.mount_as == partitioning.BOOT_MOUNT_POINT:
                        # Use LUKS1 for root and boot partitions
                        partition.path = encrypt_partition(partition)
                    else:
                        # Use LUKS2 for all other partitions
                        partition.path = encrypt_partition(partition, 2)

                # report it. should grab the total count of filesystems to be formatted ..
                msg = _("Formatting %(partition)s as %(format)s ..." % {'partition':partition.path, 'format':partition.format_as})
                self.update_progress(pulse=True, message=msg)

                # Check if a previous LUKS partition needs closing first
                if partition.path == partition.enc_status['device'] and \
                   exists(partition.enc_status['active']):
                        mapped_name = basename(partition.enc_status['active'])
                        # Make sure the partition is not mounted
                        partitioning.do_unmount(partition.path, True)
                        self.local_exec("cryptsetup close {}".format(mapped_name))

                #Format it
                if partition.format_as == "swap":
                    cmd = "mkswap %s" % partition.path
                elif partition.format_as[:3] == 'ext':
                    cmd = "mkfs.%s -F -q %s" % (partition.format_as, partition.path)
                elif partition.format_as == "jfs":
                    cmd = "mkfs.%s -q %s" % (partition.format_as, partition.path)
                elif partition.format_as in ("xfs", "btrfs", "nilfs2", "ntfs"):
                    cmd = "mkfs.%s -f -q %s" % (partition.format_as, partition.path)
                elif partition.format_as == "vfat":
                    cmd = "mkfs.%s %s -F 32" % (partition.format_as, partition.path)
                elif partition.format_as == "f2fs":
                    # You need to wipe all signatures from the partition before you can format to f2fs
                    # You can only label f2fs during format
                    if newLabel:
                        f2fs_label = "-l \"%s\"" % newLabel
                    cmd = "wipefs -a -f -q %s; mkfs.%s -f -q %s %s" % (partition.path, partition.format_as, f2fs_label, partition.path)
                else:
                    cmd = "mkfs.%s %s" % (partition.format_as, partition.path) # works with bfs, minix, msdos, exfat

                # Make sure the partition is not mounted
                partitioning.do_unmount(partition.path, True)
                # Start formatting the device
                self.local_exec(cmd)

                partition.type = partition.format_as

            # Set the label of the partition
            curLabel = partitioning.get_partition_label(partition.path)[0:16].strip()
            if newLabel and curLabel != newLabel:
                if "fat" in partition.type:
                    # Fat label cannot be longer than 12 characters
                    cmd = "fatlabel {} \"{}\"".format(partition.path, newLabel[0:11])
                elif "btrfs" in partition.type:
                    cmd = "btrfs {} \"{}\"".format(partition.path, newLabel)
                elif "ntfs" in partition.type:
                    cmd = "ntfslabel {} \"{}\"".format(partition.path, newLabel)
                elif "exfat" in partition.type:
                    cmd = "exfatlabel {} \"{}\"".format(partition.path, newLabel)
                elif "nilfs" in partition.type:
                    cmd = "nilfs-tune -L \"{}\" {}".format(newLabel, partition.path)
                elif partition.type == "swap":
                    cmd = "mkswap -L \"{}\" {}".format(newLabel, partition.path)
                elif partition.type == "jfs":
                    cmd = "jfs_tune -L \"{}\" {}".format(newLabel, partition.path)
                elif partition.type == "xfs":
                    # Limits to 12 characters
                    cmd = "xfs_admin  -L \"{}\" {}".format(newLabel[0:11], partition.path)
                else:
                    # ext2, ext3, or ext4
                    cmd = "e2label {} \"{}\"".format(partition.path, newLabel)

                try:
                    # Make sure the partition is not mounted
                    partitioning.do_unmount(partition.path, True)
                    # Set the label
                    self.local_exec(cmd)
                except:
                    self.setup.log.write("Could not write label \"{}\" to partition {}".format(partition.label, partition.path), "InstallerEngine.step_format_partitions")

    def step_mount_source(self):
        # Mount the installation media
        self.setup.log.write(" --> Mounting partitions", "InstallerEngine.step_mount_source", "info")
        msg = _("Mounting %(partition)s on %(mountpoint)s") % {'partition':self.media, 'mountpoint':"/source/"}
        self.update_progress(message=msg)
        partitioning.do_mount(self.media, "/source/", self.media_type, options="loop")

    def step_mount_partitions(self):
        # Mount source
        if not self.setup.oem_setup:
            self.step_mount_source()

        # Mount the sorted partitions
        for partition in self.sorted_partitions:
            if "/" in partition.mount_as:
                target = self.setup.target_dir
                if partition.mount_as != partitioning.ROOT_MOUNT_POINT:
                    target += partition.mount_as
                msg = _("Mounting %(partition)s on %(mountpoint)s") % {'partition':partition.path, 'mountpoint':target}
                self.update_progress(message=msg)
                self.local_exec("mkdir -p %s 2>/dev/null" % target)
                partition_type = partition.type
                if partition_type.startswith('fat'):
                    partition_type = 'vfat'
                elif partition_type == 'luks':
                    partition_type = partition.enc_status['filesystem']
                partitioning.do_mount(partition.path, target, partition_type, None)

    def init_install(self):
        self.setup.log.write(" --> Installation started", 'InstallerEngine.init_install', "info")

        if self.setup.oem_setup:
            self.step_format_partitions()
            self.step_mount_partitions()
        else:
            self.do_unmount_dev()

            # mount the media location.
            if not exists(self.setup.target_dir):
                if self.setup.skip_mount:
                    msg = _("You must first manually mount your target filesystem(s) at %s to do a custom install!" % self.setup.target_dir)
                    self.setup.log.write(msg, 'InstallerEngine.init_install', "error")
                    self.show_dialog(ERROR,
                                   _("Not mounted"),
                                    msg)
                    return
                os.mkdir(self.setup.target_dir)
            if not exists("/source"):
                os.mkdir("/source")
            # find the squashfs..
            if not exists(self.media):
                msg = _("Something is wrong with the installation medium! This is usually caused by burning tools which are not compatible with {}. Please burn the ISO image to DVD/USB using a different tool.".format(self.setup.distribution_name))
                self.setup.log.write(msg, 'InstallerEngine.init_install')
                self.show_dialog(ERROR,
                               _("Base filesystem does not exist"),
                                msg)
                return

            if not self.setup.skip_mount:
                self.step_boot_prepare()
                self.step_format_partitions()
                self.step_mount_partitions()
            else:
                self.step_mount_source()

            # # Preserve /root if it exists
            if isdir("%s/root" % self.setup.target_dir):
                # shutil.copytree gave errors on kde cache files
                self.local_exec("cp -r %s/root /tmp/" % self.setup.target_dir)

            # Transfer the files
            self.our_current = 0
            prev_sec = -1
            SOURCE = '/source'

            # assume: #(files to copy) ~= #(used inodes on /)
            self.our_total = int(getoutput("df --inodes /{src} | awk '/^.+?\/{src}$/{{ print $3 }}'".format(src=SOURCE.strip('/')), logger=self.setup.log))
            self.setup.log.write(" --> Copying {} files".format(self.our_total), 'InstallerEngine.init_install', "info")
            rsync = shell_exec_popen("rsync -aAXv --exclude={\"*/dev/*\",\"*/proc/*\",\"*/sys/*\",\"*/tmp/*\",\"*/run/*\",\"*/mnt/*\"," \
                                     "\"*/media/*\",\"*/home/*\",\"*/lost+found\"} %(src)s/ %(trg)s/" % {'src': SOURCE, 'trg': self.setup.target_dir})

            # Check the output of rsync
            while rsync.poll() is None:
                # Cleanup the line: only path of file to be copied
                try:
                    line = rsync.stdout.readline().strip()
                    line = line[0:line.index(' ')]
                except:
                    pass
                if not line:
                    time.sleep(0.1)
                else:
                    self.our_current = min(self.our_current + 1, self.our_total)

                    # Check if localtime is on the second to prevent flooding the queue
                    sec = time.localtime()[5]
                    if sec != prev_sec:
                        self.update_progress(message="{} {}".format(_("Copying"), line), log=False)
                        prev_sec = sec

            rsync_return_code = rsync.poll()
            if rsync_return_code > 0:
                # Warn the user when an rsync error occured
                # e.g.: rsync return code 23 = I/O error (bad disk)
                msg = _("System copy ended abruptly.\nYour system might not function properly (rsync code: %s)." % str(rsync_return_code))
                self.setup.log.write(msg, 'InstallerEngine.init_install', "error")
                self.show_dialog(ERROR,
                               _("System copy error"),
                                msg)

            # Restore /root if it was preserved
            if isdir("/tmp/root"):
                if isdir("%s/root" % self.setup.target_dir):
                    self.local_exec("mv %s/root /tmp/root.install" % self.setup.target_dir)
                self.local_exec("mv /tmp/root %s/" % self.setup.target_dir)

            # Check if we need to install offline Broadcom drivers
            ddm_path = join(self.scriptDir, 'scripts/ddm-broadcom.sh')
            if exists(ddm_path):
                device_ids = getoutput("lspci -n -d 14e4: | awk '{print $3}' | cut -d':' -f 2", True, logger=self.setup.log)
                if device_ids:
                    #print "Broadcom deviceids: {}".format(' '.join(device_ids))
                    wl_ids = getoutput("cat {} | grep 'WLDEBIAN=' | cut -d'=' -f 2".format(ddm_path), logger=self.setup.log).split('|')
                    for did in device_ids:
                        did = did.strip()
                        if did:
                            for wl_id in wl_ids:
                                if did == wl_id:
                                    self.setup.log.write("Supported Broadcom deviceid found: {}".format(did), 'InstallerEngine.init_install', "info")
                                    self.installBroadcom = True
                                    break
                            if self.installBroadcom:
                                break

            # Steps:
            self.our_total = self.get_progress_total()
            self.our_current = 0

            # chroot
            self.setup.log.write(" --> Chrooting", 'InstallerEngine.init_install', "info")
            self.update_progress(message=_("Entering the system ..."))
            self.do_mount_dev()
            # Check if /target/dev is mounted
            if partitioning.get_mount_point('', "%s/dev" % self.setup.target_dir) == '':
                msg = _("%s/dev not mounted - exiting" % self.setup.target_dir)
                self.setup.log.write(msg, 'InstallerEngine.init_install', "error")
                self.show_dialog(ERROR,
                               _("Not mounted"),
                                msg)
                return
            self.local_exec("mv %s/etc/resolv.conf %s/etc/resolv.conf.bk" % (self.setup.target_dir, self.setup.target_dir))
            self.local_exec("cp -f /etc/resolv.conf %s/etc/resolv.conf" % self.setup.target_dir)

            if self.setup.gptonefi:
                self.setup.log.write(" --> Installing EFI packages", 'InstallerEngine.init_install', "info")
                self.update_progress(message=_("Installing EFI packages..."))

                self.local_exec("mkdir -p %s/debs" % self.setup.target_dir)
                self.local_exec("cp %s/offline/*efi*.deb %s/debs/" % (self.medium_dir, self.setup.target_dir))
                ret = self.exec_cmd("dpkg --force-confdef --force-confnew --force-depends --force-overwrite -i /debs/*.deb")
                self.local_exec("rm -rf %s/debs" % self.setup.target_dir)
                if int(ret) != 0:
                    if has_internet_connection():
                        self.exec_cmd("apt-get purge grub-efi")
                    # TODO: Errors were reported after installing grub-efi and leaving grub-pc
                    # (although it should have been removed in the previous process)
                    self.exec_cmd("apt-get purge grub-pc")
                    self.exec_cmd("apt-get install -f")

            # Detect cdrom device
            # TODO : properly detect cdrom device
            # Mount it
            # os.system("mkdir -p %s/media/cdrom" % self.setup.target_dir)
            # if int(os.system("mount /dev/sr0 %s/media/cdrom" % self.setup.target_dir)):
            #     print " --> Failed to mount CDROM. Install will fail"
            # self.exec_cmd("apt-cdrom -o Acquire::cdrom::AutoDetect=false -m add")

            if self.installBroadcom:
                self.setup.log.write(" --> Installing drivers", 'InstallerEngine.init_install', "info")
                self.update_progress(message=_("Installing drivers"))
                self.local_exec("mkdir -p %s/debs" % self.setup.target_dir)
                self.local_exec("cp %s/offline/broadcom*.deb %s/debs/" % (self.medium_dir, self.setup.target_dir))
                self.exec_cmd("dpkg --force-confdef --force-confnew --force-depends --force-overwrite -i /debs/*.deb")
                self.exec_cmd("modprobe wl")
                self.local_exec("rm -rf %s/debs" % self.setup.target_dir)
                with open("%s/etc/modprobe.d/blacklist-broadcom.conf" % self.setup.target_dir, "w") as conf:
                    conf.write('blacklist b43 brcmsmac bcma ssb')

        if self.setup.oem_setup:
            self.our_total = self.get_progress_total()
            self.our_current = 0

        # write the /etc/fstab, /etc/crypttab, /etc/initramfs-tools/modules
        self.update_progress(message=_("Writing filesystem mount information to /etc/fstab"))
        fstab_path = "%s/etc/fstab" % self.setup.target_dir
        crypttab_path = "%s/etc/crypttab" % self.setup.target_dir
        modules_path = "%s/etc/initramfs-tools/modules" % self.setup.target_dir
        with open(fstab_path, 'w') as f:
            f.write('# <file system>\t<mount point>\t<type>\t<options>\t<dump>\t<pass>\n')
        with open(crypttab_path, 'w') as f:
            f.write('# <target name>\t<source device>\t<key file>\t<options>\n')

        # Configure the system
        # Check UUIDs
        border = '=' * 25
        self.setup.log.write("{}\n>> Compare fstab/crypttab UUIDS with blkid output <<\n"
                       "fstab: /dev/mapper/sdXY UUIDs, crypttab: /dev/sdXY UUIDS\n"
                       "{} blkid {}\n{}\n{}\n".format(border * 2, border, border,
                       "\n".join(getoutput("blkid", logger=self.setup.log)), border * 2), 'InstallerEngine.init_install', "info")

        # Check if partitions need to be encrypted and decide if we need to create a key file
        keyfile_partition = None
        prev_keyfile_partition = None
        enc_root_configured = False
        for partition in self.sorted_partitions:
            if partition.encrypt:
                if partition.mount_as == partitioning.ROOT_MOUNT_POINT:
                    # Encrypted root: save key file here.
                    keyfile_partition = partition
                    break
                else:
                    # Save key file on first partition only when there are
                    # more than one partitions encrypted (excl. swap).
                    if prev_keyfile_partition:
                        keyfile_partition = prev_keyfile_partition
                        break
                    elif partition.mount_as != partitioning.SWAP_MOUNT_POINT:
                        prev_keyfile_partition = partition

        for partition in self.sorted_partitions:
            #print((">> fstab partition mount point = %s" % partition.mount_as))
            # Skip if partition doesn't need to be mounted
            if not partition.mount_as:
                continue
            partition_type = partition.type
            if partition_type.startswith('fat'):
                partition_type = 'vfat'
            elif partition_type == 'luks':
                partition_type = partition.enc_status['filesystem']
            elif partition_type in ('nilfs2', 'f2fs') and partition.mount_as == partitioning.ROOT_MOUNT_POINT:
                # Load nilfs2 kernel module
                mod = "%s\n" % partition_type
                if partition_type =='f2fs':
                    mod += "crc32\n"
                with open(modules_path, 'a') as f:
                    f.write(mod)
            comment = False
            if self.setup.username[-4:] == "-oem" and partition.mount_as == partitioning.HOME_MOUNT_POINT:
                comment = True
            self.write_fstab(fstab_path, partition, partition_type, comment)

            # crypttab/keyfile
            if partition.encrypt or partition.type == 'luks':
                if partition.enc_status['device'] != '':
                    key = None

                    # Create key file
                    if keyfile_partition is not None:
                        # Root is encrypted
                        if partition.mount_as == partitioning.ROOT_MOUNT_POINT:
                            key = join(keyfile_partition.mount_as, "keys/%s.key" % basename(partition.path))
                            key_pattern = join(dirname(key), '*.key')
                            
                            if self.setup.gptonefi:
                                keyfile_path = join(self.setup.target_dir, key.lstrip(sep))
                                self.setup.log.write(("Path to key file: %s" % keyfile_path), 'InstallerEngine.init_install', "debug")
                                create_keyfile(keyfile_path, partition)
                            else:
                                # Do not set key file on non-efi systems
                                key = None
                            
                            conf_hook = join(self.setup.target_dir, 'etc/cryptsetup-initramfs/conf-hook')
                            
                            # Make sure cryptsetup-initramfs is installed
                            self.exec_cmd("apt-get install cryptsetup-initramfs")
                            
                            # Add keyfile to initrd
                            replace_pattern_in_file(r'^#?KEYFILE_PATTERN\s*=.*', 'KEYFILE_PATTERN="{kp}"'.format(kp=key_pattern), conf_hook)
                            
                            # Add cryptsetup and its dependencies to the initramfs image.
                            # By default, they're only added when
                            # a device is detected that needs to be unlocked at initramfs stage
                            # (such as root or resume devices or ones with explicit 'initramfs' flag
                            # in /etc/crypttab).
                            replace_pattern_in_file(r'^#?CRYPTSETUP\s*=.*', 'CRYPTSETUP=y', conf_hook)
                            
                            # Set restrictive umask for initramfs
                            initramfs_conf = join(self.setup.target_dir, 'etc/initramfs-tools/initramfs.conf')
                            replace_pattern_in_file(r'^#?UMASK\s*=.*', 'UMASK=0077', initramfs_conf)
                            
                            # Let Grub know there is an encrypted boot partition
                            default_grub = join(self.setup.target_dir, "etc/default/grub")
                            replace_pattern_in_file(r'^#?GRUB_ENABLE_CRYPTODISK\s*=.*', 'GRUB_ENABLE_CRYPTODISK=y', default_grub)
                                
                            enc_root_configured = True
                                
                            
                        elif partition.mount_as != keyfile_partition.mount_as:
                            # Non-root encrypted partitions
                            key = join(keyfile_partition.mount_as, "keys/%s.key" % basename(partition.path))
                            keyfile_path = join(self.setup.target_dir, key.lstrip(sep))
                            self.setup.log.write(("Path to key file: %s" % keyfile_path), 'InstallerEngine.init_install', "debug")
                            create_keyfile(keyfile_path, partition)

                    # Write /etc/crypttab
                    write_crypttab(crypttab_path, partition, key)
                    
            if not enc_root_configured:
                # root partition has not been encrypted:
                # add cryptsetup-initramfs to list of removable packages
                self.setup.packages_remove.append('cryptsetup-initramfs')

        # Configure system for SSD or installing to detachable device
        if self.ssd or self.detachable:
            # SDD optimization
            ram = "\n# RAM disks\n" \
            "tmpfs   /tmp                    tmpfs   defaults,noatime,mode=1777              0       0\n" \
            "#tmpfs   /var/cache/apt/archives tmpfs   defaults,noexec,nosuid,nodev,mode=0755 0       0\n" \
            "tmpfs   /var/tmp                tmpfs   defaults,noatime                        0       0\n" \
            "tmpfs   /var/backups            tmpfs   defaults,noatime                        0       0\n" \
            "# Disable /var/log/* tmpfs dirs when enabling tmpfs on /var/log\n" \
            "#tmpfs   /var/log                tmpfs   defaults,noatime                        0       0\n" \
            "#tmpfs   /var/log/apt            tmpfs   defaults,noatime,mode=0755              0       0\n" \
            "#tmpfs   /var/log/lightdm        tmpfs   defaults,noatime,mode=0755              0       0\n" \
            "#tmpfs   /var/log/samba          tmpfs   defaults,noatime,mode=0755              0       0\n" \
            "tmpfs   /var/log/cups           tmpfs   defaults,noatime,mode=0755               0       0\n" \
            "tmpfs   /var/log/ConsoleKit     tmpfs   defaults,noatime,mode=0755               0       0\n" \
            "tmpfs   /var/log/clamav         tmpfs   defaults,noatime,mode=0755,uid=clamav,gid=clamav 0       0\n"
            
            with open(fstab_path, "a") as fstab:
                fstab.write(ram)

            # Fstrim
            if exists("%s/lib/systemd/system/fstrim.timer" % self.setup.target_dir):
                self.exec_cmd('systemctl enable fstrim.timer')
            else:
                fstrim_path = "%s/etc/cron.weekly/fstrim_job" % self.setup.target_dir
                fstrim_cont = "#!/bin/sh\n" \
                              "for fs in $(lsblk -o MOUNTPOINT,DISC-MAX,FSTYPE | grep -E '^/.* [1-9]+.* ' | awk '{print $1}'); do\n" \
                              "  fstrim \"$fs\"\n" \
                              "done\n"
                with open(fstrim_path, "w") as fstrim:
                    fstrim.write(fstrim_cont)
                self.local_exec("chmod +x {}".format(fstrim_path))

            # Swappiness
            swappiness_path = "%s/etc/sysctl.d/sysctl.conf" % self.setup.target_dir
            swappiness_cont = "vm.swappiness=1\n" \
                              "vm.vfs_cache_pressure=25\n" \
                              "vm.dirty_ratio=50\n" \
                              "vm.dirty_background_ratio=3\n"
            with open(swappiness_path, "w") as swappiness:
                swappiness.write(swappiness_cont)

            # Sysfs
            sysfs_path = "%s/etc/sysfs.conf" % self.setup.target_dir
            sysfs_cont = "block/{}/queue/scheduler=deadline\n".format(basename(self.ssd_partition))
            with open(sysfs_path, "w") as sysfs:
                sysfs.write(sysfs_cont)

            # Browser RAM cache
            # Only configure browser RAM cache for user on a pen drive
            if self.detachable:
                cache_path = "%s/etc/profile.d/browser-cache.sh" % self.setup.target_dir
                cache_cont = "# Check for interactive bash and that we haven't already been sourced.\n" \
                             "if [ -n \"$BASH_VERSION\" -a -n \"$PS1\" -a -z \"$BASH_COMPLETION_COMPAT_DIR\" ]; then\n" \
                             "  # Create RAM cache for Firefox and Chromium\n" \
                             "  IDS=$(cat /etc/passwd | grep bash | grep home | cut -d':' -f 3)\n" \
                             "  for ID in $IDS; do\n" \
                             "    FF=\"/run/user/$ID/firefox-cache\"\n" \
                             "    if [ ! -e $FF ]; then\n" \
                             "      mkdir -p $FF &\n" \
                             "      chown -R $ID:$ID $FF\n" \
                             "    fi\n" \
                             "    CH=\"/run/user/$ID/chromium-cache\"\n" \
                             "    if [ ! -e $CH ]; then\n" \
                             "      mkdir -p $CH &\n" \
                             "      chown -R $ID:$ID $CH\n" \
                             "    fi\n" \
                             "  done\n" \
                             "fi\n"
                with open(cache_path, "w") as cache:
                    cache.write(cache_cont)

                prefs_path = "%s%s/%s/.mozilla/firefox/user.default/prefs.js" % (self.setup.target_dir, partitioning.HOME_MOUNT_POINT, self.setup.username)
                if exists(prefs_path):
                    # Save to prefs file
                    prefs_cont = "user_pref(\"browser.cache.disk.parent_directory\", \"/run/user/1000/firefox-cache\");"
                    with open(prefs_path, "a") as prefs:
                        prefs.write(prefs_cont)

        # Show the fstab contents
        if exists(fstab_path):
            with open(fstab_path, "r") as fstab:
                self.setup.log.write("{} fstab {}\n{}\n{}\n".format(border, border, fstab.read(), border *2), 'InstallerEngine.init_install')

        # Show the crypttab contents
        if exists(crypttab_path):
            with open(crypttab_path, "r") as crypttab:
                self.setup.log.write("{} crypttab {}\n{}\n{}\n".format(border, border, crypttab.read(), border * 2), 'InstallerEngine.init_install')
                
        # Show the fstab contents
        if exists(modules_path):
            with open(modules_path, "r") as modules:
                self.setup.log.write("{} modules {}\n{}\n{}\n".format(border, border, modules.read(), border *2), 'InstallerEngine.init_install')

        # Mount partitions in fstab
        if self.setup.oem_setup:
            self.step_mount_partitions()

        # set the locale
        self.setup.log.write(" --> Setting the locale", "InstallerEngine.finish_install", "info")
        self.update_progress(message=_("Setting locale"))
        cmd = "echo '{0}' > /etc/timezone && " \
              "rm /etc/localtime; ln -sf /usr/share/zoneinfo/{0} /etc/localtime && " \
              "sed -i -e 's/^#\s*{1}.UTF-8 UTF-8/{1}.UTF-8 UTF-8/' /etc/locale.gen && " \
              "echo 'LANG={1}.UTF-8' > /etc/default/locale && " \
              "dpkg-reconfigure --frontend=noninteractive locales && " \
              "update-locale LANG={1}.UTF-8".format(self.setup.timezone, self.setup.language)
        self.exec_cmd(cmd)
        
        # add new user
        self.setup.log.write(" --> Adding new user", 'InstallerEngine.init_install', "info")
        self.update_progress(message=_("Adding new user to the system"))
        # Do not create oem user dir in /home (in case the home partition needs encrypting)
        oem_prm = ''
        if self.setup.username[-4:] == "-oem":
            oem_prm = "--home /%s --firstuid 990 --lastuid 999 --ingroup root" % self.setup.username
        self.exec_cmd('adduser {oem} --disabled-login --gecos "{real_name}" {username}'.format(oem=oem_prm,real_name=self.setup.real_name.replace('"', r'\"'), username=self.setup.username))
        if in_virtualbox():
            # Make sure the vboxsf and vboxusers groups exist
            self.exec_cmd("addgroup vboxsf")
            self.exec_cmd("addgroup vboxusers")
        for group in 'adm audio bluetooth cdrom dialout fax floppy fuse lpadmin netdev plugdev powerdev sambashare scanner sudo tape users vboxsf vboxusers video systemd-journal'.split():
            self.exec_cmd("adduser {user} {group}".format(user=self.setup.username, group=group))

        # Double check if the user directory exists
        # Had this when setting a previously encrypted partition to /home
        user_dir = "%s%s/%s" % (self.setup.target_dir, partitioning.HOME_MOUNT_POINT, self.setup.username)
        if not exists(user_dir):
            self.setup.log.write("Create user dir: {}".format(user_dir), 'InstallerEngine.init_install', "info")
            self.local_exec("mkdir {}".format(user_dir))

        # Save passwords
        # Using a temporary file fails for the new user (but correctly sets the root's password)
        # Using mkpasswd prevents not setting a password when special characters like $ or " are used
        pwd = getoutput("mkpasswd --method=sha-512 '{}'".format(self.setup.password1), logger=self.setup.log)
        self.setup.log.write('Encrypt password {} for user {} to {}'.format(self.setup.password1, self.setup.username, pwd), 'InstallerEngine.init_install')
        set_pwd_path = '/set_pwd.sh'
        if not self.setup.oem_setup:
            set_pwd_path = "%s%s" % (self.setup.target_dir, set_pwd_path)
        set_pwd_cont = "#!/bin/bash\necho '{0}:{1}' | chpasswd -e\necho 'root:{1}' | chpasswd -e\n".format(self.setup.username, pwd)
        self.setup.log.write("Write password bash: {}:\n{}".format(set_pwd_path, set_pwd_cont), 'InstallerEngine.init_install')
        with open(set_pwd_path, 'w') as f:
            f.write(set_pwd_cont)
        self.local_exec('chmod +x {}'.format(set_pwd_path))
        self.exec_cmd('. /set_pwd.sh')
        os.remove(set_pwd_path)
        #self.exec_cmd("echo '{}:{}' | chpasswd -e".format(self.setup.username, pwd))
        #self.exec_cmd("echo 'root:{}' | chpasswd -e".format(pwd))

        # Set autologin for user if they so elected
        conf = join(self.setup.target_dir, 'etc/lightdm/lightdm.conf')
        if self.setup.autologin:
            # LightDM
            replace_pattern_in_file(r'^#?autologin-user\s*=.*', 'autologin-user={user}'.format(user=self.setup.username), conf)
        else:
            # Remove autologin in live session with Lightdm
            comment_line('autologin-user', conf)

        # Add user's face if it doesn't already exist
        face_path = "%s%s/%s/.face" % (self.setup.target_dir, partitioning.HOME_MOUNT_POINT, self.setup.username)
        if not exists(face_path):
            self.local_exec("cp /tmp/live-installer-3-face.png %s" % face_path)

    def local_exec(self, command):
        shell_exec(command, self.setup.log)

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
            return self.local_exec(command)
        else:
            return chroot_exec(command, self.setup.target_dir, self.setup.language)

    def write_fstab(self, fstab_path, partition, partition_type, comment=False):
        # Get file system options
        opts = 'defaults'
        if partition_type == 'swap':
            opts = 'sw'
        elif self.ssd or self.detachable:
            if 'ext' in partition_type:
                opts = 'rw,noatime,errors=remount-ro'
            elif partition_type == 'nilfs2':
                opts = 'defaults,noatime'
            elif partition_type == 'f2fs':
                opts = 'rw,noatime,background_gc=on,user_xattr,acl,active_logs=6'
        
        if partition.encrypt or partition.type == 'luks':
            uuid = partition.path
        else:
            # Get partition UUID
            uuid = "UUID=%s" % getoutput('blkid -s UUID -o value ' + partition.path, logger=self.setup.log) or partition.path

        # Skip mounting home if setting up the OEM user
        # This gives the OEM user the chance to encrypt the home directory
        if comment:
            uuid = "#%s" % uuid

        # Decide when to use fsck
        fsck = 0 if partition_type in ('ntfs', 'swap', 'vfat', 'nilfs2') else 1 if partition.mount_as == partitioning.ROOT_MOUNT_POINT else 2
        
        # Build fstab line for partition
        new_line = '%s\t%s\t%s\t%s\t0\t%s\n' % (uuid, partition.mount_as, partition_type, opts, fsck)

        with open(fstab_path, "a") as fstab:
            fstab.write(new_line)


    def finish_install(self):
        # write host+hostname infos
        self.setup.log.write(" --> Writing hostname", "InstallerEngine.finish_install", "info")
        self.update_progress(message=_("Setting hostname"))
        hostname_path = "%s/etc/hostname" % self.setup.target_dir
        with open(hostname_path, "w") as hostnamefh:
            line = "%s\n" % self.setup.hostname
            self.setup.log.write("Hostname: %s" % line, "InstallerEngine.finish_install", "info")
            hostnamefh.write(line)

        hosts_path = "%s/etc/hosts" % self.setup.target_dir
        with open(hosts_path, "w") as hostsfh:
            cont = "127.0.0.1\tlocalhost\n" \
                   "127.0.1.1\t%s\n" \
                   "# The following lines are desirable for IPv6 capable hosts\n" \
                   "::1     localhost ip6-localhost ip6-loopback\n" \
                   "fe00::0 ip6-localnet\n" \
                   "ff00::0 ip6-mcastprefix\n" \
                   "ff02::1 ip6-allnodes\n" \
                   "ff02::2 ip6-allrouters\n" \
                   "ff02::3 ip6-allhosts\n" % self.setup.hostname
            hostsfh.write(cont)

        # Upgrade the system if needed
        if has_internet_connection():
            self.setup.log.write(" --> Upgrade the new system when needed", "InstallerEngine.finish_install", "info")
            self.update_progress(message=_("Update apt cache"))
            self.exec_cmd("apt-get update")
            self.update_progress(message=_("Update the new system"))
            self.exec_cmd("apt-get upgrade")

        # localizing
        if self.setup.language != "en_US":
            if exists(join(self.medium_dir, "pool")):
                self.setup.log.write(" --> Localizing packages", "InstallerEngine.finish_install", "info")
                self.update_progress(message=_("Localizing packages"))
                self.local_exec("mkdir -p %s/debs" % self.setup.target_dir)
                language_code = self.setup.language
                if "_" in self.setup.language:
                    language_code = self.setup.language.split("_")[0]
                l10ns = getoutput("find %/pool | grep 'l10n-%s\\|hunspell-%s'" % (self.medium_dir, language_code, language_code), logger=self.setup.log)
                for l10n in l10ns.split("\n"):
                    self.local_exec("cp %s %s/debs/" % (l10n, self.setup.target_dir))
                self.exec_cmd("dpkg -i /debs/*")
                self.local_exec("rm -rf %s/debs" % self.setup.target_dir)
            if has_internet_connection():
                # Localize
                loc = Localize(self.setup, self.setup.target_dir)
                loc.set_progress_hook(self.update_progress)
                loc.start()

        # set the keyboard options..
        self.setup.log.write(" --> Setting the keyboard", "InstallerEngine.finish_install", "info")
        self.update_progress(message=_("Setting keyboard options"))
        console_setup = '/etc/default/console-setup'
        if not self.setup.oem_setup:
            console_setup = "%s/etc/default/console-setup" % self.setup.target_dir
        with open(console_setup, "r") as consolefh:
            lines = consolefh.readlines()
        with open("%s.new" % console_setup, "w") as newconsolefh:
            for line in lines:
                line = line.rstrip("\r\n")
                if line.startswith("XKBMODEL="):
                    newconsolefh.write("XKBMODEL=\"%s\"\n" % self.setup.keyboard_model)
                elif line.startswith("XKBLAYOUT="):
                    newconsolefh.write("XKBLAYOUT=\"%s\"\n" % self.setup.keyboard_layout)
                elif line.startswith("XKBVARIANT=") and self.setup.keyboard_variant is not None:
                    newconsolefh.write("XKBVARIANT=\"%s\"\n" % self.setup.keyboard_variant)
                else:
                    newconsolefh.write("%s\n" % line)
        self.local_exec("rm %s" % console_setup)
        self.local_exec("mv %s.new %s" % (console_setup, console_setup))

        keyboard = '/etc/default/keyboard'
        if not self.setup.oem_setup:
            keyboard = "%s/etc/default/keyboard" % self.setup.target_dir
        with open(keyboard, "r") as consolefh:
            lines = consolefh.readlines()
        with open("%s.new" % keyboard, "w") as newconsolefh:
            for line in lines:
                line = line.rstrip("\r\n")
                if line.startswith("XKBMODEL="):
                    newconsolefh.write("XKBMODEL=\"%s\"\n" % self.setup.keyboard_model)
                elif line.startswith("XKBLAYOUT="):
                    newconsolefh.write("XKBLAYOUT=\"%s\"\n" % self.setup.keyboard_layout)
                elif line.startswith("XKBVARIANT=") and self.setup.keyboard_variant is not None:
                    newconsolefh.write("XKBVARIANT=\"%s\"\n" % self.setup.keyboard_variant)
                else:
                    newconsolefh.write("%s\n" % line)
        self.local_exec("rm %s" % keyboard)
        self.local_exec("mv %s.new %s" % (keyboard, keyboard))

        if self.setup.username[-4:] == "-oem":
            # Make sure live-installer starts on next boot full screen
            with open("%s/etc/xdg/autostart/oem-setup.desktop" % self.setup.target_dir, "w") as oemf:
                cont = "[Desktop Entry]\n" \
                       "Encoding=UTF-8\n" \
                       "Name=OEM Setup\n" \
                       "Comment=Setup user for OEM installation\n" \
                       "Exec=live-installer --oem\n" \
                       "Terminal=false\n" \
                       "Type=Application\n"
                # Comment the following line when testing OEM setup
                oemf.write(cont)

            # OEM user does not need to set a root password
            oem_no_pwd = "%s/etc/sudoers.d/oem-no-pwd" % self.setup.target_dir
            with open(oem_no_pwd, "w") as nopwd:
                nopwd.write("%s ALL=(ALL) NOPASSWD: ALL\n" % self.setup.username)
            self.local_exec("chmod 440 %s" % oem_no_pwd)
            
            # Force pkexec not to ask a password
            policy = join(self.setup.target_dir, "usr/share/polkit-1/actions/com.solydxk.pkexec.live-installer-3.policy")
            replace_pattern_in_file(r'auth_admin', 'yes', policy)
            

        # Configure sensors
        if exists("%s/usr/sbin/sensors-detect" % self.setup.target_dir):
            self.setup.log.write(" --> Configuring sensors", "InstallerEngine.finish_install", "info")
            self.update_progress(message=_("Configuring sensors"))
            self.exec_cmd('/usr/bin/yes YES | /usr/sbin/sensors-detect')

        # Remove VirtualBox when not installing to VirtualBox or installing to pen drive
        if not in_virtualbox() or self.detachable:
            self.setup.log.write(" --> Remove VirtualBox", "InstallerEngine.finish_install", "info")
            self.update_progress(pulse=True, message=_("Removing VirtualBox"))
            self.exec_cmd("apt-get purge virtualbox*")

        # Remove os-prober when installing to pen drive
        if self.detachable:
            self.setup.log.write(" --> Remove os-prober", "InstallerEngine.finish_install", "info")
            self.update_progress(pulse=True, message=_("Removing os-prober"))
            self.exec_cmd("apt-get purge os-prober")

        # write MBR (grub)
        if self.setup.grub_device is None:
            shutil.rmtree(join(self.setup.target_dir, 'boot/grub'))
        else:
            self.setup.log.write(" --> Configuring Grub", "InstallerEngine.finish_install", "info")
            # Copy mo files for Grub if needed
            cmd = "mkdir -p {0}/boot/grub/locale && " \
                  "for F in $(find {0}/usr/share/locale -name 'grub.mo'); do " \
                  "MO='{0}/boot/grub/locale/'$(echo $F | cut -d'/' -f 6)'.mo'; " \
                  "cp -afuv $F $MO; done".format(self.setup.target_dir)
            self.local_exec(cmd)
            

            # Save current boot parameters
            default_grub = join(self.setup.target_dir, "etc/default/grub")
            if exists(default_grub) and len(self.boot_parms) > 0:
                if in_virtualbox():
                    # We needed nomodeset in a live session in VB but not when installed
                    if 'nomodeset' in self.boot_parms:
                        self.boot_parms.remove('nomodeset')
                    # When booting in EFI in VirtualBox with an encrypted partition Plymouth will break the system!
                    if 'splash' in self.boot_parms:
                        for partition in self.sorted_partitions:
                            if partition.mount_as:
                                if partition.encrypt or partition.type == 'luks':
                                    self.setup.log.write("Remove splash from boot parameters", "InstallerEngine.finish_install", "info")
                                    self.boot_parms.remove('splash')
                                    break

                replace_pattern_in_file(r'^#?GRUB_CMDLINE_LINUX_DEFAULT\s*=.*', 'GRUB_CMDLINE_LINUX_DEFAULT="{parms}"'.format(parms=' '.join(self.boot_parms)), default_grub)

                # Configure Plymouth
                if exists("%s/bin/plymouth" % self.setup.target_dir) and 'splash' in self.boot_parms:
                    replace_pattern_in_file(r'^#?GRUB_GFXMODE\s*=.*', 'GRUB_GFXMODE=1024x768', default_grub)

                # Create grub.cfg
                self.do_configure_grub()
                grub_retries = 0
                while not self.do_check_grub():
                    self.do_configure_grub()
                    grub_retries = grub_retries + 1
                    if grub_retries >= 5:
                        msg = _("The grub bootloader was not configured properly! You need to configure it manually.")
                        self.setup.log.write(msg, "InstallerEngine.finish_install")
                        self.show_dialog(WARNING,
                                       _("Grub not configured"),
                                       msg)
                        break
                
                
            if not self.setup.oem_setup:
                self.update_progress(pulse=True, message=_("Installing bootloader"))
                
                # DeprecationWarning: platform.dist() and platform.linux_distribution() functions are deprecated in Python 3.5
                linux_dist = linux_distribution()
                
                bootloader_id = linux_dist[0].lower() + linux_dist[1]
                grub_efi_var = '--efi-directory={0} --bootloader-id="{1}"'.format(partitioning.EFI_MOUNT_POINT, bootloader_id)

                if self.detachable:
                    # Install legacy grub on a pen drive
                    self.setup.log.write(" --> Install legacy grub on pen drive", "InstallerEngine.finish_install", "info")
                    self.exec_cmd('grub-install --force --target=i386-pc --recheck --boot-directory={0} {1}'.format(partitioning.BOOT_MOUNT_POINT, self.setup.grub_device))
                    if self.setup.gptonefi:
                        # Install both i386 and x86_64 EFI on a pen drive if EFI is installed
                        self.setup.log.write(" --> Installing i386 EFI on pen drive", "InstallerEngine.finish_install", "info")
                        self.exec_cmd('grub-install --target=i386-efi --removable --recheck {0} {1}'.format(grub_efi_var, self.setup.grub_device))
                        self.setup.log.write(" --> Installing x86_64 EFI on pen drive", "InstallerEngine.finish_install", "info")
                        self.exec_cmd('grub-install --target=x86_64-efi --removable --recheck {0} {1}'.format(grub_efi_var, self.setup.grub_device))
                else:
                    self.setup.log.write(" --> Running grub-install", "InstallerEngine.finish_install", "info")
                    self.exec_cmd('grub-install --force {0} {1}'.format(grub_efi_var, self.setup.grub_device))

        # remove live-packages (or w/e)
        self.setup.log.write(" --> Removing live packages", "InstallerEngine.finish_install", "info")
        msg = _("Removing live configuration (packages)")
        if self.setup.username[-4:] == "-oem":
            self.update_progress(pulse=True, message=msg)
            # Save the packages-remove file for the OEM setup
            with open('{}/root/packages_remove'.format(self.setup.target_dir), 'w') as f:
                f.write(' '.join(self.setup.packages_remove))
            self.local_exec("cp %s %s/root/" % (packages_remove, self.setup.target_dir))
            # But remove the live packages
            self.exec_cmd("apt-get purge ^live-*")
        else:
            pck_str = ''
            
            # We are in oem setup
            if self.setup.oem_setup:
                pr_file = '/root/packages-remove'
                if exists(pr_file):
                    with open(pr_file, 'r') as f:
                        self.setup.packages_remove = f.read().split()
            
            # Show some progress
            self.update_progress(pulse=True, message=msg)
            
            # Check each package if it is installed and create packages string
            for package in self.setup.packages_remove:
                if is_package_installed(package):
                    pck_str += '{} '.format(package)
            
            if pck_str:
                self.exec_cmd('apt-get purge {0}'.format(pck_str))
            else:
                # At least remove all live packages
                self.exec_cmd("apt-get purge ^live-*")
        
        # Clean APT
        self.setup.log.write(" --> Cleaning APT", "InstallerEngine.finish_install", "info")
        self.update_progress(pulse=True, message=_("Cleaning APT"))
        cleanup = "#!/bin/bash\n" \
                  "sed -i 's/^deb cdrom/#deb cdrom/' /etc/apt/sources.list\n" \
                  "{df} dpkg --configure -a\n" \
                  "{df} apt-get install {ao} -f\n" \
                  "{df} apt-get clean\n" \
                  "{df} apt-get {ao} autoremove\n" \
                  "while [ \"$(deborphan)\" ]; do\n" \
                  "  {df} apt-get {ao} purge $(deborphan)\n" \
                  "done\n".format(df = self.setup.debian_frontend,
                                  ao = self.setup.apt_options)
        with open("%s/cleanup.sh" % self.setup.target_dir, "w") as f:
            f.write(cleanup)
        self.local_exec("chmod +x %s/cleanup.sh" % self.setup.target_dir)
        self.exec_cmd(". /cleanup.sh")
        os.remove("%s/cleanup.sh" % self.setup.target_dir)
        
        # We need to update initramfs in case of partition encryption
        # Note: before cleanup you would have to use: /usr/sbin/update-initramfs.orig.initramfs-tools -u
        self.setup.log.write(" --> Update Initramfs", "InstallerEngine.finish_install", "info")
        self.update_progress(pulse=True, message=_("Update Initramfs"))
        self.exec_cmd("update-initramfs -u")
        
        # Remove temporary files
        for f in self.setup.post_install_remove:
            tf = '{0}/{1}'.format(self.setup.target_dir, f)
            if exists(tf):
                os.remove(tf)

        # Fix EFI in VirtualBox
        if self.setup.gptonefi and not self.detachable:
            if in_virtualbox():
                efi_root = "%s%s" % (self.setup.target_dir, partitioning.EFI_MOUNT_POINT)
                efi_files = get_files_from_dir(efi_root, "grubx*.efi")
                if len(efi_files) > 0:
                    efi_path = efi_files[0].replace(efi_root, '').replace("/", "\\")
                    # Create startup.nsh to make boot with EFI possible within VB
                    with open("{}/startup.nsh".format(efi_root), "w") as f:
                        f.write("{}\n".format(efi_path))
                            
        # Create new machine-id
        self.exec_cmd('rm -f /etc/machine-id /var/lib/dbus/machine-id; dbus-uuidgen --ensure=/etc/machine-id')

        # Create SHA file of initrd.img
        kernelversion = getoutput("uname -r", logger=self.setup.log)
        self.exec_cmd("/usr/bin/sha1sum %s/initrd.img-%s > /var/lib/initramfs-tools/%s" % (partitioning.BOOT_MOUNT_POINT, kernelversion, kernelversion))

        #Make absolutely sure that the new user is owner of its own home directory
        self.exec_cmd("chown -R %s:%s %s/%s" % (self.setup.username, self.setup.username, partitioning.HOME_MOUNT_POINT, self.setup.username))

        if not self.setup.oem_setup:
            # now unmount it
            self.setup.log.write(" --> Unmounting partitions", "InstallerEngine.finish_install", "info")
            self.update_progress(message=_("Unmounting partitions"))
            #if self.setup.gptonefi:
                #self.local_exec("umount --force %s/media/cdrom")
            self.do_unmount_dev()
            self.local_exec("rm -f %s/etc/resolv.conf" % self.setup.target_dir)
            self.local_exec("mv %s/etc/resolv.conf.bk %s/etc/resolv.conf" % (self.setup.target_dir, self.setup.target_dir))
            if not self.setup.skip_mount:
                # Unmount partitions
                for partition in reversed(self.sorted_partitions):
                    if "/" in partition.mount_as:
                        target = self.setup.target_dir
                        if partition.mount_as != partitioning.ROOT_MOUNT_POINT:
                            target += partition.mount_as
                        partitioning.do_unmount(target)
            partitioning.do_unmount("/source")
            self.local_exec("rmdir %s" % self.setup.target_dir)

        self.update_progress(done=True, message=_("Installation finished"))
        self.setup.log.write(" --> All done", "InstallerEngine.finish_install", "info")

    def do_unmount_dev(self):
        partitioning.do_unmount("%s/dev/shm" % self.setup.target_dir, True)
        partitioning.do_unmount("%s/dev/pts" % self.setup.target_dir, True)
        partitioning.do_unmount("%s/dev" % self.setup.target_dir, True)
        partitioning.do_unmount("%s/sys/fs/fuse/connections" % self.setup.target_dir, True)
        partitioning.do_unmount("%s/sys" % self.setup.target_dir, True)
        partitioning.do_unmount("%s/proc" % self.setup.target_dir, True)

    def do_mount_dev(self):
        partitioning.do_mount(device="/dev", mountpoint="%s/dev" % self.setup.target_dir, filesystem=None, options="--bind")
        partitioning.do_mount("/dev/shm", "%s/dev/shm" % self.setup.target_dir, None, "--bind")
        partitioning.do_mount("/dev/pts", "%s/dev/pts" % self.setup.target_dir, None, "--bind")
        partitioning.do_mount("/sys", "%s/sys" % self.setup.target_dir, None, "--bind")
        partitioning.do_mount("/sys/fs/fuse/connections", "%s/sys/fs/fuse/connections" % self.setup.target_dir, None, "--bind")
        partitioning.do_mount("/proc", "%s/proc" % self.setup.target_dir, None, "--bind")

    def do_configure_grub(self):
        self.update_progress(message=_("Configuring bootloader"))
        self.setup.log.write(" --> Running grub-mkconfig", "InstallerEngine.do_configure_grub", "info")
        grub_output = getoutput("chroot %s/ /bin/sh -c \"grub-mkconfig -o %s/grub/grub.cfg\"" % (self.setup.target_dir, partitioning.BOOT_MOUNT_POINT), logger=self.setup.log)
        if grub_output:
            self.setup.log.write("\n".join(grub_output), "InstallerEngine.do_configure_grub")

    def do_check_grub(self):
        self.update_progress(message=_("Checking bootloader"))
        self.setup.log.write(" --> Checking Grub configuration", "InstallerEngine.do_check_grub", "info")
        time.sleep(5)
        found_entry = False
        grub_cfg = "%s%s/grub/grub.cfg" % (self.setup.target_dir, partitioning.BOOT_MOUNT_POINT)
        if exists(grub_cfg):
            with open(grub_cfg, "r") as grubfh:
                for line in grubfh:
                    line = line.rstrip("\r\n")
                    if "menuentry " in line:
                        found_entry = True
                        self.setup.log.write("Found Grub entry: %s " % line, "InstallerEngine.do_check_grub")
                        break
            return found_entry
        else:
            self.setup.log.write(_("No %s file found!") % grub_cfg, "InstallerEngine.do_check_grub", "error")
            return False

    def get_progress_total(self):
        total = 8

        if not self.setup.oem_setup:
            total += 4
            if self.setup.gptonefi:
                total += 1
            if self.installBroadcom:
                total += 1
            if has_internet_connection():
                total += 1
            if exists(join(self.medium_dir, "pool")):
                total += 1
            if not in_virtualbox():
                total += 1
            if self.detachable:
                total += 2
            if self.setup.grub_device is not None:
                total += 1

        if has_internet_connection():
            total += 2
            if self.setup.language != "en_US":
                if exists(join(self.medium_dir, "pool")):
                    total += 1
                localizeConf = join(self.scriptDir, "localize/%s" % self.setup.language)
                if exists(localizeConf):
                    total += 1
                if is_package_installed("kde-runtime"):
                    total += 1
                if is_package_installed("libreoffice"):
                    total += 1
                if is_package_installed("abiword"):
                    total += 1
                if is_package_installed("firefox") or is_package_installed("firefox-esr"):
                    total += 1
                if is_package_installed("thunderbird"):
                    total += 1

        return total


# Represents the choices made by the user
class Setup(object):
    def __init__(self):
        self.config = get_config_dict(CONFIG_FILE)
        self.logged_user = getoutput("logname")
        self.oem_setup = False
        if self.logged_user[-4:] == "-oem":
            self.oem_setup = True
        self.target_dir = self.config.get('target', '/target')
        self.my_ip = self.config.get('my_ip', 'https://ifconfig.me')
        self.face = self.config.get('face', '/usr/share/pixmaps/faces/user_generic.png')
        if self.oem_setup:
            self.target_dir = ""
        self.debian_frontend = "DEBIAN_FRONTEND=%s" % self.config.get('debian_frontend', 'noninteractive')
        self.apt_options = self.config.get('apt_options_9', '')
        self.rec_root_size_gb = int(self.config.get('rec_root_size_gb', 30))
        self.min_root_size_gb = int(self.config.get('min_root_size_gb', 8))
        self.min_home_size_gb = int(self.config.get('min_home_size_gb', 1))
        self.min_efi_size_mb = int(self.config.get('min_efi_size_mb', 500))
        self.min_boot_size_mb = int(self.config.get('min_efi_size_mb', 500))
        self.available_fs = getoutput("dpkg -S bin/mkfs. | grep -v cramfs | cut -d'.' -f 2 | sort")
        self.exclude_system_fs = self.config.get('exclude_system_fs', '').split()
        self.post_install_remove = self.config.get('post_install_remove', '').split()
        self.packages_remove = self.config.get('packages_remove', 'live-*').split()
        self.available_system_fs = [x for x in self.available_fs if x not in self.exclude_system_fs]
        self.oem_home_encryption_pwd1 = None
        self.oem_home_encryption_pwd2 = None
        self.distribution_name = None
        self.distribution_id = None
        self.distribution_version = None
        self.language = None
        self.timezone = None
        self.keyboard_model = None
        self.keyboard_layout = None
        self.keyboard_variant = None
        self.partitions = [] #Array of PartitionSetup objects
        self.username = None
        self.hostname = None
        self.autologin = False
        self.password1 = None
        self.password2 = None
        self.real_name = None
        self.grub_device = None
        self.disks = []
        self.gptonefi = False
        self.boot_partition = None
        self.boot_flag_partition = None
        self.efi_partition = None
        self.skip_mount = False
        self.home_partition = None
        self.root_partition = None

        #Descriptions (used by the summary screen)
        self.keyboard_model_description = None
        self.keyboard_layout_description = None
        self.keyboard_variant_description = None

        # Log
        self.log = Logger("/var/log/live-installer-3.log")
        self.log.write("--------------------->>> Setup information init <<<---------------------", "installer.Setup")
        setup_description = "Live Setup"
        if self.oem_setup:
            setup_description = "OEM Setup"
        self.log.write(setup_description, "installer.Setup")
        self.log.write("Logged user: %s" % self.logged_user, "installer.Setup")
        self.log.write("Target directory: %s" % self.target_dir, "installer.Setup")
        self.log.write("Debian frontend: %s" % self.debian_frontend, "installer.Setup")
        self.log.write("apt options: %s" % self.apt_options, "installer.Setup")
        self.log.write("-------------------------------------------------------------------------", "installer.Setup")

    def print_setup(self, info=''):
        self.log.write("--------------------->>> Setup information %s <<<---------------------" % info, info)
        self.log.write("Language: %s" % self.language, info)
        self.log.write("Time zone: %s" % self.timezone, info)
        self.log.write("Keyboard model: %s (%s), layout: %s (%s), variant: %s (%s)" %
                      (self.keyboard_model,
                       self.keyboard_model_description,
                       self.keyboard_layout,
                       self.keyboard_layout_description,
                       self.keyboard_variant,
                       self.keyboard_variant_description), info)
        self.log.write("Host name: %s " % self.hostname, info)
        self.log.write("User: %s/%s (autologin: %s)" % (self.username, self.real_name, str(self.autologin)), info)
        self.log.write("Password: %s" % self.password1, info)
        if self.oem_setup and \
           not self.oem_home_encryption_pwd1 is None and \
           self.oem_home_encryption_pwd1 == self.oem_home_encryption_pwd2:
            self.log.write("Encrypt home partion with password %s" % self.oem_home_encryption_pwd1, info)
        self.log.write("Grub device: %s " % self.grub_device, info)
        self.log.write("Boot partition: %s" % self.boot_partition, info)
        self.log.write("Boot flag partition: %s" % self.boot_flag_partition, info)
        if not self.skip_mount:
            self.log.write("GPT partition table: %s" % str(self.gptonefi), info)
            self.log.write("Disks:", info)
            for disk in self.disks:
                self.log.write("{}\t{}".format(disk[0], disk[1], disk[2], disk[3]), info)
            self.log.write("Partitions:", info)
            for partition in self.partitions:
                partition.print_partition()
        else:
            self.log.write("Skip mount", info)
        self.log.write("-------------------------------------------------------------------------", info)
