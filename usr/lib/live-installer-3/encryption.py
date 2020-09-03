#!/usr/bin/env python3

import os
from utils import shell_exec, getoutput, \
                  get_package_version, compare_package_versions


def clear_partition(partition):
    unmount_partition(partition)
    enc_key = '-pbkdf2'
    openssl_version = get_package_version('openssl')
    if compare_package_versions(openssl_version, '1.1.1') == 'smaller':
        # deprecated key derivation in openssl 1.1.1+
        enc_key = '-aes-256-ctr'
    # Check "man openssl-enc" for options
    shell_exec("head -c 5M /dev/zero | openssl enc {0} -pass pass:\"$(dd if=/dev/urandom bs=128 count=1 2>/dev/null | base64)\" -nosalt > {1}".format(enc_key, partition.enc_status['device']))


def encrypt_partition(partition, luks_version=1):
    unmount_partition(partition)
    # Cannot use echo to pass the passphrase to cryptsetup because that adds a carriadge return
    # Use LUKS1 for now because grub does not support LUKS2, yet: 
    # https://git.savannah.gnu.org/cgit/grub.git/commit/?id=365e0cc3e7e44151c14dd29514c2f870b49f9755
    shell_exec("printf \"{passphras}\" | cryptsetup --cipher aes-xts-plain64 --key-size 512 --hash sha512 " \
               "--use-random --type luks{luks_version} --iter-time 5000 luksFormat {device}".format(passphras=partition.enc_passphrase, 
                                                                                                    luks_version=luks_version, 
                                                                                                    device=partition.enc_status['device']))
    return connect_block_device(partition)


def unmount_partition(partition):
    shell_exec("umount --force {}".format(partition.path))
    if "/dev/mapper" in partition.path:
        shell_exec("cryptsetup close {}".format(partition.path))


def connect_block_device(partition):
    mapped_name = os.path.basename(partition.enc_status['device'])
    shell_exec("printf \"{}\" | cryptsetup open {} {}".format(partition.enc_passphrase, partition.enc_status['device'], mapped_name))
    status = get_status(partition.path)
    return status['active']


def is_connected(partition):
    mapped_name = os.path.basename(partition.path)
    if os.path.exists(os.path.join("/dev/mapper", mapped_name)) and \
       partition.enc_passphrase != '':
        return True
    return False


def is_encrypted(partition_path):
    if "crypt" in get_filesystem(partition_path).lower() or '/dev/mapper' in partition_path:
        return True
    return False


def get_status(partition_path):
    status_dict = {'offset': '', 'mode': '', 'device': '', 'cipher': '', 'keysize': '', 'filesystem': '', 'active': '', 'type': '', 'size': ''}
    mapped_name = os.path.basename(partition_path)
    status_info = getoutput("env LANG=C cryptsetup status %s" % mapped_name)
    for line in status_info:
        parts = line.split(':')
        if len(parts) == 2:
            status_dict[parts[0].strip()] = parts[1].strip()
        elif " active" in line:
            parts = line.split(' ')
            status_dict['active'] = parts[0]
            status_dict['filesystem'] = get_filesystem(parts[0])

    # No info has been retrieved: save minimum
    if status_dict['device'] == '':
        status_dict['device'] = partition_path
    if status_dict['active'] == '' and is_encrypted(partition_path):
        mapped_name = os.path.basename(partition_path)
        status_dict['active'] = "/dev/mapper/{}".format(mapped_name)

    if status_dict['type'] != '':
        print(("Encryption: mapped drive status = {}".format(status_dict)))
    return status_dict


def get_filesystem(partition_path):
    ret = getoutput("blkid -o value -s TYPE {}".format(partition_path))
    if isinstance(ret, str):
        return ret
    elif ret:
        return ret[0]
    return ''


def get_uuid(partition_path):
    ret = getoutput("blkid -o value -s UUID {}".format(partition_path))
    if isinstance(ret, str):
        return ret
    elif ret:
        return ret[0]
    return ''


def get_partition_path(uuid):
    ret = getoutput("blkid -U {}".format(uuid.replace('UUID=', '')))
    if isinstance(ret, str):
        return ret
    elif ret:
        return ret[0]
    return ''


def create_keyfile(keyfile_path, partition):
    # Note: do this outside the chroot.
    # https://www.martineve.com/2012/11/02/luks-encrypting-multiple-partitions-on-debianubuntu-with-a-single-passphrase/
    if not os.path.exists(keyfile_path):
        os.makedirs(os.path.dirname(keyfile_path), exist_ok=True)
        #print((">> Create keyfile = %s" % keyfile_path))
        shell_exec("dd if=/dev/urandom of=%s bs=512 count=8 iflag=fullblock" % keyfile_path)
        shell_exec("chmod 0400 %s" % keyfile_path)
    #print((">> Add key to keyfile for device %s" % partition.enc_status['device']))
    shell_exec("printf \"%s\" | cryptsetup luksAddKey %s %s" % (partition.enc_passphrase, partition.enc_status['device'], keyfile_path))


def write_crypttab(crypttab_path, partition, keyfile_path=None):
    if keyfile_path is None:
        keyfile_path = str(keyfile_path).lower()
    crypttab_uuid = "UUID=%s" % getoutput("blkid -s UUID -o value %s" % partition.enc_status['device']) or partition.enc_status['device']
    swap = ''
    if partition.type == 'swap':
        swap = 'swap,'
    with open(crypttab_path, "a") as crypttab:
        crypttab.write("%s %s %s %sluks\n" % (os.path.basename(partition.path), crypttab_uuid, keyfile_path, swap))


# Returns dictionary {device: {target_name, uuid, key_file}}
def get_crypttab_info(crypttab_path):
    crypttab_info = {}
    if os.path.exists(crypttab_path):
        with open(crypttab_path, 'r') as f:
            for line in f.readlines():
                line = line.strip()
                lineData = line.split()
                uuid = lineData[1].split('=')[0]
                device = get_partition_path(uuid)
                if device != '':
                    key_file = lineData[2]
                    if key_file == 'none':
                        key_file = ''
                    crypttab_info[device] = {'target_name': lineData[0],
                                             'uuid': uuid,
                                             'key_file': key_file}
    return crypttab_info
