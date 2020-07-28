#!/bin/bash

# ==============================================
# DDM Broadcom driver installation for Stretch
# ==============================================
# Based on: https://wireless.wiki.kernel.org/en/users/drivers/b43
# Last update: 04-09-2017
# ==============================================

# Broadcom hardware list (device ids)
B43='|4307|4311|4312|4315|4318|4319|4320|4321|4322|4324|4328|4329|432b|432c|4331|4350|4353|4357|4358|4359|43a9|43aa|a8d8|a8db|'
B43LEGACY='|4301|4306|4325|'
WLDEBIAN='|0576|4313|432a|432d|4365|43a0|43b1|435a|4727|a8d6|a99d|'
BRCMDEBIAN=''
UNKNOWN='|4360|'

# Default value to use backports
BACKPORTS=false

# Default value for testing
# Set TESTHWCARD to test the script with different hardware.
TEST=false
#TESTHWCARD='Broadcom Corporation BCM4322 802.11a/b/g/n Wireless LAN Controller [14e4:432b]'
TESTHWCARD='Broadcom Corporation BCM4306 802.11bgn Wireless Network Adapter [14e4:4320]'

# Default to show supported hardware only
SHOW=false

# ==============================================
# End configuration
# ==============================================

# Log file for traceback
MAX_SIZE_KB=5120
LOG_SIZE_KB=0
LOG=/var/log/solydxk-system.log
LOG2=/var/log/solydxk-system.log.1
if [ -f $LOG ]; then
  LOG_SIZE_KB=$(ls -s $LOG | awk '{print $1}')
  if [ $LOG_SIZE_KB -gt $MAX_SIZE_KB ]; then
    mv -f $LOG $LOG2
  fi
fi

# ==============================================

function get_backports_string() {
  PCK=$1
  local BPSTR=''
  BP=$(grep backports /etc/apt/sources.list /etc/apt/sources.list.d/*.list | grep debian | grep -v 'list:#' | awk '{print $3}')
  if [ "$BP" != '' ]; then
    BP=$(echo $BP | cut -d' ' -f 1)
    PCKCHK=$(apt-cache madison $PCK | grep "$BP")
    if [ "$PCKCHK" != '' ]; then
      BPSTR="-t $BP"
    fi
  fi
  echo $BPSTR
}

# ==============================================

function usage() {
  echo
  echo "Device Driver Manager Help for $(basename $0)"
  echo 
  echo 'The following options are allowed:'
  echo
  echo '-b           Use backported packages when available.'
  echo
  echo '-h           Show this help.'
  echo
  echo '-s           Show supported hardware.'
  echo
  echo '-t           For developers: simuluate driver installation.'
  echo '             Change TESTHWCARD in this script to select different hardware.'
  echo
}

# ==============================================
# ==============================================

# Get bash arguments
FORCE=false
while getopts ':bfhst' opt; do
  case $opt in
    b)
      # Backports
      BACKPORTS=true
      ;;
    f)
      FORCE=true
      ;;
    h)
      usage
      exit 0
      ;;
    s)
      # Show supported hardware
      SHOW=true
      ;;
    t)
      # Testing
      TEST=true
      ;;
    :)
      echo "Option -$OPTARG requires an argument."
      exit 2
      ;;
    *)
      # Unknown error
      echo "Unknown argument $@"
      exit 2
      ;;
  esac
done

# Run this script as root
if [ $UID -ne 0 ] && ! $SHOW; then
  sudo "$0" "$@"
  exit $?
fi

# Get distribution release
if [ -f /etc/debian_version ]; then
  DISTRIB_RELEASE=$(head -n 1 /etc/debian_version 2>/dev/null | sed 's/[a-zA-Z]/0/' | cut -d'.' -f 1)
fi

if [ -z $DISTRIB_RELEASE ]; then
  echo '[BROADCOM] Cannot get the Debian version from /etc/debian_version.' | tee -a $LOG
  echo '[BROADCOM] Please install the base-files package.' | tee -a $LOG
  exit 8
fi

# Non-numeric values means that it's testing (sid)
if [[ $DISTRIB_RELEASE =~ '^[0-9]+$' ]] ; then
  if [ $DISTRIB_RELEASE -lt 9 ]; then
    echo '[BROADCOM] This script is for Debian Stretch and beyond.' | tee -a $LOG
    exit 0
  fi
fi

# Get device ids for Broadcom
BCID='14e4'
HWCARDS=`lspci -nn -d $BCID:`
# Cleanup
HWCARDS="${HWCARDS#*: }"
HWCARDS="${HWCARDS%(rev*}"
# Testing
if $TEST; then
  HWCARDS=$TESTHWCARD
fi
DEVICEIDS=$(echo "$HWCARDS" | grep -Po '(?<=\:)[0-9a-z]*(?=\])')

if [ "$DEVICEIDS" == '' ]; then
  if ! $SHOW; then
    echo '[BROADCOM] No Broadcom device found.' | tee -a $LOG
  fi
  exit 0
fi

# Install the Broadcom drivers
# Get the appropriate driver
if [ ! $TEST ]; then
  apt-get update
fi
for DID in $DEVICEIDS; do
  if [[ "$B43" =~ "|$DID|" ]] ; then
    PCKS='firmware-b43-installer'
    MODPROBE='b43'
  elif [[ "$B43LEGACY" =~ "|$DID|" ]] ; then
    PCKS='firmware-b43legacy-installer'
    MODPROBE='b43legacy'
  elif [[ "$WLDEBIAN" =~ "|$DID|" ]] ; then
    PCKS='broadcom-sta-dkms'
    BLACKLIST='blacklist b43 brcmsmac bcma ssb'
    MODPROBE='wl'
  elif [[ "$BRCMDEBIAN" =~ "|$DID|" ]] ; then
    PCKS='firmware-brcm80211'
    MODPROBE='brcmsmac'
  fi
  
  # Show supported hardware only
  if [ "$HWCARDS" != '' ] && $SHOW; then
    echo "$HWCARDS [$PCKS]"
    exit 0
  fi
  
  if [ -z $PCKS ]; then
    echo "[BROADCOM] This Broadcom device is not supported: $DID" | tee -a $LOG
    exit 7
  else
    # Add dependencies and check if they are already installed
    ADDITIONALPCKS=`apt-cache depends $PCKS | grep Depends: | awk '{print $2}' | sed '/>/d' | tr '\n' ' '`
    ADDITIONALPCKS="linux-headers-$(uname -r) $ADDITIONALPCKS"
    for PCK in $ADDITIONALPCKS; do
      INSTALLED=`env LANG=C apt-cache policy $PCK | grep Installed | awk '{print $2}' | tr -d ' '`
      if [ "$INSTALLED" == '' ]; then
        PCKS="$PCKS $PCK"
      fi
    done
    
    if $TEST && ! $FORCE; then
      echo "[BROADCOM] - TEST - Install packages: $PCKS" | tee -a $LOG
    else
      # Preseed debconf answers
      echo 'b43-fwcutter b43-fwcutter/install-unconditional boolean true' | debconf-set-selections

      # Create download directory
      CURDIR=$PWD
      DLDIR='/tmp/dl'
      mkdir -p $DLDIR 2>/dev/null
      cd $DLDIR
      rm -f *.deb 2>/dev/null
      
      # Download the packages
      
      for PCK in $PCKS; do
        # Backport?
        BP=''
        if $BACKPORTS; then
          BP=$(get_backports_string $PCKS)
        fi
        echo "[BROADCOM] Run command: apt-get download $BP $PCKS" | tee -a $LOG
        apt-get download $BP $PCKS 2>&1 | tee -a $LOG
      done
      
      # Check if packages were downloaded
      CNT=`ls -1 *.deb 2>/dev/null | wc -l`
      if [ $CNT -eq 0 ]; then
        echo '[BROADCOM] - ERROR - No packages were downloaded.' | tee -a $LOG
        exit 5
      fi

      # Remove modules
      modprobe -rf b44
      modprobe -rf b43
      modprobe -rf b43legacy
      modprobe -rf ssb
      modprobe -rf brcmsmac

      # Install the downloaded packages
      dpkg -i *.deb 2>&1 | tee -a $LOG
      
      # Remove download directory
      cd $CURDIR
      rm -r $DLDIR
      
      # Blacklist if needed
      CONF='/etc/modprobe.d/blacklist-broadcom.conf'
      if [ -z $BLACKLIST ]; then
        rm -f $CONF 2>/dev/null
      else
        echo "[BROADCOM] Blacklist written: $BLACKLIST" | tee -a $LOG
        echo $BLACKLIST > $CONF
      fi

      # Start the new driver
      modprobe $MODPROBE

      echo '[BROADCOM] Broadcomm drivers installed.' | tee -a $LOG  
    fi
  fi
done
